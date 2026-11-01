"""Version payroll event and benefit catalog snapshots.

Revision ID: 0006_catalog_versions
Revises: 0005_training_import
"""
from alembic import op
from sqlalchemy import Column, Integer, inspect, text

revision = "0006_catalog_versions"
down_revision = "0005_training_import"
branch_labels = None
depends_on = None


def upgrade():
    inspector = inspect(op.get_bind())
    for table in ("payroll_events", "benefit_catalog"):
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "version" not in columns:
            op.add_column(table, Column("version", Integer(), nullable=False, server_default=text("1")))


def downgrade():
    for table in ("benefit_catalog", "payroll_events"):
        if "version" in {column["name"] for column in inspect(op.get_bind()).get_columns(table)}:
            op.drop_column(table, "version")
