notification-thing: Gtk3 (PyGI) notification daemon
--------------------

There are quite a few of the [notification spec](http://developer.gnome.org/notification-spec/)
implementations, but this one is designed to be not tied to any DE, unlike
(unfortunately) most of the others were at the time.

Another thing is that simple implementation of the spec doesn't work for me -
I need rate-limiting (but without silent dropping of any messages - it's much
worse!) to maintain sanity while still paying any attention to these popups.

Flexible-enough filtering is another thing.

Auto-disabling and general ability to block them at will (and again, emphasis on
not losing messages, which makes you always doubt and second-guess
notifications, ending up doing the manual info-polling) of these popups during
fullscreen apps (like when watching video or working on some urgent matter) is
yet another...

Any number of notification-thing instances can be linked via zeromq pub-sub
sockets (i.e. each one subscribed to all the others) and display notifications
that arrive via dbus on all other hosts.

Actual notification rendering is inspired (and based in part on)
[notipy](https://github.com/the-isz/notipy) project.

I wrote a few extended notes on the subject over time
([link1](http://blog.fraggod.net/2010/2/libnotify-notification-daemon-shortcomings-and-my-solution),
[link2](http://blog.fraggod.net/2010/12/Further-improvements-on-notification-daemon),
[link3](http://blog.fraggod.net/2011/8/Notification-daemon-in-python)), but it's
mostly summarized above.

How it looks (with built-in css, see below on how to override):
![displayed notifications shot](https://freecode.com/screenshots/99/a6/99a6235e6a09da8de7316684be59bccf_medium.png
"A few notifications with a compositing wm (e17). Headers are colored (by default) by priority.")


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

* [Python 2.7 (not 3.X)](http://python.org/)
* [dbus-python](http://www.freedesktop.org/wiki/Software/DBusBindings#dbus-python)
* [GObject-Introspection](https://live.gnome.org/GObjectIntrospection/)-enabled
  [Gtk+](http://www.gtk.org/) 3.X (including Glib, Pango) and
  [PyGObject](http://live.gnome.org/PyGObject)

* (optional) [PyYAML](http://pyyaml.org/) - to configure daemon via YAML file,
  not CLI (--conf option).
* (optional) [pyzmq](http://zeromq.github.io/pyzmq/) - to broadcast/receive
  notification messages over zeromq pub/sub sockets.

Note that [libnotify](http://developer.gnome.org/libnotify/) is not needed here -
it's usually used to send the messages, not receive and display them.


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

File ~/.notification_filter (configurable via --filter-file option) can be used
to control filtering mechanism at runtime.

It's the simple scheme script, see [scheme
submodule](https://github.com/mk-fg/notification-thing/blob/master/notification_thing/scheme.py)
or [original Peter Norvig's implementation](http://norvig.com/lispy2.html) for
details.

It's evaluation should return the function which will be called for each
notification and should return either #t or #f verdict for whether to display it
or not. Example:

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
	      (~ "^\*\*\* #\S+ (was created on|modes:) " body)))))

~/.notification_filter is reloaded on-the-fly if updated, any errors there will
yield backtraces in notification windows.

"--filter-test" option can be used to test message summary + body (supplied
after option) against filter file - will just print filtering verdict for
supplied data and exit.


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
