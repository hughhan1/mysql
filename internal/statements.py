import operator
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final

from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import (
    ColumnElement,
    CursorResult,
    Delete,
    Select,
    Table,
    Update,
    and_,
    delete,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import Insert, insert
from sqlalchemy.sql.expression import ColumnClause, TextClause
from sqlalchemy.types import DateTime

from internal.contract import (
    ColumnRef,
    Condition,
    Expression,
    InsertedRef,
    Join,
    LabeledColumn,
    Or,
    OrderBy,
    Predicate,
    Scalar,
    Value,
)

type Column = ColumnClause[Any]
type Resolve = Callable[[str], Table]

COMPARISONS: Final[dict[str, Callable[[Any, Any], ColumnElement[bool]]]] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
}


def scalar(item: object) -> Scalar:
    match item:
        case None | bool() | int() | float() | str():
            return item
        case datetime():
            return item.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        case date():
            return item.isoformat()
        case Decimal():
            return str(item)
    raise ToolError("Unsupported column value.")


def rows(result: CursorResult[Any]) -> list[dict[str, Scalar]]:
    columns = list(result.keys())
    return [{name: scalar(item) for name, item in zip(columns, row)} for row in result]


def column(tables: Sequence[Table], name: str) -> Column:
    table_name, _, column_name = name.rpartition(".")
    matches = [
        table.c[column_name]
        for table in tables
        if table_name in ("", table.name) and column_name in table.c
    ]
    if len(matches) != 1:
        raise ToolError(f"Unknown or ambiguous column {name}.")
    return matches[0]


def bound(target: Column, item: Scalar) -> object:
    if isinstance(item, str) and isinstance(target.type, DateTime):
        try:
            moment = datetime.fromisoformat(item)
        except ValueError:
            raise ToolError(f"Invalid datetime for {target.name}.") from None
        if moment.tzinfo:
            return moment.astimezone(UTC).replace(tzinfo=None)
        return moment
    return item


def expression(item: Expression) -> TextClause:
    return text(item.sql).bindparams(**(item.parameters or {}))


def condition(tables: Sequence[Table], item: Condition) -> ColumnElement[bool]:
    target = column(tables, item.column)
    match item.op, item.value:
        case "is_null", None:
            return target.is_(None)
        case "is_not_null", None:
            return target.is_not(None)
        case "in", list() as values:
            return target.in_([bound(target, value) for value in values])
        case "not_in", list() as values:
            return target.not_in([bound(target, value) for value in values])
        case "like", str() as pattern:
            return target.like(pattern)
        case "not_like", str() as pattern:
            return target.not_like(pattern)
        case op, ColumnRef(column=other) if op in COMPARISONS:
            return COMPARISONS[op](target, column(tables, other))
        case op, Expression() as value if op in COMPARISONS:
            return COMPARISONS[op](target, expression(value))
        case op, (str() | int() | float()) as value if op in COMPARISONS:
            return COMPARISONS[op](target, bound(target, value))
    raise ToolError(f"Invalid {item.op} condition on {item.column}.")


def where(
    tables: Sequence[Table], items: Sequence[Predicate]
) -> list[ColumnElement[bool]]:
    if any(isinstance(item, Or) and not item.or_ for item in items):
        raise ToolError("OR group needs a condition.")
    return [
        or_(*(condition(tables, arm) for arm in item.or_))
        if isinstance(item, Or)
        else condition(tables, item)
        for item in items
    ]


def assignments(
    table: Table, items: dict[str, Value], inserted: Any = None
) -> dict[str, object]:
    assigned: dict[str, object] = {}
    for name, item in items.items():
        target = column([table], name)
        match item:
            case ColumnRef(column=other):
                assigned[target.name] = column([table], other)
            case InsertedRef(inserted=other) if inserted is not None:
                assigned[target.name] = inserted[column([table], other).name]
            case InsertedRef():
                raise ToolError("Inserted references need on_duplicate_key_update.")
            case Expression():
                assigned[target.name] = expression(item)
            case _:
                assigned[target.name] = bound(target, item)
    return assigned


def selection(
    resolve: Resolve,
    table: str,
    *,
    columns: Sequence[str | LabeledColumn] | None,
    joins: list[Join] | None,
    conditions: list[Predicate] | None,
    order_by: list[OrderBy] | None,
    limit: int | None,
    offset: int | None,
    for_update: bool,
) -> Select[Any]:
    tables = [resolve(table)]
    source: Any = tables[0]
    for join in joins or []:
        tables.append(resolve(join.table))
        if not join.on:
            raise ToolError(f"Join on {join.table} needs a condition.")
        source = source.join(
            tables[-1], and_(*where(tables, join.on)), isouter=join.isouter
        )
    if columns:
        selected = [
            column(tables, item).label(item)
            if isinstance(item, str)
            else column(tables, item.column).label(item.label)
            for item in columns
        ]
    else:
        selected = [
            item.label(f"{item.table.name}.{item.name}" if joins else item.name)
            for item in (item for target in tables for item in target.c)
        ]
    statement = (
        select(*selected)
        .select_from(source)
        .where(*where(tables, conditions or []))
        .order_by(
            *(
                column(tables, item.column).desc()
                if item.direction == "desc"
                else column(tables, item.column).asc()
                for item in order_by or []
            )
        )
        .limit(limit)
        .offset(offset)
    )
    return statement.with_for_update() if for_update else statement


def insertion(
    table: Table,
    values: dict[str, Value],
    on_duplicate_key_update: dict[str, Value] | None,
) -> Insert:
    if not values:
        raise ToolError("Insert needs at least one value.")
    statement = insert(table).values(assignments(table, values))
    if on_duplicate_key_update:
        statement = statement.on_duplicate_key_update(
            assignments(table, on_duplicate_key_update, statement.inserted)
        )
    return statement


def modification(
    table: Table, values: dict[str, Value], conditions: list[Condition]
) -> Update:
    if not values or not conditions:
        raise ToolError("Update needs values and a where condition.")
    return (
        update(table)
        .where(*where([table], conditions))
        .values(assignments(table, values))
    )


def deletion(table: Table, conditions: list[Condition]) -> Delete:
    if not conditions:
        raise ToolError("Delete needs a where condition.")
    return delete(table).where(*where([table], conditions))
