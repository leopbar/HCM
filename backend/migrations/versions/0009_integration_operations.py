"""Audited, idempotent integration execution and reversible version withdrawal."""
from alembic import op
import sqlalchemy as sa
from app.migration_utils import add_column, create_index, create_table
revision = "0009_integration_operations"
down_revision = "0008_unique_employee_access"
branch_labels = None
depends_on = None

def upgrade():
    add_column("vacation_configurations", sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()))
    create_table("integration_operations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("approved_by", sa.String(100), nullable=False),
        *[sa.Column(key, sa.JSON(), nullable=False) for key in ("payload", "before_snapshot", "after_snapshot", "report", "created_ids")],
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    create_index("ix_integration_operations_company_id", "integration_operations", ["company_id"])

def downgrade():
    op.drop_table("integration_operations")
    op.drop_column("vacation_configurations", "revoked")
