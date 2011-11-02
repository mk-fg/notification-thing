#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function


import argparse

class Enum(dict):
	def __init__(self, *keys, **kwz):
		if not keys: super(Enum, self).__init__(**kwz)
		else:
			vals = kwz.pop('vals', range(len(keys)))
			if kwz: raise TypeError(kwz)
			super(Enum, self).__init__(zip(keys, vals))
	def __getattr__(self, k):
		if not k.startswith('__'): return self[k]
		else: raise AttributeError
	def by_id(self, v_chk):
		for k,v in self.viewitems():
			if v == v_chk: return k
		else: raise KeyError(v_chk)

def EnumAction(enum):
	class EnumAction(argparse.Action):
		def __call__(self, parser, namespace, values, option_string=None):
			setattr(namespace, self.dest, self.enum[values])
	EnumAction.enum = enum
	return EnumAction


####

optz=dict( activity_timeout=5*60, popup_timeout=5, queue_len=10,
	tbf_size=4, tbf_tick=15, tbf_max_delay=60, tbf_inc=2, tbf_dec=2 )
poll_interval = 60

dbus_id = 'org.freedesktop.Notifications'
dbus_path = '/org/freedesktop/Notifications'

urgency_levels = Enum('low', 'normal', 'critical')
close_reasons = Enum('expired', 'dismissed', 'closed', 'undefined', vals=range(1, 5))

layout_anchor = Enum('top_left', 'top_right', 'bottom_left', 'bottom_right')
layout_direction = Enum('horizontal', 'vertical')

####


parser = argparse.ArgumentParser(description='Desktop notification server.')

parser.add_argument('-f', '--no-fs-check',
	action='store_false', dest='fs_check', default=True,
	help='Dont queue messages if active window is fullscreen')
parser.add_argument('-u', '--no-urgency-check',
	action='store_false', dest='urgency_check', default=True,
	help='Queue messages even if urgency is critical')
parser.add_argument('-c', '--activity-timeout', type=int, default=int(optz['activity_timeout']),
	help='No-activity (dbus calls) timeout before closing the daemon instance'
		' (less or equal zero - infinite, default: %(default)ss)')
parser.add_argument('--no-status-notify',
	action='store_false', dest='status_notify', default=True,
	help='Do not send notification on changes in proxy settings.')
parser.add_argument('--filter-file', default='~/.notification_filter',
	help='Read simple scheme rules for filtering notifications from file (default: %(default)s).')

parser.add_argument('-t', '--popup-timeout', type=int, default=int(optz['popup_timeout']*1000),
	help='Default timeout for notification popups removal (default: %(default)sms)')
parser.add_argument('-q', '--queue-len', type=int, default=optz['queue_len'],
	help='How many messages should be queued on tbf overflow  (default: %(default)s)')

parser.add_argument('--layout-anchor', choices=layout_anchor,
	action=EnumAction(layout_anchor), default=layout_anchor.top_left,
	help='Screen corner notifications gravitate to (default: top_left).')
parser.add_argument('--layout-direction', choices=layout_direction,
	action=EnumAction(layout_direction), default=layout_direction.vertical,
	help='Direction for notification stack growth from --layout-anchor corner (default: vertical).')
parser.add_argument('--layout-margin', default=3,
	help='Margin between notifications, screen edges, and some misc stuff (default: %(default)spx).')

parser.add_argument('--tbf-size', type=int, default=optz['tbf_size'],
	help='Token-bucket message-flow filter (tbf) bucket size (default: %(default)s)')
parser.add_argument('--tbf-tick', type=int, default=optz['tbf_tick'],
	help='tbf update interval (new token), so token_inflow = token / tbf_tick (default: %(default)ss)')
parser.add_argument('--tbf-max-delay', type=int, default=optz['tbf_max_delay'],
	help='Maxmum amount of seconds, between message queue flush (default: %(default)ss)')
parser.add_argument('--tbf-inc', type=int, default=optz['tbf_inc'],
	help='tbf_tick multiplier on consequent tbf overflow (default: %(default)s)')
parser.add_argument('--tbf-dec', type=int, default=optz['tbf_dec'],
	help='tbf_tick divider on successful grab from non-empty bucket,'
		' wont lower multiplier below 1 (default: %(default)s)')

parser.add_argument('--debug', action='store_true', help='Enable debug logging to stderr.')
optz = parser.parse_args()


import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, Pango

import itertools as it, operator as op, functools as ft
from dbus.mainloop.glib import DBusGMainLoop
from fgc.fc import FC_TokenBucket, RRQ
import dbus, dbus.service, gobject, glib, urllib
import os, sys

from collections import deque, OrderedDict, namedtuple
from time import time

from fgc.scheme import load, init_env
from fgc.err import ext_traceback
import re


import logging
logging.basicConfig(level=logging.DEBUG if optz.debug else logging.WARNING)
log = logging.getLogger()

optz.filter_file = os.path.expanduser(optz.filter_file)

DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()



class Notification(dict):
	__slots__ = tuple()
	dbus_args = 'app_name', 'replaces_id', 'icon',\
		'summary', 'body', 'actions', 'hints', 'timeout'

	@classmethod
	def from_dbus(cls, *argz):
		'Get all arguments in dbus-interface order.'
		return cls(**dict(it.izip(cls.dbus_args, argz)))

	def __init__( self, summary='', body='', timeout=-1, icon='',
			app_name='generic', replaces_id=dbus.UInt32(), actions=dbus.Array(signature='s'),
			hints=dict(urgency=dbus.Byte(urgency_levels.critical, variant_level=1)) ):
		if timeout == -1: timeout = optz.popup_timeout # yes, -1 is special-case value in specs
		argz = self.__init__.func_code.co_varnames # a bit hacky, but DRY
		super(Notification, self).__init__(
			it.izip(argz, op.itemgetter(*argz)(locals())) )

	def __iter__(self):
		return iter(op.itemgetter(*self.dbus_args)(self))

	def __getattr__(self, k):
		if not k.startswith('__'): return self[k]
		else: raise AttributeError
	def __setattr__(self, k, v): self[k] = v



class NotificationDisplay(object):
	'''Interface to display notification stack.
		Should have "display(note, cb_dismiss=None) -> nid(UInt32, >0)", "close(nid)"
			methods and NoWindowError(nid) exception, raised on erroneous nid's in close().
		Current implementation based on notipy: git://github.com/the-isz/notipy.git'''
	window = namedtuple('Window', 'gobj event_boxes')

	def __init__(self):
		self.margins = dict(it.chain.from_iterable(map(
			lambda ax: ( (2**ax, optz.layout_margin),
				(-2**ax, optz.layout_margin) ), xrange(2) )))
		self.layout_anchor = optz.layout_anchor
		self.layout_direction = optz.layout_direction

		self._windows = OrderedDict()

		self._default_style = Gtk.CssProvider()
		self._default_style.load_from_data( b'''
			#notification { background-color: white; }
			#notification #hs { background-color: black; }

			#notification #critical { background-color: #ffaeae; }
			#notification #normal { background-color: #f0ffec; }
			#notification #low { background-color: #bee3c6; }

			#notification #summary {
				font-size: 10;
				text-shadow: 1 1 0 gray;
			}
			#notification #body { font-size: 8; }''' )
		Gtk.StyleContext.add_provider_for_screen(
			Gdk.Screen.get_default(), self._default_style,
			Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION )

	def _update_layout(self):
		# Yep, I was SMOKING CRACK here, and it all made sense at the time
		base = tuple(map(
			lambda ax, gdk_dim=('width', 'height'):\
				(getattr(Gdk.Screen, gdk_dim[ax])() - self.margins[2**ax])\
					if 2**ax & self.layout_anchor else self.margins[-2**ax], xrange(2) ))
		for win in map(op.attrgetter('gobj'), self._windows.viewvalues()):
			win.move(*map(lambda ax: base[ax] - ( win.get_size()[ax]
				if 2**ax & self.layout_anchor else 0 ), xrange(2)))
			margin = self.margins[(2 * ( (2**self.layout_direction)
				& self.layout_anchor ) / 2**self.layout_direction - 1) * 2**self.layout_direction]
			base = tuple(map(
				lambda ax: base[ax] if self.layout_direction != ax else\
					base[ax] + (margin + win.get_size()[ax])\
						* (2 * (2**ax ^ (2**ax & self.layout_anchor)) / 2**ax - 1), xrange(2) ))

	def _create_win(self, summary, body, icon=None, urgency_label=None):
		log.debug( 'Creating window with parameters: {}'\
			.format(', '.join(map(unicode, [summary, body, icon, urgency_label]))) )

		win = Gtk.Window(name='notification', type=Gtk.WindowType.POPUP)
		win.set_default_size(400, 20)
		ev_boxes = [win]

		frame = Gtk.Frame(shadow_type=Gtk.ShadowType.ETCHED_OUT)
		win.add(frame)

		widget_icon = None
		if icon is not None:
			if isinstance(icon, unicode):
				icon_path = os.path.expanduser(urllib.url2pathname(icon))
				if icon_path.startswith('file://'): icon_path = icon_path[7:]
				if os.path.isfile(icon_path):
					widget_icon = Gtk.Image()
					widget_icon.set_from_file(icon_path)
				else:
					# available names: Gtk.IconTheme.get_default().list_icons(None)
					theme = Gtk.IconTheme.get_default()
					if theme.has_icon(icon):
						widget_icon = Gtk.Image()
						widget_icon.set_from_icon_name(icon, Gtk.IconSize.DND) # XXX: why this IconSize?
					else:
						log.warn(( '"{}" seems to be neither a valid icon file nor'
							' a name in a freedesktop.org-compliant icon theme (or your theme'
							' doesnt have that name). Ignoring.' ).format(icon))
			else:
				# For image-data and icon_data, image should look like this:
				# dbus.Struct(
				#  (dbus.Int32, # width
				#   dbus.Int32, # height
				#   dbus.Int32, # rowstride
				#   dbus.Boolean, # has alpha
				#   dbus.Int32, # bits per sample
				#   dbus.Int32, # channels
				#   dbus.Array([dbus.Byte, ...])) # image data
				# )
				# data, colorspace, has_alpha, bits_per_sample,
				#  width, height, rowstride, destroy_fn, destroy_fn_data
				# XXX: Do I need to free the image via a function callback?
				pixbuf = GdkPixbuf.Pixbuf.new_from_data(
					bytearray(icon[6]), GdkPixbuf.Colorspace.RGB, icon[3], icon[4],
					icon[0], icon[1], icon[2], lambda x, y: None, None )
				widget_icon = Gtk.Image()
				widget_icon.set_from_pixbuf(pixbuf)

		v_box = Gtk.VBox(spacing=optz.layout_margin, expand=False)
		if widget_icon is not None:
			h_box = Gtk.HBox(spacing=optz.layout_margin * 2)
			frame.add(h_box)
			h_box.pack_start(widget_icon, False, False, 0)
			h_box.pack_start(v_box, True, True, 0)
		else: frame.add(v_box)

		widget_summary = Gtk.Label(name='summary', label=summary)
		widget_summary.set_alignment(0, 0)
		if urgency_label:
			summary_box = Gtk.EventBox(name=urgency_label)
			summary_box.add(widget_summary)
		else: summary_box = widget_summary
		v_box.pack_start(summary_box, False, False, 0)

		v_box.pack_start(Gtk.HSeparator(name='hs'), False, False, 0)

		widget_body = Gtk.TextView( name='body',
			wrap_mode=Gtk.WrapMode.WORD_CHAR )
		widget_body_buffer = widget_body.get_buffer()
		widget_body_buffer.set_text(body)
		v_box.pack_start(widget_body, False, False, 0)
		ev_boxes.append(widget_body)

		win.show_all()
		return self.window(win, ev_boxes)


	def display(self, note, cb_dismiss=None, cb_hover=None, cb_leave=None):
		try:
			# Priorities for icon sources:
			#  image{-,_}data: hint. raw image data structure of signature (iiibiiay)
			#  image{-,_}path: hint. either an URI (file://...) or a name in a f.o-compliant icon theme
			#  app_icon: parameter. same as image-path
			#  icon_data: hint. same as image-data
			# image_* is a workaround for some broken apps and libs which don't use libnotify
			hints = note.hints.copy()
			k = '__app_icon' # to avoid clobbering anything
			hints[k] = note.icon
			for k in 'image-data', 'image_data',\
					'image-path', 'image_path', k, 'icon_data':
				image = hints.get(k)
				if image:
					log.debug('Got icon image from hint: {}'.format(k))
					break

			urgency = note.hints.get('urgency')
			if urgency is not None: urgency = urgency_levels.by_id(int(urgency))
			win = self._create_win(note.summary, note.body, image, urgency)
			for eb in win.event_boxes:
				eb.add_events(
					Gdk.EventMask.BUTTON_PRESS_MASK
					| Gdk.EventMask.POINTER_MOTION_MASK
					| Gdk.EventMask.LEAVE_NOTIFY_MASK )
				for ev,cb in [
						('button-press-event', cb_dismiss),
						('motion-notify-event', cb_hover),
						('leave-notify-event', cb_leave) ]:
					if cb: eb.connect(ev, lambda w,ev,cb,nid: cb(nid), cb, note.id)

			self._windows[note.id] = win
			self._update_layout()

		except: log.exception('Failed to create notification window')


	class NoWindowError(Exception): pass

	def _close(self, nid):
		try: win = self._windows.pop(nid).gobj
		except KeyError: raise self.NoWindowError(nid)
		win.hide(), win.destroy()

	def close(self, nid):
		self._close(nid)
		self._update_layout()



class NotificationDaemon(dbus.service.Object):
	plugged, timeout_cleanup = False, True
	_activity_timer = None

	def __init__(self, *argz, **kwz):
		tick_strangle_max = op.truediv(optz.tbf_max_delay, optz.tbf_tick)
		super(NotificationDaemon, self).__init__(*argz, **kwz)
		self._note_limit = FC_TokenBucket(
			tick=optz.tbf_tick, burst=optz.tbf_size,
			tick_strangle=lambda x: min(x*optz.tbf_inc, tick_strangle_max),
			tick_free=lambda x: max(op.truediv(x, optz.tbf_dec), 1) )
		self._note_buffer = RRQ(optz.queue_len)
		self._note_windows = dict()
		self._note_id_pool = it.chain.from_iterable(
			it.imap(ft.partial(xrange, 1), it.repeat(2**30)) )
		self._renderer = NotificationDisplay()
		self._activity_event()


	def exit(self, reason=None):
		log.debug('Exiting cleanly{}'.format(', reason: {}'.format(reason) if reason else ''))
		sys.exit()

	def _activity_event(self):
		if self._activity_timer: gobject.source_remove(self._activity_timer)
		self._activity_timer = gobject.timeout_add_seconds(
			optz.activity_timeout, self.exit, 'activity timeout ({}s)'.format(optz.activity_timeout) )

	_dbus_method = ft.partial(dbus.service.method, dbus_id)
	_dbus_signal = ft.partial(dbus.service.signal, dbus_id)


	@_dbus_method('', 'ssss')
	def GetServerInformation(self):
		self._activity_event()
		return 'Notifications', 'freedesktop.org', '0.1', '0.7.1'

	@_dbus_method('', 'as')
	def GetCapabilities(self):
		# action-icons, actions, body, body-hyperlinks, body-images,
		#  body-markup, icon-multi, icon-static, persistence, sound
		self._activity_event()
		return ['body', 'persistence', 'icon-static']

	@_dbus_signal('uu')
	def NotificationClosed(self, nid, reason):
		log.debug(
			'NotificationClosed signal (id: {}, reason: {})'\
			.format(nid, close_reasons.by_id(reason)) )

	@_dbus_signal('us')
	def ActionInvoked(self, nid, action_key):
		log.debug('Um... some action invoked? Params: {}'.format([nid, action_key]))


	@_dbus_method('', '')
	def Flush(self):
		log.debug('Manual flush of the notification buffer')
		self._activity_event()
		return self.flush(force=True)

	@_dbus_method('a{sb}', '')
	def Set(self, params):
		self._activity_event()
		# Urgent-passthrough controls
		if params.pop('urgent_toggle', None): params['urgent'] = not optz.urgency_check
		try: val = params.pop('urgent')
		except KeyError: pass
		else:
			optz.urgency_check = val
			if optz.status_notify:
				self.display(
					'Urgent messages passthrough {}'.format(
						'enabled' if optz.urgency_check else 'disabled' ) )
		# Plug controls
		if params.pop('plug_toggle', None): params['plug'] = not self.plugged
		try: val = params.pop('plug')
		except KeyError: pass
		else:
			if val:
				self.plugged = True
				log.debug('Notification queue plugged')
				if optz.status_notify:
					self.display( 'Notification proxy: queue is plugged',
						'Only urgent messages will be passed through'
							if optz.urgency_check else 'All messages will be stalled' )
			else:
				self.plugged = False
				log.debug('Notification queue unplugged')
				if optz.status_notify:
					self.display('Notification proxy: queue is unplugged')
				if self._note_buffer:
					log.debug('Flushing plugged queue')
					self.flush()
		# Timeout override
		if params.pop('cleanup_toggle', None): params['cleanup'] = not self.timeout_cleanup
		try: val = params.pop('cleanup')
		except KeyError: pass
		else:
			self.timeout_cleanup = val
			log.debug('Cleanup timeout: {}'.format(self.timeout_cleanup))
			if optz.status_notify:
				self.display( 'Notification proxy: cleanup timeout is {}'\
					.format('enabled' if self.timeout_cleanup else 'disabled') )
		# Notify about malformed arguments, if any
		if params and optz.status_notify:
			self.display('Notification proxy: unrecognized parameters', repr(params))

	@_dbus_method('susssasa{sv}i', 'u')
	def Notify(self, app_name, nid, icon, summary, body, actions, hints, timeout):
		self._activity_event()
		note = Notification.from_dbus(
			app_name, nid, icon, summary, body, actions, hints, timeout )
		try: return self.filter(note)
		except Exception:
			log.exception('Unhandled error')
			return 0

	@_dbus_method('u', '')
	def CloseNotification(self, nid):
		log.debug('CloseNotification call (id: {})'.format(nid))
		self._activity_event()
		self.close(nid, reason=close_reasons.closed)


	_filter_ts_chk = 0
	_filter_callback = None, 0

	def _notification_check(self, summary, body):
		(cb, mtime), ts = self._filter_callback, time()
		if self._filter_ts_chk < ts - poll_interval:
			self._filter_ts_chk = ts
			try: ts = int(os.stat(optz.filter_file).st_mtime)
			except (OSError, IOError): return True
			if ts > mtime:
				mtime = ts
				init_env({'~': lambda regex, string: bool(re.search(regex, string))})
				try: cb = load(optz.filter_file)
				except:
					ex, self._filter_callback = ext_traceback(), (None, 0)
					log.debug('Failed to load notification filters (from {}):\n{}'.format(optz.filter_file, ex))
					if optz.status_notify:
						self.display('Notification proxy: failed to load notification filters', ex)
					return True
				else:
					log.debug('(Re)Loaded notification filters')
					self._filter_callback = cb, mtime
		if cb is None: return True # no filtering defined
		elif not callable(cb): return bool(cb)
		try: return cb(summary, body)
		except:
			ex = ext_traceback()
			log.debug('Failed to execute notification filters:\n{}'.format(ex))
			if optz.status_notify:
				self.display('Notification proxy: notification filters failed', ex)
			return True

	@property
	def _fullscreen_check(self, jitter=5):
		screen = Gdk.Screen.get_default()
		win = screen.get_active_window()
		win_state = win.get_state()
		x,y,w,h = win.get_geometry()
		return win_state & win_state.FULLSCREEN\
			or ( x <= jitter and y <= jitter
				and w >= screen.get_width() - jitter
				and h >= screen.get_height() - jitter )

	def filter(self, note):
		# TODO: also, just update timeout if content is the same as one of the displayed
		try: urgency = int(note.hints['urgency'])
		except KeyError: urgency = None

		plug = self.plugged or (optz.fs_check and self._fullscreen_check)
		urgent = optz.urgency_check and urgency == urgency_levels.critical

		if urgent: # special case - no buffer checks
			self._note_limit.consume()
			log.debug( 'Urgent message immediate passthru'
				', tokens left: {}'.format(self._note_limit.tokens) )
			return self.display(note)

		if not self._notification_check(note.summary, note.body):
			log.debug('Dropped notification due to negative filtering result')
			return 0

		if plug or not self._note_limit.consume():
			# Delay notification
			to = self._note_limit.get_eta() if not plug else poll_interval
			if to > 1: # no need to bother otherwise, note that it'll be an extra token ;)
				self._note_buffer.append(note)
				to = int(to) + 1 # +1 is to ensure token arrival by that time
				log.debug( 'Queueing notification. Reason: {}. Flush attempt in {}s'\
					.format('plug or fullscreen window detected' if plug else 'notification rate limit', to) )
				self.flush(timeout=to)
				return 0

		if self._note_buffer:
			self._note_buffer.append(note)
			log.debug('Token-flush of notification buffer')
			self.flush()
			return 0
		else:
			log.debug('Token-pass, {} token(s) left'.format(self._note_limit.tokens))
			return self.display(note)


	_flush_timer = _flush_id = None

	def flush(self, force=False, timeout=None):
		if self._flush_timer:
			gobject.source_remove(self._flush_timer)
			self._flush_timer = None
		if timeout:
			log.debug('Scheduled notification buffer flush in {}s'.format(timeout))
			self._flush_timer = gobject.timeout_add_seconds(timeout, self.flush)
			return
		if not self._note_buffer:
			log.debug('Flush event with empty notification buffer')
			return

		log.debug( 'Flushing notification buffer ({} msgs, {} dropped)'\
			.format(len(self._note_buffer), self._note_buffer.dropped) )

		self._note_limit.consume()
		if not force:
			if optz.fs_check and (self.plugged or self._fullscreen_check):
				log.debug( '{} detected, delaying buffer flush by {}s'\
					.format(('Fullscreen window' if not self.plugged else 'Plug'), poll_interval) )
				self.flush(timeout=poll_interval)
				return

		if self._note_buffer:
			# Decided not to use replace_id here - several feeds are okay
			self._flush_id = self.display( self._note_buffer[0]\
				if len(self._note_buffer) == 1 else Notification(
					'Feed' if not self._note_buffer.dropped
						else 'Feed ({} dropped)'.format(self._note_buffer.dropped),
					'\n\n'.join(it.starmap( '--- {}\n  {}'.format,
						it.imap(op.itemgetter('summary', 'body'), self._note_buffer) )),
					app_name='notification-feed', icon='FBReader' ) )
			self._note_buffer.flush()
			log.debug('Notification buffer flushed')


	def display(self, note_or_summary, *argz, **kwz):
		note = note_or_summary if isinstance(note_or_summary, Notification)\
			else Notification(note_or_summary, *argz, **kwz)

		if note.replaces_id in self._note_windows:
			self.close(note.replaces_id, close_reasons.closed)
			note.id = self._note_windows[note.replaces_id]
		else: note.id = next(self._note_id_pool)
		nid = note.id

		self._renderer.display( note,
			cb_hover=ft.partial(self.close, delay=True),
			cb_leave=ft.partial(self.close, delay=False),
			cb_dismiss=ft.partial(self.close, reason=close_reasons.dismissed) )

		if self.timeout_cleanup and note.timeout > 0:
			self._note_windows[nid] = note
			timeout = int(note.timeout / 1000) # TODO: find better timer
			note.timer_created, note.timer_left = time(), timeout
			note.timer_id = gobject.timeout_add_seconds(
				timeout, self.close, nid, close_reasons.expired )

		log.debug( 'Created notification (id: {}, timeout: {}ms)'\
			.format(nid, self.timeout_cleanup and note.timeout) )
		return nid

	def close(self, nid=None, reason=close_reasons.undefined, delay=None):
		if nid:
			note = self._note_windows.get(nid, None)
			if note and 'timer_id' in note:
				if note.timer_id: gobject.source_remove(note.timer_id)

				if delay is None: del self._note_windows[nid]
				else: # these get sent very often
					if delay:
						if note.timer_id:
							note.timer_id, note.timer_left = None, note.timer_left - (time() - note.timer_created)
					else:
						note.timer_created = time()
						note.timer_id = gobject.timeout_add_seconds(
							max(int(note.timer_left), 1), self.close, nid, close_reasons.expired )
					return

			if delay is None: # try it, even if there's no note object
				log.debug(
					'Closing notification(s) (id: {}, reason: {})'\
					.format(nid, close_reasons.by_id(reason)) )
				try: self._renderer.close(nid)
				except self._renderer.NoWindowError: pass # no such window
				else: self.NotificationClosed(nid, reason)

		else: # close all of them
			for nid in self._note_windows.keys(): self.close(nid, reason)



daemon = NotificationDaemon(bus, dbus_path, dbus.service.BusName(dbus_id, bus))
loop = gobject.MainLoop()
log.debug('Starting gobject loop')
loop.run()
