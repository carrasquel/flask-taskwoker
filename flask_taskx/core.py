# encoding: utf-8
# app/extensions/scheduler/worker.py

import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from peewee import MySQLDatabase, PostgresqlDatabase, SqliteDatabase
from playhouse.db_url import connect

TASKER_DATABASE_URI = "TASKER_DATABASE_URI"
TASKER_DRIVER = "TASKER_DRIVER"
TASKER_INTERVAL_TIME = "TASKER_INTERVAL_TIME"


class NoneDatabaseURIException(Exception):
    "Raised when there is not available database uri defined"
    
    def __init__(self, message="Database uri not defined in Flask Config"):
        self.message = message
        super().__init__(self.message)


class _TaskManager:
    def __init__(self):
        self.tasks = {}
        self.crons = []
        self.dates = []

    def append(self, f, name):
        self.tasks[name] = f

    def run(self, name, payload):
        f = self.tasks[name]
        result = f(**payload)

        return result

    def add_cron(self, f, *args, **kwargs):
        self.crons.append(
            (
                f,
                (
                    args,
                    kwargs,
                ),
            )
        )

    def add_date(self, f, *args, **kwargs):
        self.dates.append(
            (
                f,
                (
                    args,
                    kwargs,
                ),
            )
        )


class BaseTask:
    def __init__(self, name, scheduler):
        self._name = name
        self._scheduler = scheduler

    def apply(self, payload):
        """Function to schedule a deferred function execution in the tasks scheduler.

        :param payload: a dictionary holding the param names as key and param values as value.
        """

        self._scheduler._append_task(self._name, payload)


class BaseTaskWorker:
    def __init__(self):
        self._manager = _TaskManager()
        self._handler = None
        self._app = None
        self._db = None
        self.config = {
            TASKER_DATABASE_URI: "",
            TASKER_DRIVER: "",
            TASKER_INTERVAL_TIME: 5,
        }

    def init_app(self, app):
        self._app = app
        self.initialize_db()
        self.create_db()

        if TASKER_INTERVAL_TIME in self._app.config:
            interval_time = int(self._app.config[TASKER_INTERVAL_TIME])
            self.set_interval_time(interval_time)

    def run_job(self, job, payload):
        result = self._manager.run(job, payload)

        return result

    def set_interval_time(self, time):
        self.config[TASKER_INTERVAL_TIME] = time

    def set_database_uri(self, database_uri):
        self.config[TASKER_DATABASE_URI] = database_uri

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
        It returns an instance of a BaseTask class than can be appliable for 
        later executions.

        :param f: a function to be decorated, this function will be used
        for tasks execution.

        :return: [BaseTask]
        """

        def inner():
            name = "{module}.{name}".format(module=f.__module__, name=f.__name__)
            task = BaseTask(name, self)
            self._manager.append(f, name)
            return task

        return inner()

    def define_cron_task(self, *args, **kwargs):
        """
        Decorator function to define cron tasks within the context of Flask
        it triggers tasks when current time matches all specified time constraints,
        similarly to how the UNIX cron scheduler works.

        :param int|str year: 4-digit year
        :param int|str month: month (1-12)
        :param int|str day: day of month (1-31)
        :param int|str week: ISO week (1-53)
        :param int|str day_of_week: number or name of weekday (0-6 or mon,tue,wed,thu,fri,sat,sun)
        :param int|str hour: hour (0-23)
        :param int|str minute: minute (0-59)
        :param int|str second: second (0-59)
        :param datetime|str start_date: earliest possible date/time to trigger on (inclusive)
        :param datetime|str end_date: latest possible date/time to trigger on (inclusive)
        :param datetime.tzinfo|str timezone: time zone to use for the date/time calculations (defaults
            to scheduler timezone)
        :param int|None jitter: delay the job execution by ``jitter`` seconds at most

        .. note:: The first weekday is always **monday**.
        """
        def inner(f):
            self._manager.add_cron(f, *args, **kwargs)
            return f

        return inner()

    def define_date_task(self, *args, **kwargs):
        """
        Decorator function to define cron tasks within the context of Flask
        it triggers tasks once on the given datetime. If ``run_date`` is left empty, current time is used.

        :param datetime|str run_date: the date/time to run the job at
        :param datetime.tzinfo|str timezone: time zone for ``run_date`` if it doesn't have one already
        """
        def inner(f):
            self._manager.add_date(f, *args, **kwargs)
            return f

        return inner()

    def get_crons(self):
        return self._manager.crons

    def get_dates(self):
        return self._manager.dates

    def create_db(self):
        driver = self.config[TASKER_DRIVER]
        database_uri = self.config[TASKER_DATABASE_URI]

        if not database_uri:
            try:
                database_uri = self._app.config["SQLALCHEMY_DATABASE_URI"]
            except:
                raise NoneDatabaseURIException

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
                },
            )

        self._db = database
        self._db.proxy.initialize(db)
        self._database = db

    def create_tables(self):
        self._database.create_tables([self._db.Schedule])

    def date_executor(self, f):
        def wrapper():
            now = datetime.datetime.utcnow()
            output = None
            fail_message = None
            with self._app.app_context():
                try:
                    output = f()
                except Exception as e:
                    fail_message = str(e)

            later = datetime.datetime.utcnow()

            name = f.__name__
            self._db.save_task(
                name, now, later, output=output, fail_message=fail_message
            )

        return wrapper

    def cron_executor(self, f):
        def wrapper():
            now = datetime.datetime.utcnow()
            output = None
            fail_message = None
            with self._app.app_context():
                try:
                    output = f()
                except Exception as e:
                    fail_message = str(e)

            later = datetime.datetime.utcnow()

            name = f.__name__
            self._db.save_task(
                name, now, later, output=output, fail_message=fail_message
            )

        return wrapper

    def task_executor(self):
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

    def initialize_db(
        self,
    ):
        if TASKER_DATABASE_URI in self._app.config:
            self.set_database_uri(self._app.config[TASKER_DATABASE_URI])

        if TASKER_DRIVER in self._app.config:
            self.set_driver(self._app.config[TASKER_DRIVER])

    def register_task(self):
        interval_time = self.config[TASKER_INTERVAL_TIME]
        self.add_job(self.task_executor, "interval", seconds=interval_time)

    def register_crons(self):
        crons = self.get_crons()

        if not crons:
            return

        for cron in crons:
            f, params = cron
            args, kwargs = params

            f = self.cron_executor(f)
            self.add_job(f, "cron", *args, **kwargs)

    def register_dates(self):
        dates = self.get_dates()

        if not dates:
            return

        for date in dates:
            f, params = date
            args, kwargs = params

            f = self.date_executor(f)
            self.add_job(f, "date", *args, **kwargs)

    def start(self):
        self.create_tables()
        self.register_task()
        self.register_crons()
        self.register_dates()


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
        app._task_worker = self

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
        app._task_worker = self

    def start(self):
        BaseTaskWorker.start(self)
        BlockingScheduler.start(self)
