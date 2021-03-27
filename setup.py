from setuptools import setup, find_packages
import os, runpy

pkg_root = os.path.dirname(__file__)
__version__ = runpy.run_path(
	os.path.join(pkg_root, 'notification_thing', '__init__.py') )['__version__']


# Error-handling here is to allow package to be built w/o README included
try: readme = open(os.path.join(pkg_root, 'README.md')).read()
except IOError: readme = ''

setup(

	name = 'notification-thing',
	version = __version__,
	author = 'Mike Kazantsev',
	author_email = 'mk.fraggod@gmail.com',
	license = 'WTFPL',
	keywords = 'desktop notification popups libnotify dbus'
		' gtk+ gtk3 gobject-introspection rate-limiting distraction zeromq',
	url = 'http://github.com/mk-fg/notification-thing',

	description = 'Python-based implementation of'
		' Desktop Notifications Specification (notification-daemon)',
	long_description = readme,

	classifiers = [
		'Development Status :: 4 - Beta',
		'Environment :: X11 Applications :: GTK',
		'Intended Audience :: End Users/Desktop',
		'License :: OSI Approved',
		'Operating System :: POSIX',
		'Programming Language :: Python',
		'Programming Language :: Python :: 3 :: Only',
		'Topic :: Desktop Environment' ],

	dependency_links = [
		'https://live.gnome.org/PyGObject#Source',
		'http://dbus.freedesktop.org/releases/dbus-python/' ],

	packages = find_packages(),

	entry_points = dict(console_scripts=[
		'notification-thing = notification_thing.daemon:main',
		'notify-net = notification_thing.net_client:main',
		'notify-net-dump = notification_thing.dumper_client:main' ]) )
