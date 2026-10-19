import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from hosting import hosted
from sqlalchemy import MetaData, create_engine, pool, text
from sqlalchemy.engine import make_url

SCHEMA = (
    """
CREATE TABLE items (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    source VARCHAR(512) COLLATE utf8mb4_0900_bin NOT NULL,
    slug VARCHAR(255) COLLATE utf8mb4_0900_bin NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT uq_items_identity UNIQUE (source, slug)
)
""",
    """
CREATE TABLE item_details (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    item_id VARCHAR(36) NOT NULL UNIQUE,
    status ENUM('PENDING','ACTIVE') NOT NULL,
    label VARCHAR(200) NOT NULL,
    fallback_label VARCHAR(254) NOT NULL,
    description TEXT NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
)
""",
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def database_url():
    url = make_url(os.environ["TEST_DATABASE_URL"])
    worker = url.set(database=f"{url.database}_{uuid4().hex}")
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.begin() as connection:
        connection.execute(text(f"CREATE DATABASE `{worker.database}`"))
    try:
        yield worker
    finally:
        with engine.begin() as connection:
            connection.execute(text(f"DROP DATABASE `{worker.database}`"))
        engine.dispose()


@pytest.fixture
async def database(monkeypatch, database_url):
    monkeypatch.delenv("CLOUD_SQL_INSTANCE", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL", database_url.render_as_string(hide_password=False)
    )
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.begin() as connection:
        for statement in SCHEMA:
            connection.execute(text(statement))
    try:
        yield engine
    finally:
        tables = MetaData()
        tables.reflect(engine)
        tables.drop_all(engine)
        engine.dispose()


@pytest.fixture
async def providers(database):
    async with hosted():
        yield


@pytest.fixture
def clock(monkeypatch):
    import database
    import transactions

    clock = SimpleNamespace(now=time.monotonic())
    timer = SimpleNamespace(monotonic=lambda: clock.now)
    monkeypatch.setattr(database, "time", timer)
    monkeypatch.setattr(transactions, "time", timer)
    return clock
