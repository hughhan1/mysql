import secrets
import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta

from internal.contract import Done, Transaction
from internal.database import Database, Lease, failure


def begin(database: Database) -> Transaction:
    with failure(), ExitStack() as stack:
        connection = stack.enter_context(database.engine.connect())
        transaction = connection.begin()
        now = time.monotonic()
        key = secrets.token_urlsafe()
        expires_at = datetime.now(UTC) + timedelta(seconds=database.transaction_timeout)
        result = Transaction(
            transaction=key, expires_at=expires_at.isoformat().replace("+00:00", "Z")
        )
        database.leases[key] = Lease(
            connection,
            transaction,
            now + database.transaction_timeout,
            now + database.transaction_idle_timeout,
        )
        stack.pop_all()
        return result


def end(database: Database, key: str, *, commit: bool) -> Done:
    with database.connection(key):
        database.release(key, database.leases[key], commit=commit)
    return Done()
