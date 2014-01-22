# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from collections import deque
from time import time, sleep


class RRQ(deque): # round-robin queue
	dropped = 0
	def __init__(self, limit):
		self._limit = limit
		super(RRQ, self).__init__()
	def _trim(self, size=None):
		if size is None: size = self._limit
		while len(self) > size:
			self.popleft()
			self.dropped += 1
	def append(self, *argz):
		self._trim()
		super(RRQ, self).append(*argz)
	def extend(self, *argz):
		self._trim()
		super(RRQ, self).extend(*argz)
	def flush(self):
		self._trim(0)
		self.dropped = 0
	@property
	def is_full(self):
		return len(self) == self._limit


FC_UNDEF = 0
FC_OK = 1
FC_EMPTY = 2
FC_STARVE = 4

class FC_TokenBucket(object):
	'''Token bucket flow control mechanism implementation.

		Essentially it behaves like a bucket of given capacity (burst),
		which fills by fill_rate (flow) tokens per time unit (tick, seconds).
		Every poll / consume call take tokens to execute, and either
		block until theyre available (consume+block) or return False,
		if specified amount of tokens is not available.
		Blocking request for more tokens when bucket capacity raises an
		exception.

		tick_strangle / tick_free is a functions (or values) to set/adjust
		fill_rate coefficient (default: 1) in case of consequent blocks /
		grabs - cases when bucket fill_rate is constantly lower
		(non-blocking requests doesnt counts) / higher than token
		requests.'''

	_tick_mul = 1
	_spree = FC_UNDEF

	def __init__( self, flow=1, burst=5, tick=1,
			tick_strangle=None, tick_free=None, start=None ):
		'''flow: how many tokens are added per tick;
			burst: bucket size;
			tick (seconds): time unit of operation;
			tick_strangle / tick_free:
				hooks for consequent token shortage / availability,
				can be either int/float/long or a function, accepting
				current flow multiplier as a single argument;
			start:
				starting bucket size, either int/float/long or a function
				of bucket capacity.'''
		self.fill_rate = flow
		self.capacity = burst
		self._tokens = burst if start is None else self._mod(start, burst)
		self._tick = tick
		self._tick_strangle = tick_strangle
		self._tick_free = tick_free
		self._synctime = time()

	_mod = lambda s, method, val: \
		method if isinstance(method, (int, float, long)) else method(s._tick_mul)

	def _flow_adjust(self):
		tc = self.tokens # logic-independent update of the bucket
		if self._spree & FC_STARVE or tc < 1:
			if self._spree & FC_EMPTY: self._strangle()
			self._spree = FC_EMPTY
		else:
			if self._spree & FC_OK: self._free()
			self._spree = FC_OK

	def _free(self): self._tick_mul = self._mod(self._tick_free, self._tick_mul)
	def _strangle(self): self._tick_mul = self._mod(self._tick_strangle, self._tick_mul)
	## Above methods should only be called _right_after_ self._synctime update
	##  (like flow_adjust does), otherwise they'll screw up token flow calculations
	free = lambda s: (s.tokens, s._free) and None
	strangle = lambda s: (s.tokens, s._strangle) and None


	@property
	def tick(self):
		'Current time unit, adjusted by strangle/free functions'
		return float(self._tick * self._tick_mul)

	@property
	def tokens(self):
		'Number of tokens in the bucket at the moment'
		ts = time()
		if self._tokens < self.capacity:
			self._tokens = min( self.capacity,
				self._tokens + self.fill_rate * (ts - self._synctime) / self.tick )
		self._synctime = ts
		return self._tokens

	def get_eta(self, count=1):
		'Return amount of seconds until the given number of tokens will be available'
		if count > self.capacity:
			## TODO: Implement buffered grab for this case?
			raise ValueError, ( 'Token bucket deadlock:'
				' %s tokens requested, while max capacity is %s'%(count, self.capacity) )
		return self.tick - time() % self.tick

	def consume(self, count=1, block=False, force=False):
		'Take tokens from the bucket'
		tc = self.tokens

		if force or count <= tc: # enough tokens are available
			self._tokens -= count
			self._flow_adjust()
			return True

		elif block: # wait for tokens
			sleep(self.get_eta(count))
			self._spree |= FC_STARVE # to ensure the 'empty' set/check
			return self.consume(count=count, block=block)

		else:
			self._spree = FC_EMPTY | FC_STARVE
			return False

	def poll(self, count=1):
		'Check token availability w/o taking any'
		if count <= self.tokens: return True
		else: return False
