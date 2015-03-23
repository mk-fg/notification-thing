# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from collections import namedtuple, MutableMapping
from time import time
import dbus, argparse, re, logging

from .scheme import load, init_env
from .rate_control import FC_TokenBucket, RRQ
from . import __version__


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

optz = dict(
	activity_timeout=10*60, popup_timeout=5,
	queue_len=10, history_len=30,
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

	init_args = 'summary', 'body', 'timeout', 'icon',\
		'app_name', 'replaces_id', 'actions', 'hints', 'plain'
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
			replaces_id=dbus.UInt32(), actions=dbus.Array(signature='s'), hints=dict(), plain=None ):
		self.created = time()
		if timeout == -1: timeout = self.default_timeout # yes, -1 is special-case value in specs
		elif timeout is None: timeout = -1 # to be serialized or whatever
		self.data = dict(it.izip(self.init_args, op.itemgetter(*self.init_args)(locals())))

	def __iter__(self):
		return iter(op.itemgetter(*self.dbus_args)(self.data))
	def __getattr__(self, k):
		if not k.startswith('__'):
			if k in self.init_args: return self.data[k]
			return super(Notification, self).__getattr__(k)
		else: raise AttributeError
	def __setattr__(self, k, v):
		if not self.data or k not in self.data: self.__dict__[k] = v
		else: self.data[k] = v

	def __len__(self): return len(self.data)
	def __getitem__(self, k): return self.data[k]
	def __setitem__(self, k, v): self.data[k] = v
	def __delitem__(self, k): del self.data[k]

	def __repr__(self):
		return '<Notification[{:x}] summary={!r} body={!r}>'\
			.format(id(self), self.summary, self.body)

	def clone(self): return Notification(**self.data)

# As serialized for pubsub transport
NotificationMessage = namedtuple('NotificationMessage', 'hostname ts note')


_scheme_init = False

def get_filter(path, sound_env=None):
	if not _scheme_init:
		sound_env = sound_env or dict()
		init_env({
			'~': lambda regex, string: bool(re.search(regex, string)),
			'sound-play': sound_env.get('play'),
			'sound-cache': sound_env.get('cache'),
			'sound-play-sync': sound_env.get('play_sync') })
	return load(path)

def get_sound_env():
	assert not _scheme_init # must be initialized before scheme env
	# XXX: pass window position and allow configuration of canberra props
	from .sounds import NotificationSounds, NSoundError, NSoundInitError
	log = logging.getLogger('core.sound')
	try:
		env = NotificationSounds()
		env.change_props({
			'application.id': 'net.fraggod.notification-thing',
			'application.name': 'notification-thing',
			'application.version': __version__,
			'application.language': 'en_US' })
		env.open()
	except NSoundError as err:
		log.exception('Failed to initialize sound output: %s', err)
	else:
		def snd(func, name):
			log.debug('Sound sample %r: %r', func, name)
			try: getattr(env, func)(name)
			except NSoundError as err:
				log.exception('Failed to play sound sample %r: %s', name, err)
		return dict((k, ft.partial(snd, k)) for k in b'play play_sync cache'.split())
