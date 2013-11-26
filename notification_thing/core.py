# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from collections import namedtuple, MutableMapping
from time import time
import dbus, argparse, re

from .scheme import load, init_env
from .rate_control import FC_TokenBucket, RRQ


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


####

optz = dict( activity_timeout=10*60, popup_timeout=5, queue_len=10,
	tbf_size=4, tbf_tick=15, tbf_max_delay=60, tbf_inc=2, tbf_dec=2,
	dbus_interface='org.freedesktop.Notifications', dbus_path='/org/freedesktop/Notifications' )
poll_interval = 60

urgency_levels = Enum('low', 'normal', 'critical')
close_reasons = Enum('expired', 'dismissed', 'closed', 'undefined', vals=range(1, 5))

layout_anchor = Enum('top_left', 'top_right', 'bottom_left', 'bottom_right')
layout_direction = Enum('horizontal', 'vertical')

####


class Notification(MutableMapping):

	data = created = None

	dbus_args = 'app_name', 'replaces_id', 'icon',\
		'summary', 'body', 'actions', 'hints', 'timeout'
	default_timeout = optz['popup_timeout']

	@classmethod
	def from_dbus(cls, *argz):
		'Get all arguments in dbus-interface order.'
		return cls(**dict(it.izip(cls.dbus_args, argz)))

	@classmethod
	def system_message(cls, *argz, **kwz):
		kwz.setdefault('hints', dict()).setdefault(
			'urgency', dbus.Byte(urgency_levels.critical, variant_level=1) )
		return cls(*argz, **kwz)

	def __init__( self, summary='', body='', timeout=-1, icon='', app_name='generic',
			replaces_id=dbus.UInt32(), actions=dbus.Array(signature='s'), hints=dict() ):
		self.created = time()
		if timeout == -1: timeout = self.default_timeout # yes, -1 is special-case value in specs
		elif timeout is None: timeout = -1 # to be serialized or whatever

		args = 'summary', 'body', 'timeout', 'icon', 'app_name', 'replaces_id', 'actions', 'hints'
		self.data = dict(it.izip(args, op.itemgetter(*args)(locals())))

	def __iter__(self):
		return iter(op.itemgetter(*self.dbus_args)(self.data))
	def __getattr__(self, k):
		if not k.startswith('__'): return self.data[k]
		else: raise AttributeError
	def __setattr__(self, k, v):
		if hasattr(self, k) or k not in self.data: self.__dict__[k] = v
		else: self.data[k] = v

	def __len__(self): return len(self.data)
	def __getitem__(self, k): return self.data[k]
	def __setitem__(self, k, v): self.data[k] = v
	def __delitem__(self, k): del self.data[k]

# As serialized for pubsub transport
NotificationMessage = namedtuple('NotificationMessage', 'hostname ts note')


_scheme_init = False

def get_filter(path):
	if not _scheme_init:
		init_env({'~': lambda regex, string: bool(re.search(regex, string))})
	return load(path)
