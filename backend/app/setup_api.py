"""Company master data from the "Configurações por Empresa" tabs: Tabela Salarial,
Quadro de Vagas, Posição, Vale-Transporte, plus the Organograma built from the hierarchy."""
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Activity, Company, Department, Employee, HeadcountPlan, JobPosition, Position, SalaryRange, TransportLine, User
from app.processes import headcount_status
from app.security import current_user, require_roles

router = APIRouter(prefix="/api/companies/{company_id}/setup", tags=["Configurações por empresa"])
MANAGERS = ("system_admin", "consultant", "hr_manager")

class SalaryRangeInput(BaseModel):
    position_id: int
    level: str = Field(min_length=1, max_length=20)
    min_salary: float = Field(gt=0, le=10000000)
    max_salary: float = Field(gt=0, le=10000000)

    @model_validator(mode="after")
    def check(self):
        if self.min_salary > self.max_salary:
            raise ValueError("O salário mínimo da faixa não pode superar o máximo.")
        return self

class HeadcountInput(BaseModel):
    department_id: int
    position_id: int
    planned: int = Field(ge=0, le=100000)

class JobPositionInput(BaseModel):
    department_id: int
    position_id: int
    quantity: int = Field(default=1, ge=1, le=50)

class AssignInput(BaseModel):
    employee_id: int | None = None

class TransportLineInput(BaseModel):
    code: str = Field(min_length=1, max_length=30)
    description: str = Field(min_length=2, max_length=180)
    fare: float = Field(gt=0, le=1000)
    active: bool = True

def company_for(db: Session, user: User, company_id: int) -> Company:
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404, "Empresa não encontrada")
    if user.role == "hr_manager" and user.company_id != company_id:
        raise HTTPException(403, "Acesso restrito à empresa vinculada ao seu perfil")
    return company

def check_owned(db: Session, company_id: int, **items):
    models = {"department_id": Department, "position_id": Position, "employee_id": Employee}
    for key, value in items.items():
        row = db.get(models[key], value) if value else None
        if value and (not row or row.company_id != company_id):
            raise HTTPException(422, f"{key}: item não pertence à empresa")

def commit(db: Session, message: str):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, message) from exc

def names(db: Session, company_id: int):
    return ({d.id: d.name for d in db.scalars(select(Department).where(Department.company_id == company_id))},
            {p.id: p.title for p in db.scalars(select(Position).where(Position.company_id == company_id))})

# Tabela Salarial
@router.get("/salary-ranges")
def list_ranges(company_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id); _, positions = names(db, company_id)
    return [{"id": r.id, "position_id": r.position_id, "position": positions.get(r.position_id), "level": r.level, "min_salary": float(r.min_salary), "max_salary": float(r.max_salary)}
            for r in db.scalars(select(SalaryRange).where(SalaryRange.company_id == company_id).order_by(SalaryRange.position_id, SalaryRange.min_salary))]

@router.put("/salary-ranges")
def save_range(company_id: int, payload: SalaryRangeInput, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company = company_for(db, user, company_id); check_owned(db, company_id, position_id=payload.position_id)
    row = db.scalar(select(SalaryRange).where(SalaryRange.position_id == payload.position_id, SalaryRange.level == payload.level.strip()))
    if not row:
        row = SalaryRange(company_id=company_id, position_id=payload.position_id, level=payload.level.strip(), min_salary=0, max_salary=0); db.add(row)
    row.min_salary, row.max_salary = Decimal(str(payload.min_salary)), Decimal(str(payload.max_salary))
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"Tabela salarial atualizada em {company.trade_name} (nível {row.level})."))
    commit(db, "Faixa salarial já cadastrada")
    return {"id": row.id}

@router.delete("/salary-ranges/{range_id}", status_code=204)
def delete_range(company_id: int, range_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id)
    row = db.get(SalaryRange, range_id)
    if not row or row.company_id != company_id: raise HTTPException(404, "Faixa não encontrada")
    db.delete(row); db.commit()

# Quadro de Vagas
@router.get("/headcount")
def list_headcount(company_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id); departments, positions = names(db, company_id)
    out = []
    for plan in db.scalars(select(HeadcountPlan).where(HeadcountPlan.company_id == company_id)):
        status = headcount_status(db, company_id, plan.department_id, plan.position_id)
        out.append({"id": plan.id, "department_id": plan.department_id, "department": departments.get(plan.department_id), "position_id": plan.position_id, "position": positions.get(plan.position_id), **status,
                    "available": max(0, plan.planned - status["occupied"] - status["pending_requisitions"] - status["open_positions"])})
    return sorted(out, key=lambda x: (x["department"] or "", x["position"] or ""))

@router.put("/headcount")
def save_headcount(company_id: int, payload: HeadcountInput, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id); check_owned(db, company_id, department_id=payload.department_id, position_id=payload.position_id)
    row = db.scalar(select(HeadcountPlan).where(HeadcountPlan.department_id == payload.department_id, HeadcountPlan.position_id == payload.position_id))
    if not row:
        row = HeadcountPlan(company_id=company_id, department_id=payload.department_id, position_id=payload.position_id, planned=0); db.add(row)
    row.planned = payload.planned
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"Quadro de vagas ajustado para {payload.planned} posição(ões)."))
    commit(db, "Quadro de vagas já cadastrado")
    return {"id": row.id}

# Posições
@router.get("/positions")
def list_positions(company_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id); departments, positions = names(db, company_id)
    employees = {e.id: e.full_name for e in db.scalars(select(Employee).where(Employee.company_id == company_id))}
    return [{"id": p.id, "code": p.code, "department_id": p.department_id, "department": departments.get(p.department_id), "position_id": p.position_id, "position": positions.get(p.position_id),
             "status": p.status, "employee_id": p.employee_id, "employee_name": employees.get(p.employee_id), "requisition_id": p.requisition_id}
            for p in db.scalars(select(JobPosition).where(JobPosition.company_id == company_id).order_by(JobPosition.code))]

@router.post("/positions", status_code=201)
def create_positions(company_id: int, payload: JobPositionInput, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id); check_owned(db, company_id, department_id=payload.department_id, position_id=payload.position_id)
    count = db.scalar(select(func.count(JobPosition.id)).where(JobPosition.company_id == company_id)) or 0
    codes = []
    for _ in range(payload.quantity):
        count += 1
        code = f"POS-{count:05d}"
        while db.scalar(select(JobPosition.id).where(JobPosition.company_id == company_id, JobPosition.code == code)):
            count += 1; code = f"POS-{count:05d}"
        db.add(JobPosition(company_id=company_id, code=code, department_id=payload.department_id, position_id=payload.position_id, status="vacant")); db.flush(); codes.append(code)
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"{len(codes)} posição(ões) criada(s)."))
    commit(db, "Código de posição duplicado")
    return {"codes": codes}

@router.patch("/positions/{position_id}")
def assign_position(company_id: int, position_id: int, payload: AssignInput, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id)
    seat = db.get(JobPosition, position_id)
    if not seat or seat.company_id != company_id: raise HTTPException(404, "Posição não encontrada")
    if payload.employee_id:
        employee = db.get(Employee, payload.employee_id)
        if not employee or employee.company_id != company_id or employee.status == "Desligado": raise HTTPException(422, "Colaborador inválido")
        if (employee.department_id, employee.position_id) != (seat.department_id, seat.position_id): raise HTTPException(422, "O colaborador deve ter o mesmo departamento e cargo da posição")
        if seat.employee_id and seat.employee_id != employee.id: raise HTTPException(409, "Posição já ocupada")
        previous = db.scalar(select(JobPosition).where(JobPosition.employee_id == employee.id))
        if previous and previous.id != seat.id: previous.employee_id, previous.status = None, "vacant"
        seat.employee_id, seat.status = employee.id, "occupied"
    else:
        seat.employee_id, seat.status = None, "vacant"
    commit(db, "Colaborador já ocupa outra posição")
    return {"id": seat.id, "status": seat.status}

@router.delete("/positions/{position_id}", status_code=204)
def delete_position(company_id: int, position_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id)
    seat = db.get(JobPosition, position_id)
    if not seat or seat.company_id != company_id: raise HTTPException(404, "Posição não encontrada")
    if seat.employee_id: raise HTTPException(409, "Desocupe a posição antes de excluí-la")
    db.delete(seat); db.commit()

# Vale-transporte
@router.get("/transport-lines")
def list_lines(company_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    company_for(db, user, company_id)
    if user.role == "employee" and user.company_id != company_id: raise HTTPException(403, "Acesso restrito")
    return [{"id": l.id, "code": l.code, "description": l.description, "fare": float(l.fare), "active": l.active} for l in db.scalars(select(TransportLine).where(TransportLine.company_id == company_id).order_by(TransportLine.code))]

@router.put("/transport-lines")
def save_line(company_id: int, payload: TransportLineInput, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    company_for(db, user, company_id)
    row = db.scalar(select(TransportLine).where(TransportLine.company_id == company_id, TransportLine.code == payload.code.strip().upper()))
    if not row:
        row = TransportLine(company_id=company_id, code=payload.code.strip().upper(), description="", fare=0); db.add(row)
    row.description, row.fare, row.active = payload.description.strip(), Decimal(str(payload.fare)), payload.active
    commit(db, "Linha já cadastrada")
    return {"id": row.id}

# Organograma
@router.get("/org-chart")
def org_chart(company_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    company_for(db, user, company_id)
    if user.role == "employee" and user.company_id != company_id: raise HTTPException(403, "Acesso restrito")
    departments, positions = names(db, company_id)
    rows = db.scalars(select(Employee).where(Employee.company_id == company_id, Employee.status != "Desligado").order_by(Employee.full_name)).all()
    ids = {r.id for r in rows}
    nodes = {r.id: {"id": r.id, "name": r.full_name, "position": positions.get(r.position_id), "department": departments.get(r.department_id), "status": r.status, "children": []} for r in rows}
    roots = []
    for r in rows:
        (nodes[r.manager_id]["children"] if r.manager_id in ids else roots).append(nodes[r.id])
    return {"roots": roots, "total": len(rows)}

# Grupos de colaboradores (participações adicionais) e tipos de cargo
class MembershipInput(BaseModel):
    employee_id: int

class JobTypeInput(BaseModel):
    position_id: int
    job_type: str = Field(min_length=1, max_length=20)

@router.get("/collaborator-groups")
def list_collaborator_groups(company_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    from app.models import EmployeeGroup, EmployeeGroupMembership
    company_for(db, user, company_id)
    out = []
    for group in db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id == company_id).order_by(EmployeeGroup.name)):
        primary = db.scalars(select(Employee).where(Employee.group_id == group.id, Employee.status != "Desligado").order_by(Employee.full_name)).all()
        extra = db.scalars(select(Employee).join(EmployeeGroupMembership, EmployeeGroupMembership.employee_id == Employee.id).where(EmployeeGroupMembership.group_id == group.id).order_by(Employee.full_name)).all()
        out.append({"id": group.id, "name": group.name, "primary_count": len(primary), "members": [{"id": e.id, "name": e.full_name, "primary": True} for e in primary] + [{"id": e.id, "name": e.full_name, "primary": False} for e in extra]})
    return out

@router.post("/collaborator-groups/{group_id}/members", status_code=201)
def add_member(company_id: int, group_id: int, payload: MembershipInput, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    from app.models import EmployeeGroup, EmployeeGroupMembership
    company_for(db, user, company_id)
    group, employee = db.get(EmployeeGroup, group_id), db.get(Employee, payload.employee_id)
    if not group or group.company_id != company_id or not employee or employee.company_id != company_id:
        raise HTTPException(422, "Grupo e colaborador devem pertencer à empresa")
    if employee.group_id == group.id:
        raise HTTPException(409, "Este já é o grupo principal do colaborador")
    db.add(EmployeeGroupMembership(employee_id=employee.id, group_id=group.id))
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"{employee.full_name} incluído(a) no grupo de colaboradores {group.name}."))
    commit(db, "O colaborador já participa deste grupo")
    return {"ok": True}

@router.delete("/collaborator-groups/{group_id}/members/{employee_id}", status_code=204)
def remove_member(company_id: int, group_id: int, employee_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    from app.models import EmployeeGroupMembership
    company_for(db, user, company_id)
    row = db.scalar(select(EmployeeGroupMembership).where(EmployeeGroupMembership.group_id == group_id, EmployeeGroupMembership.employee_id == employee_id))
    if not row:
        raise HTTPException(404, "Participação não encontrada (o grupo principal é alterado pela Movimentação)")
    db.delete(row); db.commit()

@router.get("/job-types")
def list_job_types(company_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    from app.policies import JOB_TYPES
    company_for(db, user, company_id)
    return {"job_types": list(JOB_TYPES), "positions": [{"id": p.id, "title": p.title, "job_type": p.job_type} for p in db.scalars(select(Position).where(Position.company_id == company_id).order_by(Position.title))]}

@router.put("/job-types")
def save_job_type(company_id: int, payload: JobTypeInput, db: Session = Depends(get_db), user: User = Depends(require_roles(*MANAGERS))):
    from app.policies import JOB_TYPES
    company_for(db, user, company_id); check_owned(db, company_id, position_id=payload.position_id)
    if payload.job_type not in JOB_TYPES:
        raise HTTPException(422, f"Tipo de cargo deve ser um de: {', '.join(JOB_TYPES)}")
    position = db.get(Position, payload.position_id)
    position.job_type = payload.job_type
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"Cargo {position.title}: tipo de cargo {payload.job_type}."))
    db.commit()
    return {"id": position.id, "job_type": position.job_type}
