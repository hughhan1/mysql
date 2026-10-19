from functools import partial
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field
from sqlalchemy import text

from internal import statements, transactions
from internal.contract import (
    Affected,
    Condition,
    Done,
    Join,
    LabeledColumn,
    OrderBy,
    Predicate,
    Rows,
    Scalar,
    Transaction,
    Value,
)
from internal.database import Database, lifespan

server = MCPServer("mysql", version="0.4.1", lifespan=lifespan)


@server.tool(
    description=(
        "[BEGIN](https://dev.mysql.com/doc/refman/8.4/en/commit.html) "
        "starts a new transaction."
    )
)
def begin(ctx: Context[Database]) -> Transaction:
    return transactions.begin(ctx.request_context.lifespan_context)


@server.tool(
    description=(
        "[COMMIT](https://dev.mysql.com/doc/refman/8.4/en/commit.html) "
        "commits the current transaction, making its changes permanent."
    )
)
def commit(ctx: Context[Database], transaction: str) -> Done:
    return transactions.end(
        ctx.request_context.lifespan_context, transaction, commit=True
    )


@server.tool(
    description=(
        "[ROLLBACK](https://dev.mysql.com/doc/refman/8.4/en/commit.html) "
        "rolls back the current transaction, canceling its changes."
    )
)
def rollback(ctx: Context[Database], transaction: str) -> Done:
    return transactions.end(
        ctx.request_context.lifespan_context, transaction, commit=False
    )


@server.tool(
    description=(
        "Execute an SQL statement. "
        "[MySQL SQL statements](https://dev.mysql.com/doc/refman/8.4/en/sql-statements.html)"
    )
)
def execute(
    ctx: Context[Database],
    *,
    sql: str,
    parameters: dict[str, Scalar] | None = None,
    transaction: str | None = None,
) -> Rows | Affected:
    database = ctx.request_context.lifespan_context
    with database.connection(transaction) as connection:
        result = connection.execute(text(sql), parameters)
        if result.returns_rows:
            return Rows(rows=statements.rows(result))
        return Affected(rowcount=result.rowcount)


@server.tool(
    description=(
        "[SELECT](https://dev.mysql.com/doc/refman/8.4/en/select.html) "
        "is used to retrieve rows selected from one or more tables."
    )
)
def select(
    ctx: Context[Database],
    *,
    table: str,
    columns: list[str] | list[LabeledColumn] | None = None,
    joins: list[Join] | None = None,
    where: list[Predicate] | None = None,
    order_by: list[OrderBy] | None = None,
    limit: Annotated[int, Field(ge=0)] | None = None,
    offset: Annotated[int, Field(ge=0)] | None = None,
    for_update: bool = False,
    transaction: str | None = None,
) -> Rows:
    database = ctx.request_context.lifespan_context
    with database.connection(transaction) as connection:
        statement = statements.selection(
            partial(database.table, connection),
            table,
            columns=columns,
            joins=joins,
            conditions=where,
            order_by=order_by,
            limit=limit,
            offset=offset,
            for_update=for_update,
        )
        return Rows(rows=statements.rows(connection.execute(statement)))


@server.tool(
    description=(
        "[INSERT](https://dev.mysql.com/doc/refman/8.4/en/insert.html) "
        "inserts new rows into an existing table."
    )
)
def insert(
    ctx: Context[Database],
    *,
    table: str,
    values: dict[str, Value],
    on_duplicate_key_update: dict[str, Value] | None = None,
    transaction: str | None = None,
) -> Affected:
    database = ctx.request_context.lifespan_context
    with database.connection(transaction) as connection:
        result = connection.execute(
            statements.insertion(
                database.table(connection, table), values, on_duplicate_key_update
            )
        )
    return Affected(rowcount=result.rowcount)


@server.tool(
    description=(
        "[UPDATE](https://dev.mysql.com/doc/refman/8.4/en/update.html) "
        "is a DML statement that modifies rows in a table."
    )
)
def update(
    ctx: Context[Database],
    *,
    table: str,
    values: dict[str, Value],
    where: list[Condition],
    transaction: str | None = None,
) -> Affected:
    database = ctx.request_context.lifespan_context
    with database.connection(transaction) as connection:
        result = connection.execute(
            statements.modification(database.table(connection, table), values, where)
        )
    return Affected(rowcount=result.rowcount)


@server.tool(
    description=(
        "[DELETE](https://dev.mysql.com/doc/refman/8.4/en/delete.html) "
        "is a DML statement that removes rows from a table."
    )
)
def delete(
    ctx: Context[Database],
    *,
    table: str,
    where: list[Condition],
    transaction: str | None = None,
) -> Affected:
    database = ctx.request_context.lifespan_context
    with database.connection(transaction) as connection:
        result = connection.execute(
            statements.deletion(database.table(connection, table), where)
        )
    return Affected(rowcount=result.rowcount)


if __name__ == "__main__":
    server.run()
