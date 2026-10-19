import secrets
from contextlib import suppress

from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.exc import SQLAlchemyError

from internal.contract import Done, Transaction
from internal.database import Database, TransactionState


def begin(database: Database) -> Transaction:
    result = Transaction(transaction=secrets.token_urlsafe())
    try:
        connection = database.engine.connect()
        try:
            database.transactions[result.transaction] = TransactionState(
                connection, connection.begin()
            )
        except BaseException:
            with suppress(SQLAlchemyError):
                connection.close()
            raise
    except SQLAlchemyError as error:
        raise ToolError(str(getattr(error, "orig", error))) from error
    return result


def end(database: Database, key: str, *, commit: bool) -> Done:
    with database.connection(key):
        database.release(key, commit=commit)
    return Done()
