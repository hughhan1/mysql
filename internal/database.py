import os
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import (
    asynccontextmanager,
    closing,
    contextmanager,
    nullcontext,
    suppress,
)
from dataclasses import dataclass, field
from typing import Any

from google.cloud.sql.connector import Connector, IPTypes
from google.cloud.sql.connector.enums import RefreshStrategy
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ConfigDict
from pymysql.constants import CLIENT
from sqlalchemy import Connection, MetaData, RootTransaction, Table, create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import InvalidRequestError, SQLAlchemyError


@dataclass(frozen=True, slots=True)
class TransactionState:
    connection: Connection
    transaction: RootTransaction
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True, slots=True)
class Database:
    engine: Engine
    metadata: MetaData = field(default_factory=MetaData, init=False)
    reflecting: threading.Lock = field(default_factory=threading.Lock, init=False)
    transactions: dict[str, TransactionState] = field(default_factory=dict, init=False)

    def table(self, connection: Connection, name: str) -> Table:
        with self.reflecting:
            if name not in self.metadata.tables:
                try:
                    self.metadata.reflect(connection, only=[name])
                except InvalidRequestError:
                    raise ToolError(f"Unknown table {name}.") from None
            return self.metadata.tables[name]

    def release(self, key: str, commit: bool = False) -> None:
        state = self.transactions.pop(key, None)
        if state is not None:
            with state.connection, state.transaction:
                if not commit:
                    state.transaction.rollback()

    def close(self) -> None:
        for key in list(self.transactions):
            with suppress(SQLAlchemyError):
                self.release(key)

    @contextmanager
    def connection(self, transaction: str | None) -> Iterator[Connection]:
        try:
            if transaction is None:
                with self.engine.begin() as connection:
                    yield connection
                return
            state = self.transactions.get(transaction)
            if state is None:
                raise ToolError("Unknown transaction.")
            if not state.lock.acquire(blocking=False):
                raise ToolError("Transaction busy.")
            try:
                if self.transactions.get(transaction) is not state:
                    raise ToolError("Unknown transaction.")
                yield state.connection
            except BaseException:
                with suppress(SQLAlchemyError):
                    self.release(transaction)
                raise
            finally:
                state.lock.release()
        except SQLAlchemyError as error:
            raise ToolError(str(getattr(error, "orig", error))) from error


@contextmanager
def database_engine() -> Iterator[Engine]:
    url = make_url(os.environ["DATABASE_URL"])
    connection_options: dict[str, Any] = {
        "init_command": "SET time_zone = '+00:00'",
        "client_flag": CLIENT.FOUND_ROWS,
    }
    instance = os.getenv("CLOUD_SQL_INSTANCE")
    with (
        Connector(ip_type=IPTypes.PRIVATE, refresh_strategy=RefreshStrategy.LAZY)
        if instance
        else nullcontext()
    ) as connector:
        options: dict[str, Any] = {}
        if connector:
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
            hide_parameters=True,
            connect_args=connection_options,
            **options,
        )
        try:
            yield engine
        finally:
            engine.dispose()


@asynccontextmanager
async def lifespan(server: MCPServer[Any]) -> AsyncIterator[Database]:
    for tool in server._tool_manager.list_tools():
        tool.fn_metadata.arg_model.model_config.update(
            ConfigDict(extra="forbid", hide_input_in_errors=True)
        )
        tool.fn_metadata.arg_model.model_rebuild(force=True)
    with database_engine() as engine, closing(Database(engine)) as database:
        yield database
