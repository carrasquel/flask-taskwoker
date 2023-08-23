# encoding: utf-8
# app/extensions/scheduler/worker.py

import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from peewee import SqliteDatabase, PostgresqlDatabase, MySQLDatabase
from playhouse.db_url import connect

TASKER_ENGINE = "TASKER_ENGINE"
TASKER_DRIVER = "TASKER_DRIVER"


class _TaskManager:
    def __init__(self):
        self.tasks = {}

    def append(self, f, name):
        self.tasks[name] = f

    def run(self, name, payload):
        f = self.tasks[name]
        result = f(**payload)

        return result


class BaseTask:
    def __init__(self, name, scheduler):

        self._name = name
        self._scheduler = scheduler

    def apply(self, payload):
        """Function to schedule a deferred function execution in the tasks scheduler.

        :param payload: a dictionary holding the param names as key and param values as value.
        """

        self._scheduler._append_task(self._name, payload)


class BaseTaskWorker():
    def __init__(self):
        self._manager = _TaskManager()
        self._handler = None
        self._app = None
        self._db = None
        self.config = {
            TASKER_ENGINE: "",
            TASKER_DRIVER: ""
        }

    def init_app(self, app):
        self._app = app
        self.initialize_db()
        self.create_db()

    def run_job(self, job, payload):
        result = self._manager.run(job, payload)

        return result
    
    def set_engine(self, engine):
        self.config[TASKER_ENGINE] = engine

    def set_driver(self, driver):
        self.config[TASKER_DRIVER] = driver

    def _append_task(self, task, payload):
        self._db.append_task(task, payload)

    def _define_task(self, name):
        def outter(f):
            def inner():
                self._manager.append(f, name)
                return f

            return inner()

        return outter

    def define_task(self, f):
        """Decorator function to define tasks within the context of Flask.

        :param f: a function to be decorated, this function will be used
        for tasks execution.
        """

        def inner():
            name = "{module}.{name}".format(module=f.__module__, name=f.__name__)
            task = BaseTask(name, self)
            self._manager.append(f, name)
            return task

        return inner()

    def create_db(self):
        engine = self.config[TASKER_ENGINE]
        driver = self.config[TASKER_DRIVER]
        database_uri = ""

        if not engine == "SQLALCHEMY":
            return
        else:
            database_uri = self._app.config["SQLALCHEMY_DATABASE_URI"]
        
        if driver == "postgres":
            from .sql import postgres as database
            db = connect(database_uri)

        elif driver == "mysql":
            from .sql import mysql as database
            database_uri = database_uri.replace("mysql+pymysql", "mysql")
            db = connect(database_uri)

        elif driver == "sqlite":
            from .sql import sqlite as database
            database_uri = database_uri.replace("\\", "/")
            database_uri = database_uri.replace("sqlite:///", "")
            db = SqliteDatabase(
                database_uri,
                pragmas={
                    "journal_mode": "wal",
                    "journal_size_limit": 1024,
                    "cache_size": -1024 * 64,  # 64MB
                    "foreign_keys": 1,
                    "ignore_check_constraints": 0,
                    "synchronous": 0,
                }
            )

        self._db = database
        self._db.proxy.initialize(db)
        self._database = db

    def create_tables(self):
            
        self._database.create_tables([self._db.Schedule])

    def event_handler(self):

        with self._app.app_context():
            now = datetime.datetime.utcnow()
            schedule = self._db.pop_task()

            if not schedule:
                return

            if now < schedule.scheduled_date:
                return
            try:
                automation = schedule.automation
                payload = schedule.payload

                result = self.run_job(automation, payload)
                self._db.complete_task(schedule, result)
            except Exception as e:
                self._db.pushback_task(schedule, str(e))

    def initialize_db(self, ):
        if TASKER_ENGINE in self._app.config:
            self.set_engine(self._app.config[TASKER_ENGINE])
        
        if TASKER_DRIVER in self._app.config:
            self.set_driver(self._app.config[TASKER_DRIVER])

    def start(self):
        
        self.create_tables()
        self.add_job(self.event_handler, "interval", seconds=5)


class BackgroundTaskWorker(BaseTaskWorker, BackgroundScheduler):
    """Manages scheduled background tasks

    :param app: Flask instance
    """


    def __init__(self, app=None):

        BackgroundScheduler.__init__(self)
        BaseTaskWorker.__init__(self)

        if app:
            BaseTaskWorker.init_app(self, app)

    def init_app(self, app):
        """Initializes your tasks settings from the application settings.

        You can use this if you want to set up your BackgroundTaskWorker instance
        at configuration time.

        :param app: Flask application instance
        """

        BaseTaskWorker.init_app(self, app)

    def start(self):
        BaseTaskWorker.start(self)
        BackgroundScheduler.start(self)


class BlockingTaskWorker(BaseTaskWorker, BlockingScheduler):
    """Manages scheduled tasks

    :param app: Flask instance
    """


    def __init__(self, app=None):

        BlockingScheduler.__init__(self)
        BaseTaskWorker.__init__(self)

        if app:
            BaseTaskWorker.init_app(self, app)

    def init_app(self, app):
        """Initializes your tasks settings from the application settings.

        You can use this if you want to set up your BlockingTaskWorker instance
        at configuration time.

        :param app: Flask application instance
        """

        BaseTaskWorker.init_app(self, app)

    def start(self):
        BaseTaskWorker.start(self)
        BlockingScheduler.start(self)