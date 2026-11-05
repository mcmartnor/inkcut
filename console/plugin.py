# -*- coding: utf-8 -*-
"""
Copyright (c) 2017, Jairus Martin.

Distributed under the terms of the GPL v3 License.

The full license is in the file LICENSE, distributed with this software.

Created on Dec 10, 2015

@author: jrm
"""
import os
import sys
import logging
from inkcut.core.api import Plugin, log


class ConsolePlugin(Plugin):
    def is_supported(self):
        #: On macOS 27 the in-process IPython kernel can leave the whole app
        #: deaf to mouse/keyboard events (beach ball) when the user moves the
        #: mouse during startup. Off by default there; INKCUT_CONSOLE=1 opts in.
        if sys.platform == 'darwin' and os.environ.get('INKCUT_CONSOLE') != '1':
            log.info("IPython console disabled on macOS (set INKCUT_CONSOLE=1)")
            return False
        try:
            from enaml.qt import qt_ipython_console
            return True
        except ImportError as e:
            log.warning("IPython console plugin is missing dependencies")
            log.exception(e)
            return False

    def start(self):
        """ Set the log level for IPython stuff to warn """
        for name in ['ipykernel.inprocess.ipkernel', 'traitlets']:
            log = logging.getLogger(name)
            log.setLevel(logging.WARNING)
