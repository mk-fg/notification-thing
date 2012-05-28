# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
import dbus, argparse

from fgc.scheme import load as scheme_load, init_env as scheme_init_env
from fgc.fc import FC_TokenBucket, RRQ
from fgc.err import ext_traceback


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
	tbf_size=4, tbf_tick=15, tbf_max_delay=60, tbf_inc=2, tbf_dec=2 )
poll_interval = 60

dbus_id = 'org.freedesktop.Notifications'
dbus_path = '/org/freedesktop/Notifications'

urgency_levels = Enum('low', 'normal', 'critical')
close_reasons = Enum('expired', 'dismissed', 'closed', 'undefined', vals=range(1, 5))

layout_anchor = Enum('top_left', 'top_right', 'bottom_left', 'bottom_right')
layout_direction = Enum('horizontal', 'vertical')

####


class Notification(dict):
	__slots__ = tuple()
	dbus_args = 'app_name', 'replaces_id', 'icon',\
		'summary', 'body', 'actions', 'hints', 'timeout'
	default_timeout = optz['popup_timeout']

	@classmethod
	def from_dbus(cls, *argz):
		'Get all arguments in dbus-interface order.'
		return cls(**dict(it.izip(cls.dbus_args, argz)))

	def __init__( self, summary='', body='', timeout=-1, icon='',
			app_name='generic', replaces_id=dbus.UInt32(), actions=dbus.Array(signature='s'),
			hints=dict(urgency=dbus.Byte(urgency_levels.critical, variant_level=1)) ):
		if timeout == -1: timeout = self.default_timeout # yes, -1 is special-case value in specs
		argz = self.__init__.func_code.co_varnames # a bit hacky, but DRY
		super(Notification, self).__init__(
			it.izip(argz, op.itemgetter(*argz)(locals())) )

	def __iter__(self):
		return iter(op.itemgetter(*self.dbus_args)(self))

	def __getattr__(self, k):
		if not k.startswith('__'): return self[k]
		else: raise AttributeError
	def __setattr__(self, k, v): self[k] = v
