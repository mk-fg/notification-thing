#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from time import time
from dbus.mainloop.glib import DBusGMainLoop
import dbus, dbus.service
import os, sys, traceback, types

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import GObject

try: from gi.repository import Gdk
except RuntimeError as err: # less verbose errors in case X isn't running
	print('Gdk init error, exiting: {}'.format(err.message), file=sys.stderr)
	sys.exit(1)


if __name__ == '__main__':
	# Try to import submodules from the same path, not the site-packages
	from os.path import join, realpath, dirname
	module_root = realpath(dirname(dirname(__file__)))
	if module_root not in sys.path: sys.path.insert(0, module_root)
	from notification_thing.display import NotificationDisplay
	from notification_thing import core

else:
	from .display import NotificationDisplay
	from . import core


DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()

optz, poll_interval, close_reasons, urgency_levels =\
	core.optz, core.poll_interval, core.close_reasons, core.urgency_levels



class NotificationMethods(object):
	plugged, timeout_cleanup = False, True
	_activity_timer = None

	def __init__(self):
		tick_strangle_max = op.truediv(optz.tbf_max_delay, optz.tbf_tick)
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


	def GetServerInformation(self):
		self._activity_event()
		return 'notification-thing', 'mk.fraggod@gmail.com', 'git', '1.2'

	def GetCapabilities(self):
		# action-icons, actions, body, body-hyperlinks, body-images,
		#  body-markup, icon-multi, icon-static, persistence, sound
		self._activity_event()
		return ['body', 'persistence', 'icon-static']

	def NotificationClosed(self, nid, reason=None):
		log.debug(
			'NotificationClosed signal (id: {}, reason: {})'\
			.format(nid, close_reasons.by_id(reason)) )

	def ActionInvoked(self, nid, action_key):
		log.debug('Um... some action invoked? Params: {}'.format([nid, action_key]))


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

		log.debug('Property change: {} = {}'.format(k, v))

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
			log.debug('Cleanup timeout: {}'.format(self.timeout_cleanup))
			if optz.status_notify:
				self.display( 'Notification proxy: cleanup timeout is {}'\
					.format('enabled' if self.timeout_cleanup else 'disabled') )

		elif optz.status_notify:
			self.display( 'notification-thing:'
				' unrecognized parameter', 'Key: {!r}, value: {!r}'.format(k, v) )
			return

		self.PropertiesChanged(iface, {k: v}, [])

	def PropertiesChanged(self, iface, props_changed, props_invalidated):
		log.debug( 'PropertiesChanged signal: {}'\
			.format([iface, props_changed, props_invalidated]) )


	def Flush(self):
		log.debug('Manual flush of the notification buffer')
		self._activity_event()
		return self.flush(force=True)

	def List(self):
		log.debug('NotificationList call')
		self._activity_event()
		return self._note_windows.keys()

	def Cleanup(self, timeout, max_count):
		log.debug( 'NotificationCleanup call'
			' (timeout={:.1f}s, max_count={})'.format(timeout, max_count) )
		self._activity_event()
		if max_count <= 0: max_count = None
		ts_min = time() - timeout
		for nid, note in sorted(self._note_windows.viewitems(), key=lambda t: t[1].created):
			if note.created < ts_min: self.close(nid, reason=close_reasons.closed)
			if max_count is not None:
				max_count -= 1
				if max_count <= 0: break


	def Notify(self, app_name, nid, icon, summary, body, actions, hints, timeout):
		self._activity_event()
		note = core.Notification.from_dbus(
			app_name, nid, icon, summary, body, actions, hints, timeout )
		if nid: self.close(nid, reason=close_reasons.closed)
		try: return self.filter(note)
		except Exception:
			log.exception('Unhandled error')
			return 0

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
		if not win: return False
		win_state = win.get_state()
		w, h = win.get_width(), win.get_height()
		# get_geometry fails with "BadDrawable" from X if the window is closing,
		#  and x/y parameters there are not absolute and useful anyway.
		# x, y, w, h = win.get_geometry()
		return (win_state & win_state.FULLSCREEN)\
			or (w >= screen.get_width() - jitter and h >= screen.get_height() - jitter)

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
				if note.get('timer_id'): GObject.source_remove(note.timer_id)

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
		def __init__(self, *dbus_svc_argz, **dbus_svc_kwz):
			NotificationMethods.__init__(self)
			dbus.service.Object.__init__(self, *dbus_svc_argz, **dbus_svc_kwz)

	return NotificationDaemon(*dbus_svc_argz, **dbus_svc_kwz)



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

	parser.add_argument('--dbus-interface', default=optz['dbus_interface'],
		help='DBus interface to use (default: %(default)s)')
	parser.add_argument('--dbus-path', default=optz['dbus_path'],
		help='DBus object path to bind to (default: %(default)s)')

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
	log = logging.getLogger(__name__)

	daemon = notification_daemon_factory( bus,
		optz.dbus_path, dbus.service.BusName(optz.dbus_interface, bus) )
	loop = GObject.MainLoop()
	log.debug('Starting gobject loop')
	loop.run()


if __name__ == '__main__': main()
