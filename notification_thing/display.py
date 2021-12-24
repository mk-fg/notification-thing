import itertools as it, operator as op, functools as ft
from xml.sax.saxutils import escape as xml_escape
import html.parser, html.entities
import os, re, collections as cs, urllib.request as ulr

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Pango

from . import core

import logging
log = logging.getLogger(__name__)


class MarkupToText(html.parser.HTMLParser):
	def handle_starttag(self, tag, attrs): pass
	def handle_endtag(self, tag): pass
	def handle_entityref(self, ref): self.d.append(f'&{ref};')
	def handle_charref(self, ref): self.d.append(f'&#{ref};')
	def handle_data(self, data): self.d.append(data)

	def __call__(self, s):
		self.d = list()
		self.feed(s)
		return ''.join(self.d).strip()

strip_markup = MarkupToText()


class NotificationDisplay:
	'''Interface to display notification stack.
		Should have "display(note, cb_dismiss=None) -> nid(UInt32, >0)", "close(nid)"
			methods and NoWindowError(nid) exception, raised on erroneous nid's in close().
		Current implementation based on notipy: git://github.com/the-isz/notipy.git'''

	window = cs.namedtuple('Window', 'gobj event_boxes')
	base_css = b'''
		#notification { background: transparent; }
		#notification #frame { background-color: #d4ded8; padding: 3px; }
		#notification #hs { background-color: black; }

		#notification #critical { background-color: #ffaeae; }
		#notification #normal { background-color: #f0ffec; }
		#notification #low { background-color: #bee3c6; }

		#notification #summary {
			color: black;
			padding-left: 5px;
			font-size: 1.2em;
			text-shadow: 1px 1px 0px gray;
		}
		#notification #body { color: black; font-size: 1em; }
		#notification #body * { color: black; background-color: #d4ded8; }
	'''
	base_css_min = b'#notification * { font-size: 8; }' # simpliest fallback


	def __init__( self, layout_margin,
			layout_anchor, layout_direction, icon_scale=dict(),
			markup_default=False, markup_warn=False, markup_strip=False ):
		self.margins = dict(it.chain.from_iterable(map(
			lambda ax: ( (2**ax, layout_margin),
				(-2**ax, layout_margin) ), range(2) )))
		self.layout_anchor = layout_anchor
		self.layout_direction = layout_direction
		self.icon_scale = icon_scale
		self.markup_default = markup_default
		self.markup_warn, self.markup_strip = markup_warn, markup_strip

		self._windows = dict()

		self._default_style = self._get_default_css()
		screen = Gdk.Screen.get_default()
		if not screen: raise core.StartupFailure('No X screen detected')
		Gtk.StyleContext.add_provider_for_screen(
			screen, self._default_style,
			Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION )


	def _pango_markup_parse(self, text, _err_mark='[TN82u8] '):
		try:
			success, _, text, _ = Pango.parse_markup(text, -1, '\0')
			if not success: raise GLib.GError('pango_parse_markup failure')
		except GLib.GError as err:
			success = False # should be rendered as text
			if self.markup_warn:
				msg_start = f'{_err_mark}Pango formatting failed'
				if msg_start not in text: # detect and avoid possible feedback loops
					log.warn('%s (%s) for text, stripping markup: %r', msg_start, err, text)
			if self.markup_strip: # strip + re-parse to convert xml entities and such
				text = strip_markup(text)
				try: _, _, text, _ = Pango.parse_markup(text, -1, '\0')
				except GLib.GError: pass
		return success, text


	def _get_default_css(self):
		css, base_css = Gtk.CssProvider(), self.base_css
		for attempt in range(6):
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
					if 2**ax & self.layout_anchor else self.margins[-2**ax], range(2) ))
		# Iterate over windows in order, placing each one starting from a "base" corner
		for win in map(op.attrgetter('gobj'), self._windows.values()):
			win.move(*map(lambda ax: base[ax] - ( win.get_size()[ax]
				if 2**ax & self.layout_anchor else 0 ), range(2)))
			margin = self.margins[(2 * ( (2**self.layout_direction)
				& self.layout_anchor ) / 2**self.layout_direction - 1) * 2**self.layout_direction]
			base = tuple(map(
				lambda ax: base[ax] if self.layout_direction != ax else\
					base[ax] + (margin + win.get_size()[ax])\
						* (2 * (2**ax ^ (2**ax & self.layout_anchor)) / 2**ax - 1), range(2) ))


	def _get_icon(self, icon, remote=False):
		widget_icon = None

		if icon is not None:
			if isinstance(icon, str):
				icon_path = os.path.expanduser(ulr.url2pathname(icon))
				if icon_path.startswith('file://'): icon_path = icon_path[7:]
				if os.path.isfile(icon_path):
					widget_icon = GdkPixbuf.Pixbuf.new_from_file(icon_path)
				else:
					# Available names: Gtk.IconTheme.get_default().list_icons(None)
					theme = Gtk.IconTheme.get_default()
					icon_size = any(self.icon_scale.get('fixed', list())) or 32
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
					scale_down = (box_w and w > box_w) or (box_h and h > box_h)
					if scale_down: scale = min # factor<1, unspec=1, must fit on both dimensions
					elif box_w and box_h: scale = min # factor>1, but still pick min to fit on both
					else: scale = max # ignore unspec=1 and scale to max possible factor
					scale = scale(float(box_w or w) / w, float(box_h or h) / h)
					box_w, box_h = w * scale, h * scale
					log.debug( 'Scaling image (%s, criteria: %s) by a factor of'
						' %.3f: %dx%d -> %dx%d', ['up', 'down'][scale_down], k, scale, w, h, box_w, box_h )
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

		box_margin = 3
		v_box = Gtk.VBox(spacing=box_margin, expand=False)
		if widget_icon is not None:
			h_box = Gtk.HBox(spacing=box_margin * 2)
			frame.pack_start(h_box, True, True, 0)
			h_box.pack_start(widget_icon, False, False, 0)
			h_box.pack_start(v_box, True, True, 0)
			ev_boxes.append(h_box)
		else: frame.pack_start(v_box, True, True, 0)

		widget_summary = Gtk.Label(name='summary')

		# Sanitize tags through pango first, so set_markup won't produce empty label
		markup_summary = markup
		if markup_summary:
			markup_summary, text = self._pango_markup_parse(summary)
			if markup_summary: widget_summary.set_markup(summary)
			else: summary = text
		if not markup_summary: widget_summary.set_text(summary)

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

		# Same as with summary - sanitize tags through pango first
		markup_body = markup
		if markup_body:
			markup_body, text = self._pango_markup_parse(body)
			if markup_body:
				cursor = widget_body_buffer.get_end_iter()
				widget_body_buffer.insert_markup(cursor, body, -1)
			else: body = text
		if not markup_body: widget_body_buffer.set_text(body)

		v_box.pack_start(widget_body, True, True, 0)
		ev_boxes.append(widget_body)

		# Make sure the window is initially drawn off-screen, because it can't be
		#  placed properly until it's size is known, and it's size is unknown until it's
		#  actually handled by window manager and then drawn by X
		# Proper placement is done on update_layout() call
		win.move(-2000, -2000)

		win.show_all()
		return self.window(win, ev_boxes)


	def get_note_markup(self, note):
		return note.hints.get('x-nt-markup', self.markup_default)

	def get_note_text(self, note):
		'Returns note text, stripped of all markup, if any (and if enabled).'
		markup, summary, body = self.get_note_markup(note), note.summary, note.body
		if markup:
			_, summary = self._pango_markup_parse(summary)
			_, body = self._pango_markup_parse(body)
		return summary, body


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
				image, urgency, markup=markup, remote=note.hints.get('x-nt-from-remote') )

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
