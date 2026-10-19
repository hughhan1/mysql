from typing import Annotated, Literal

from pydantic import BaseModel, Field

NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"
QUALIFIED = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$"

Name = Annotated[str, Field(pattern=NAME)]
ColumnName = Annotated[str, Field(pattern=QUALIFIED)]
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

    column: ColumnName


class InsertedRef(BaseModel):
    model_config = {"extra": "forbid"}

    inserted: Name


class Expression(BaseModel):
    model_config = {"extra": "forbid"}

    sql: str
    parameters: dict[str, Scalar] | None = None


Value = Scalar | ColumnRef | InsertedRef | Expression


class Condition(BaseModel):
    model_config = {"extra": "forbid"}

    column: ColumnName
    op: Operator
    value: Scalar | ColumnRef | Expression | list[Scalar] = None


class Or(BaseModel):
    model_config = {"extra": "forbid"}

    or_: list[Condition]


Predicate = Condition | Or


class LabeledColumn(BaseModel):
    model_config = {"extra": "forbid"}

    column: ColumnName
    label: Name


class OrderBy(BaseModel):
    model_config = {"extra": "forbid"}

    column: ColumnName
    direction: Literal["asc", "desc"] = "asc"


class Join(BaseModel):
    model_config = {"extra": "forbid"}

    table: Name
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
