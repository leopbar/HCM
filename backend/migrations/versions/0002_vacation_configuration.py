"""Vacation configuration, history, requests and balance.

Revision ID: 0002_vacation_configuration
Revises: 0001_initial
"""
from alembic import op
from sqlalchemy import inspect, Integer, Column, text
from app.models import Base

revision = "0002_vacation_configuration"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    bind=op.get_bind()
    # Create the new tables idempotently. The initial migration uses current metadata,
    # so a fresh database may already contain these tables.
    Base.metadata.create_all(bind=bind, checkfirst=True)
    columns={column["name"] for column in inspect(bind).get_columns("employees")}
    if "vacation_balance_days" not in columns:
        op.add_column("employees", Column("vacation_balance_days", Integer(), nullable=False, server_default=text("30")))

def downgrade():
    bind=op.get_bind()
    inspector=inspect(bind)
    if "vacation_requests" in inspector.get_table_names():
        op.drop_table("vacation_requests")
    inspector=inspect(bind)
    if "vacation_configuration_history" in inspector.get_table_names():
        op.drop_table("vacation_configuration_history")
    inspector=inspect(bind)
    if "vacation_configurations" in inspector.get_table_names():
        op.drop_table("vacation_configurations")
    if "vacation_balance_days" in {column["name"] for column in inspect(bind).get_columns("employees")}:
        op.drop_column("employees", "vacation_balance_days")
