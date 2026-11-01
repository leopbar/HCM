"""Company access groups and individual user accounts."""
from alembic import op
import sqlalchemy as sa
from app.migration_utils import add_column, create_foreign_key, create_index, create_table, has_table

revision = "0007_access_management"
down_revision = "0006_catalog_versions"
branch_labels = None
depends_on = None

def upgrade():
    create_table("access_groups", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False), sa.Column("name", sa.String(120), nullable=False), sa.Column("modules", sa.JSON(), nullable=False), sa.UniqueConstraint("company_id", "name"))
    create_index("ix_access_groups_company_id", "access_groups", ["company_id"])
    add_column("users", sa.Column("access_group_id", sa.Integer(), nullable=True))
    create_foreign_key("fk_users_access_group", "users", "access_groups", ["access_group_id"], ["id"])

def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_access_group", type_="foreignkey")
        batch.drop_column("access_group_id")
    if has_table("access_groups"):
        op.drop_table("access_groups")
