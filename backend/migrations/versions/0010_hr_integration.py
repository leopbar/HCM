"""Reversible HR configuration versions."""
from alembic import op
import sqlalchemy as sa
from app.migration_utils import add_column
revision = "0010_hr_integration"
down_revision = "0009_integration_operations"
branch_labels = None
depends_on = None
def upgrade():
    add_column("hr_module_configurations", sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()))
def downgrade():
    op.drop_column("hr_module_configurations", "revoked")
