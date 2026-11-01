"""Ensure one account per employee."""
from alembic import op
from app.migration_utils import create_index
revision = "0008_unique_employee_access"
down_revision = "0007_access_management"
branch_labels = None
depends_on = None

def upgrade():
    create_index("uq_users_employee_access", "users", ["employee_id"], unique=True)

def downgrade():
    op.drop_index("uq_users_employee_access", table_name="users")
