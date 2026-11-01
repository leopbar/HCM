"""Idempotent DDL helpers.

Revision 0001 builds the schema from the current models, so on a fresh database later
revisions find their tables and columns already present. Every structural change after
0001 must go through these helpers so that both fresh and upgraded databases converge.
"""
import sqlalchemy as sa
from alembic import op


def _inspector():
    return sa.inspect(op.get_bind())


def has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def has_column(table: str, column: str) -> bool:
    return has_table(table) and column in {c["name"] for c in _inspector().get_columns(table)}


def has_index(table: str, name: str) -> bool:
    return has_table(table) and name in {i["name"] for i in _inspector().get_indexes(table)}


def has_foreign_key(table: str, name: str) -> bool:
    return has_table(table) and name in {fk.get("name") for fk in _inspector().get_foreign_keys(table)}


def create_table(name: str, *columns, **kw):
    if not has_table(name):
        op.create_table(name, *columns, **kw)


def add_column(table: str, column: sa.Column):
    if not has_column(table, column.name):
        op.add_column(table, column)


def create_index(name: str, table: str, columns: list[str], **kw):
    if not has_index(table, name):
        op.create_index(name, table, columns, **kw)


def create_foreign_key(name: str, source: str, referent: str, local: list[str], remote: list[str], **kw):
    if not has_foreign_key(source, name):
        op.create_foreign_key(name, source, referent, local, remote, **kw)
