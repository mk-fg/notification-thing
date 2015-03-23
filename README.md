notification-thing
--------------------

Gtk3/Python (PyGI) notification daemon with flexible (scriptable) filtering,
rate-limiting and misc other cool features, not tied to any particular DE.

Features:

* Implements [Desktop Notification spec](http://developer.gnome.org/notification-spec/).

  Should be compatible with any dbus clients (e.g. libnotify, Gtk3, etc).
  Supports icons and resizing of these.

* Tries hard to never drop any notifications silently by itself, even in case of
  any unexpected errors.

* [Pango markup](https://developer.gnome.org/pango/stable/PangoMarkupFormat.html)
  support in notification summary and body, option to enable/disable that for
  individual messages via notification parameters, broken-markup-safe.

* Configurable appearance via
  [Gtk3 styles](http://developer.gnome.org/gtk3/unstable/GtkCssProvider.html)
  (simple css files) and themes.

* Rate-limiting using "leaky" token-bucket algorithm, with all the knobs
  configurable.

  When/if several messages get delayed, they will be displayed batched into one
  "digest" message, up to a limit (number of last ones), and dropped with a
  warning line (and a count of these) beyond that.

* Dbus interface allows calls to pause passing notifications, but still
  buffering these to "digest", force-flushing such buffer, displaying previous
  (cleaned-up) notifications, changing/pausing default cleanup timeout, etc.

* Can send/receive json-serialized notifications via
  [ZeroMQ](http://zeromq.org/) pub-sub queues.

  This allows to e.g. tie up several machines to see all notifications which are
  from any of them or send desktop notifications from a remote machine
  (doesn't have to be a desktop one either, see included "notify-net" script).

* Filtering using simple but very powerful scheme scripting
  (based on [Peter Norvig's lispy2](http://norvig.com/lispy2.html)).

* Can play any sounds from anywhere in the filtering scripts
  (via [libcanberra](http://0pointer.de/lennart/projects/libcanberra/)).

  I.e. on specific occasions, like some regexp-match, not for every message
  (though that is certainly possible as well), or even multiple samples for one
  message.

* All options/features are configurable and can be disabled entirely, either
  from command-line or a YAML configuration file.

* Easy to change and debug - it's just a Python after all.

See below for a detailed description of each particular feature.

Actual notification rendering is inspired (and based in part on)
[notipy](https://github.com/the-isz/notipy) project.

I wrote a few extended notes on the project over time
([link1](http://blog.fraggod.net/2010/2/libnotify-notification-daemon-shortcomings-and-my-solution),
[link2](http://blog.fraggod.net/2010/12/Further-improvements-on-notification-daemon),
[link3](http://blog.fraggod.net/2011/8/Notification-daemon-in-python)), but
these should be mostly mostly summarized in this README.


Installation
--------------------

It's a regular package for Python 2.7 (not 3.X), but not in pypi, so can be
installed from a checkout with something like that:

	% python setup.py install

Better way would be to use [pip](http://pip-installer.org/) to install all the
necessary dependencies as well:

	% pip install 'git+https://github.com/mk-fg/notification-thing.git#egg=notification-thing'

Note that to install stuff in system-wide PATH and site-packages, elevated
privileges are often required.
Use "install --user",
[~/.pydistutils.cfg](http://docs.python.org/install/index.html#distutils-configuration-files)
or [virtualenv](http://pypi.python.org/pypi/virtualenv) to do unprivileged
installs into custom paths.

Alternatively, `./notification-thing` can be run right from the checkout tree,
without any installation.

### Requirements

* [Python 2.7 (not 3.X)](http://python.org/).

* [dbus-python](http://www.freedesktop.org/wiki/Software/DBusBindings#dbus-python).

* [GObject-Introspection](https://live.gnome.org/GObjectIntrospection/)-enabled
  [Gtk+](http://www.gtk.org/) 3.X (including Glib, Pango) and
  [PyGObject](http://live.gnome.org/PyGObject).

* (optional) [PyYAML](http://pyyaml.org/) - to configure daemon via YAML file,
  not CLI (--conf option).

* (optional) [pyzmq](http://zeromq.github.io/pyzmq/) - to broadcast/receive
  notification messages over zeromq pub/sub sockets.

* (optional) [libcanberra](http://0pointer.de/lennart/projects/libcanberra/) -
  to play sounds (from XDG themes or files).

Note that [libnotify](http://developer.gnome.org/libnotify/) is not needed here -
it is usually used to send the messages, not receive and display these (that's
the job of notification-daemon, which generally come with DEs).


Usage
--------------------

Just make sure nothing else is already listening on the same dbus path/interface
and start the daemon by hand.

Alternatively, dbus service file can be installed, so daemon can be started
whenever notifications arrive (and exiting during silence timeouts):

	cp org.freedesktop.Notifications.service /usr/share/dbus-1/services/


##### Configuration

Lots of tunable options are available (run the thing with "--help" option to see
the full list), but all-defaults should be the norm (naturally use defaults myself).

Use --debug option to get a verbose log of all that's happening there, which
decisions are made and based on what data.

[YAML](https://en.wikipedia.org/wiki/YAML) configuration file can be used to
specify a lot of options in a more convenient and brief format, see --conf
option and "notification_thing.example.yaml" config in the repo.


##### Filtering

File ~/.notification_filter (configurable via "--filter-file" option) can be
used to control filtering mechanism at runtime and play sounds where necessary
(see below).

It's the simple scheme script, see [scheme
submodule](https://github.com/mk-fg/notification-thing/blob/master/notification_thing/scheme.py)
or [original Peter Norvig's implementation](http://norvig.com/lispy2.html) for
details.

It's evaluation should return the function which will be called for each
notification and should return either #t or #f verdict for whether to display it
or not.

Example:

	(define-macro define-matcher (lambda
	  (name op comp last rev-args)
	  `(define ,name (lambda args
	    (if (= (length args) 1) ,last
	      (let ((atom (car args)) (args (cdr args)))
	        (,comp
	          (,op ,@(if rev-args '((car args) atom) '(atom (car args))))
	          (apply ,name (cons atom (cdr args))))))))))

	(define-matcher ~all ~ and #t #f)
	(define-matcher all~ ~ and #t #t)
	(define-matcher ~any ~ or #f #f)
	(define-matcher any~ ~ or #f #t)

	(lambda (summary body)
	  (not (or

	    ;; hl-only high-traffic channels
	    (and
	      (any~ summary
	        "^erc: #(gunicorn|zeromq|bookz)$"
	        "^erc: #anon")
	      (not (~ "MK_FG" body)))

	    ;; irrelevant service messages
	    (~ "Undefined CTCP query received. Silently ignored" body)
	    (and
	      (~ "^erc: #\S+" summary)
	      (~ "^\*\*\* #\S+ (was created on|modes:) " body))

	    ;; play sound on irc nick highlights
	    (and (~ "^erc:" summary) (~ "MK_FG" body) (sound-play "bell")))))

~/.notification_filter is reloaded on-the-fly if updated, any errors there will
yield backtraces in notification windows.

"--filter-test" option can be used to test message summary + body
(supplied after option) against filter file - will just print filtering verdict for
supplied summary/body and exit.


##### Sounds

Special "sound-play" function (see filtering example above) can plays specified
sound sample (and always returns #f) via libcanberra from the filtering scripts.
If libcanberra is not available or failed to init, message will be logged to
stderr on daemon start and sound-related stuff will simply be ignored.

"sound-play-sync" function works same as "sound-play" but delays filtering until
sound ends, and is intended for rare cases when e.g. one might want to play
several different samples in sequence.

There's also "sound-cache" function to use libcanberra's "cache" function
(to reuse sample on the audio daemon).

Sounds are played only when and where these functions gets invoked from the
filtering scripts, i.e. not played anywhere at all by default.

"--no-filter-sound" cli/config option can be used to force-disable these,
don't init/touch libcanberra at all and make sound-* into a no-op functions.


##### Extra dbus commands

DBus interface can be inspected via usual introspection methods (add "--xml" to
get more canonical form):

	gdbus introspect --session \
	  --dest org.freedesktop.Notifications \
	  --object-path /org/freedesktop/Notifications

Extra non-spec methods:

 - "Flush" - no args, no returns - display all queued (due to rate-limiting)
   notifications.

 - "Cleanup" - args: timeout (double), max_count (uint32), no returns - close
   currently-displayed notifications older than passed timeout (seconds).
   Notification bubbles are closed in oldest-first order up to "max_count" value
   (0 - all), so to close one oldest note, one might pass timeout=0,
   max_count=1.

 - "List" - no args, returns array of int32 - return list of currently-displayed
   notification ids.

 - "Redisplay" - no args, returns uint32 id of notification - re-displays last
   message again, then the one before it and so on (see --history-len option).

Daemon also implements "org.freedesktop.DBus.Properties" interface.
Supported properties (full list can be acquired via usual "GetAll" method) are:

 - "plug" (bool or "toggle") - block notification bubbles from displaying,
   queueing them up to display when it will be disabled.

 - "urgent" (bool or "toggle") - display notifications with urgency=critical
   immediately, regardless of rate-limiting or fullscreen-app check.

 - "cleanup" (bool or "toggle") - enable/disable cleanup timeout for
   notification bubbles. Disabled timeout will mean that they will hang around
   forever, until manually dismissed (either by clicking or via "Flush" method).

For example, to temporarily block/unblock all but the urgent notifications:

	dbus-send --type=method_call \
	  --dest=org.freedesktop.Notifications \
	  /org/freedesktop/Notifications \
	  org.freedesktop.DBus.Properties.Set \
	  org.freedesktop.Notifications \
	  string:plug variant:string:toggle


##### Appearance / styles

Appearance (and some behavior) of the popup windows is subject to
[gtk3 styles](http://developer.gnome.org/gtk3/unstable/GtkCssProvider.html)
(simple css files), with default being the light one (see the actual code for
up-to-date stylesheet though):

	#notification * { background-color: white; }
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


##### Markup

[Pango markup](https://developer.gnome.org/pango/stable/PangoMarkupFormat.html)
both in message body and summary is supported and processing of it is enabled by
default (can be disabled with --markup-disable).

Whether it should be parsed can also be controlled on per-message basis by
passing boolean "x-nt-markup" hint (true - enabled, false - disabled).

Other --markup-* options are available to control what happens when pango fails
to parse the tags in text - whether it should be stripped (--markup-strip-on-err
option) and/or warning should be issued.

Technically GtkTextView widget used for message body doesn't allow pango markup
(though GtkLabel holding summary does, see also
[gnome bug 59390](https://bugzilla.gnome.org/show_bug.cgi?id=59390)) and uses it's own
[GtkTextTag](https://developer.gnome.org/gtk3/unstable/GtkTextTag.html)-based markup,
which is very similar to pango, so pango stuff gets ad-hoc converted to
GtkTextTags (see `display.pango_markup_to_gtk`), and potentially (but unlikely)
can get something wrong.


##### Network broadcasting

Needs pyzmq module, if used.

Allows to serialize notifications received from dbus interface and publish them
on zmq_pub socket.

Any instances connected to that will receive notification, and any transient
network issues should be handled by zeromq - pub socket should keep messages
queued for subscribers it has seen (connected) at least once.

Furthermore, it's not required that subscribers should connect to publishers or
vice versa - any zeromq sockets can initiate connection to any other ones, so
that e.g. "notify-net" tool (included) can create "pub" sucket and connect to a
running daemon's "sub" socket on another machine - or any number of machines,
just specify -d option many times - then publish messages there.

See --net-* options for all that.

Only limitation here is sanity - not much point linking e.g. subscriber sockets
to each other.
