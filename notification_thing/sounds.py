# -*- coding: utf-8 -*-
from __future__ import print_function

import itertools as it, operator as op, functools as ft
import os, sys, ctypes, time

# http://0pointer.de/lennart/projects/libcanberra/gtkdoc/libcanberra-canberra.html


class NSoundError(Exception): pass
class NSoundTimeout(NSoundError): pass

class NotificationSounds(object):
	'Simple ctypes wrapper for libcanberra.'

	ca_context_t = ctypes.c_void_p
	ca_context_funcs = ( 'create destroy open set_driver change_device'
		' change_props change_props_full play play_full cancel cache cache_full playing' ).split()

	ca_errs = dict((-n, k) for n, k in enumerate((
		' none notsupported invalid state oom nodriver system corrupt'
		' toobig notfound destroyed canceled notavailable access io'
		' internal disabled forked disconnected' ).split()))

	ca_ids = iter(it.chain.from_iterable(it.imap(xrange, it.repeat(2**30))))
	ca_props = set((
		'application.icon application.icon_name application.id application.language'
		' application.name application.process.binary application.process.host'
		' application.process.id application.process.user application.version'

		' canberra.cache-control canberra.enable canberra.force_channel'
		' canberra.volume canberra.xdg-theme.name canberra.xdg-theme.output-profile'

		' event.description event.id event.mouse.button'
		' event.mouse.hpos event.mouse.vpos event.mouse.x event.mouse.y'

		' media.artist media.filename media.icon media.icon_name'
		' media.language media.name media.role media.title'

		' window.desktop window.height window.hpos window.icon window.icon_name'
		' window.id window.name window.vpos window.width window.x window.x11.display'
		' window.x11.monitor window.x11.screen window.x11.xid window.y' ).split())

	@classmethod
	def _chk_int(cls, res, func, args, gt0=False):
		if res < 0 or (gt0 and res == 0):
			errno_ = ctypes.get_errno()
			raise NSoundError(dict(
				result=res, result_ca_strerror=cls.ca_errs.get(res, 'unknown'),
				errno=errno_, errno_strerror=os.strerror(errno_) ))
		return res

	_lib_ca = None
	@classmethod
	def _get_lib(cls):
		if cls._lib_ca is None:
			libca = cls._lib_ca = ctypes.CDLL('libcanberra.so.0')
			for k in cls.ca_context_funcs:
				getattr(libca, 'ca_context_{}'.format(k)).errcheck = cls._chk_int
		return cls._lib_ca

	@classmethod
	def play_once(cls, *play_sync_args, **play_sync_kws):
		with cls() as snd:
			snd.play_sync(*play_sync_args, **play_sync_kws)


	def __init__(self):
		self._ctx, self._lib = self.ca_context_t(), self._get_lib()
		self._lib.ca_context_create(ctypes.byref(self._ctx))

	def __enter__(self):
		self.open()
		return self
	def __exit__(self, *err): self.close()
	def __del__(self): self.close()

	def _ctx_call(self, func, *args):
		assert func in self.ca_context_funcs, func
		assert self._ctx is not None
		func = getattr(self._lib, 'ca_context_{}'.format(func))
		func(self._ctx, *args)

	def _ctx_call_props(self, func, *args):
		props_dict = (args and args[-1]) or None
		args = list(args[:-1]) + self._ctx_props(props_dict)
		return self._ctx_call(func, *args)

	def _ctx_props(self, props_dict=None):
		props_dict = props_dict or dict()
		assert self.ca_props.issuperset(props_dict.viewkeys()), props_dict
		props = list(it.chain.from_iterable(it.imap(bytes, t) for t in props_dict.viewitems()))
		props.append(None)
		return props


	def open(self): self._ctx_call('open')
	def close(self): self.destroy()
	def destroy(self):
		if self._ctx is not None:
			self._ctx_call('destroy')
			self._ctx = None

	def change_props(self, props_dict):
		self._ctx_call_props('change_props', props_dict)

	def play(self, name=None, play_id=None, props_dict=None):
		props_dict = props_dict or dict()
		if name: props_dict['event.id'] = name
		if play_id is None: play_id = next(self.ca_ids)
		self._ctx_call_props('play', play_id, props_dict)
		return play_id

	def play_sync(self, name=None, play_id=None, props_dict=None, **wait_kws):
		play_id = self.play(name, play_id=play_id, props_dict=props_dict)
		self.wait(play_id, **wait_kws)

	def cache(self, props_dict):
		self._ctx_call_props('cache', props_dict)

	def cancel(self, play_id):
		self._ctx_call('cancel', play_id)

	def playing(self, play_id):
		res = ctypes.c_int()
		self._ctx_call('playing', play_id, ctypes.byref(res))
		return bool(res)

	def wait(self, play_id, poll_delay=0.2, timeout=60):
		deadline, countdown = time.time(), iter(xrange(int(timeout / poll_delay)))
		while True:
			try: next(countdown)
			except StopIteration:
				if time.time() > deadline: raise NSoundTimeout()
			if not self.playing(play_id): return
			time.sleep(poll_delay)


if __name__ == '__main__':
	NotificationSounds.play_once('phone-incoming-call')
