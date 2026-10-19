import secrets
import time
from datetime import UTC, datetime, timedelta

from contract import Done, Transaction
from database import IDLE_SECONDS, Database, Lease, failure

TIMEOUT_SECONDS = 30


def begin(database: Database) -> Transaction:
    with failure():
        connection = database.engine.connect()
        try:
            transaction = connection.begin()
        except BaseException:
            connection.close()
            raise
    now = time.monotonic()
    key = secrets.token_urlsafe(32)
    database.leases[key] = Lease(
        connection, transaction, now + TIMEOUT_SECONDS, now + IDLE_SECONDS
    )
    expires_at = datetime.now(UTC) + timedelta(seconds=TIMEOUT_SECONDS)
    return Transaction(
        transaction=key, expires_at=expires_at.isoformat().replace("+00:00", "Z")
    )


def end(database: Database, key: str, *, commit: bool) -> Done:
    with database.connection(key):
        database.release(key, database.leases[key], commit=commit)
    return Done()
