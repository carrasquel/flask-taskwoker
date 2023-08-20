# -*- coding: utf-8 -*-
"""
    flaskext.taskworker
    ~~~~~~~~~~~~~

    Flask extension for creating and running tasks.

    :copyright: (c) 2023 by Nelson Carrasquel.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import with_statement

__version__ = '0.0.1'


from .core import TaskWorker, define_task, append_task  # noqa: F401
