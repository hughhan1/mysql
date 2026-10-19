from typing import Literal

from pydantic import BaseModel

Scalar = str | int | float | bool | None
Operator = Literal[
    "eq",
    "ne",
    "lt",
    "le",
    "gt",
    "ge",
    "in",
    "not_in",
    "like",
    "not_like",
    "is_null",
    "is_not_null",
]


class ColumnRef(BaseModel):
    model_config = {"extra": "forbid"}

    column: str


class InsertedRef(BaseModel):
    model_config = {"extra": "forbid"}

    inserted: str


class Expression(BaseModel):
    model_config = {"extra": "forbid"}

    sql: str
    parameters: dict[str, Scalar] | None = None


Value = Scalar | ColumnRef | InsertedRef | Expression


class Condition(BaseModel):
    model_config = {"extra": "forbid"}

    column: str
    op: Operator
    value: Scalar | ColumnRef | Expression | list[Scalar] = None


class Or(BaseModel):
    model_config = {"extra": "forbid"}

    or_: list[Condition]


Predicate = Condition | Or


class LabeledColumn(BaseModel):
    model_config = {"extra": "forbid"}

    column: str
    label: str


class OrderBy(BaseModel):
    model_config = {"extra": "forbid"}

    column: str
    direction: Literal["asc", "desc"] = "asc"


class Join(BaseModel):
    model_config = {"extra": "forbid"}

    table: str
    on: list[Predicate]
    isouter: bool = False


class Rows(BaseModel):
    rows: list[dict[str, Scalar]]


class Affected(BaseModel):
    rowcount: int


class Transaction(BaseModel):
    transaction: str
    expires_at: str


class Done(BaseModel):
    pass
