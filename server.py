from functools import partial
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from pydantic import Field
from sqlalchemy import text

import statements
import transactions
from contract import (
    Affected,
    ColumnName,
    Condition,
    Done,
    Join,
    LabeledColumn,
    Name,
    OrderBy,
    Predicate,
    Rows,
    Scalar,
    Transaction,
    Value,
)
from database import Database, lifespan

server = MCPServer("mysql", version="0.1.0", lifespan=lifespan)


@server.tool(
    description=(
        "Begin a transaction and return its handle. Keep it secret: anyone holding "
        "the handle can use the transaction. Pass the handle as `transaction` "
        "to statements, then commit or roll back. The handle expires after 30 "
        "seconds, or after 10 idle seconds, and any failed statement rolls the "
        "whole transaction back."
    )
)
def begin(ctx: Context[Database]) -> Transaction:
    return transactions.begin(ctx.request_context.lifespan_context)


@server.tool(description="Commit a transaction and end its handle.")
def commit(ctx: Context[Database], transaction: str) -> Done:
    return transactions.end(
        ctx.request_context.lifespan_context, transaction, commit=True
    )


@server.tool(description="Roll back a transaction and end its handle.")
def rollback(ctx: Context[Database], transaction: str) -> Done:
    return transactions.end(
        ctx.request_context.lifespan_context, transaction, commit=False
    )


@server.tool(
    description=(
        "Run one SQL statement with named `:parameters`. Without `transaction` the "
        "statement commits on its own."
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
        "Select rows. Conditions in `where` and in each join's `on` are combined with "
        "AND; {'or_': [conditions]} groups nonempty lists of simple alternatives. "
        "Joins are inner unless `isouter` is true. Columns are a list of names or "
        "a list of {'column': name, 'label': name} objects to rename results. "
        "Names may be qualified as table.column and must be unambiguous. "
        "`order_by` takes columns with direction 'asc' (default) or 'desc'. "
        "`limit` and `offset` are nonnegative; omitted values leave rows unbounded "
        "and start at the first row. Use an explicit order for stable pages. "
        "`for_update` locks the selected rows until the transaction ends."
    )
)
def select(
    ctx: Context[Database],
    *,
    table: Name,
    columns: list[ColumnName] | list[LabeledColumn] | None = None,
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
        "Insert one row. `on_duplicate_key_update` may reference the attempted row "
        "with {'inserted': column}."
    )
)
def insert(
    ctx: Context[Database],
    *,
    table: Name,
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


@server.tool(description="Update the rows matching every `where` condition.")
def update(
    ctx: Context[Database],
    *,
    table: Name,
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


@server.tool(description="Delete the rows matching every `where` condition.")
def delete(
    ctx: Context[Database],
    *,
    table: Name,
    where: list[Condition],
    transaction: str | None = None,
) -> Affected:
    database = ctx.request_context.lifespan_context
    with database.connection(transaction) as connection:
        result = connection.execute(
            statements.deletion(database.table(connection, table), where)
        )
    return Affected(rowcount=result.rowcount)
