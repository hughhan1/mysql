import os
import re
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass, field
from typing import Any

from google.cloud.sql.connector import Connector
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ConfigDict
from pymysql.constants import CLIENT
from sqlalchemy import Connection, MetaData, RootTransaction, Table, create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import InvalidRequestError, SQLAlchemyError

IDLE_SECONDS = 10
FAILURES = {1205: "Lock wait timeout", 1213: "Deadlock", 3572: "Row is locked"}
DUPLICATE_KEY = re.compile(r"for key '([^']+)'\Z")


@dataclass
class Lease:
    connection: Connection
    transaction: RootTransaction
    deadline: float
    idle: float
    lock: threading.Lock = field(default_factory=threading.Lock)

    def expired(self) -> bool:
        return time.monotonic() >= min(self.deadline, self.idle)


@dataclass
class Database:
    engine: Engine
    metadata: MetaData = field(default_factory=MetaData)
    reflecting: threading.Lock = field(default_factory=threading.Lock)
    leases: dict[str, Lease] = field(default_factory=dict)

    def table(self, connection: Connection, name: str) -> Table:
        with self.reflecting:
            if name not in self.metadata.tables:
                try:
                    self.metadata.reflect(connection, only=[name])
                except InvalidRequestError:
                    raise ToolError(f"Unknown table {name}.") from None
            return self.metadata.tables[name]

    def release(self, key: str, lease: Lease, commit: bool = False) -> None:
        self.leases.pop(key, None)
        with failure():
            try:
                if commit:
                    lease.transaction.commit()
            except BaseException:
                lease.transaction.rollback()
                raise
            finally:
                lease.connection.close()

    @contextmanager
    def connection(self, transaction: str | None) -> Iterator[Connection]:
        if transaction is None:
            with failure(), self.engine.begin() as connection:
                yield connection
            return
        lease = self.leases.get(transaction)
        if lease is None:
            raise ToolError("Unknown transaction.")
        if not lease.lock.acquire(blocking=False):
            raise ToolError("Transaction busy.")
        try:
            if self.leases.get(transaction) is not lease:
                raise ToolError("Unknown transaction.")
            if lease.expired():
                self.release(transaction, lease)
                raise ToolError("Transaction expired.")
            try:
                with failure():
                    yield lease.connection
            except BaseException:
                with suppress(ToolError):
                    self.release(transaction, lease)
                raise
            lease.idle = time.monotonic() + IDLE_SECONDS
        finally:
            lease.lock.release()

    def reap(self, stop: threading.Event) -> None:
        while not stop.wait(1):
            for key, lease in list(self.leases.items()):
                if lease.expired() and lease.lock.acquire(blocking=False):
                    try:
                        if self.leases.get(key) is lease:
                            with suppress(ToolError):
                                self.release(key, lease)
                    finally:
                        lease.lock.release()


@contextmanager
def failure() -> Iterator[None]:
    try:
        yield
    except SQLAlchemyError as error:
        args: tuple[Any, ...] = getattr(getattr(error, "orig", None), "args", ())
        code = args[0] if args and isinstance(args[0], int) else None
        if code is None:
            raise ToolError("Database unavailable.") from None
        if code == 1062 and (key := DUPLICATE_KEY.search(str(args[-1]))):
            raise ToolError(f"Duplicate key {key[1]} ({code}).") from None
        raise ToolError(f"{FAILURES.get(code, 'Database error')} ({code}).") from None


@contextmanager
def database_engine() -> Iterator[Engine]:
    url = make_url(os.environ["DATABASE_URL"])
    connection_options = {
        "init_command": "SET time_zone = '+00:00', innodb_lock_wait_timeout = 5",
        "client_flag": CLIENT.FOUND_ROWS,
        "connect_timeout": 5,
        "read_timeout": 10,
        "write_timeout": 10,
    }
    with ExitStack() as stack:
        options = {}
        if instance := os.getenv("CLOUD_SQL_INSTANCE"):
            connector = stack.enter_context(
                Connector(ip_type="PRIVATE", refresh_strategy="LAZY")
            )
            options["creator"] = lambda: connector.connect(
                instance,
                "pymysql",
                user=url.username,
                password=url.password,
                db=url.database,
                **connection_options,
            )
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
            pool_timeout=1,
            hide_parameters=True,
            connect_args=connection_options,
            **options,
        )
        stack.callback(engine.dispose)
        yield engine


@asynccontextmanager
async def lifespan(server: MCPServer[Any]) -> AsyncIterator[Database]:
    for tool in server._tool_manager.list_tools():
        tool.fn_metadata.arg_model.model_config.update(
            ConfigDict(extra="forbid", hide_input_in_errors=True)
        )
        tool.fn_metadata.arg_model.model_rebuild(force=True)
    with database_engine() as engine:
        database = Database(engine)
        stop = threading.Event()
        reaper = threading.Thread(target=database.reap, args=(stop,), daemon=True)
        reaper.start()
        try:
            yield database
        finally:
            stop.set()
            reaper.join()
            for key, lease in list(database.leases.items()):
                with suppress(ToolError):
                    database.release(key, lease)
