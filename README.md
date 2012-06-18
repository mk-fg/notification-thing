notification-thing: pygi (gtk3) notification daemon
--------------------

There are quite a few of the [notification
spec](http://developer.gnome.org/notification-spec/) implementations, but this
one is designed to be not tied to any DE, unlike (unfortunately) most of the
others.

Another thing is that simple implementation of the spec doesn't work for me - I
need rate-limiting (but without silent dropping of any messages - it's much
worse!) to maintain sanity while still paying any attention to these
popups.

Flexible-enough filtering is another thing.

Auto-disabling and general ability to block them at will (and again, emphasis on
not losing messages, which makes you always doubt and second-guess
notifications, ending up doing the manual info-polling) of these popups during
fullscreen apps (like when watching video or working on some urgent matter) is
yet another...

Actual notification rendering is inspired (and based in part on)
[notipy](https://github.com/the-isz/notipy) project.

I wrote a few extended notes on the subject over time
([link1](http://blog.fraggod.net/2010/2/libnotify-notification-daemon-shortcomings-and-my-solution),
[link2](http://blog.fraggod.net/2010/12/Further-improvements-on-notification-daemon),
[link3](http://blog.fraggod.net/2011/8/Notification-daemon-in-python)), but it's
mostly summarized above.


Requirements
--------------------

* [Python 2.7 (not 3.X)](http://python.org/)
* GTK+ 3.X and
  [gobject-introspection](https://live.gnome.org/GObjectIntrospection/)-enabled
  [pygobject](http://live.gnome.org/PyGObject)
* Some [fgc modules](https://github.com/mk-fg/fgc)
  ([fgc.fc](https://github.com/mk-fg/fgc/blob/master/fgc/fc.py) for
  rate-limiting,
  [fgc.scheme](https://github.com/mk-fg/fgc/blob/master/fgc/scheme.py) for
  filtering, etc).

Note that [libnotify](http://developer.gnome.org/libnotify/) is not needed here -
it's usually used to send the messages, not receive and display them.


Installation
--------------------

It's a regular package for Python 2.7 (not 3.X), but not in pypi, so can be
installed from a checkout with something like that:

	% python setup.py install

Note that to install stuff in system-wide PATH and site-packages, elevated
privileges are often required.
Use
[~/.pydistutils.cfg](http://docs.python.org/install/index.html#distutils-configuration-files)
or [virtualenv](http://pypi.python.org/pypi/virtualenv) to do unprivileged
installs into custom paths.

Better way would be to use [pip](http://pip-installer.org/) to install all the
necessary dependencies as well:

	% pip install -e 'git://github.com/mk-fg/notification-thing.git#egg=notification-thing'

Alternatively, `./notification-thing` can be run right from the checkout tree,
without any installation.


Usage
--------------------

Just make sure nothing else is already listening on the same dbus path/interface
and start the daemon by hand.

Alternatively, dbus service file can be installed, so daemon can be started
whenever notifications arrive (and exiting during silence timeouts):

	cp org.freedesktop.Notifications.service /usr/share/dbus-1/services/

File ~/.notification_filter can be used to control filtering mechanism at
runtime.

It's the simple scheme script, see
[fgc.scheme](https://github.com/mk-fg/fgc/blob/master/fgc/scheme.py) or
[original Peter Norvig's implementation](http://norvig.com/lispy2.html) for
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

Lots of tunable options are available, but all-defaults should be the norm
(naturally I use the defaults myself, because I'm the one who sets them;).

	% notification-thing -h
	usage: notification-thing [-h] [-f] [-u] [-c ACTIVITY_TIMEOUT]
	                          [--no-status-notify] [--filter-file PATH]
	                          [--filter-test SUMMARY BODY] [-t POPUP_TIMEOUT]
	                          [-q QUEUE_LEN]
	                          [--layout-anchor {top_right,bottom_left,bottom_right,top_left}]
	                          [--layout-direction {horizontal,vertical}]
	                          [--layout-margin LAYOUT_MARGIN] [--icon-width px]
	                          [--icon-height px] [--tbf-size TBF_SIZE]
	                          [--tbf-tick TBF_TICK]
	                          [--tbf-max-delay TBF_MAX_DELAY] [--tbf-inc TBF_INC]
	                          [--tbf-dec TBF_DEC] [--debug]

	Desktop notification server.

	optional arguments:
	  -h, --help            show this help message and exit
	  -f, --no-fs-check     Dont queue messages if active window is fullscreen
	  -u, --no-urgency-check
	                        Queue messages even if urgency is critical
	  -c ACTIVITY_TIMEOUT, --activity-timeout ACTIVITY_TIMEOUT
	                        No-activity (dbus calls) timeout before closing the
	                        daemon instance (less or equal zero - infinite,
	                        default: 600s)
	  --no-status-notify    Do not send notification on changes in proxy settings.
	  --filter-file PATH    Read simple scheme rules for filtering notifications
	                        from file (default: ~/.notification_filter).
	  --filter-test SUMMARY BODY
	                        Do not start daemon, just test given summary and body
	                        against filter-file and print the result back to
	                        terminal.
	  -t POPUP_TIMEOUT, --popup-timeout POPUP_TIMEOUT
	                        Default timeout for notification popups removal
	                        (default: 5000ms)
	  -q QUEUE_LEN, --queue-len QUEUE_LEN
	                        How many messages should be queued on tbf overflow
	                        (default: 10)
	  --layout-anchor {top_right,bottom_left,bottom_right,top_left}
	                        Screen corner notifications gravitate to (default:
	                        top_left).
	  --layout-direction {horizontal,vertical}
	                        Direction for notification stack growth from --layout-
	                        anchor corner (default: vertical).
	  --layout-margin LAYOUT_MARGIN
	                        Margin between notifications, screen edges, and some
	                        misc stuff (default: 3px).
	  --icon-width px, --img-w px
	                        Scale icon (preserving aspect ratio) to width.
	  --icon-height px, --img-h px
	                        Scale icon (preserving aspect ratio) to height.
	  --tbf-size TBF_SIZE   Token-bucket message-flow filter (tbf) bucket size
	                        (default: 4)
	  --tbf-tick TBF_TICK   tbf update interval (new token), so token_inflow =
	                        token / tbf_tick (default: 15s)
	  --tbf-max-delay TBF_MAX_DELAY
	                        Maxmum amount of seconds, between message queue flush
	                        (default: 60s)
	  --tbf-inc TBF_INC     tbf_tick multiplier on consequent tbf overflow
	                        (default: 2)
	  --tbf-dec TBF_DEC     tbf_tick divider on successful grab from non-empty
	                        bucket, wont lower multiplier below 1 (default: 2)
	  --debug               Enable debug logging to stderr.

Use --debug option to get a verbose log of all that's happening there, which
decisions are made and based on what data.

DBus interface lacks proper introspection at the moment, but is extended with a
few functions, more info on which can be found
[here](http://blog.fraggod.net/2010/12/Further-improvements-on-notification-daemon)
and [here](http://blog.fraggod.net/2011/8/Notification-daemon-in-python). For
example, to temporarily block/unblock all but the urgent notifications:

	dbus-send --type=method_call\
	  --dest=org.freedesktop.Notifications
	  /org/freedesktop/Notifications\
	  org.freedesktop.Notifications.Set\
	  dict:string:boolean:plug_toggle,true

Appearance (and some behavior) of the popup windows is subject to [gtk3
styles](http://developer.gnome.org/gtk3/3.1/GtkCssProvider.html) (simple css
files), with default being the light one (see the actual code for up-to-date
stylesheet though):

	#notification { background-color: white; }
	#notification #hs { background-color: black; }

	#notification #critical { background-color: #ffaeae; }
	#notification #normal { background-color: #f0ffec; }
	#notification #low { background-color: #bee3c6; }

	#notification #summary {
	  font-size: 10;
	  text-shadow: 1 1 0 gray;
	}
	#notification #body { font-size: 8; }
