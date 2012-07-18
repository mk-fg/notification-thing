# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from collections import OrderedDict, namedtuple
import os, urllib

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, Pango

from .core import urgency_levels

import logging
log = logging.getLogger(__name__)


class NotificationDisplay(object):
	'''Interface to display notification stack.
		Should have "display(note, cb_dismiss=None) -> nid(UInt32, >0)", "close(nid)"
			methods and NoWindowError(nid) exception, raised on erroneous nid's in close().
		Current implementation based on notipy: git://github.com/the-isz/notipy.git'''
	window = namedtuple('Window', 'gobj event_boxes')

	def __init__( self, layout_margin,
			layout_anchor, layout_direction, icon_width, icon_height ):
		self.margins = dict(it.chain.from_iterable(map(
			lambda ax: ( (2**ax, layout_margin),
				(-2**ax, layout_margin) ), xrange(2) )))
		self.layout_margin = layout_margin
		self.layout_anchor = layout_anchor
		self.layout_direction = layout_direction
		self.icon_width = icon_width
		self.icon_height = icon_height

		self._windows = OrderedDict()

		self._default_style = Gtk.CssProvider()
		self._default_style.load_from_data( b'''
			#notification { background-color: white; }
			#notification #hs { background-color: black; }

			#notification #critical { background-color: #ffaeae; }
			#notification #normal { background-color: #f0ffec; }
			#notification #low { background-color: #bee3c6; }

			#notification #summary {
				font-size: 10;
				text-shadow: 1 1 0 gray;
			}
			#notification #body { font-size: 8; }''' )
		Gtk.StyleContext.add_provider_for_screen(
			Gdk.Screen.get_default(), self._default_style,
			Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION )

	def _update_layout(self):
		# Get the coordinates of the "anchor" corner (screen corner +/- margins)
		base = tuple(map(
			lambda ax, gdk_dim=('width', 'height'):\
				(getattr(Gdk.Screen, gdk_dim[ax])() - self.margins[2**ax])\
					if 2**ax & self.layout_anchor else self.margins[-2**ax], xrange(2) ))
		# Iterate over windows in order, placing each one starting from a "base" corner
		for win in map(op.attrgetter('gobj'), self._windows.viewvalues()):
			win.move(*map(lambda ax: base[ax] - ( win.get_size()[ax]
				if 2**ax & self.layout_anchor else 0 ), xrange(2)))
			margin = self.margins[(2 * ( (2**self.layout_direction)
				& self.layout_anchor ) / 2**self.layout_direction - 1) * 2**self.layout_direction]
			base = tuple(map(
				lambda ax: base[ax] if self.layout_direction != ax else\
					base[ax] + (margin + win.get_size()[ax])\
						* (2 * (2**ax ^ (2**ax & self.layout_anchor)) / 2**ax - 1), xrange(2) ))

	def _create_win(self, summary, body, icon=None, urgency_label=None):
		log.debug( 'Creating window with parameters: {}'\
			.format(', '.join(map(unicode, [summary, body, icon, urgency_label]))) )

		win = Gtk.Window(name='notification', type=Gtk.WindowType.POPUP)
		win.set_default_size(400, 20)
		ev_boxes = [win]

		frame = Gtk.Frame(shadow_type=Gtk.ShadowType.ETCHED_OUT)
		win.add(frame)

		widget_icon = None
		if icon is not None:
			if isinstance(icon, unicode):
				icon_path = os.path.expanduser(urllib.url2pathname(icon))
				if icon_path.startswith('file://'): icon_path = icon_path[7:]
				if os.path.isfile(icon_path):
					widget_icon = GdkPixbuf.Pixbuf.new_from_file(icon_path)
				else:
					# Available names: Gtk.IconTheme.get_default().list_icons(None)
					theme = Gtk.IconTheme.get_default()
					if theme.has_icon(icon):
						icon_size = self.icon_width or self.icon_height or 32
						widget_icon = theme.lookup_icon(
							icon, icon_size, Gtk.IconLookupFlags.USE_BUILTIN ).load_icon()
					else:
						log.warn(( '"{}" seems to be neither a valid icon file nor'
							' a name in a freedesktop.org-compliant icon theme (or your theme'
							' doesnt have that name). Ignoring.' ).format(icon))
			else:
				# For image-data and icon_data, image should look like this:
				# dbus.Struct(
				#  (dbus.Int32, # width
				#   dbus.Int32, # height
				#   dbus.Int32, # rowstride
				#   dbus.Boolean, # has alpha
				#   dbus.Int32, # bits per sample
				#   dbus.Int32, # channels
				#   dbus.Array([dbus.Byte, ...])) # image data
				# )
				# data, colorspace, has_alpha, bits_per_sample,
				#  width, height, rowstride, destroy_fn, destroy_fn_data
				# XXX: Do I need to free the image via a function callback?
				widget_icon = GdkPixbuf.Pixbuf.new_from_data(
					bytearray(icon[6]), GdkPixbuf.Colorspace.RGB, icon[3], icon[4],
					icon[0], icon[1], icon[2], lambda x, y: None, None )

		if widget_icon:
			if self.icon_width or self.icon_height: # scale icon
				w, h = widget_icon.get_width(), widget_icon.get_height()
				# Use max (among w/h) factor on scale-up and min on scale-down,
				#  so resulting icon will always fit in a specified box,
				#  and will match it by (at least) w or h (ideally - both)
				scale = (self.icon_width and w > self.icon_width)\
					or (self.icon_height and h > self.icon_height) # True if it's a scale-up
				scale = (min if bool(scale) ^ bool(
						self.icon_width and self.icon_height ) else max)\
					(float(self.icon_width or w) / w, float(self.icon_height or h) / h)
				widget_icon = widget_icon.scale_simple(
					w * scale, h * scale, GdkPixbuf.InterpType.BILINEAR )
			widget_icon, pixbuf = Gtk.Image(), widget_icon
			widget_icon.set_from_pixbuf(pixbuf)

		v_box = Gtk.VBox(spacing=self.layout_margin, expand=False)
		if widget_icon is not None:
			h_box = Gtk.HBox(spacing=self.layout_margin * 2)
			frame.add(h_box)
			h_box.pack_start(widget_icon, False, False, 0)
			h_box.pack_start(v_box, True, True, 0)
		else: frame.add(v_box)

		widget_summary = Gtk.Label(name='summary', label=summary)
		widget_summary.set_alignment(0, 0)
		if urgency_label:
			summary_box = Gtk.EventBox(name=urgency_label)
			summary_box.add(widget_summary)
		else: summary_box = widget_summary
		v_box.pack_start(summary_box, False, False, 0)

		v_box.pack_start(Gtk.HSeparator(name='hs'), False, False, 0)

		widget_body = Gtk.TextView( name='body',
			wrap_mode=Gtk.WrapMode.WORD_CHAR )
		widget_body_buffer = widget_body.get_buffer()
		widget_body_buffer.set_text(body)
		v_box.pack_start(widget_body, False, False, 0)
		ev_boxes.append(widget_body)

		# Make sure the window is initially drawn off-screen, because it can't be
		#  placed properly until it's size is known, and it's size is unknown until it's
		#  actually handled by window manager and then drawn by X
		# Proper placement is done on update_layout() call
		win.move(-2000, -2000)

		win.show_all()
		return self.window(win, ev_boxes)


	def display(self, note, cb_dismiss=None, cb_hover=None, cb_leave=None):
		try:
			# Priorities for icon sources:
			#  image{-,_}data: hint. raw image data structure of signature (iiibiiay)
			#  image{-,_}path: hint. either an URI (file://...) or a name in a f.o-compliant icon theme
			#  app_icon: parameter. same as image-path
			#  icon_data: hint. same as image-data
			# image_* is a deprecated hints from 1.1 spec, 1.2 is preferred
			#  (don't seem to be even mentioned in 1.2 spec icon priorities section)
			hints = note.hints.copy()
			k = '__app_icon' # to avoid clobbering anything
			hints[k] = note.icon
			for k in 'image-data', 'image_data',\
					'image-path', 'image_path', k, 'icon_data':
				image = hints.get(k)
				if image:
					log.debug('Got icon image from hint: {}'.format(k))
					break

			urgency = note.hints.get('urgency')
			if urgency is not None: urgency = urgency_levels.by_id(int(urgency))
			win = self._create_win(note.summary, note.body, image, urgency)
			for eb in win.event_boxes:
				eb.add_events(
					Gdk.EventMask.BUTTON_PRESS_MASK
					| Gdk.EventMask.POINTER_MOTION_MASK
					| Gdk.EventMask.LEAVE_NOTIFY_MASK )
				for ev,cb in [
						('button-press-event', cb_dismiss),
						('motion-notify-event', cb_hover),
						('leave-notify-event', cb_leave) ]:
					if cb: eb.connect(ev, lambda w,ev,cb,nid: cb(nid), cb, note.id)
			if cb_dismiss and win.event_boxes:
				# Connect only to window object (or first eventbox in the list)
				win.event_boxes[0].connect( 'destroy',
					lambda w,cb,nid: cb(nid), cb_dismiss, note.id )

			# update_layout() *must* be delayed until window "configure-event", because
			#  actual window size is unknown until it's resized by window manager and drawn by X
			# See the list of caveats here:
			#  http://developer.gnome.org/gtk3/unstable/GtkWindow.html#gtk-window-get-size
			win.gobj.connect('configure-event', lambda w,void: self._update_layout())
			self._windows[note.id] = win

		except: log.exception('Failed to create notification window')


	class NoWindowError(Exception): pass

	def _close(self, nid):
		try: win = self._windows.pop(nid).gobj
		except KeyError: raise self.NoWindowError(nid)
		win.hide(), win.destroy()

	def close(self, nid):
		self._close(nid)
		self._update_layout()
