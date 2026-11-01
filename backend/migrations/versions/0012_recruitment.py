"""Recruitment and selection: vacancies, talent pool, applications, evaluations and interviews.

Revision ID: 0012_recruitment
Revises: 0011_security_and_processes
"""
from alembic import op
import sqlalchemy as sa
from app.migration_utils import add_column, create_foreign_key
from app.models import Base

revision = "0012_recruitment"
down_revision = "0011_security_and_processes"
branch_labels = None
depends_on = None

NEW_TABLES = ("vacancies", "candidates", "applications", "application_events", "interviews")


def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables[name] for name in NEW_TABLES], checkfirst=True)
    add_column("admission_requests", sa.Column("application_id", sa.Integer(), nullable=True))
    create_foreign_key("fk_admission_application", "admission_requests", "applications", ["application_id"], ["id"], ondelete="SET NULL")


def downgrade():
    op.drop_constraint("fk_admission_application", "admission_requests", type_="foreignkey")
    op.drop_column("admission_requests", "application_id")
    for name in reversed(NEW_TABLES):
        op.drop_table(name)
