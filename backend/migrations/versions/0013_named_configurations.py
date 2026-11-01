"""Named vacation configurations, multiple group membership, job types and calculation groups.

Revision ID: 0013_named_configurations
Revises: 0012_recruitment
"""
from alembic import op
import sqlalchemy as sa
from app.migration_utils import add_column
from app.models import Base

revision = "0013_named_configurations"
down_revision = "0012_recruitment"
branch_labels = None
depends_on = None


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables["employee_group_memberships"]], checkfirst=True)
    add_column("positions", sa.Column("job_type", sa.String(20), nullable=False, server_default="Mensal"))
    add_column("employees", sa.Column("calculation_group", sa.String(60), nullable=False, server_default="Mensalistas"))
    add_column("vacation_configurations", sa.Column("config_code", sa.String(20), nullable=True))
    add_column("vacation_configurations", sa.Column("name", sa.String(120), nullable=True))
    add_column("vacation_configurations", sa.Column("simulation_text", sa.Text(), nullable=True))
    # Interns are paid through their own calculation group.
    op.execute("UPDATE employees SET calculation_group = 'Estagiários' WHERE contract_type = 'Estágio'")


def downgrade():
    for table, column in (("vacation_configurations", "simulation_text"), ("vacation_configurations", "name"), ("vacation_configurations", "config_code"),
                          ("employees", "calculation_group"), ("positions", "job_type")):
        op.drop_column(table, column)
    op.drop_table("employee_group_memberships")
