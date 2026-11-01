"""Admission, timekeeping, demo payroll and benefits modules.

Revision ID: 0003_hr_modules
Revises: 0002_vacation_configuration
"""
from alembic import op
from app.models import Base

revision = "0003_hr_modules"
down_revision = "0002_vacation_configuration"
branch_labels = None
depends_on = None

def upgrade():
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)

def downgrade():
    bind=op.get_bind()
    for table in ("employee_benefits","benefit_catalog","payroll_simulations","payroll_events","time_records","admission_requests","hr_module_configuration_history","hr_module_configurations"):
        op.drop_table(table)
