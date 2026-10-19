import secrets
import time
from datetime import UTC, datetime, timedelta

from internal.contract import Done, Transaction
from internal.database import Database, Lease, failure


def begin(database: Database) -> Transaction:
    with failure():
        connection = database.engine.connect()
        try:
            transaction = connection.begin()
        except BaseException:
            connection.close()
            raise
    now = time.monotonic()
    key = secrets.token_urlsafe()
    database.leases[key] = Lease(
        connection,
        transaction,
        now + database.transaction_timeout,
        now + database.transaction_idle_timeout,
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=database.transaction_timeout)
    return Transaction(
        transaction=key, expires_at=expires_at.isoformat().replace("+00:00", "Z")
    )


def end(database: Database, key: str, *, commit: bool) -> Done:
    with database.connection(key):
        database.release(key, database.leases[key], commit=commit)
    return Done()
