import asyncio
import logging
from threading import Event
from uuid import uuid4

import pytest
from mcp import Client
from sqlalchemy import Engine, event

from server import server

pytestmark = pytest.mark.anyio

JOIN_DETAILS = {
    "table": "item_details",
    "on": [
        {
            "column": "item_details.item_id",
            "op": "eq",
            "value": {"column": "items.id"},
        }
    ],
}


async def ok(client, tool, **arguments):
    result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    return result.structured_content


async def fails(client, tool, **arguments):
    result = await client.call_tool(tool, arguments)
    assert result.is_error, result.structured_content
    return result.content[0].text


def item(slug):
    return {"id": str(uuid4()), "source": "catalog", "slug": slug}


def detail(owner, **values):
    return {
        "id": str(uuid4()),
        "name": "Acme",
        "status": "PENDING",
        "item_id": owner,
        "label": "Applicant",
        "fallback_label": "applicant@example.com",
    } | values


def slugs(found):
    return sorted(row["slug"] for row in found["rows"])


async def test_statements_cover_insert_select_update_delete_and_raw_sql(database):
    async with Client(server) as client:
        ids = {}
        for slug in ("a", "b", "c"):
            added = item(slug)
            assert await ok(client, "insert", table="items", values=added) == {
                "rowcount": 1
            }
            ids[slug] = added["id"]

        for op, value, expected in [
            ("eq", "b", ["b"]),
            ("ne", "b", ["a", "c"]),
            ("lt", "b", ["a"]),
            ("lt", {"sql": "CONCAT(:p, '')", "parameters": {"p": "b"}}, ["a"]),
            ("le", "b", ["a", "b"]),
            ("gt", "b", ["c"]),
            ("ge", "b", ["b", "c"]),
            ("in", ["a", "c"], ["a", "c"]),
            ("not_in", ["a"], ["b", "c"]),
            ("like", "b%", ["b"]),
            ("not_like", "b%", ["a", "c"]),
            ("is_not_null", None, ["a", "b", "c"]),
            ("is_null", None, []),
        ]:
            matched = await ok(
                client,
                "select",
                table="items",
                columns=["slug"],
                where=[{"column": "slug", "op": op, "value": value}],
            )
            assert slugs(matched) == expected, op

        created = detail(ids["a"], created_at="2026-01-02T05:04:05.123456+02:00")
        inserted = await ok(client, "insert", table="item_details", values=created)
        assert inserted == {"rowcount": 1}
        revised = await ok(
            client,
            "insert",
            table="item_details",
            values=detail(ids["a"], name="Revised"),
            on_duplicate_key_update={
                "name": {"inserted": "name"},
                "label": {"column": "fallback_label"},
                "updated_at": {
                    "sql": "TIMESTAMP(:moment)",
                    "parameters": {"moment": "2026-03-04 05:06:07"},
                },
            },
        )
        assert revised == {"rowcount": 2}
        joined = await ok(
            client,
            "select",
            table="items",
            joins=[JOIN_DETAILS],
            columns=[
                "items.slug",
                "name",
                "label",
                "item_details.created_at",
                "updated_at",
            ],
        )
        assert joined["rows"] == [
            {
                "items.slug": "a",
                "name": "Revised",
                "label": "applicant@example.com",
                "item_details.created_at": "2026-01-02T03:04:05.123456Z",
                "updated_at": "2026-03-04T05:06:07Z",
            }
        ]
        ambiguous = await fails(
            client, "select", table="items", joins=[JOIN_DETAILS], columns=["id"]
        )
        assert "Unknown or ambiguous column id." in ambiguous

        changed = await ok(
            client,
            "update",
            table="items",
            values={"slug": "z"},
            where=[{"column": "slug", "op": "eq", "value": "c"}],
        )
        assert changed == {"rowcount": 1}
        await ok(
            client,
            "delete",
            table="item_details",
            where=[{"column": "item_id", "op": "eq", "value": ids["a"]}],
        )
        removed = await ok(
            client,
            "delete",
            table="items",
            where=[{"column": "slug", "op": "in", "value": ["b", "z"]}],
        )
        assert removed == {"rowcount": 2}

        renamed = await ok(
            client,
            "execute",
            sql="UPDATE items SET slug = :new WHERE slug = :old",
            parameters={"new": "a2", "old": "a"},
        )
        assert renamed == {"result": {"rowcount": 1}}
        raw = await ok(
            client,
            "execute",
            sql="SELECT slug, created_at FROM items WHERE source = :source",
            parameters={"source": "catalog"},
        )
        [row] = raw["result"]["rows"]
        assert row["slug"] == "a2" and row["created_at"].endswith("Z")


async def test_select_outer_joins_groups_labels_and_pages(database):
    async with Client(server) as client:
        for slug in ("a", "b", "c", "d", "e"):
            added = item(slug)
            await ok(client, "insert", table="items", values=added)
            if slug != "a":
                await ok(
                    client,
                    "insert",
                    table="item_details",
                    values=detail(
                        added["id"], status="PENDING" if slug == "e" else "ACTIVE"
                    ),
                )
        query = {
            "table": "items",
            "columns": [
                {"column": "items.slug", "label": "slug"},
                {"column": "item_details.status", "label": "detail_status"},
            ],
            "joins": [JOIN_DETAILS | {"isouter": True}],
            "where": [
                {
                    "or_": [
                        {"column": "items.slug", "op": "in", "value": ["a", "d"]},
                        {
                            "column": "item_details.status",
                            "op": "eq",
                            "value": "ACTIVE",
                        },
                    ]
                },
                {"column": "items.slug", "op": "ne", "value": "d"},
            ],
            "order_by": [
                {"column": "item_details.name"},
                {"column": "items.slug", "direction": "desc"},
            ],
        }
        expected = [
            {"slug": "a", "detail_status": None},
            {"slug": "c", "detail_status": "ACTIVE"},
            {"slug": "b", "detail_status": "ACTIVE"},
        ]
        for page, rows in [
            ({}, expected),
            ({"limit": 0}, []),
            ({"offset": 1, "limit": 1}, [expected[1]]),
            ({"offset": 2}, [expected[2]]),
            ({"offset": 3, "limit": 1}, []),
        ]:
            assert (await ok(client, "select", **(query | page)))["rows"] == rows

        joined = await ok(
            client,
            "select",
            table="items",
            columns=["items.slug", "item_details.status"],
            joins=[
                JOIN_DETAILS
                | {
                    "isouter": True,
                    "on": JOIN_DETAILS["on"]
                    + [
                        {
                            "or_": [
                                {"column": "items.slug", "op": "eq", "value": "a"},
                                {"column": "items.slug", "op": "eq", "value": "b"},
                            ]
                        }
                    ],
                }
            ],
            where=[{"column": "items.slug", "op": "in", "value": ["b", "c"]}],
            order_by=[{"column": "items.slug"}],
        )
        assert joined["rows"] == [
            {"items.slug": "b", "item_details.status": "ACTIVE"},
            {"items.slug": "c", "item_details.status": None},
        ]


async def test_invalid_statements_change_nothing_and_never_echo_inputs(
    database, caplog
):
    caplog.set_level(logging.INFO, logger="mcp")
    async with Client(server) as client:
        await ok(client, "insert", table="items", values=item("private-slug"))
        for tool, arguments, message in [
            ("select", {"table": "missing"}, "Unknown table missing."),
            (
                "select",
                {"table": "items", "columns": ["missing"]},
                "Unknown or ambiguous column missing.",
            ),
            (
                "select",
                {
                    "table": "items",
                    "where": [{"column": "slug", "op": "eq", "value": None}],
                },
                "Invalid eq condition on slug.",
            ),
            (
                "select",
                {
                    "table": "items",
                    "where": [{"column": "slug", "op": "in", "value": "private-slug"}],
                },
                "Invalid in condition on slug.",
            ),
            (
                "select",
                {"table": "items", "joins": [{"table": "item_details", "on": []}]},
                "Join on item_details needs a condition.",
            ),
            (
                "insert",
                {"table": "items", "values": {"slug": {"inserted": "slug"}}},
                "Inserted references need on_duplicate_key_update.",
            ),
            (
                "insert",
                {"table": "items", "values": {"created_at": "private-slug"}},
                "Invalid datetime for created_at.",
            ),
            (
                "insert",
                {"table": "items", "values": {}},
                "Insert needs at least one value.",
            ),
            (
                "update",
                {
                    "table": "items",
                    "values": {"slug": "private-slug"},
                    "where": [],
                },
                "Update needs values and a where condition.",
            ),
            (
                "delete",
                {"table": "items", "where": []},
                "Delete needs a where condition.",
            ),
            ("select", {"table": "items;private-slug"}, "validation error"),
            (
                "select",
                {"table": "items", "where": [{"or_": []}]},
                "OR group needs a condition.",
            ),
            (
                "select",
                {"table": "items", "extra": "private-slug"},
                "validation error",
            ),
            (
                "insert",
                {"table": "items", "values": {"id": {"bogus": "private-slug"}}},
                "validation error",
            ),
        ]:
            text = await fails(client, tool, **arguments)
            assert message in text, (tool, arguments)
            assert "private-slug" not in text
        assert slugs(await ok(client, "select", table="items")) == ["private-slug"]
    assert "private-slug" not in caplog.text


async def test_transactions_isolate_commit_and_roll_back(database):
    async with Client(server) as client:
        first = (await ok(client, "begin"))["transaction"]
        await ok(
            client, "insert", table="items", values=item("committed"), transaction=first
        )
        visible = await ok(client, "select", table="items", transaction=first)
        assert slugs(visible) == ["committed"]
        assert slugs(await ok(client, "select", table="items")) == []
        assert await ok(client, "commit", transaction=first) == {}
        assert "Unknown transaction." in await fails(
            client, "commit", transaction=first
        )

        second = (await ok(client, "begin"))["transaction"]
        await ok(
            client, "insert", table="items", values=item("rolled"), transaction=second
        )
        await ok(client, "rollback", transaction=second)

        failed = (await ok(client, "begin"))["transaction"]
        await ok(
            client, "insert", table="items", values=item("failed"), transaction=failed
        )
        duplicate = await fails(
            client,
            "insert",
            table="items",
            values=item("committed"),
            transaction=failed,
        )
        assert "Duplicate key items.uq_items_identity (1062)." in duplicate
        assert "Unknown transaction." in await fails(
            client, "commit", transaction=failed
        )

        abandoned = (await ok(client, "begin"))["transaction"]
        await ok(
            client,
            "insert",
            table="items",
            values=item("abandoned"),
            transaction=abandoned,
        )

    async with Client(server) as client:
        assert slugs(await ok(client, "select", table="items")) == ["committed"]


async def test_handles_are_bearer_and_serve_one_statement_at_a_time(clients):
    client, other = clients
    handle = (await ok(client, "begin"))["transaction"]
    await ok(client, "insert", table="items", values=item("shared"), transaction=handle)
    assert slugs(await ok(other, "select", table="items", transaction=handle)) == [
        "shared"
    ]
    assert slugs(await ok(other, "select", table="items")) == []
    assert "Unknown transaction." in await fails(
        other, "select", table="items", transaction="missing"
    )

    entered, resume = Event(), Event()

    def hold_statement(*, statement, **kwargs):
        if statement == "SELECT 1":
            entered.set()
            assert resume.wait(5)

    event.listen(Engine, "before_cursor_execute", hold_statement, named=True)
    running = asyncio.create_task(
        ok(client, "execute", sql="SELECT 1", transaction=handle)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert "Transaction busy." in await fails(
            other, "select", table="items", transaction=handle
        )
    finally:
        resume.set()
        await running
        event.remove(Engine, "before_cursor_execute", hold_statement)
    await ok(other, "commit", transaction=handle)
    assert slugs(await ok(client, "select", table="items")) == ["shared"]


@pytest.mark.parametrize("idle_timeout", [None, 4])
async def test_idle_handles_expire(database, clock, monkeypatch, *, idle_timeout):
    if idle_timeout is not None:
        monkeypatch.setenv("TRANSACTION_IDLE_TIMEOUT", str(idle_timeout))
    async with Client(server) as client:
        handle = (await ok(client, "begin"))["transaction"]
        await ok(
            client, "insert", table="items", values=item("idle"), transaction=handle
        )
        clock.now += 9 if idle_timeout is None else 3
        assert slugs(await ok(client, "select", table="items", transaction=handle)) == [
            "idle"
        ]
        clock.now += 10 if idle_timeout is None else 4
        refused = await fails(client, "commit", transaction=handle)
        assert "Unknown transaction." in refused or "Transaction expired." in refused
        assert slugs(await ok(client, "select", table="items")) == []


@pytest.mark.parametrize("timeout", [None, 12])
async def test_active_transactions_still_expire_at_the_absolute_deadline(
    database, clock, monkeypatch, *, timeout
):
    if timeout is not None:
        monkeypatch.setenv("TRANSACTION_TIMEOUT", str(timeout))
    async with Client(server) as client:
        handle = (await ok(client, "begin"))["transaction"]
        await ok(
            client, "insert", table="items", values=item("expired"), transaction=handle
        )
        for _ in range(3):
            clock.now += 9 if timeout is None else 3
            assert slugs(
                await ok(client, "select", table="items", transaction=handle)
            ) == ["expired"]
        clock.now += 3
        refused = await fails(client, "commit", transaction=handle)
        assert "Unknown transaction." in refused or "Transaction expired." in refused
        assert slugs(await ok(client, "select", table="items")) == []


async def test_row_locks_hold_until_the_transaction_ends(database):
    async with Client(server) as client:
        locked, free = item("locked"), item("free")
        for added in (locked, free):
            await ok(client, "insert", table="items", values=added)

        def by_id(row):
            return [{"column": "id", "op": "eq", "value": row["id"]}]

        holder = (await ok(client, "begin"))["transaction"]
        await ok(
            client,
            "select",
            table="items",
            where=by_id(locked),
            for_update=True,
            transaction=holder,
        )
        rename = {"slug": "renamed"}
        assert "Lock wait timeout (1205)." in await fails(
            client, "update", table="items", values=rename, where=by_id(locked)
        )
        assert await ok(
            client, "update", table="items", values=rename, where=by_id(free)
        ) == {"rowcount": 1}
        await ok(client, "rollback", transaction=holder)
        assert await ok(
            client,
            "update",
            table="items",
            values={"slug": "x"},
            where=by_id(locked),
        ) == {"rowcount": 1}


@pytest.mark.parametrize("overrides", [False, True])
async def test_mysql_session_configuration(database, monkeypatch, overrides):
    monkeypatch.delenv("INNODB_LOCK_WAIT_TIMEOUT")
    if overrides:
        monkeypatch.setenv("WAIT_TIMEOUT", "120")
        monkeypatch.setenv("INNODB_LOCK_WAIT_TIMEOUT", "2")
    async with Client(server) as client:
        result = await ok(
            client,
            "execute",
            sql="""
            SELECT @@session.wait_timeout AS idle,
                   @@global.wait_timeout AS default_idle,
                   @@session.innodb_lock_wait_timeout AS locks,
                   @@global.innodb_lock_wait_timeout AS default_locks,
                   @@session.time_zone AS time_zone
        """,
        )
        [row] = result["result"]["rows"]
        assert row["idle"] == (120 if overrides else row["default_idle"])
        assert row["locks"] == (2 if overrides else row["default_locks"])
        assert row["time_zone"] == "+00:00"
