#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from contextlib import closing
from time import sleep
import os, sys, select

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
		description='Receive (over pubsub transport)'
			' and dump all notification messages from a remote peer.')
	parser.add_argument('bind',
		help='Port number or address to bind to (e.g. 1.2.3.4:5678).')
	parser.add_argument('--debug', action='store_true', help='Verbose operation mode.')
	opts = parser.parse_args(sys.argv[1:] if args is None else args)

	import logging
	logging.basicConfig(level=logging.DEBUG if opts.debug else logging.WARNING)
	log = logging.getLogger()

	with closing(PubSub()) as pub:
		if opts.bind.isdigit(): opts.bind = '[::]:{}'.format(opts.bind)
		pub.bind_sub(opts.bind)
		s = select.epoll()
		s.register(pub.fileno(), select.POLLIN | select.POLLPRI)
		log.debug('Entering message-dump loop')
		while True:
			s.poll()
			log.debug('Poll event')
			while True:
				msg = pub.recv()
				if msg is None: break
				if msg.note.get('plain'): summary, body = msg.note.plain
				else: summary, body = op.itemgetter('summary', 'body')(msg.note)
				print(
					'Message:\n  Host: {0.hostname}\n  Summary: {1}\n  Body:\n{2}\n'
					.format(msg, summary, '\n'.join(it.imap('    {}'.format, body.split('\n')))) )


if __name__ == '__main__': sys.exit(main())
