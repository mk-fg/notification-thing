#!/usr/bin/env python

from setuptools import setup, find_packages
import os

pkg_root = os.path.dirname(__file__)

setup(

	name = 'notification-thing',
	version = '12.05.1',
	author = 'Mike Kazantsev',
	author_email = 'mk.fraggod@gmail.com',
	license = 'WTFPL',
	keywords = 'desktop notification popups libnotify dbus'
		' gtk+ gtk3 gobject-introspection rate-limiting distraction',
	url = 'http://github.com/mk-fg/notification-thing',

	description = 'Python-based implementation of'
		' Desktop Notifications Specification (notification-daemon)',
	long_description = open(os.path.join(pkg_root, 'README.md')).read(),

	classifiers = [
		'Development Status :: 4 - Beta',
		'Environment :: X11 Applications :: GTK',
		'Intended Audience :: End Users/Desktop',
		'License :: OSI Approved',
		'Operating System :: POSIX',
		'Programming Language :: Python',
		'Programming Language :: Python :: 2.7',
		'Programming Language :: Python :: 2 :: Only',
		'Topic :: Desktop Environment' ],

	dependency_links = [
		'git://github.com/mk-fg/fgc.git#egg=fgc',
		'https://live.gnome.org/PyGObject#Source' ],

	packages = find_packages(),

	entry_points = dict(console_scripts=[
		'notification-thing = notification_thing.daemon:main' ]) )
