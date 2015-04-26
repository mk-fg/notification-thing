#!/usr/bin/env python2
# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from time import time
from collections import Mapping
from dbus.mainloop.glib import DBusGMainLoop
import dbus, dbus.service
import os, sys, traceback, types, math

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GLib

try: from gi.repository import Gdk
except RuntimeError as err: # less verbose errors in case X isn't running
	print('Gdk init error, exiting: {}'.format(err.message), file=sys.stderr)
	sys.exit(1)


if __name__ == '__main__':
	# Try to import submodules from the same path, not the site-packages
	from os.path import join, realpath, dirname
	module_root = realpath(dirname(dirname(__file__)))
	if module_root not in sys.path: sys.path.insert(0, module_root)
	from notification_thing.display import NotificationDisplay, strip_markup
	from notification_thing.pubsub import PubSub
	from notification_thing import core

else:
	from .display import NotificationDisplay
	from .pubsub import PubSub
	from . import core

optz, poll_interval, close_reasons, urgency_levels =\
	core.optz, core.poll_interval, core.close_reasons, core.urgency_levels



def flatten_dict(data, path=tuple()):
	dst = list()
	for k,v in data.iteritems():
		k = path + (k,)
		if isinstance(v, Mapping):
			for v in flatten_dict(v, k): dst.append(v)
		else: dst.append((k, v))
	return dst

def ts_diff_format( seconds, add_ago=False,
		_units_days=dict(y=365.25, mo=30.5, w=7, d=0),
		_units_s=dict(h=3600, m=60, s=0) ):
	days = seconds // (24*3600)
	seconds -= days * (24*3600)

	res, d = list(), days
	for unit, unit_days in sorted(
			_units_days.iteritems(), key=op.itemgetter(1), reverse=True):
		if d > unit_days or res:
			res.append('{0:.0f}{1}'.format(
				math.floor(d / unit_days) if unit_days else d, unit ))
			if len(res) >= 2 or not unit_days: break
			d = days % unit_days

	if len(res) < 2:
		s = seconds
		for unit, unit_s in sorted(
				_units_s.iteritems(), key=op.itemgetter(1), reverse=True):
			if s > unit_s or res:
				res.append('{0:.0f}{1}'.format(s / unit_s if unit_s else s, unit))
				if len(res) >= 2 or not unit_s: break
				s = seconds % unit_s

	if not res: return 'just now'
	else:
		if add_ago: res.append('ago')
		return ' '.join(res)



class NotificationMethods(object):
	plugged, timeout_cleanup = False, True
	_activity_timer = None

	def __init__(self, pubsub=None):
		tick_strangle_max = op.truediv(optz.tbf_max_delay, optz.tbf_tick)
		self._note_limit = core.FC_TokenBucket(
			tick=optz.tbf_tick, burst=optz.tbf_size,
			tick_strangle=lambda x: min(x*optz.tbf_inc, tick_strangle_max),
			tick_free=lambda x: max(op.truediv(x, optz.tbf_dec), 1) )
		self._note_buffer = core.RRQ(optz.queue_len)
		self._note_history = core.RRQ(optz.history_len)
		self._note_windows = dict()
		self._note_id_pool = it.chain.from_iterable(
			it.imap(ft.partial(xrange, 1), it.repeat(2**30)) )
		self._renderer = NotificationDisplay(
			optz.layout_margin, optz.layout_anchor,
			optz.layout_direction, optz.icon_width, optz.icon_height,
			markup_default=not optz.markup_disable,
			markup_warn=optz.markup_warn_on_err, markup_strip=optz.markup_strip_on_err )
		self._activity_event()

		self.pubsub = pubsub
		if pubsub:
			GLib.io_add_watch( pubsub.fileno(),
				GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_PRI, self._notify_pubsub )

		if optz.test_message:
			# Also test crazy web-of-90s markup here :P
			summary = 'Notification daemon started <small><tt>¯\(°_o)/¯</tt></small>'
			body = ( 'Desktop notification daemon started successfully on host: <u>{host}</u>'
					'\nCode path: <small>{code}</small>'
					'\nSound enabled: <span color="{sound_color}">{sound}</span>'
					'\nPubSub enabled: <span color="{pubsub_color}">{pubsub}</span>' )\
				.format( host=os.uname()[1],
					sound_color='green' if optz.filter_sound else 'red',
					sound=unicode(bool(optz.filter_sound)).lower(),
					pubsub_color='green' if pubsub else 'red',
					pubsub=unicode(bool(pubsub)).lower(),
					code=os.path.abspath(os.path.dirname(core.__file__)) )
			if not self._renderer.markup_default:
				summary, body = it.imap(strip_markup, [summary, body])
			self.display(summary, body)


	def exit(self, reason=None):
		log.debug('Exiting cleanly%s', ', reason: {}'.format(reason) if reason else '')
		sys.exit()

	def _activity_event(self, callback=False):
		if callback:
			if not self._note_windows:
				self.exit(reason='activity timeout ({}s)'.format(optz.activity_timeout))
			else:
				log.debug( 'Ignoring inacivity timeout event'
					' due to existing windows (retry in %ss).', optz.activity_timeout )
				self._activity_timer = None
		if self._activity_timer: GLib.source_remove(self._activity_timer)
		if optz.activity_timeout and optz.activity_timeout > 0:
			self._activity_timer = GLib.timeout_add_seconds(
				optz.activity_timeout, self._activity_event, True )


	def GetServerInformation(self):
		self._activity_event()
		return 'notification-thing', 'mk.fraggod@gmail.com', 'git', '1.2'

	def GetCapabilities(self):
		# action-icons, actions, body, body-hyperlinks, body-images,
		#  body-markup, icon-multi, icon-static, persistence, sound
		self._activity_event()
		caps = ['body', 'persistence', 'icon-static']
		if not self._renderer.markup_default: caps.append('body-markup')
		return sorted(caps)

	def NotificationClosed(self, nid, reason=None):
		log.debug(
			'NotificationClosed signal (id: %s, reason: %s)',
			nid, close_reasons.by_id(reason) )

	def ActionInvoked(self, nid, action_key):
		log.debug('Um... some action invoked? Params: %s', [nid, action_key])


	def Get(self, iface, k):
		return self.GetAll(iface)[k]

	def GetAll(self, iface):
		if iface != self.dbus_interface:
			raise dbus.exceptions.DBusException(
				'This object does not implement the {!r} interface'.format(unicode(iface)) )
		self._activity_event()
		return dict( urgent=optz.urgency_check,
			plug=self.plugged, cleanup=self.timeout_cleanup )

	def Set(self, iface, k, v):
		if iface != self.dbus_interface:
			raise dbus.exceptions.DBusException(
				'This object does not implement the {!r} interface'.format(unicode(iface)) )
		self._activity_event()

		if isinstance(v, types.StringTypes):
			v = unicode(v)
			if v == 'toggle': k, v = '{}_toggle'.format(k), True
			elif v.lower() in {'n', 'no', 'false', 'disable', 'off', '-'}: v = False
		k, v = unicode(k), bool(v)
		if k.endswith('_toggle'):
			k = k[:-7]
			v = not self.Get(iface, k)

		log.debug('Property change: %s = %s', k, v)

		if k == 'urgent':
			if optz.urgency_check == v: return
			optz.urgency_check = v
			if optz.status_notify:
				self.display(
					'Urgent messages passthrough {}'.format(
						'enabled' if optz.urgency_check else 'disabled' ) )

		elif k == 'plug':
			if self.plugged == v: return
			if v:
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

		elif k == 'cleanup':
			if self.timeout_cleanup == v: return
			self.timeout_cleanup = v
			log.debug('Cleanup timeout: %s', self.timeout_cleanup)
			if optz.status_notify:
				self.display( 'Notification proxy: cleanup timeout is {}'\
					.format('enabled' if self.timeout_cleanup else 'disabled') )

		elif optz.status_notify:
			self.display( 'notification-thing:'
				' unrecognized parameter', 'Key: {!r}, value: {!r}'.format(k, v) )
			return

		self.PropertiesChanged(iface, {k: v}, [])

	def PropertiesChanged(self, iface, props_changed, props_invalidated):
		log.debug( 'PropertiesChanged signal: %s',
			[iface, props_changed, props_invalidated] )


	def Flush(self):
		log.debug('Manual flush of the notification buffer')
		self._activity_event()
		return self.flush(force=True)

	def List(self):
		log.debug('NotificationList call')
		self._activity_event()
		return self._note_windows.keys()

	def Redisplay(self):
		log.debug('Redisplay call')
		self._activity_event()
		if not self._note_history: return 0
		note = self._note_history.pop()
		return self.display(note, redisplay=True)

	def Cleanup(self, timeout, max_count):
		log.debug( 'NotificationCleanup call'
			' (timeout=%.1fs, max_count=%s)', timeout, max_count )
		self._activity_event()
		if max_count <= 0: max_count = None
		ts_min = time() - timeout
		for nid, note in sorted(self._note_windows.viewitems(), key=lambda t: t[1].created):
			if note.created > ts_min: break
			self.close(nid, reason=close_reasons.closed)
			if max_count is not None:
				max_count -= 1
				if max_count <= 0: break


	def _notify_pubsub(self, _fd, _ev):
		try:
			while True:
				msg = self.pubsub.recv()
				if msg is None: break
				self._activity_event()
				note = msg.note
				prefix, ts_diff = msg.hostname, time() - msg.ts
				if ts_diff > 15 * 60: # older than 15min
					prefix = '{}[{}]'.format(prefix, ts_diff_format(ts_diff))
				note.summary = '{} // {}'.format(prefix, note.summary)
				self.filter_display(note)
		except: log.exception('Unhandled error with remote notification')
		finally: return True # for glib to keep watcher

	def Notify(self, app_name, nid, icon, summary, body, actions, hints, timeout):
		self._activity_event()
		try:
			note = core.Notification.from_dbus(
				app_name, nid, icon, summary, body, actions, hints, timeout )
			if self.pubsub:
				try: self._note_plaintext(note) # make sure plain version is cached
				except Exception as err:
					log.info('Failed to attach plain version to net message: %s', err)
				self.pubsub.send(note)
			if nid: self.close(nid, reason=close_reasons.closed)
			return self.filter_display(note)
		except Exception:
			log.exception('Unhandled error')
			return 0

	def CloseNotification(self, nid):
		log.debug('CloseNotification call (id: %s)', nid)
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
				try: cb = core.get_filter(optz.filter_file, optz.filter_sound)
				except:
					ex, self._filter_callback = traceback.format_exc(), (None, 0)
					log.debug( 'Failed to load'
						' notification filters (from %s):\n%s', optz.filter_file, ex )
					if optz.status_notify:
						self.display('notification-thing: failed to load notification filters', ex)
					return True
				else:
					log.debug('(Re)Loaded notification filters')
					self._filter_callback = cb, mtime
		if cb is None: return True # no filtering defined
		elif not callable(cb): return bool(cb)
		try: return cb(summary, body)
		except:
			ex = traceback.format_exc()
			log.debug('Failed to execute notification filters:\n%s', ex)
			if optz.status_notify:
				self.display('notification-thing: notification filters failed', ex)
			return True

	def _note_plaintext(self, note):
		note_plain = note.get('plain')
		if note_plain: summary, body = note_plain
		else:
			summary, body = self._renderer.get_note_text(note)
			note.plain = summary, body
		return summary, body

	def _fullscreen_check(self, jitter=5):
		screen = Gdk.Screen.get_default()
		win = screen.get_active_window()
		if not win: return False
		win_state = win.get_state()
		w, h = win.get_width(), win.get_height()
		# get_geometry fails with "BadDrawable" from X if the window is closing,
		#  and x/y parameters there are not absolute and useful anyway.
		# x, y, w, h = win.get_geometry()
		return (win_state & win_state.FULLSCREEN)\
			or (w >= screen.get_width() - jitter and h >= screen.get_height() - jitter)

	def filter_display(self, note):
		note_summary, note_body = self._note_plaintext(note)
		if not self._notification_check(note_summary, note_body):
			log.debug('Dropped notification due to negative filtering result: %r', note_summary)
			return 0

		try: urgency = int(note.hints['urgency'])
		except (KeyError, ValueError): urgency = None
		if optz.urgency_check and urgency == core.urgency_levels.critical:
			self._note_limit.consume()
			log.debug('Urgent message immediate passthru, tokens left: %s', self._note_limit.tokens)
			return self.display(note)

		plug = self.plugged or (optz.fs_check and self._fullscreen_check())
		if plug or not self._note_limit.consume(): # Delay notification
			to = self._note_limit.get_eta() if not plug else poll_interval
			self._note_buffer.append(note)
			log.debug( 'Queueing notification. Reason: %s. Flush attempt in %ss',
				'plug or fullscreen window detected' if plug else 'notification rate limit', to )
			self.flush(timeout=to)
			return 0

		if self._note_buffer:
			self._note_buffer.append(note)
			log.debug('Token-flush of notification buffer')
			self.flush()
			return 0
		else:
			log.debug('Token-pass, %s token(s) left', self._note_limit.tokens)
			return self.display(note)


	_flush_timer = _flush_id = None

	def flush(self, force=False, timeout=None):
		if self._flush_timer:
			GLib.source_remove(self._flush_timer)
			self._flush_timer = None
		if timeout:
			log.debug('Scheduled notification buffer flush in %ss', timeout)
			self._flush_timer = GLib.timeout_add(int(timeout * 1000), self.flush)
			return
		if not self._note_buffer:
			log.debug('Flush event with empty notification buffer')
			return

		log.debug(
			'Flushing notification buffer (%s msgs, %s dropped)',
			len(self._note_buffer), self._note_buffer.dropped )

		self._note_limit.consume(force=True)
		if not force:
			if optz.fs_check and (self.plugged or self._fullscreen_check()):
				log.debug( '%s detected, delaying buffer flush by %ss',
					('Fullscreen window' if not self.plugged else 'Plug'), poll_interval )
				self.flush(timeout=poll_interval)
				return

		if self._note_buffer:
			# Decided not to use replace_id here - several feeds are okay
			self._flush_id = self.display( self._note_buffer[0]\
				if len(self._note_buffer) == 1\
				else core.Notification.system_message(
					'Feed' if not self._note_buffer.dropped
						else 'Feed ({} dropped)'.format(self._note_buffer.dropped),
					'\n\n'.join(it.starmap( '--- {}\n  {}'.format,
						it.imap(op.itemgetter('summary', 'body'), self._note_buffer) )),
					app_name='notification-feed', icon='FBReader' ) )
			self._note_buffer.flush()
			log.debug('Notification buffer flushed')


	def display(self, note_or_summary, body='', redisplay=False):
		if isinstance(note_or_summary, core.Notification):
			if body:
				raise TypeError('Either Notification object or summary/body should be passed, not both.')
			note = note_or_summary
		else:
			note = core.Notification.system_message(note_or_summary, body)

		if not redisplay:
			clone = note.clone()
			clone.display_time = time()
			self._note_history.append(clone)
		else:
			ts = getattr(note, 'display_time', None)
			if ts: note.body += '\n\n[from {}]'.format(ts_diff_format(time() - ts, add_ago=True))

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
			note.timer_id = GLib.timeout_add(
				note.timeout, self.close, nid, close_reasons.expired )

		log.debug(
			'Created notification (id: %s, timeout: %s (ms))',
			nid, self.timeout_cleanup and note.timeout )
		return nid


	def close(self, nid=None, reason=close_reasons.undefined, delay=None):
		if nid:
			note = self._note_windows.get(nid, None)
			if note:
				if getattr(note, 'timer_id', None):
					GLib.source_remove(note.timer_id)

				if delay is None: del self._note_windows[nid]
				elif hasattr(note, 'timer_id'): # these get sent very often
					if delay:
						if note.timer_id:
							note.timer_id, note.timer_left = None,\
								note.timer_left - (time() - note.timer_created)
					else:
						note.timer_created = time()
						note.timer_id = GLib.timeout_add(
							int(max(note.timer_left, 1) * 1000),
							self.close, nid, close_reasons.expired )
					return

			if delay is None: # try it, even if there's no note object
				log.debug(
					'Closing notification(s) (id: %s, reason: %s)',
					nid, close_reasons.by_id(reason) )
				try: self._renderer.close(nid)
				except self._renderer.NoWindowError: pass # no such window
				else: self.NotificationClosed(nid, reason)

		else: # close all of them
			for nid in self._note_windows.keys(): self.close(nid, reason)



def _add_dbus_decorators(cls_name, cls_parents, cls_attrs):
	cls_attrs['dbus_interface'] = optz.dbus_interface
	method, signal = dbus.service.method, dbus.service.signal
	assert NotificationMethods in cls_parents
	methods = vars(NotificationMethods)
	# Main interface
	for wrapper in [
			(method, 'GetCapabilities', '', 'as'),
			(method, 'GetServerInformation', '', 'ssss'),
			(signal, 'NotificationClosed', 'uu'),
			(signal, 'ActionInvoked', 'us'),
			(method, 'Flush', '', ''),
			(method, 'List', '', 'ai'),
			(method, 'Redisplay', '', 'u'),
			(method, 'Cleanup', 'du', ''),
			(method, 'Notify', 'susssasa{sv}i', 'u'),
			(method, 'CloseNotification', 'u', '') ]:
		(wrapper, name), args = wrapper[:2], wrapper[2:]
		cls_attrs[name] = wrapper(optz.dbus_interface, *args)(methods[name])
	# Properties interface
	for wrapper in [
			(method, 'Get', 'ss', 'v'),
			(method, 'GetAll', 's', 'a{sv}'),
			(method, 'Set', 'ssv', ''),
			(signal, 'PropertiesChanged', 'sa{sv}as') ]:
		(wrapper, name), args = wrapper[:2], wrapper[2:]
		cls_attrs[name] = wrapper(dbus.PROPERTIES_IFACE, *args)(methods[name])
	return type(cls_name, cls_parents, cls_attrs)

def notification_daemon_factory(*dbus_svc_argz, **dbus_svc_kwz):
	'Build NotificationDaemon class on a configured dbus interface.'
	# Necessary because dbus interface is embedded into method decorators,
	#  so it's either monkey-patching of a global class or customized creation.

	class NotificationDaemon(NotificationMethods, dbus.service.Object):
		__metaclass__ = _add_dbus_decorators
		def __init__(self, pubsub, *dbus_svc_argz, **dbus_svc_kwz):
			NotificationMethods.__init__(self, pubsub)
			dbus.service.Object.__init__(self, *dbus_svc_argz, **dbus_svc_kwz)

	return NotificationDaemon(*dbus_svc_argz, **dbus_svc_kwz)



def main(argv=None):
	global optz, log
	import argparse

	def EnumAction(enum):
		class EnumAction(argparse.Action):
			def __call__(self, parser, namespace, values, option_string=None):
				setattr(namespace, self.dest, self.enum[values])
		EnumAction.enum = enum
		return EnumAction

	parser = argparse.ArgumentParser(description='Desktop notification server.')

	parser.add_argument('--conf', metavar='path',
		help='Read option values from specified YAML configuration file.'
			' Keys in subsectons (like "tbf.size") will be joined with'
				' parent section name with dash (e.g. --tbf-size).'
			' Any values specified on command line will override corresponding ones from file.'
			' See also notification_thing.example.yaml file.')

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
		help='Do not send notification on changes in daemon settings.')
	parser.add_argument('--test-message', action='store_true',
		help='Issue test notification right after start.')

	parser.add_argument('--filter-file', default='~/.notification_filter', metavar='PATH',
		help='Read simple scheme rules for filtering notifications from file (default: %(default)s).')
	parser.add_argument('--filter-test', nargs=2, metavar=('SUMMARY', 'BODY'),
		help='Do not start daemon, just test given summary'
			' and body against filter-file and print the result back to terminal.')
	parser.add_argument('--no-filter-sound',
		action='store_false', dest='filter_sound', default=True,
		help='Make sound calls in --filters-file scheme interpreter a no-op.'
			' Only makes sense if sound calls in filters are actually used'
				' and libcanberra is available, otherwise there wont be any sounds anyway.')

	parser.add_argument('-t', '--popup-timeout', type=int, default=int(optz['popup_timeout']*1000),
		help='Default timeout for notification popups removal (default: %(default)sms)')
	parser.add_argument('-q', '--queue-len', type=int, default=optz['queue_len'],
		help='How many messages should be queued on tbf overflow (default: %(default)s)')
	parser.add_argument('-s', '--history-len', type=int, default=optz['history_len'],
		help='How many last *displayed* messages to'
			' remember to display again on demand (default: %(default)s)')

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

	parser.add_argument('--markup-disable', action='store_true',
		help='Enable pango markup (tags, somewhat similar to html)'
				' processing in all message summary/body parts by default.'
			' These will either be rendered by pango'
				' or stripped/shown-as-is (see --pango-markup-strip-on-err option), if invalid.'
			' "x-nt-markup" bool hint can be used to'
				' enable/disable markup on per-message basis, regardless of this option value.'
			' See "Markup" section of the README for more details.')
	parser.add_argument('--markup-strip-on-err', action='store_true',
		help='Strip markup tags if pango fails to parse them'
			' (when parsing markup is enabled) instead of rendering message with them as text.')
	parser.add_argument('--markup-warn-on-err', action='store_true',
		help='Issue loggin warning if passed markup tags cannot be parsed (when it is enabled).')

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

	parser.add_argument('--dbus-interface', default=optz['dbus_interface'],
		help='DBus interface to use (default: %(default)s)')
	parser.add_argument('--dbus-path', default=optz['dbus_path'],
		help='DBus object path to bind to (default: %(default)s)')

	parser.add_argument('--net-pub-bind',
		action='append', metavar='ip:port',
		help='Publish messages over network on a specified socket endpoint.'
			' Can be either ip:port (assumed to be tcp socket,'
				' e.g. 1.2.3.4:5678) or full zmq url (e.g. tcp://1.2.3.4:5678).'
			' Can be specified multiple times.')
	parser.add_argument('--net-pub-connect',
		action='append', metavar='ip:port',
		help='Send published messages to specified subscriber socket (see --net-sub-bind).'
			' Same format as for --net-pub-bind.  Can be specified multiple times.')
	parser.add_argument('--net-sub-bind',
		action='append', metavar='ip:port',
		help='Create subscriber socket that'
				' publishers can connect and send messages to (see --net-pub-connect).'
			' Same format as for --net-pub-bind.  Can be specified multiple times.')
	parser.add_argument('--net-sub-connect',
		action='append', metavar='ip:port',
		help='Receive published messages from a specified pub socket (see --net-pub-bind).'
			' Same format as for --net-pub-bind.  Can be specified multiple times.')
	parser.add_argument('--net-settings', metavar='yaml',
		help='Optional yaml/json encoded settings'
			' for PubSub class init (e.g. hostname, buffer, reconnect_max, etc).')

	parser.add_argument('--debug', action='store_true', help='Enable debug logging to stderr.')

	args = argv or sys.argv[1:]
	optz = parser.parse_args(args)

	if optz.conf:
		import yaml
		for k, v in flatten_dict(yaml.load(open(optz.conf)) or dict()):
			if v is None: continue
			k = '_'.join(k).replace('-', '_')
			if not hasattr(optz, k):
				parser.error('Unrecognized option in config file ({}): {}'.format(optz.conf, k))
			setattr(optz, k, v)
		optz = parser.parse_args(args, optz) # re-parse to override cli-specified values

	import logging
	logging.basicConfig(level=logging.DEBUG if optz.debug else logging.WARNING)
	log = logging.getLogger('daemon')

	optz.filter_file = os.path.expanduser(optz.filter_file)
	core.Notification.default_timeout = optz.popup_timeout
	if optz.filter_sound: optz.filter_sound = core.get_sound_env(force_sync=optz.filter_test)

	if optz.filter_test:
		func = core.get_filter(optz.filter_file, optz.filter_sound)
		filtering_result = func(*optz.filter_test)
		msg_repr = 'Message - summary: {!r}, body: {!r}'.format(*optz.filter_test)
		print('{}\nFiltering result: {} ({})'.format( msg_repr,
			filtering_result, 'will pass' if filtering_result else "won't pass" ))
		sys.exit()

	DBusGMainLoop(set_as_default=True)
	bus = dbus.SessionBus()

	if optz.net_pub_bind or optz.net_pub_connect\
			or optz.net_sub_bind or optz.net_sub_connect:
		if optz.net_settings and not isinstance(optz.net_settings, Mapping):
			import yaml
			optz.net_settings = yaml.load(optz.net_settings)
		pubsub = PubSub(**(optz.net_settings or dict()))
		for addrs, call in [
				(optz.net_pub_bind, pubsub.bind_pub),
				(optz.net_sub_bind, pubsub.bind_sub),
				(optz.net_pub_connect, pubsub.connect),
				(optz.net_sub_connect, pubsub.subscribe) ]:
			if isinstance(addrs, types.StringTypes): addrs = [addrs]
			for addr in set(addrs or set()):
				log.debug('zmq link: %s %s', call.im_func.func_name, addr)
				call(addr)
	else: pubsub = None

	daemon = notification_daemon_factory( pubsub, bus,
		optz.dbus_path, dbus.service.BusName(optz.dbus_interface, bus) )
	loop = GLib.MainLoop()
	log.debug('Starting gobject loop')
	loop.run()


if __name__ == '__main__': sys.exit(main())
