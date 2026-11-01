"""Independent homologation/production configurations and approval flow.

Revision ID: 0004_environment_publication
Revises: 0003_hr_modules
"""
from alembic import op
from sqlalchemy import Column, String, inspect, text
from app.models import Base

revision = "0004_environment_publication"
down_revision = "0003_hr_modules"
branch_labels = None
depends_on = None

COLUMNS={
    "vacation_configurations":("environment",),
    "vacation_configuration_history":("environment",),
    "hr_module_configurations":("environment",),
    "hr_module_configuration_history":("environment",),
    "payroll_events":("environment",),
    "payroll_simulations":("environment",),
    "benefit_catalog":("environment",),
}

def _add_column_if_missing(table,name):
    inspector=inspect(op.get_bind())
    if name not in {c["name"] for c in inspector.get_columns(table)}:
        op.add_column(table,Column(name,String(20),nullable=False,server_default=text("'production'")))

def _replace_unique(table,old_name,new_name,columns):
    constraints={c.get("name") for c in inspect(op.get_bind()).get_unique_constraints(table)}
    if old_name in constraints: op.drop_constraint(old_name,table,type_="unique")
    constraints={c.get("name") for c in inspect(op.get_bind()).get_unique_constraints(table)}
    if new_name not in constraints: op.create_unique_constraint(new_name,table,columns)

def upgrade():
    for table,names in COLUMNS.items():
        for name in names: _add_column_if_missing(table,name)
    _replace_unique("vacation_configurations","uq_vacation_config_scope_version","uq_vacation_config_scope_version_env",["scope_key","version","environment"])
    _replace_unique("hr_module_configurations","uq_hr_module_company_version","uq_hr_module_company_version_env",["company_id","module","version","environment"])
    _replace_unique("payroll_events","uq_payroll_event_company_code","uq_payroll_event_company_env_code",["company_id","environment","code"])
    _replace_unique("benefit_catalog","uq_benefit_company_code","uq_benefit_company_env_code",["company_id","environment","code"])
    indexes={index["name"] for index in inspect(op.get_bind()).get_indexes("vacation_configurations")}
    if "ix_vacation_config_company_effective" in indexes:
        op.drop_index("ix_vacation_config_company_effective",table_name="vacation_configurations")
    op.create_index("ix_vacation_config_company_effective","vacation_configurations",["company_id","environment","effective_from"])
    for table in ("vacation_configurations","vacation_configuration_history","hr_module_configurations","hr_module_configuration_history","payroll_events","payroll_simulations","benefit_catalog"):
        name=f"ix_{table}_environment"
        existing={i["name"] for i in inspect(op.get_bind()).get_indexes(table)}
        if name not in existing: op.create_index(name,table,["environment"])
    Base.metadata.create_all(bind=op.get_bind(),checkfirst=True)

def downgrade():
    bind=op.get_bind()
    if "environment_publications" in inspect(bind).get_table_names(): op.drop_table("environment_publications")
