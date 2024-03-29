import operator as op, functools as ft, collections as cs
import dbus, argparse, re, logging, time

from .scheme import load, init_env
from .rate_control import FC_TokenBucket, RRQ
from . import __version__


class StartupFailure(Exception): pass

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
		for k,v in self.items():
			if v == v_chk: return k
		else: raise KeyError(v_chk)


def to_bytes(obj, encoding='utf-8', errors='backslashreplace'):
	if not isinstance(obj, (str, bytes)): obj = str(obj)
	if isinstance(obj, str): obj = obj.encode(encoding, errors)
	return obj

def to_str(obj, encoding='utf-8', errors='replace'):
	if not isinstance(obj, (str, bytes)): obj = str(obj)
	if isinstance(obj, bytes): obj = obj.decode(encoding, errors)
	return obj

def format_trunc(v, proc=to_str, len_max=None):
	try:
		v = proc(v)
		if len_max is None: len_max = 1024 # len_max_default
		if len(v) > len_max: v = v[:len_max] + type(v)(f'... (len: {len(v)})')
	except Exception as err:
		logging.getLogger('core.strings')\
			.exception('Failed to process string %r: %s', v, err)
	return v

def repr_trunc(v, len_max=None):
	return format_trunc(v, proc=repr, len_max=len_max)

def repr_trunc_rec(v, len_max=None, len_max_val=None, level=1):
	# Formats all dict values as strings with quotes - it's fine, not worth the trouble fixing
	if level == 0: return format_trunc(v)
	if len_max is None: len_max = 2048 # len_max_default
	if len_max_val is None: len_max_val = 512 # len_max_default
	rec = ft.partial( repr_trunc_rec,
		len_max=len_max, len_max_val=len_max_val, level=level-1 )
	if isinstance(v, dict): v = dict((k, rec(v)) for k,v in v.items())
	elif isinstance(v, (tuple, list)): v = list(map(rec, v))
	else: return format_trunc(v, len_max=len_max_val)
	return repr_trunc(v, len_max=len_max)


####

optz = dict(
	activity_timeout=10*60, popup_timeout=5,
	queue_len=10, history_len=200, feed_icon=None,
	tbf_size=4, tbf_tick=15, tbf_max_delay=60, tbf_inc=2, tbf_dec=2 )
poll_interval = 60

urgency_levels = Enum('low', 'normal', 'critical')
close_reasons = Enum('expired', 'dismissed', 'closed', 'undefined', vals=range(1, 5))

layout_anchor = Enum('top_left', 'top_right', 'bottom_left', 'bottom_right')
layout_direction = Enum('horizontal', 'vertical')

####


class Notification(cs.UserDict):

	data = created = None

	init_args = ( 'summary', 'body', 'timeout',
		'icon', 'app_name', 'replaces_id', 'actions', 'hints', 'plain' )
	dbus_args = ( 'app_name', 'replaces_id',
		'icon', 'summary', 'body', 'actions', 'hints', 'timeout' )
	default_timeout = optz['popup_timeout']

	@classmethod
	def from_dbus(cls, *argz):
		'Get all arguments in dbus-interface order.'
		return cls(**dict(zip(cls.dbus_args, argz)))

	@classmethod
	def system_message(cls, *argz, **kwz):
		kwz.setdefault('hints', dict()).setdefault(
			'urgency', dbus.Byte(urgency_levels.critical, variant_level=1) )
		return cls(*argz, **kwz)

	def __init__( self, summary='', body='', timeout=-1, icon='', app_name='generic',
			replaces_id=dbus.UInt32(), actions=dbus.Array(signature='s'), hints=dict(), plain=None ):
		self.created = time.monotonic()
		if timeout == -1: timeout = self.default_timeout # yes, -1 is special-case value in specs
		elif timeout is None: timeout = -1 # to be serialized or whatever
		self.data = dict(zip(self.init_args, op.itemgetter(*self.init_args)(locals())))

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
		return f'<Notification[{id(self):x}] summary={self.summary!r} body={self.body!r}>'

	def clone(self): return Notification(**self.data)

# As serialized for pubsub transport
NotificationMessage = cs.namedtuple('NotificationMessage', 'hostname ts note')


_scheme_state = None

def get_filter(path, sound_env=None):
	global _scheme_state
	if _scheme_state is None:
		sound_env = sound_env or dict()
		noop_func = lambda *a: None
		init_env({
			'~': lambda regex, string: bool(re.search(regex, string)),
			'sound-play': sound_env.get('play', noop_func),
			'sound-cache': sound_env.get('cache', noop_func),
			'sound-play-sync': sound_env.get('play_sync', noop_func),
			'props': lambda *props: _scheme_state.update(props=props) })
		_scheme_state = dict()
	scheme_func = load(path)
	def filter_func(summary, body, note=None):
		_scheme_state.clear()
		result = scheme_func(summary, body)
		if note and (props := _scheme_state.get('props')):
			for k, v in zip(*([iter(props)]*2)):
				if not k.startswith('hints.'): note[k] = v
				else: note.setdefault('hints', dict())[k[6:]] = v
		return result
	return filter_func

def get_sound_env(force_sync=False, trap_errors=False):
	assert _scheme_state is None # must be initialized before scheme env
	# Can pass window position and allow configuration of canberra props here
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
				if not trap_errors:
					log.exception('Failed to play sound sample %r: %s', name, err)
		res = dict((k, ft.partial(snd, k)) for k in 'play play_sync cache'.split())
		if force_sync: res['play'] = res['play_sync']
		return res
