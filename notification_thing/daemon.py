#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from time import time
from dbus.mainloop.glib import DBusGMainLoop
import dbus, dbus.service
import os, sys, traceback

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GObject, Gdk

from notification_thing import core
from notification_thing.display import NotificationDisplay


DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()

optz, poll_interval, close_reasons, urgency_levels =\
	core.optz, core.poll_interval, core.close_reasons, core.urgency_levels


class NotificationDaemon(dbus.service.Object):
	plugged, timeout_cleanup = False, True
	_activity_timer = None

	def __init__(self, *argz, **kwz):
		tick_strangle_max = op.truediv(optz.tbf_max_delay, optz.tbf_tick)
		super(NotificationDaemon, self).__init__(*argz, **kwz)
		self._note_limit = core.FC_TokenBucket(
			tick=optz.tbf_tick, burst=optz.tbf_size,
			tick_strangle=lambda x: min(x*optz.tbf_inc, tick_strangle_max),
			tick_free=lambda x: max(op.truediv(x, optz.tbf_dec), 1) )
		self._note_buffer = core.RRQ(optz.queue_len)
		self._note_windows = dict()
		self._note_id_pool = it.chain.from_iterable(
			it.imap(ft.partial(xrange, 1), it.repeat(2**30)) )
		self._renderer = NotificationDisplay(
			optz.layout_margin, optz.layout_anchor,
			optz.layout_direction, optz.icon_width, optz.icon_height )
		self._activity_event()


	def exit(self, reason=None):
		log.debug('Exiting cleanly{}'.format(', reason: {}'.format(reason) if reason else ''))
		sys.exit()

	def _activity_event(self, callback=False):
		if callback:
			if not self._note_windows:
				self.exit(reason='activity timeout ({}s)'.format(optz.activity_timeout))
			else:
				log.debug( 'Ignoring inacivity timeout event'
					' due to existing windows (retry in {}s).'.format(optz.activity_timeout) )
				self._activity_timer = None
		if self._activity_timer: GObject.source_remove(self._activity_timer)
		self._activity_timer = GObject.timeout_add_seconds(
			optz.activity_timeout, self._activity_event, True )

	_dbus_method = ft.partial(dbus.service.method, core.dbus_id)
	_dbus_signal = ft.partial(dbus.service.signal, core.dbus_id)


	@_dbus_method('', 'ssss')
	def GetServerInformation(self):
		self._activity_event()
		return 'notification-thing', 'mk.fraggod@gmail.com', 'git', '1.2'

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
		note = core.Notification.from_dbus(
			app_name, nid, icon, summary, body, actions, hints, timeout )
		if nid: self.close(nid, reason=close_reasons.closed)
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
				try: cb = core.get_filter(optz.filter_file)
				except:
					ex, self._filter_callback = traceback.format_exc(), (None, 0)
					log.debug( 'Failed to load'
						' notification filters (from {}):\n{}'.format(optz.filter_file, ex) )
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
			ex = traceback.format_exc()
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
		urgent = optz.urgency_check and urgency == core.urgency_levels.critical

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
				to = to + 1 # +1 is to ensure token arrival by that time
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
			GObject.source_remove(self._flush_timer)
			self._flush_timer = None
		if timeout:
			log.debug('Scheduled notification buffer flush in {}s'.format(timeout))
			self._flush_timer = GObject.timeout_add(int(timeout * 1000), self.flush)
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
					.format(( 'Fullscreen window'
						if not self.plugged else 'Plug' ), poll_interval) )
				self.flush(timeout=poll_interval)
				return

		if self._note_buffer:
			# Decided not to use replace_id here - several feeds are okay
			self._flush_id = self.display( self._note_buffer[0]\
				if len(self._note_buffer) == 1 else core.Notification(
					'Feed' if not self._note_buffer.dropped
						else 'Feed ({} dropped)'.format(self._note_buffer.dropped),
					'\n\n'.join(it.starmap( '--- {}\n  {}'.format,
						it.imap(op.itemgetter('summary', 'body'), self._note_buffer) )),
					app_name='notification-feed', icon='FBReader' ) )
			self._note_buffer.flush()
			log.debug('Notification buffer flushed')


	def display(self, note_or_summary, *argz, **kwz):
		note = note_or_summary\
			if isinstance(note_or_summary, core.Notification)\
			else core.Notification(note_or_summary, *argz, **kwz)

		if note.replaces_id in self._note_windows:
			self.close(note.replaces_id, close_reasons.closed)
			note.id = self._note_windows[note.replaces_id]
		else: note.id = next(self._note_id_pool)
		nid = note.id

		self._renderer.display( note,
			cb_hover=ft.partial(self.close, delay=True),
			cb_leave=ft.partial(self.close, delay=False),
			cb_dismiss=ft.partial(self.close, reason=close_reasons.dismissed) )
		self._note_windows[nid] = note

		if self.timeout_cleanup and note.timeout > 0:
			note.timer_created, note.timer_left = time(), note.timeout / 1000.0
			note.timer_id = GObject.timeout_add(
				note.timeout, self.close, nid, close_reasons.expired )

		log.debug( 'Created notification (id: {}, timeout: {} (ms))'\
			.format(nid, self.timeout_cleanup and note.timeout) )
		return nid

	def close(self, nid=None, reason=close_reasons.undefined, delay=None):
		if nid:
			note = self._note_windows.get(nid, None)
			if note:
				if getattr(note, 'timer_id', None): GObject.source_remove(note.timer_id)

				if delay is None: del self._note_windows[nid]
				elif 'timer_id' in note: # these get sent very often
					if delay:
						if note.timer_id:
							note.timer_id, note.timer_left = None,\
								note.timer_left - (time() - note.timer_created)
					else:
						note.timer_created = time()
						note.timer_id = GObject.timeout_add(
							int(max(note.timer_left, 1) * 1000),
							self.close, nid, close_reasons.expired )
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


def main():
	global optz, log
	import argparse

	def EnumAction(enum):
		class EnumAction(argparse.Action):
			def __call__(self, parser, namespace, values, option_string=None):
				setattr(namespace, self.dest, self.enum[values])
		EnumAction.enum = enum
		return EnumAction

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

	parser.add_argument('--filter-file', default='~/.notification_filter', metavar='PATH',
		help='Read simple scheme rules for filtering notifications from file (default: %(default)s).')
	parser.add_argument('--filter-test', nargs=2, metavar=('SUMMARY', 'BODY'),
		help='Do not start daemon, just test given summary'
			' and body against filter-file and print the result back to terminal.')

	parser.add_argument('-t', '--popup-timeout', type=int, default=int(optz['popup_timeout']*1000),
		help='Default timeout for notification popups removal (default: %(default)sms)')
	parser.add_argument('-q', '--queue-len', type=int, default=optz['queue_len'],
		help='How many messages should be queued on tbf overflow  (default: %(default)s)')

	parser.add_argument('--layout-anchor', choices=core.layout_anchor,
		action=EnumAction(core.layout_anchor), default=core.layout_anchor.top_left,
		help='Screen corner notifications gravitate to (default: top_left).')
	parser.add_argument('--layout-direction', choices=core.layout_direction,
		action=EnumAction(core.layout_direction), default=core.layout_direction.vertical,
		help='Direction for notification stack growth from --layout-anchor corner (default: vertical).')
	parser.add_argument('--layout-margin', default=3,
		help='Margin between notifications, screen edges, and some misc stuff (default: %(default)spx).')
	parser.add_argument('--icon-width', '--img-w', type=int, metavar='px',
		help='Scale icon (preserving aspect ratio) to width.')
	parser.add_argument('--icon-height', '--img-h', type=int, metavar='px',
		help='Scale icon (preserving aspect ratio) to height.')

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
	optz.filter_file = os.path.expanduser(optz.filter_file)
	core.Notification.default_timeout = optz.popup_timeout

	if optz.filter_test:
		func = core.get_filter(optz.filter_file)
		filtering_result = func(*optz.filter_test)
		msg_repr = 'Message - summary: {!r}, body: {!r}'.format(*optz.filter_test)
		print('{}\nFiltering result: {} ({})'.format( msg_repr,
			filtering_result, 'will pass' if filtering_result else "won't pass" ))
		sys.exit()

	import logging
	logging.basicConfig(level=logging.DEBUG if optz.debug else logging.WARNING)
	log = logging.getLogger()

	daemon = NotificationDaemon( bus,
		core.dbus_path, dbus.service.BusName(core.dbus_id, bus) )
	loop = GObject.MainLoop()
	log.debug('Starting gobject loop')
	loop.run()


if __name__ == '__main__': main()
