#!/usr/bin/env python


from contextlib import closing
from time import sleep
import os, sys

if __name__ == '__main__':
	# For running from a checkout
	from os.path import join, realpath, dirname
	module_root = realpath(dirname(dirname(__file__)))
	if module_root not in sys.path: sys.path.insert(0, module_root)

from notification_thing.core import Notification, urgency_levels
from notification_thing.pubsub import PubSub


def main(args=None):
	import argparse
	parser = argparse.ArgumentParser(
		description='Send notification message to remote peer over pubsub transport.')
	parser.add_argument('summary', help='Message summary header.')
	parser.add_argument('body', nargs='?', default='', help='Message body (can be empty).')

	parser.add_argument('-d', '--dst',
		required=True, action='append', metavar='ip:port',
		help='Peer address - can be either ip:port (assumed to be'
				' tcp socket, e.g. 1.2.3.4:5678) or full zmq url (e.g. tcp://1.2.3.4:5678).'
			' Can be specified multiple times to deliver message to more than one peer.'
			' Must be specified at least once.')

	parser.add_argument('-n', '--hostname',
		metavar='network_name', default=os.uname()[1],
		help='Source name to use for dispatched message.')
	parser.add_argument('-s', '--stdin', action='store_true', help='Read message body from stdin.')
	parser.add_argument('-w', '--wait-connect',
		type=float, metavar='seconds', default=0.5,
		help='Timeout to wait for peer connections to'
			' establish (default: %(default)s) and unsent messages to linger.')

	parser.add_argument('-u', '--urgency', metavar='low/normal/critical',
		help='Urgency hint to use for the message - can be either integer'
			' in 0-2 range (level id) or symbolic level name - low/normal/critical.')
	parser.add_argument('-t', '--expire-time', type=float, metavar='display_seconds',
		help='Timeout (in seconds) at which to expire the notification.')
	parser.add_argument('-a', '--app-name', metavar='name', default='notify-net',
		help='App name for the icon (default: %(default)s).')
	parser.add_argument('-i', '--icon', action='append', metavar='icon',
		help='Icon name, path or alias. Can be specified multiple times (for fallback icon names).')
	parser.add_argument('-c', '--category', action='append', metavar='type',
		help='Category hint(s) to attach to notification. Can be specified multiple times.')
	parser.add_argument('-x', '--hint', metavar='type:name:value',
		help='Arbitrary hint to attach to notification. Supported types: int, double, string, byte.')

	parser.add_argument('--debug', action='store_true', help='Verbose operation mode.')
	opts = parser.parse_args(sys.argv[1:] if args is None else args)

	import logging
	logging.basicConfig(level=logging.DEBUG if opts.debug else logging.WARNING)
	log = logging.getLogger()

	## Build notification object with specified parameters
	if opts.stdin:
		if opts.body:
			parser.error('Message body must be either passed'
				' as a cli argument or to stdin with --stdin flag, not both.')
		opts.body = sys.stdin.read()

	timeout = opts.expire_time and int(opts.expire_time * 1000)
	note = Notification(
		summary=opts.summary, body=opts.body,
		app_name=opts.app_name, timeout=timeout,
		icon=','.join(opts.icon or []) )

	if opts.urgency:
		try: urgency = int(opts.urgency)
		except ValueError:
			try: urgency = getattr(urgency_levels, opts.urgency)
			except AttributeError:
				parser.error(f'Unrecognized urgency level name: {opts.urgency}')
		else:
			if not 0 <= urgency <= 2:
				parser.error(f'Urgency level id must be in 0-2 range: {opts.urgency}')
		note.hints['urgency'] = urgency
	if opts.category: note.hints['category'] = ','.join(opts.category)
	if opts.hint: raise NotImplementedError(opts.hint)

	## Dispatch
	with closing(PubSub(opts.hostname, reconnect_max=None)) as pub:
		log.debug('Connecting to %s peer(s)', len(opts.dst))
		for dst in opts.dst: pub.connect(dst)
		sleep(opts.wait_connect)
		log.debug('Dispatching notification')
		pub.send(note)


if __name__ == '__main__': sys.exit(main())
