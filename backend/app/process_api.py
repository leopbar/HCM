import os
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import AccessGroup, Company, Department, Dependent, Employee, EmployeeGroup, Notification, PayrollEvent, Position, ProcessRequest, TransportLine, User
from app.policies import DEFAULTS, FORM_PROCESSES, LABELS, PROCESS_KEYS, active_config, field_catalog
from app.processes import (APPROVER_LABELS, PROCESSES, allowed_target_ids, can_act, can_view, cancel, check_target, decide, recess_balance, request_out,
    start, validate, visible_requests_query)
from app.security import current_user, require_roles

router = APIRouter(prefix="/api", tags=["Processos parametrizáveis"])
FORM_PROCESSES = tuple(k for k, v in PROCESSES.items() if v["form"])

class ProcessInput(BaseModel):
    process: Literal[FORM_PROCESSES]
    company_id: int
    employee_id: int | None = None
    data: dict = Field(default_factory=dict)

class DecisionInput(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)

def _company(db: Session, user: User, company_id: int) -> Company:
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(404, "Empresa não encontrada")
    if user.role in ("hr_manager", "employee") and user.company_id != company_id:
        raise HTTPException(403, "Acesso restrito à empresa vinculada ao seu perfil")
    return company

def _check_group_module(db: Session, user: User):
    if user.access_group_id:
        group = db.get(AccessGroup, user.access_group_id)
        if group and "processes" not in group.modules:
            raise HTTPException(403, "Seu grupo de acesso não permite processos de autoatendimento")

def _resolve(db: Session, user: User, payload: ProcessInput, simulation: bool = False) -> tuple[Company, Employee | None]:
    company = _company(db, user, payload.company_id)
    _check_group_module(db, user)
    definition = PROCESSES[payload.process]
    employee = None
    if definition["target"] == "employee":
        employee = db.get(Employee, payload.employee_id or 0)
        if not employee or employee.company_id != company.id:
            raise HTTPException(422, "Selecione um colaborador da empresa")
        if not (simulation and user.role == "consultant"):
            check_target(db, user, payload.process, employee)
    else:
        if user.role == "consultant" and not simulation:
            raise HTTPException(403, "O consultor parametriza e simula; requisições são abertas pelo RH ou gestor.")
        if user.role == "employee" and not allowed_target_ids(db, user, payload.process):
            raise HTTPException(403, "Requisição disponível a gestores com subordinados, RH e administradores.")
    return company, employee

@router.get("/processes/catalog")
def catalog(company_id: int | None = None, environment: str = "production", db: Session = Depends(get_db), user: User = Depends(current_user)):
    modules = [] if user.role == "employee" else [{"module": key, "label": LABELS.get(key, key), "fields": field_catalog(key), "defaults": DEFAULTS[key]} for key in DEFAULTS]
    additional = {}
    if company_id:
        _company(db, user, company_id)
        if user.role in ("employee", "hr_manager") or environment not in ("homologation", "production"):
            environment = "production"
        additional = active_config(db, company_id, "process_fields", environment)
    processes = [{"process": key, "label": value["label"], "target": value["target"], "self_service": value["self_service"], "form": value["form"], "fields": value["fields"],
                  "additional_fields": additional.get(key, [])} for key, value in PROCESSES.items()]
    return {"processes": processes, "modules": modules, "approvers": APPROVER_LABELS, "process_keys": PROCESS_KEYS, "form_processes": list(FORM_PROCESSES)}

@router.get("/processes/targets")
def targets(process: str, company_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if process not in PROCESSES:
        raise HTTPException(404, "Processo desconhecido")
    _company(db, user, company_id)
    allowed = allowed_target_ids(db, user, process)
    query = select(Employee).where(Employee.company_id == company_id, Employee.status != "Desligado").order_by(Employee.full_name)
    rows = db.scalars(query).all()
    if allowed is not None:
        rows = [r for r in rows if r.id in allowed]
    return [{"id": r.id, "full_name": r.full_name, "registration": r.registration, "contract_type": r.contract_type, "is_self": r.id == user.employee_id} for r in rows]

@router.get("/processes/options")
def options(company_id: int, employee_id: int | None = None, target_company_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Lookup values for the generic process forms."""
    _company(db, user, company_id)
    def catalog_of(cid):
        return {"departments": [{"id": d.id, "name": d.name} for d in db.scalars(select(Department).where(Department.company_id == cid).order_by(Department.name))],
                "positions": [{"id": p.id, "name": p.title} for p in db.scalars(select(Position).where(Position.company_id == cid).order_by(Position.title))],
                "groups": [{"id": g.id, "name": g.name} for g in db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id == cid).order_by(EmployeeGroup.name))]}
    out = catalog_of(company_id)
    if user.role != "employee":
        out["employees"] = [{"id": e.id, "name": f"{e.full_name} ({e.registration})"} for e in db.scalars(select(Employee).where(Employee.company_id == company_id, Employee.status != "Desligado").order_by(Employee.full_name))]
        out["companies"] = [{"id": c.id, "name": c.trade_name} for c in db.scalars(select(Company).where(Company.id != company_id, Company.status == "Ativa").order_by(Company.trade_name))]
        out["payroll_events"] = [{"id": e.code, "name": f"{e.code} - {e.description}"} for e in db.scalars(select(PayrollEvent).where(PayrollEvent.company_id == company_id, PayrollEvent.environment == "production", PayrollEvent.active.is_(True)).order_by(PayrollEvent.code))]
    else:
        allowed = allowed_target_ids(db, user, "movement") or set()
        out["employees"] = [{"id": e.id, "name": e.full_name} for e in db.scalars(select(Employee).where(Employee.id.in_(allowed | {user.employee_id or 0})))]
    if target_company_id and user.role != "employee":
        out["target"] = catalog_of(target_company_id)
    out["transport_lines"] = [{"id": l.id, "name": f"{l.code} - {l.description} (R$ {float(l.fare):.2f})"} for l in db.scalars(select(TransportLine).where(TransportLine.company_id == company_id, TransportLine.active.is_(True)).order_by(TransportLine.code))]
    if employee_id:
        employee = db.get(Employee, employee_id)
        if employee and employee.company_id == company_id:
            check_target(db, user, "dependents", employee) if user.role == "employee" else None
            out["dependents"] = [{"id": d.id, "name": d.full_name} for d in db.scalars(select(Dependent).where(Dependent.employee_id == employee.id, Dependent.active.is_(True)))]
            out["recess_balance"] = recess_balance(db, employee, active_config(db, company_id, "recess")) if employee.contract_type == "Estágio" else None
            out["employee"] = {"contract_type": employee.contract_type, "phone": employee.phone, "personal_email": employee.personal_email, "marital_status": employee.marital_status, "address": employee.address, "emergency_contact": employee.emergency_contact}
    return out

@router.post("/processes/simulate")
def simulate(payload: ProcessInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Validation only: nothing is recorded. The consultant uses it to test the parametrization."""
    company, employee = _resolve(db, user, payload, simulation=True)
    data = validate(db, payload.process, company.id, employee, payload.data, user)
    from app.processes import resolve_steps
    steps, _ = resolve_steps(db, company.id, payload.process, employee, user)
    return {"allowed": True, "data": data, "steps": [{**s, "approver_label": APPROVER_LABELS[s["approver"]]} for s in steps]}

@router.post("/processes/requests", status_code=201)
def create_request(payload: ProcessInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    company, employee = _resolve(db, user, payload)
    if employee and payload.process in ("termination", "transfer", "movement") and db.scalar(select(ProcessRequest.id).where(ProcessRequest.process.in_(("termination", "transfer")), ProcessRequest.employee_id == employee.id, ProcessRequest.status == "pending_approval").limit(1)):
        raise HTTPException(409, "Já existe desligamento ou transferência em andamento para este colaborador.")
    data = validate(db, payload.process, company.id, employee, payload.data, user)
    req = start(db, payload.process, company.id, employee, user, data)
    db.commit(); db.refresh(req)
    return request_out(db, req, user, detail=True)

@router.get("/processes/requests")
def list_requests(scope: Literal["mine", "tasks", "all"] = "all", process: str | None = None, status: str | None = None, company_id: int | None = None, overdue: bool = False,
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    if scope == "tasks":
        query = select(ProcessRequest).where(ProcessRequest.status == "pending_approval")
    elif scope == "mine":
        query = select(ProcessRequest).where(ProcessRequest.requested_by_id == user.id)
    else:
        query = visible_requests_query(user)
    if process: query = query.where(ProcessRequest.process == process)
    if status: query = query.where(ProcessRequest.status == status)
    if company_id:
        if user.role in ("hr_manager", "employee") and company_id != user.company_id:
            raise HTTPException(403, "Acesso restrito à empresa vinculada ao seu perfil")
        query = query.where(ProcessRequest.company_id == company_id)
    rows = db.scalars(query.order_by(ProcessRequest.created_at.desc(), ProcessRequest.id.desc()).limit(300)).all()
    if scope == "tasks":
        rows = [r for r in rows if can_act(db, user, r)]
    else:
        rows = [r for r in rows if can_view(db, user, r)]
    out = [request_out(db, r, user) for r in rows]
    return [r for r in out if r["overdue"]] if overdue else out

@router.get("/processes/requests/{request_id}")
def get_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = db.get(ProcessRequest, request_id)
    if not req or not can_view(db, user, req):
        raise HTTPException(404, "Solicitação não encontrada")
    return request_out(db, req, user, detail=True)

@router.post("/processes/requests/{request_id}/decision")
def decide_request(request_id: int, payload: DecisionInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = db.scalar(select(ProcessRequest).where(ProcessRequest.id == request_id).with_for_update())
    if not req or not can_view(db, user, req) and not can_act(db, user, req):
        raise HTTPException(404, "Solicitação não encontrada")
    decide(db, req, user, payload.approved, payload.comment)
    db.commit(); db.refresh(req)
    return request_out(db, req, user, detail=True)

@router.post("/processes/requests/{request_id}/cancel")
def cancel_request(request_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    req = db.get(ProcessRequest, request_id)
    if not req or not can_view(db, user, req):
        raise HTTPException(404, "Solicitação não encontrada")
    cancel(db, req, user)
    db.commit(); db.refresh(req)
    return request_out(db, req, user, detail=True)

@router.get("/processes/sla")
def sla_report(company_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("system_admin", "consultant", "hr_manager"))):
    """Gerenciador de processos: volume, pendências e atrasos por processo."""
    _company(db, user, company_id)
    rows = db.scalars(select(ProcessRequest).where(ProcessRequest.company_id == company_id)).all()
    report = {}
    for row in rows:
        item = report.setdefault(row.process, {"process": row.process, "label": PROCESSES[row.process]["label"], "total": 0, "pending": 0, "overdue": 0, "approved": 0, "rejected": 0, "cancelled": 0})
        item["total"] += 1
        out = request_out(db, row)
        if row.status == "pending_approval":
            item["pending"] += 1
            item["overdue"] += out["overdue"]
        elif row.status in ("approved", "scheduled"):
            item["approved"] += 1
        elif row.status in item:
            item[row.status] += 1
    return sorted(report.values(), key=lambda x: x["label"])

@router.get("/notifications")
def notifications(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(50)).all()
    return [{"id": n.id, "message": n.message, "request_id": n.request_id, "read": bool(n.read_at), "email_status": n.email_status, "created_at": n.created_at.isoformat() if n.created_at else None} for n in rows]

@router.post("/notifications/read")
def mark_read(db: Session = Depends(get_db), user: User = Depends(current_user)):
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None)).values(read_at=datetime.now(timezone.utc)))
    db.commit()
    return {"ok": True}


@router.get("/system/email")
def email_status(user: User = Depends(require_roles("system_admin", "consultant"))):
    """Guia Servidor de E-mail: o servidor é definido no .env (as credenciais nunca saem do servidor)."""
    host = os.getenv("SMTP_HOST", "")
    return {"configured": bool(host), "host": host or None, "port": int(os.getenv("SMTP_PORT", "587")), "sender": os.getenv("SMTP_FROM") or None,
            "starttls": os.getenv("SMTP_STARTTLS", "true").lower() == "true", "authenticated": bool(os.getenv("SMTP_USER"))}
