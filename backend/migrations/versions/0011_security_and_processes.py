"""Server-side sessions, account lockout and the parameterizable processes.

Revision ID: 0011_security_and_processes
Revises: 0010_hr_integration
"""
from alembic import op
import sqlalchemy as sa
from app.migration_utils import add_column, create_foreign_key
from app.models import Base

revision = "0011_security_and_processes"
down_revision = "0010_hr_integration"
branch_labels = None
depends_on = None

NEW_TABLES = (
    "user_sessions", "time_record_adjustments", "process_requests", "process_actions", "dependents",
    "employee_leaves", "employee_movements", "terminations", "recess_records", "salary_ranges",
    "headcount_plans", "job_positions", "transport_lines", "transport_enrollments", "value_entries",
    "notifications",
)


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables[name] for name in NEW_TABLES], checkfirst=True)
    add_column("users", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
    add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    add_column("users", sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"))
    add_column("users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    add_column("employees", sa.Column("contract_type", sa.String(20), nullable=False, server_default="CLT"))
    for name, kind in (("cpf", sa.String(14)), ("birth_date", sa.Date()), ("phone", sa.String(30)), ("personal_email", sa.String(180)),
                       ("marital_status", sa.String(30)), ("address", sa.String(300)), ("emergency_contact", sa.String(180)), ("termination_date", sa.Date())):
        add_column("employees", sa.Column(name, kind, nullable=True))
    add_column("vacation_requests", sa.Column("cash_bonus_days", sa.Integer(), nullable=False, server_default="0"))
    add_column("vacation_requests", sa.Column("acquisition_start", sa.Date(), nullable=True))
    add_column("vacation_requests", sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True))
    add_column("admission_requests", sa.Column("group_id", sa.Integer(), nullable=True))
    add_column("admission_requests", sa.Column("salary", sa.Numeric(12, 2), nullable=True))
    add_column("admission_requests", sa.Column("registration", sa.String(30), nullable=True))
    add_column("admission_requests", sa.Column("contract_type", sa.String(20), nullable=False, server_default="CLT"))
    add_column("admission_requests", sa.Column("job_position_id", sa.Integer(), nullable=True))
    add_column("admission_requests", sa.Column("employee_id", sa.Integer(), nullable=True))
    create_foreign_key("fk_admission_group", "admission_requests", "employee_groups", ["group_id"], ["id"], ondelete="SET NULL")
    create_foreign_key("fk_admission_job_position", "admission_requests", "job_positions", ["job_position_id"], ["id"], ondelete="SET NULL")
    create_foreign_key("fk_admission_employee", "admission_requests", "employees", ["employee_id"], ["id"], ondelete="SET NULL")
    # Demonstration accounts used a shared, published password. They stay for audit history
    # but can no longer sign in; the administrator is (re)created by app.bootstrap_admin.
    op.execute("UPDATE users SET active = false, must_change_password = true WHERE email IN ("
               "'admin@hcmlab.example.com','consultor@hcmlab.example.com','rh@emp001.example.com',"
               "'rh@emp002.example.com','joao.silva@empresaabc.example.com','colaborador01@emp002.example.com')")


def downgrade():
    for table, columns in (
        ("admission_requests", ("employee_id", "job_position_id", "contract_type", "registration", "salary", "group_id")),
        ("vacation_requests", ("acknowledged_at", "acquisition_start", "cash_bonus_days")),
        ("employees", ("termination_date", "emergency_contact", "address", "marital_status", "personal_email", "phone", "birth_date", "cpf", "contract_type")),
        ("users", ("last_login_at", "locked_until", "failed_login_attempts", "password_changed_at", "must_change_password")),
    ):
        for column in columns:
            op.drop_column(table, column)
    for name in reversed(NEW_TABLES):
        op.drop_table(name)
