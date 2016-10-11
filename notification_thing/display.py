# -*- coding: utf-8 -*-
from __future__ import unicode_literals, print_function

import itertools as it, operator as op, functools as ft
from collections import OrderedDict, namedtuple, defaultdict
from xml.sax.saxutils import escape as xml_escape
import sgmllib
import os, urllib, re, types

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

from . import core

import logging
log = logging.getLogger(__name__)


class MarkupToText(sgmllib.SGMLParser):
	# Taken from some eff-bot code on c.l.p.
	sub, entitydefs = '', dict()
	def unknown_starttag(self, tag, attr): self.d.append(self.sub)
	def unknown_endtag(self, tag): self.d.append(self.sub)
	def unknown_entityref(self, ref): self.d.extend(['&', ref, ';'])
	def handle_data(self, data): self.d.append(data)

	def __call__(self, s):
		self.d = list()
		self.feed(s)
		return ''.join(self.d).strip()

strip_markup = MarkupToText()


class NotificationDisplay(object):
	'''Interface to display notification stack.
		Should have "display(note, cb_dismiss=None) -> nid(UInt32, >0)", "close(nid)"
			methods and NoWindowError(nid) exception, raised on erroneous nid's in close().
		Current implementation based on notipy: git://github.com/the-isz/notipy.git'''

	window = namedtuple('Window', 'gobj event_boxes')
	base_css = b'''
		#notification { background: transparent; }
		#notification #frame { background-color: #d4ded8; padding: 3px; }
		#notification #hs { background-color: black; }

		#notification #critical { background-color: #ffaeae; }
		#notification #normal { background-color: #f0ffec; }
		#notification #low { background-color: #bee3c6; }

		#notification #summary {
			padding-left: 5px;
			font-size: 10px;
			text-shadow: 1px 1px 0px gray;
		}
		#notification #body { font-size: 8px; }
		#notification #body * { background-color: #d4ded8; }
	'''
	base_css_min = b'#notification * { font-size: 8; }' # simpliest fallback


	def __init__( self, layout_margin,
			layout_anchor, layout_direction, icon_scale=dict(),
			markup_default=False, markup_warn=False, markup_strip=False ):
		self.margins = dict(it.chain.from_iterable(map(
			lambda ax: ( (2**ax, layout_margin),
				(-2**ax, layout_margin) ), xrange(2) )))
		self.layout_margin = layout_margin
		self.layout_anchor = layout_anchor
		self.layout_direction = layout_direction
		self.icon_scale = icon_scale
		self.markup_default = markup_default
		self.markup_warn, self.markup_strip = markup_warn, markup_strip

		self._windows = OrderedDict()

		self._default_style = self._get_default_css()
		Gtk.StyleContext.add_provider_for_screen(
			Gdk.Screen.get_default(), self._default_style,
			Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION )


	def _pango_markup_parse(self, text, _err_mark='[TN82u8] '):
		success = True
		try: _, attr_list, text, _ = Pango.parse_markup(text, -1, '\0')
		except GLib.GError as err:
			if self.markup_warn:
				msg_start = '{}Pango formatting failed'.format(_err_mark)
				if msg_start not in text: # detect and avoid possible feedback loops
					log.warn('%s (%s) for text, stripping markup: %r', msg_start, err, text)
				else: text = xml_escape(text) # escape message so it'd render bugged part
			if self.markup_strip: text = strip_markup(text)
			try: _, attr_list, text, _ = Pango.parse_markup(text, -1, '\0')
			except GLib.GError: attr_list = None
			success = False
		return success, text, attr_list

	def _pango_markup_to_gtk( self, text, attr_list=None,
			_pango_classes={
				'SIZE': Pango.AttrInt,
				'WEIGHT': Pango.AttrInt,
				'UNDERLINE': Pango.AttrInt,
				'STRETCH': Pango.AttrInt,
				'VARIANT': Pango.AttrInt,
				'STYLE': Pango.AttrInt,
				'SCALE': Pango.AttrFloat,
				'FAMILY': Pango.AttrString,
				'FONT_DESC': Pango.AttrFontDesc,
				'STRIKETHROUGH': Pango.AttrInt,
				'BACKGROUND': Pango.AttrColor,
				'FOREGROUND': Pango.AttrColor,
				'RISE': Pango.AttrInt },
			_pango_to_gtk={'font_desc': 'font'} ):
		# See https://bugzilla.gnome.org/show_bug.cgi?id=59390 for why it is necessary
		# And doesn't work with GI anyway because of
		#  https://bugzilla.gnome.org/show_bug.cgi?id=646788
		#  "Behdad Esfahbod [pango developer]: I personally have no clue how to fix this"
		# Workaround from https://github.com/matasbbb/pitivit/commit/da815339e
		# TODO: fix when AttrList.get_iterator will be accessible via GI or textbuffer gets set_markup()
		if attr_list is None:
			_, text, attr_list = self._pango_markup_parse(text)
			if attr_list is None:
				yield (text, None)
				raise StopIteration

		gtk_tags = defaultdict(dict)
		def parse_attr(attr, _data):
			gtk_attr = attr.klass.type.value_nick
			if gtk_attr in _pango_to_gtk: gtk_attr = _pango_to_gtk[gtk_attr]
			pango_attr, span = attr.klass.type.value_name, (attr.start_index, attr.end_index)
			assert pango_attr.startswith('PANGO_ATTR_'), pango_attr
			attr.__class__ = _pango_classes[pango_attr[11:]] # allows to access attr.value
			for k in 'value', 'ink_rect', 'logical_rect', 'desc', 'color':
				if not hasattr(attr, k): continue
				val = getattr(attr, k)
				if k == 'color':
					val = '#' + ''.join('{:02x}'.format(v/256) for v in [val.red, val.green, val.blue])
				gtk_tags[span][gtk_attr] = val
				break
			else:
				raise KeyError('Failed to extract value for pango attribute: {}'.format(pango_attr))
			return False
		attr_list.filter(parse_attr, None)

		pos = 0
		for (a, b), props in sorted(gtk_tags.viewitems()):
			if a > pos: yield (text[pos:a], None)
			yield (text[a:b], props)
			pos = b
		if text[pos:]: yield (text[pos:], None)


	def _get_default_css(self):
		css, base_css = Gtk.CssProvider(), self.base_css
		for attempt in xrange(6):
			try: css.load_from_data(base_css)
			except GLib.GError as err:
				log.warn('Failed to load default CSS style (try %s): %s', attempt+1, err)
				# print(base_css)
			else: break
			# Try to work around https://bugzilla.gnome.org/show_bug.cgi?id=678876 and similar issues
			if attempt == 0:
				base_css = re.sub(br'\b(background-color:)\s*rgba\([^;]+;', br'\1 white;', base_css)
			elif attempt == 1:
				base_css = re.sub(br'\b(font-size:)\s*(\d+)px\s*;', br'\1 \2;', base_css)
			elif attempt == 2:
				base_css = re.sub(br'\b(text-shadow:)[^;]+;', br'\1 1 1 0 gray;', base_css)
			elif attempt == 3: base_css = re.sub(br'\btext-shadow:[^;]+;', b'', base_css)
			elif attempt == 4: base_css = self.base_css_min # last resort before no-css-at-all
			else: break # don't load any css
		return css


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


	def _get_icon(self, icon, remote=False):
		widget_icon = None

		if icon is not None:
			if isinstance(icon, types.StringTypes):
				icon_path = os.path.expanduser(urllib.url2pathname(icon))
				if icon_path.startswith('file://'): icon_path = icon_path[7:]
				if os.path.isfile(icon_path):
					widget_icon = GdkPixbuf.Pixbuf.new_from_file(icon_path)
				else:
					# Available names: Gtk.IconTheme.get_default().list_icons(None)
					theme = Gtk.IconTheme.get_default()
					icon_size = 32 # default, overidden by any of the "scale" opts
					for k in 'fixed', 'min', 'max':
						for v in self.icon_scale.get(k, list()):
							if not v: continue
							icon_size = v
							break
					widget_icon = theme.lookup_icon(
						icon, icon_size, Gtk.IconLookupFlags.USE_BUILTIN )
					if widget_icon: widget_icon = widget_icon.load_icon()
					else:
						# Msgs from remote hosts natually can have non-local icon paths in them
						(log.warn if not remote else log.debug)(
							'Provided icon info seem to be neither valid icon file nor'
								' a name in a freedesktop.org-compliant icon theme (or current theme'
								' does not have that one), ignoring it: %r', core.format_trunc(icon) )
			else:
				w, h, rowstride, has_alpha, bits_per_sample, channels, data = icon
				data = bytes(bytearray(data))
				widget_icon = GdkPixbuf.Pixbuf.new_from_data(
					data, GdkPixbuf.Colorspace.RGB, bool(has_alpha),
					int(bits_per_sample), int(w), int(h), int(rowstride) )
				widget_icon._data = data # must be preserved from gc

		if widget_icon:
			if any(it.chain.from_iterable(self.icon_scale.values())): # scale icon
				w, h = widget_icon.get_width(), widget_icon.get_height()
				for k in 'fixed', 'min', 'max':
					box_w, box_h = self.icon_scale.get(k, (0, 0))
					if not any([box_w, box_h]): continue
					if k == 'min' and not ((box_w and w < box_w) or (box_h and h < box_h)): continue
					if k == 'max' and not ((box_w and w > box_w) or (box_h and h > box_h)): continue
					# Use max (among w/h) factor on scale-up and min on scale-down,
					#  so resulting icon will always fit in a specified box,
					#  and will match it by (at least) w or h (ideally - both)
					scale = (box_w and w > box_w) or (box_h and h > box_h) # True if it's a scale-up
					scale = (min if bool(scale) ^ bool(box_w and box_h) else max)\
						(float(box_w or w) / w, float(box_h or h) / h)
					box_w, box_h = w * scale, h * scale
					log.debug( 'Scaling image (criteria: %s)'
						' by a factor of %.3f: %dx%d -> %dx%d', k, scale, w, h, box_w, box_h )
					widget_icon = widget_icon.scale_simple(box_w, box_h, GdkPixbuf.InterpType.BILINEAR)
					if k == 'fixed': break # no need to apply min/max after that
			widget_icon, pixbuf = Gtk.Image(), widget_icon
			widget_icon.set_from_pixbuf(pixbuf)

		return widget_icon


	def _set_visual(self, win, ev=None):
		visual = win.get_screen().get_rgba_visual()
		if visual: win.set_visual(visual)

	def _create_win( self, summary, body,
			icon=None, urgency_label=None, markup=False, remote=None ):
		log.debug( 'Creating window with parameters: %s',
			core.repr_trunc_rec(dict( summary=summary, body=body,
				icon=icon, urgency=urgency_label, markup=markup )) )

		win = Gtk.Window(name='notification', type=Gtk.WindowType.POPUP)
		win.set_default_size(400, 20)
		win.connect('screen-changed', self._set_visual)
		self._set_visual(win)
		ev_boxes = [win]

		frame = Gtk.Box(name='frame')
		win.add(frame)

		try: widget_icon = self._get_icon(icon, remote=remote)
		except Exception: # Gdk may raise errors for some images/formats
			log.exception('Failed to set notification icon')
			widget_icon = None

		v_box = Gtk.VBox(spacing=self.layout_margin, expand=False)
		if widget_icon is not None:
			h_box = Gtk.HBox(spacing=self.layout_margin * 2)
			frame.pack_start(h_box, True, True, 0)
			h_box.pack_start(widget_icon, False, False, 0)
			h_box.pack_start(v_box, True, True, 0)
			ev_boxes.append(h_box)
		else: frame.pack_start(v_box, True, True, 0)

		widget_summary = Gtk.Label(name='summary')

		# Sanitize tags through pango first, so set_markup won't produce empty label
		summary_markup, summary_text, summary\
			= self.get_display_summary(summary, markup)
		if summary_markup: widget_summary.set_markup(summary)
		else: widget_summary.set_text(summary)

		widget_summary.set_alignment(0, 0)
		if urgency_label:
			summary_box = Gtk.EventBox(name=urgency_label)
			summary_box.add(widget_summary)
		else: summary_box = widget_summary
		v_box.pack_start(summary_box, False, False, 0)
		ev_boxes.append(summary_box)

		v_box.pack_start(Gtk.HSeparator(name='hs'), False, False, 0)

		widget_body = Gtk.TextView( name='body',
			wrap_mode=Gtk.WrapMode.WORD_CHAR,
			cursor_visible=False, editable=False )
		widget_body_buffer = widget_body.get_buffer()

		body_markup, body_text, body_attrs = self.get_display_body(body, markup)
		if not body_markup: widget_body_buffer.set_text(body_text)
		else:
			# This buffer uses pango markup, even though GtkTextView does not support it
			# Most magic is in pango_markup_to_gtk(), there doesn't seem to be any cleaner way
			def get_tag(props, _tag_id=iter(xrange(2**31-1)), _tag_table=dict()):
				k = tuple(sorted(props.viewitems()))
				if k not in _tag_table:
					_tag_table[k] = widget_body_buffer\
						.create_tag('x{}'.format(next(_tag_id)), **props)
				return _tag_table[k]
			pos = widget_body_buffer.get_end_iter()
			for text, props in body_attrs:
				if props: widget_body_buffer.insert_with_tags(pos, text, get_tag(props))
				else: widget_body_buffer.insert(pos, text)

		v_box.pack_start(widget_body, False, False, 0)
		ev_boxes.append(widget_body)

		# Make sure the window is initially drawn off-screen, because it can't be
		#  placed properly until it's size is known, and it's size is unknown until it's
		#  actually handled by window manager and then drawn by X
		# Proper placement is done on update_layout() call
		win.move(-2000, -2000)

		win.show_all()
		return self.window(win, ev_boxes)


	def get_display_summary(self, summary, markup):
		if markup:
			success, text, _ = self._pango_markup_parse(summary)
			if not success: markup, summary = False, text
		else: text = summary
		return markup, text, summary

	def get_display_body(self, body, markup):
		if markup:
			_, text, attr_list = self._pango_markup_parse(body)
			if attr_list is None: markup, body_attrs = False, [(text, None)]
			else: body_attrs = self._pango_markup_to_gtk(text, attr_list)
		else: text = body
		return markup, text, body_attrs

	def get_note_markup(self, note):
		return note.hints.get('x-nt-markup', self.markup_default)

	def get_note_text(self, note):
		'Returns note text, stripped of all markup, if any (and if enabled).'
		markup = self.get_note_markup(note)
		_, summary_text, _ = self.get_display_summary(note.summary, markup)
		_, body_text, _ = self.get_display_body(note.body, markup)
		return summary_text, body_text


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
					log.debug('Got icon image from hint: %s', k)
					break

			urgency = note.hints.get('urgency')
			if urgency is not None: urgency = core.urgency_levels.by_id(int(urgency))
			markup = self.get_note_markup(note)

			win = self._create_win( note.summary, note.body,
				image, urgency, markup=markup, remote=note.hints.get('x-notification-thing-from-remote') )

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
