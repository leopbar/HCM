"""Grupos de colaboradores: o grupo principal do cadastro mais as participações adicionais.
A ordem (principal primeiro) é usada apenas como desempate; a prioridade efetiva vem da ordem
das atribuições parametrizadas."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Employee, EmployeeGroup, EmployeeGroupMembership


def employee_groups(db: Session, employee: Employee) -> list[EmployeeGroup]:
    rows = [db.get(EmployeeGroup, employee.group_id)] if employee.group_id else []
    extra = db.scalars(select(EmployeeGroup).join(EmployeeGroupMembership, EmployeeGroupMembership.group_id == EmployeeGroup.id)
                       .where(EmployeeGroupMembership.employee_id == employee.id).order_by(EmployeeGroupMembership.id)).all()
    return [g for g in rows + list(extra) if g and g.company_id == employee.company_id]


def employee_group_ids(db: Session, employee: Employee) -> set[int]:
    return {g.id for g in employee_groups(db, employee)}


def employee_group_names(db: Session, employee: Employee) -> list[str]:
    return [g.name.lower() for g in employee_groups(db, employee)]
