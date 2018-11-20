# -*- coding: utf-8 -*-
from __future__ import print_function

import os, sys, fcntl, time, base64, datetime as dt

from . import core


class FileLogger(object):

	_stream = _stream_file_id = None

	def __init__(self, dst, files=None, size_limit=1 * 2**20):
		if not os.path.exists(dst):
			dst_dir = os.path.dirname(dst)
			if dst_dir and not os.path.exists(dst_dir): os.makedirs(dst_dir, 0o700)
		elif not os.access(dst, os.W_OK): raise OSError(dst, 'non-writable')
		self.dst_path = dst
		self.rotate_files, self.rotate_bytes = max(0, files or 0), max(0, size_limit)

	def __del__(self):
		if self._stream: self._stream.close()

	def _stream_open(self):
		if self._stream: self._stream = self._stream.close()
		self._stream = open(self.dst_path, 'a')
		fcntl.lockf(self._stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
		stat = os.fstat(self._stream.fileno())
		self._stream_file_id = stat.st_dev, stat.st_ino

	def get_stream(self):
		reopen = not self._stream
		try: stat = os.stat(self.dst_path)
		except (OSError, IOError): stat, reopen = None, True
		else:
			stream_file_id = stat.st_dev, stat.st_ino
			if self._stream_file_id != stream_file_id: reopen = True
		if ( self.rotate_files > 0
				and stat and stat.st_size >= self.rotate_bytes ):
			p_func = lambda n: '{}.{}'.format(self.dst_path, n)
			for n in range(self.rotate_files-1, 0, -1):
				bak, bak_old = p_func(n), p_func(n+1)
				try: os.rename(bak, bak_old)
				except OSError: pass
			bak, reopen = p_func(1), True
			os.rename(self.dst_path, bak)
		if reopen: self._stream_open()
		return self._stream

	def write(self, title, body, urgency=None, ts=None):
		if not ts: ts = time.time()
		stream = self.get_stream()
		uid = base64.urlsafe_b64encode(os.urandom(3))
		ts_str = dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
		urgency = {core.urgency_levels.critical: '!', core.urgency_levels.low: '.'}.get(urgency, ' ')
		msg = (
			['{ts} :: {uid} {urgency} :: -- {title}'.format(
				ts=ts_str, uid=uid, urgency=urgency, title=title )]
			+ list( '{ts} :: {uid} {urgency} ::    {line}'.format(
				ts=ts_str, uid=uid, urgency=urgency, line=line ) for line in body.splitlines() ) + [''] )
		stream.write('\n'.join(msg))
		stream.flush()
