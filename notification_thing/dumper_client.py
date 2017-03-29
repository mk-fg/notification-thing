#!/usr/bin/env python2
# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from contextlib import closing
from time import sleep
import os, sys, select, time

if __name__ == '__main__':
	# For running from a checkout
	from os.path import join, realpath, dirname
	module_root = realpath(dirname(dirname(__file__)))
	if module_root not in sys.path: sys.path.insert(0, module_root)

from notification_thing.pubsub import PubSub


def main(args=None):
	import argparse
	parser = argparse.ArgumentParser(
		description='Receive (over pubsub transport)'
			' and dump all notification messages from a remote peer.')
	parser.add_argument('bind',
		help='Port number or address to bind to (e.g. 1.2.3.4:5678).')
	parser.add_argument('-j', '--json', action='store_true',
		help='Print json-serialized messages instead of formatted readable representation.')
	parser.add_argument('--debug', action='store_true', help='Verbose operation mode.')
	opts = parser.parse_args(sys.argv[1:] if args is None else args)

	import logging
	logging.basicConfig(level=logging.DEBUG if opts.debug else logging.WARNING)
	log = logging.getLogger()

	with closing(PubSub()) as sub:
		if opts.bind.isdigit(): opts.bind = '[::]:{}'.format(opts.bind)
		sub.bind_sub(opts.bind)
		s = select.epoll()
		s.register(sub.fileno(), select.POLLIN | select.POLLPRI)
		log.debug('Entering message-dump loop')
		while True:
			try: s.poll()
			except KeyboardInterrupt: return
			while True:
				msg = sub.recv(raw=opts.json)
				if msg is None: break
				if not opts.json:
					if msg.note.get('plain'): summary, body = msg.note.plain
					else: summary, body = op.itemgetter('summary', 'body')(msg.note)
					print('Message:\n  {}\n'.format('\n  '.join([
						'Host: {}'.format(msg.hostname),
						'Timestamp: {}'.format(time.strftime(
							'%Y-%m-%d %H:%M:%S', time.localtime(msg.ts) )),
						'Summary: {}'.format(summary),
						'Body:\n{}'.format('\n'.join(it.imap('    {}'.format, body.split('\n')))) ])))
				else: print(msg.strip())

	log.debug('Finished')

if __name__ == '__main__': sys.exit(main())
