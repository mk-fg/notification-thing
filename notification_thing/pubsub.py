# -*- coding: utf-8 -*-
from __future__ import print_function

import itertools as it, operator as op, functools as ft
from time import time
import os, sys, types, re

from .core import Notification, NotificationMessage


class PubSub(object):

	# Messages with higher versions will be discarded
	protocol_version = 1

	ctx = sub = pub = None


	def __init__( self, hostname=None, peer_id=None,
			buffer=30, blocking_send=False, reconnect_max=300.0 ):
		'''Opts:
			hostname - name to send to peer along with the message as "source".
				uname is used by default.
			peer_id - zmq id of this peer's sockets.
				Derived from machine-id, dbus id or uname by default.
			buffer - zmq hwm value for pub socket - how many
				messages to keep buffered for each connected peer before dropping.
			blocking_send - use dealer socket type to kinda-reliably
					deliver message(s) to all connected peers with specified timeout.
				Should be float of seconds to linger on close, attempting to deliver stuff.
			reconnect_max - max interval between peer reconnection attempts.'''
		self.hostname, self.buffer = hostname or os.uname()[1], buffer
		if blocking_send:
			raise NotImplementedError('blocking_send option does not work properly at the moment.')
		self.blocking_send = blocking_send
		self._init_id(peer_id)
		self._init_encoding()
		self._init_zmq(reconnect_max=reconnect_max)

	def _init_encoding(self):
		# Simple json should work ok here, I guess
		import json
		self.dumps, self.loads = json.dumps, json.loads

	def _init_id(self, peer_id=None):
		'This ID should - ideally - be persistent for the machine.'
		if peer_id is not None:
			self.peer_id = peer_id
			return
		for p in '/etc/machine-id', '/var/lib/dbus/machine-id':
			try:
				with open(p) as src:
					self.peer_id = src.read().strip()
			except (OSError, IOError): pass
			else: return
		else:
			self.peer_id = b'--uname--' + os.uname()[1]

	def _init_zmq(self, reconnect_max=None):
		import zmq
		self.zmq, self.ctx = zmq, zmq.Context()
		self.pub = self.ctx.socket(
			zmq.PUB if not self.blocking_send else zmq.DEALER )
		self.sub = self.ctx.socket(zmq.SUB)
		for sock in self.pub, self.sub:
			sock.setsockopt(zmq.IDENTITY, self.peer_id)
			sock.setsockopt(zmq.IPV4ONLY, False)
			if reconnect_max is not None:
				sock.setsockopt(zmq.RECONNECT_IVL_MAX, int(reconnect_max * 1000))
		self.sub.setsockopt(zmq.SUBSCRIBE, '')
		self.pub.setsockopt( zmq.LINGER,
			0 if not self.blocking_send else int(self.blocking_send * 1000) )
		self.pub.setsockopt(zmq.SNDHWM, self.buffer)

	def __del__(self):
		self.close()


	def _peer_addr_func(func):
		@ft.wraps(func)
		def _wrapper(self, addr, *args, **kws):
			if not re.search(r'^\w+://', addr):
				addr = 'tcp://{}'.format(addr)
			return func(self, addr, *args, **kws)
		return _wrapper

	@_peer_addr_func
	def subscribe(self, addr):
		'Start receiving messages from specified remote peer.'
		self.sub.connect(addr)

	@_peer_addr_func
	def connect(self, addr):
		'Publish messages to specified remote peer.'
		self.pub.connect(addr)

	@_peer_addr_func
	def bind_pub(self, addr):
		'Bind pub sucket to sepcified address.'
		self.pub.bind(addr)

	@_peer_addr_func
	def bind_sub(self, addr):
		'Bind sub sucket to sepcified address.'
		self.sub.bind(addr)

	def fileno(self):
		return self.sub.getsockopt(self.zmq.FD)

	def close(self):
		for sock in self.pub, self.sub:
			if sock: sock.close()
		if self.ctx: self.ctx.term()
		self.pub = self.sub = self.ctx = None


	def strip_dbus_types(self, data):
		# Necessary because dbus types subclass pythin types,
		#  yet don't serialize in the same way - e.g. str(dbus.Byte(1)) is '\x01'
		#  (and not '1') - which messes up simple serializers like "json" module.
		sdt = self.strip_dbus_types
		if isinstance(data, dict): return dict((sdt(k), sdt(v)) for k,v in data.viewitems())
		elif isinstance(data, (list, tuple)): return map(sdt, data)
		elif isinstance(data, types.NoneType): return data
		for t in int, long, unicode, bytes, bool, float:
			if isinstance(data, t): return t(data)
		raise ValueError(( 'Failed to sanitize data type:'
			' {} (mro: {}, value: {})' ).format(type(data), type(data).mro(), data))

	def encode(self, note):
		data = self.strip_dbus_types(note.data)
		return chr(self.protocol_version) + self.dumps([self.hostname, time(), data])

	def decode(self, msg):
		if ord(msg[0]) > self.protocol_version: return
		hostname, ts, note_data = self.loads(msg[1:])
		return NotificationMessage(hostname, ts, Notification(**note_data))

	def send(self, note):
		'Publish message to all connected peers.'
		assert isinstance(note, Notification), note
		msg = self.encode(note)
		# pub shouldn't block, but just to be safe
		try: self.pub.send(msg, self.zmq.DONTWAIT)
		except self.zmq.ZMQError as err:
			if err.errno != self.zmq.EAGAIN: raise

	def recv(self, raw=False):
		'''Receive message from any of the connected
			peers, if available, otherwise None is returned.'''
		msg = None
		while not msg:
			try: msg = self.sub.recv(self.zmq.DONTWAIT)
			except self.zmq.ZMQError as err:
				if err.errno != self.zmq.EAGAIN: raise
				return
			msg_res = self.decode(msg) # can be None on protocol mismatch
		if msg_res is not None and raw: msg_res = msg
		return msg_res
