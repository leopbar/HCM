"""Workflow engine for the parameterizable self-service processes ("Processos
Parametrizáveis"): selection assignments, approval steps per process and group, SLA in
business hours, task manager, notifications and the effect of each approved process."""
import logging
import os
import smtplib
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from email.message import EmailMessage
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.models import (Activity, AdmissionRequest, Company, Department, Dependent, Employee, EmployeeBenefit, EmployeeGroup,
    EmployeeLeave, EmployeeMovement, HeadcountPlan, JobPosition, Notification, PayrollEvent, Position, ProcessAction,
    ProcessRequest, RecessRecord, SalaryRange, Termination, TransportEnrollment, TransportLine, User, ValueEntry, VacationRequest)
from app.policies import DEFAULTS, LABELS, active_config
from app.validators import format_cpf, valid_cpf
from app.groups import employee_group_ids, employee_group_names

log = logging.getLogger("hcm.processes")
BRT = timezone(timedelta(hours=-3))
HOLIDAYS = {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)}
APPROVER_LABELS = {"direct_manager": "Gestor imediato", "hr": "RH da empresa", "system_admin": "Administrador do sistema", "target_hr": "RH da empresa de destino"}
STATUS_LABELS = {"pending_approval": "Em aprovação", "approved": "Aprovado", "rejected": "Reprovado", "cancelled": "Cancelado", "scheduled": "Agendado"}

LEAVE_KINDS = {
    "atestado_medico": {"label": "Atestado médico (até 15 dias)", "max": 15, "paid": True},
    "auxilio_doenca": {"label": "Afastamento por doença (INSS, acima de 15 dias)", "min": 16, "paid": True},
    "acidente_trabalho": {"label": "Acidente de trabalho", "paid": True},
    "licenca_maternidade": {"label": "Licença-maternidade (120 ou 180 dias)", "exact": (120, 180), "paid": True},
    "licenca_paternidade": {"label": "Licença-paternidade (5 ou 20 dias)", "exact": (5, 20), "paid": True},
    "licenca_casamento": {"label": "Licença casamento (até 3 dias, art. 473 CLT)", "max": 3, "paid": True},
    "licenca_luto": {"label": "Licença nojo/luto (até 2 dias, art. 473 CLT)", "max": 2, "paid": True},
    "falta_justificada": {"label": "Falta justificada", "max": 5, "paid": True},
    "licenca_nao_remunerada": {"label": "Licença não remunerada", "paid": False},
}
TERMINATION_REASONS = {"sem_justa_causa": "Dispensa sem justa causa", "pedido_demissao": "Pedido de demissão", "justa_causa": "Dispensa por justa causa", "acordo": "Acordo entre as partes (art. 484-A)", "termino_contrato": "Término de contrato determinado"}
NOTICE_TYPES = {"trabalhado": "Aviso trabalhado", "indenizado": "Aviso indenizado", "dispensado": "Aviso dispensado"}
RELATIONSHIPS = {"conjuge": "Cônjuge/companheiro(a)", "filho": "Filho(a)", "enteado": "Enteado(a)", "pai_mae": "Pai/Mãe", "tutelado": "Menor sob guarda/tutela", "outro": "Outro"}

def _opts(mapping: dict) -> list[dict]:
    return [{"value": k, "label": (v["label"] if isinstance(v, dict) else v)} for k, v in mapping.items()]

# Form definition for each process; the UI renders these fields generically.
PROCESSES = {
    "vacations": {"label": "Férias", "target": "employee", "self_service": True, "form": False, "fields": []},
    "benefits": {"label": "Benefícios", "target": "employee", "self_service": True, "form": False, "fields": []},
    "dependents": {"label": "Dependentes", "target": "employee", "self_service": True, "form": True, "fields": [
        {"name": "action", "label": "Ação", "type": "select", "required": True, "options": [{"value": "add", "label": "Incluir dependente"}, {"value": "remove", "label": "Excluir dependente"}]},
        {"name": "dependent_id", "label": "Dependente a excluir", "type": "dependent", "show_if": {"action": "remove"}},
        {"name": "full_name", "label": "Nome completo", "type": "text", "show_if": {"action": "add"}},
        {"name": "relationship_type", "label": "Parentesco", "type": "select", "options": _opts(RELATIONSHIPS), "show_if": {"action": "add"}},
        {"name": "birth_date", "label": "Data de nascimento", "type": "date", "show_if": {"action": "add"}},
        {"name": "cpf", "label": "CPF", "type": "text", "show_if": {"action": "add"}},
        {"name": "income_tax_dependent", "label": "Dependente para IRRF", "type": "boolean", "show_if": {"action": "add"}},
        {"name": "health_plan_dependent", "label": "Dependente no plano de saúde", "type": "boolean", "show_if": {"action": "add"}}]},
    "personal_data": {"label": "Alteração de dados pessoais", "target": "employee", "self_service": True, "form": True, "fields": [
        {"name": "phone", "label": "Telefone", "type": "text"}, {"name": "personal_email", "label": "E-mail pessoal", "type": "text"},
        {"name": "marital_status", "label": "Estado civil", "type": "select", "options": _opts({x: x for x in ("Solteiro(a)", "Casado(a)", "União estável", "Divorciado(a)", "Viúvo(a)")})},
        {"name": "address", "label": "Endereço", "type": "text"}, {"name": "emergency_contact", "label": "Contato de emergência", "type": "text"}]},
    "leave": {"label": "Afastamento / Ausência", "target": "employee", "self_service": True, "form": True, "fields": [
        {"name": "kind", "label": "Tipo", "type": "select", "required": True, "options": _opts(LEAVE_KINDS)},
        {"name": "start_date", "label": "Início", "type": "date", "required": True}, {"name": "end_date", "label": "Fim", "type": "date", "required": True},
        {"name": "note", "label": "Observação (não informe CID ou diagnóstico)", "type": "text"}]},
    "movement": {"label": "Movimentação - Movimentar colaborador", "target": "employee", "self_service": False, "form": True, "fields": [
        {"name": "effective_date", "label": "Data de efetivação", "type": "date", "required": True},
        {"name": "department_id", "label": "Novo departamento", "type": "department"}, {"name": "position_id", "label": "Novo cargo", "type": "position"},
        {"name": "group_id", "label": "Novo grupo", "type": "group"}, {"name": "manager_id", "label": "Novo gestor imediato", "type": "employee"},
        {"name": "salary", "label": "Novo salário (R$)", "type": "number", "min": 0}, {"name": "reason", "label": "Motivo", "type": "text", "required": True}]},
    "transfer": {"label": "Movimentação - Ceder / Buscar colaborador", "target": "employee", "self_service": False, "form": True, "fields": [
        {"name": "target_company_id", "label": "Empresa de destino", "type": "company", "required": True},
        {"name": "target_department_id", "label": "Departamento no destino", "type": "target_department", "required": True},
        {"name": "target_position_id", "label": "Cargo no destino", "type": "target_position", "required": True},
        {"name": "target_group_id", "label": "Grupo no destino", "type": "target_group", "required": True},
        {"name": "target_registration", "label": "Nova matrícula", "type": "text", "required": True},
        {"name": "effective_date", "label": "Data da transferência", "type": "date", "required": True}]},
    "requisition": {"label": "Requisição de pessoal", "target": "none", "self_service": False, "form": True, "fields": [
        {"name": "department_id", "label": "Departamento", "type": "department", "required": True}, {"name": "position_id", "label": "Cargo", "type": "position", "required": True},
        {"name": "quantity", "label": "Quantidade de vagas", "type": "number", "required": True, "min": 1, "max": 20},
        {"name": "reason", "label": "Motivo", "type": "select", "required": True, "options": [{"value": "aumento_quadro", "label": "Aumento de quadro"}, {"value": "substituicao", "label": "Substituição"}]},
        {"name": "replaced_employee_id", "label": "Colaborador substituído", "type": "employee", "show_if": {"reason": "substituicao"}},
        {"name": "salary_proposal", "label": "Salário proposto (R$)", "type": "number", "required": True, "min": 0},
        {"name": "justification", "label": "Justificativa", "type": "text", "required": True}]},
    "termination": {"label": "Rescisão / Desligamento", "target": "employee", "self_service": False, "form": True, "fields": [
        {"name": "reason", "label": "Motivo", "type": "select", "required": True, "options": _opts(TERMINATION_REASONS)},
        {"name": "notice_type", "label": "Aviso prévio", "type": "select", "required": True, "options": _opts(NOTICE_TYPES)},
        {"name": "termination_date", "label": "Data do aviso/comunicação", "type": "date", "required": True},
        {"name": "note", "label": "Observação", "type": "text"}]},
    "recess": {"label": "Recesso de estágio", "target": "employee", "self_service": True, "form": True, "fields": [
        {"name": "start_date", "label": "Início", "type": "date", "required": True}, {"name": "days", "label": "Dias", "type": "number", "required": True, "min": 1, "max": 30}]},
    "transport": {"label": "Vale-transporte", "target": "employee", "self_service": True, "form": True, "fields": [
        {"name": "lines", "label": "Linhas e viagens por dia", "type": "transport_lines", "required": True},
        {"name": "opt_out", "label": "Não desejo receber vale-transporte", "type": "boolean"}]},
    "value_entry": {"label": "Lançamento de valores", "target": "employee", "self_service": False, "form": True, "fields": [
        {"name": "competence", "label": "Competência (AAAA-MM)", "type": "month", "required": True},
        {"name": "event_code", "label": "Evento da folha", "type": "payroll_event", "required": True},
        {"name": "amount", "label": "Valor (R$)", "type": "number", "required": True, "min": 0.01},
        {"name": "description", "label": "Descrição", "type": "text"}]},
}

# --------------------------------------------------------------------------- helpers

def now() -> datetime:
    return datetime.now(timezone.utc)

def _date(value, label: str) -> date:
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise HTTPException(422, f"{label}: informe uma data válida.")

def _int(value, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(422, f"{label}: valor inválido.")

def _money(value, label: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except Exception:
        raise HTTPException(422, f"{label}: valor inválido.")
    if amount <= 0:
        raise HTTPException(422, f"{label}: informe um valor positivo.")
    return amount.quantize(Decimal("0.01"))

def years_of_service(employee: Employee, until: date) -> int:
    years = until.year - employee.admission_date.year - ((until.month, until.day) < (employee.admission_date.month, employee.admission_date.day))
    return max(0, years)

def employee_snapshot(employee: Employee) -> dict:
    return {"department_id": employee.department_id, "position_id": employee.position_id, "group_id": employee.group_id, "manager_id": employee.manager_id,
            "salary": float(employee.salary), "company_id": employee.company_id, "registration": employee.registration, "status": employee.status}

def subordinates(db: Session, manager_id: int, indirect: bool) -> set[int]:
    direct = set(db.scalars(select(Employee.id).where(Employee.manager_id == manager_id, Employee.status != "Desligado")).all())
    if not indirect:
        return direct
    found, frontier = set(), set(direct)
    while frontier:
        found |= frontier
        frontier = set(db.scalars(select(Employee.id).where(Employee.manager_id.in_(frontier), Employee.status != "Desligado")).all()) - found
    return found - direct

def creates_cycle(db: Session, employee_id: int, manager_id: int) -> bool:
    current, seen = manager_id, set()
    while current and current not in seen:
        if current == employee_id:
            return True
        seen.add(current)
        current = db.scalar(select(Employee.manager_id).where(Employee.id == current))
    return False

def headcount_status(db: Session, company_id: int, department_id: int, position_id: int) -> dict:
    plan = db.scalar(select(HeadcountPlan).where(HeadcountPlan.department_id == department_id, HeadcountPlan.position_id == position_id))
    occupied = db.scalar(select(func.count(Employee.id)).where(Employee.company_id == company_id, Employee.department_id == department_id, Employee.position_id == position_id, Employee.status != "Desligado")) or 0
    pending = 0
    for row in db.scalars(select(ProcessRequest).where(ProcessRequest.company_id == company_id, ProcessRequest.process == "requisition", ProcessRequest.status == "pending_approval")).all():
        if row.data.get("department_id") == department_id and row.data.get("position_id") == position_id:
            pending += int(row.data.get("quantity", 0))
    open_positions = db.scalar(select(func.count(JobPosition.id)).where(JobPosition.department_id == department_id, JobPosition.position_id == position_id, JobPosition.status == "vacant")) or 0
    return {"planned": plan.planned if plan else None, "occupied": occupied, "pending_requisitions": pending, "open_positions": open_positions}

def salary_range_errors(db: Session, position_id: int, salary: Decimal | float) -> list[str]:
    ranges = db.scalars(select(SalaryRange).where(SalaryRange.position_id == position_id)).all()
    if not ranges:
        return ["Não há faixa na tabela salarial para este cargo."]
    value = Decimal(str(salary))
    if any(Decimal(str(r.min_salary)) <= value <= Decimal(str(r.max_salary)) for r in ranges):
        return []
    limits = ", ".join(f"{r.level}: R$ {float(r.min_salary):,.2f} a R$ {float(r.max_salary):,.2f}" for r in ranges)
    return [f"Salário fora da tabela salarial do cargo ({limits})."]

def contracting_errors(db: Session, company_id: int, department_id: int, position_id: int, salary, job_position_id: int | None) -> list[str]:
    """Regras da guia Contratação: tabela salarial, quadro de vagas e controle por posição."""
    config, errors = active_config(db, company_id, "contracting"), []
    if config["enforce_salary_table"]:
        errors += salary_range_errors(db, position_id, salary)
    if config["enforce_headcount"]:
        status = headcount_status(db, company_id, department_id, position_id)
        if status["planned"] is None or status["occupied"] >= status["planned"]:
            errors.append("Sem vaga disponível no quadro de vagas para este departamento e cargo.")
    if config["position_control"] or job_position_id:
        seat = db.get(JobPosition, job_position_id) if job_position_id else None
        if not seat or seat.company_id != company_id or seat.status != "vacant" or (seat.department_id, seat.position_id) != (department_id, position_id):
            errors.append("Selecione uma posição vaga do mesmo departamento e cargo.")
    return errors

# --------------------------------------------------------------------------- selection (Atribuições de Seleção)

def _group_name(db: Session, group_id: int | None) -> str | None:
    group = db.get(EmployeeGroup, group_id) if group_id else None
    return group.name if group else None

def selection_rule(db: Session, user: User, process: str) -> dict:
    """First rule, in the parameterized order, that matches the process and one of the requester's groups.
    The ordering defines the priority when the person belongs to more than one group."""
    if not user.employee_id or not user.company_id:
        return {"targets": [], "config_source": "target"}
    requester = db.get(Employee, user.employee_id)
    names = set(employee_group_names(db, requester)) if requester else set()
    for rule in active_config(db, user.company_id, "assignments")["rules"]:
        if rule["process"] in ("*", process) and (rule["requester_group"] is None or rule["requester_group"].lower() in names):
            return rule
    return {"targets": [], "config_source": "target"}

def allowed_target_ids(db: Session, user: User, process: str, company_id: int | None = None) -> set[int] | None:
    """None means "every active employee of the company" (HR and administrators)."""
    if user.role in ("system_admin", "consultant", "hr_manager"):
        return None
    rule = selection_rule(db, user, process)
    targets, ids = set(rule["targets"]), set()
    me = db.get(Employee, user.employee_id) if user.employee_id else None
    if not me:
        return set()
    if "self" in targets and PROCESSES[process]["self_service"]:
        ids.add(me.id)
    if "direct" in targets:
        ids |= subordinates(db, me.id, indirect=False)
    if "indirect" in targets:
        ids |= subordinates(db, me.id, indirect=True)
    if "visibility" in targets:
        ids |= set(db.scalars(select(Employee.id).where(Employee.company_id == me.company_id, Employee.department_id == me.department_id, Employee.status != "Desligado")).all())
    if not PROCESSES[process]["self_service"]:
        ids.discard(me.id)
    return ids

def check_target(db: Session, user: User, process: str, employee: Employee):
    if user.role == "consultant":
        raise HTTPException(403, "O consultor parametriza e simula; solicitações operacionais são feitas pelo RH, gestor ou colaborador.")
    if user.role == "hr_manager" and employee.company_id != user.company_id and process != "transfer":
        raise HTTPException(403, "Acesso restrito à empresa vinculada ao seu perfil")
    allowed = allowed_target_ids(db, user, process)
    if allowed is not None and employee.id not in allowed:
        raise HTTPException(403, "As atribuições de seleção não permitem abrir este processo para este colaborador.")

# --------------------------------------------------------------------------- SLA (business hours)

def _minutes(text: str) -> int:
    return int(text[:2]) * 60 + int(text[3:])

def add_business_hours(start: datetime, hours: int, cfg: dict) -> datetime:
    begin, end, days = _minutes(cfg["business_start"]), _minutes(cfg["business_end"]), set(cfg["business_days"])
    current = start.astimezone(BRT)
    remaining = hours * 60
    for _ in range(3660):  # at most ~10 years of calendar days
        workday = current.weekday() in days and (current.month, current.day) not in HOLIDAYS
        minute = current.hour * 60 + current.minute
        if workday and minute < end:
            minute = max(minute, begin)
            available = end - minute
            if remaining <= available:
                result = datetime.combine(current.date(), time(0, 0), BRT) + timedelta(minutes=minute + remaining)
                return result.astimezone(timezone.utc)
            remaining -= available
        current = datetime.combine(current.date() + timedelta(days=1), time(0, 0), BRT)
    return start + timedelta(hours=hours)

# --------------------------------------------------------------------------- approvers

def resolve_steps(db: Session, company_id: int, process: str, employee: Employee | None, requester: User) -> tuple[list[dict], dict]:
    workflow = active_config(db, company_id, "workflow")
    cfg = workflow["processes"].get(process) or DEFAULTS["workflow"]["processes"][process]
    rule = selection_rule(db, requester, process) if requester.role == "employee" else {"config_source": "target"}
    source = employee if rule.get("config_source") == "target" or not requester.employee_id else db.get(Employee, requester.employee_id)
    names = set(employee_group_names(db, source)) if source else set()
    # Atribuições de Configurações: the first group override (in the parameterized order) that includes the person.
    steps = next((override for group, override in cfg["group_overrides"].items() if group.lower() in names), None)
    return [dict(step) for step in (steps if steps is not None else cfg["steps"])], workflow

def step_users(db: Session, req: ProcessRequest, step: dict) -> list[User]:
    approver = step["approver"]
    if approver == "direct_manager":
        employee = db.get(Employee, req.employee_id) if req.employee_id else None
        manager_id = employee.manager_id if employee else None
        if not employee and req.requested_by_id:
            requester = db.get(User, req.requested_by_id)
            requester_employee = db.get(Employee, requester.employee_id) if requester and requester.employee_id else None
            manager_id = requester_employee.manager_id if requester_employee else None
        return db.scalars(select(User).where(User.employee_id == manager_id, User.active.is_(True))).all() if manager_id else []
    if approver in ("hr", "target_hr"):
        company_id = req.company_id if approver == "hr" else _int(req.data.get("target_company_id"), "Empresa de destino")
        return db.scalars(select(User).where(User.role == "hr_manager", User.company_id == company_id, User.active.is_(True))).all()
    if approver == "system_admin":
        return db.scalars(select(User).where(User.role == "system_admin", User.active.is_(True))).all()
    return []

def can_act(db: Session, user: User, req: ProcessRequest) -> bool:
    if req.status != "pending_approval" or req.current_step >= len(req.steps):
        return False
    step = req.steps[req.current_step]
    if user.role == "system_admin" and step["approver"] in ("hr", "target_hr", "system_admin", "direct_manager"):
        return True  # administrators can always unblock a flow; the action is audited
    return any(u.id == user.id for u in step_users(db, req, step))

# --------------------------------------------------------------------------- notifications (Servidor de e-mail)

def _send_email(to: str, subject: str, body: str) -> str:
    host = os.getenv("SMTP_HOST")
    if not host:
        return "not_configured"
    try:
        message = EmailMessage()
        message["From"] = os.getenv("SMTP_FROM", "hcm-lab@localhost")
        message["To"], message["Subject"] = to, subject
        message.set_content(body)
        with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=5) as smtp:
            if os.getenv("SMTP_STARTTLS", "true").lower() == "true":
                smtp.starttls()
            if os.getenv("SMTP_USER"):
                smtp.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            smtp.send_message(message)
        return "sent"
    except Exception as exc:  # e-mail never blocks the business transaction
        log.warning("Falha ao enviar e-mail: %s", exc)
        return "failed"

def notify(db: Session, users: list[User], message: str, req: ProcessRequest | None = None):
    for user in {u.id: u for u in users}.values():
        status = _send_email(user.email, "HCM Lab - " + (PROCESSES[req.process]["label"] if req else "Aviso"), message)
        db.add(Notification(user_id=user.id, message=message, request_id=req.id if req else None, email_status=status))

# --------------------------------------------------------------------------- lifecycle

def _audit(db: Session, req: ProcessRequest, actor: User | None, action: str, comment: str | None = None, late: bool = False):
    db.add(ProcessAction(request_id=req.id, step=req.current_step, actor_id=actor.id if actor else None, actor_name=actor.full_name if actor else "HCM Lab", action=action, comment=comment, late=late))

def _enter_step(db: Session, req: ProcessRequest, workflow: dict):
    req.step_started_at = now()
    step = req.steps[req.current_step]
    req.due_at = add_business_hours(req.step_started_at, step["sla_hours"], workflow) if workflow.get("sla_enabled") else None
    users = step_users(db, req, step)
    if not users and step["approver"] == "direct_manager":
        # No manager with system access: the step falls back to the company HR (logged).
        # Build a new list and change the copy: steps is a plain JSON column, so an in-place edit
        # would also alter SQLAlchemy's committed value and the reassignment would emit no UPDATE.
        steps = [dict(s) for s in req.steps]
        step = steps[req.current_step]
        step["approver"] = "hr"; step["fallback"] = "Sem gestor imediato com acesso; etapa encaminhada ao RH."
        req.steps = steps
        _audit(db, req, None, "fallback", step["fallback"])
        users = step_users(db, req, step)
    if not users and step["approver"] != "system_admin":
        # Nobody with access can approve: warn the administrators, who can unblock the flow (logged).
        _audit(db, req, None, "no_approver", f"Nenhum usuário ativo como {APPROVER_LABELS[step['approver']]}; administrador avisado.")
        notify(db, step_users(db, req, {"approver": "system_admin"}), f"{PROCESSES[req.process]['label']} nº {req.id} está parado: nenhum usuário ativo como {APPROVER_LABELS[step['approver']]} para aprovar a etapa {req.current_step + 1}. Crie/ative o aprovador ou destrave o fluxo.", req)
        return
    notify(db, users, f"{PROCESSES[req.process]['label']} nº {req.id} aguarda sua aprovação ({APPROVER_LABELS[step['approver']]}).", req)

def _auto_advance(db: Session, req: ProcessRequest, requester: User, workflow: dict):
    if not workflow.get("skip_step_when_requester_is_approver"):
        return
    while req.status == "pending_approval" and req.current_step < len(req.steps):
        # Only a designated approver counts; the administrator's unblock override does not.
        if not any(u.id == requester.id for u in step_users(db, req, req.steps[req.current_step])):
            return
        _audit(db, req, requester, "auto_approved", "Etapa aprovada automaticamente: o solicitante é o aprovador desta etapa.")
        req.current_step += 1
        if req.current_step < len(req.steps):
            _enter_step(db, req, workflow)

def start(db: Session, process: str, company_id: int, employee: Employee | None, requester: User, data: dict, reference_id: int | None = None, auto_advance: bool = True) -> ProcessRequest:
    steps, workflow = resolve_steps(db, company_id, process, employee, requester)
    config = active_config(db, company_id, process) if process in DEFAULTS else {}
    req = ProcessRequest(company_id=company_id, process=process, employee_id=employee.id if employee else None, requested_by_id=requester.id, data=data,
                         status="pending_approval", current_step=0, steps=steps, config_snapshot={"process": config, "steps": [dict(s) for s in steps],"additional_fields": additional_fields(db, company_id, process)}, reference_id=reference_id)
    db.add(req); db.flush()
    _audit(db, req, requester, "submitted")
    if steps:
        _enter_step(db, req, workflow)
        if auto_advance:
            _auto_advance(db, req, requester, workflow)
    if req.status == "pending_approval" and req.current_step >= len(req.steps):
        _finish(db, req, requester, approved=True, comment=None)
    db.add(Activity(company_id=company_id, actor=requester.full_name, message=f"{PROCESSES[process]['label']} nº {req.id} aberto{' para ' + employee.full_name if employee else ''}."))
    return req

def _finish(db: Session, req: ProcessRequest, actor: User, approved: bool, comment: str | None):
    definition = HANDLERS.get(req.process)
    if approved:
        result = definition["apply"](db, req, actor) if definition else {}
        req.status = result.pop("_status", "approved") if isinstance(result, dict) else "approved"
        req.result = result
    else:
        if definition and definition.get("reject"):
            definition["reject"](db, req, actor, comment)
        req.status = "rejected"
    req.decided_at = now(); req.due_at = None
    requester = db.get(User, req.requested_by_id)
    target_users = db.scalars(select(User).where(User.employee_id == req.employee_id, User.active.is_(True))).all() if req.employee_id else []
    notify(db, [u for u in [requester, *target_users] if u], f"{PROCESSES[req.process]['label']} nº {req.id}: {STATUS_LABELS.get(req.status, req.status)}." + (f" Motivo: {comment}" if comment and not approved else ""), req)

def decide(db: Session, req: ProcessRequest, user: User, approved: bool, comment: str):
    if req.status != "pending_approval":
        raise HTTPException(409, "A solicitação já foi analisada")
    if not can_act(db, user, req):
        raise HTTPException(403, "Você não é o aprovador da etapa atual desta solicitação")
    comment = (comment or "").strip()
    if not approved and not comment:
        raise HTTPException(422, "Informe o motivo da rejeição")
    if approved:
        approval_deadline_check(db, req)
    late = bool(req.due_at and (req.due_at if req.due_at.tzinfo else req.due_at.replace(tzinfo=timezone.utc)) < now())
    _audit(db, req, user, "approved" if approved else "rejected", comment or None, late)
    workflow = active_config(db, req.company_id, "workflow")
    if not approved:
        _finish(db, req, user, approved=False, comment=comment)
    else:
        if req.process in HANDLERS and HANDLERS[req.process].get("revalidate"):
            HANDLERS[req.process]["revalidate"](db, req)
        finishing = bool(req.steps[req.current_step].get("finish"))
        req.current_step += 1
        if finishing and req.current_step < len(req.steps):
            # Redirecionamento: this step ends the process; the remaining steps are skipped.
            _audit(db, req, user, "redirected", f"Processo finalizado nesta etapa; {len(req.steps) - req.current_step} etapa(s) seguinte(s) não executada(s).")
            req.current_step = len(req.steps)
        if req.current_step < len(req.steps):
            _enter_step(db, req, workflow)
        else:
            _finish(db, req, user, approved=True, comment=comment)
    db.add(Activity(company_id=req.company_id, actor=user.full_name, message=f"{PROCESSES[req.process]['label']} nº {req.id}: etapa {'aprovada' if approved else 'reprovada'}."))

def approval_deadline_check(db: Session, req: ProcessRequest):
    """Prazo mínimo para aprovação de férias: blocks approvals too close to the start date."""
    if req.process != "vacations" or not req.reference_id:
        return
    policy = active_config(db, req.company_id, "vacation_policy")
    minimum = int(policy.get("approval_min_days_before_start") or 0)
    row = db.get(VacationRequest, req.reference_id)
    if minimum and row:
        remaining = (row.start_date - date.today()).days
        if remaining < minimum:
            raise HTTPException(409, f"Prazo mínimo para aprovação não atendido: faltam {remaining} dia(s) para o início e a parametrização exige {minimum}. Reprove ou peça uma nova solicitação.")

def cancel(db: Session, req: ProcessRequest, user: User):
    if req.status != "pending_approval":
        raise HTTPException(409, "Somente solicitações em aprovação podem ser canceladas")
    if req.requested_by_id != user.id and user.role not in ("system_admin", "hr_manager"):
        raise HTTPException(403, "Apenas o solicitante ou o RH pode cancelar")
    if user.role == "hr_manager" and user.company_id != req.company_id:
        raise HTTPException(403, "Acesso restrito à empresa vinculada ao seu perfil")
    if HANDLERS.get(req.process, {}).get("reject"):
        HANDLERS[req.process]["reject"](db, req, user, "Cancelado pelo solicitante")
    _audit(db, req, user, "cancelled")
    req.status, req.decided_at, req.due_at = "cancelled", now(), None

def visible_requests_query(user: User):
    q = select(ProcessRequest)
    if user.role == "hr_manager":
        q = q.where((ProcessRequest.company_id == user.company_id) | (ProcessRequest.process == "transfer"))
    elif user.role == "employee":
        q = q.where((ProcessRequest.requested_by_id == user.id) | (ProcessRequest.employee_id == user.employee_id))
    return q

def can_view(db: Session, user: User, req: ProcessRequest) -> bool:
    if user.role in ("system_admin", "consultant"):
        return True
    if user.role == "hr_manager":
        return req.company_id == user.company_id or (req.process == "transfer" and _int(req.data.get("target_company_id"), "") == user.company_id)
    return req.requested_by_id == user.id or req.employee_id == user.employee_id or can_act(db, user, req) or any(a.actor_id == user.id for a in db.scalars(select(ProcessAction).where(ProcessAction.request_id == req.id)).all())

def request_out(db: Session, req: ProcessRequest, user: User | None = None, detail: bool = False) -> dict:
    employee = db.get(Employee, req.employee_id) if req.employee_id else None
    requester = db.get(User, req.requested_by_id)
    step = req.steps[req.current_step] if req.status == "pending_approval" and req.current_step < len(req.steps) else None
    due = req.due_at if not req.due_at or req.due_at.tzinfo else req.due_at.replace(tzinfo=timezone.utc)
    out = {"id": req.id, "company_id": req.company_id, "company_name": (db.get(Company, req.company_id).trade_name if db.get(Company, req.company_id) else ""),
           "process": req.process, "process_label": PROCESSES[req.process]["label"], "employee_id": req.employee_id, "employee_name": employee.full_name if employee else None,
           "requested_by": requester.full_name if requester else "", "status": req.status, "status_label": STATUS_LABELS.get(req.status, req.status),
           "data": req.data, "result": req.result, "current_step": req.current_step, "total_steps": len(req.steps),
           "current_approver": APPROVER_LABELS.get(step["approver"]) if step else None,
           "steps": [{**s, "approver_label": APPROVER_LABELS.get(s["approver"], s["approver"])} for s in req.steps],
           "additional_fields": (req.config_snapshot or {}).get("additional_fields") or [],
           "due_at": due.isoformat() if due else None, "overdue": bool(due and due < now()),
           "can_act": bool(user and can_act(db, user, req)), "created_at": req.created_at.isoformat() if req.created_at else None,
           "decided_at": req.decided_at.isoformat() if req.decided_at else None}
    if detail:
        out["history"] = [{"step": a.step, "actor": a.actor_name, "action": a.action, "comment": a.comment, "late": a.late, "created_at": a.created_at.isoformat() if a.created_at else None}
                          for a in db.scalars(select(ProcessAction).where(ProcessAction.request_id == req.id).order_by(ProcessAction.id)).all()]
    return out

# --------------------------------------------------------------------------- validation and effects per process

def _pending_same(db: Session, process: str, employee_id: int) -> bool:
    return bool(db.scalar(select(ProcessRequest.id).where(ProcessRequest.process == process, ProcessRequest.employee_id == employee_id, ProcessRequest.status == "pending_approval").limit(1)))

def validate(db: Session, process: str, company_id: int, employee: Employee | None, data: dict, requester: User) -> dict:
    """Returns normalized data (raises 422 with every problem found)."""
    if process not in VALIDATORS:
        raise HTTPException(404, "Processo desconhecido")
    config = active_config(db, company_id, process)
    if not config.get("enabled", True):
        raise HTTPException(422, f"O processo {PROCESSES[process]['label']} está desabilitado para esta empresa.")
    if requester.role == "employee" and employee and employee.id == requester.employee_id and not config.get("allow_employee_requests", True):
        raise HTTPException(403, "Solicitação direta pelo colaborador desabilitada para este processo.")
    if employee and employee.status == "Desligado":
        raise HTTPException(422, "Colaborador desligado.")
    errors, normalized = VALIDATORS[process](db, company_id, employee, data or {}, config)
    extra_errors, additional = validate_additional(db, company_id, process, (data or {}).get("additional"))
    if errors or extra_errors:
        raise HTTPException(422, errors + extra_errors)
    if additional is not None:
        normalized["additional"] = additional
    return normalized

def additional_fields(db: Session, company_id: int, process: str, environment: str = "production") -> list[dict]:
    return list(active_config(db, company_id, "process_fields", environment).get(process) or [])

def validate_additional(db: Session, company_id: int, process: str, values) -> tuple[list[str], dict | None]:
    """Informações adicionais: company-defined extra fields of the process form."""
    fields = additional_fields(db, company_id, process)
    if not fields:
        return [], None
    values = values if isinstance(values, dict) else {}
    errors, out = [], {}
    for field in fields:
        raw = values.get(field["name"])
        empty = raw is None or raw == "" or (field["type"] == "boolean" and raw is False)
        if empty:
            if field["required"]:
                errors.append(f"Informe o campo {field['label']}.")
            out[field["name"]] = False if field["type"] == "boolean" else None
            continue
        if field["type"] == "number":
            try:
                out[field["name"]] = float(raw)
            except (TypeError, ValueError):
                errors.append(f"{field['label']}: informe um número.")
        elif field["type"] == "date":
            try:
                out[field["name"]] = date.fromisoformat(str(raw)).isoformat()
            except ValueError:
                errors.append(f"{field['label']}: informe uma data válida.")
        elif field["type"] == "boolean":
            out[field["name"]] = bool(raw)
        elif field["type"] == "select":
            if raw not in field["options"]:
                errors.append(f"{field['label']}: escolha uma das opções.")
            out[field["name"]] = raw
        else:
            text = str(raw).strip()
            if len(text) > 500:
                errors.append(f"{field['label']}: no máximo 500 caracteres.")
            out[field["name"]] = text
    unknown = set(values) - {f["name"] for f in fields}
    if unknown:
        errors.append("Campos adicionais não reconhecidos: " + ", ".join(sorted(unknown)) + ".")
    return errors, out

def _v_dependents(db, company_id, employee, data, config):
    errors = []
    action = data.get("action")
    if action == "remove":
        dep = db.get(Dependent, _int(data.get("dependent_id"), "Dependente") or 0)
        if not dep or dep.employee_id != employee.id or not dep.active:
            errors.append("Selecione um dependente ativo do colaborador.")
        return errors, {"action": "remove", "dependent_id": dep.id if dep else None, "full_name": dep.full_name if dep else None}
    if action != "add":
        return ["Informe se deseja incluir ou excluir um dependente."], {}
    name = str(data.get("full_name") or "").strip()
    if len(name) < 3: errors.append("Informe o nome completo do dependente.")
    if data.get("relationship_type") not in RELATIONSHIPS: errors.append("Selecione o parentesco.")
    birth = _date(data.get("birth_date"), "Data de nascimento") if data.get("birth_date") else None
    if not birth or birth > date.today(): errors.append("Informe a data de nascimento (não futura).")
    cpf = str(data.get("cpf") or "").strip()
    if cpf and not valid_cpf(cpf): errors.append("CPF do dependente inválido.")
    if config["require_cpf"] and not cpf: errors.append("O CPF do dependente é obrigatório.")
    active = db.scalar(select(func.count(Dependent.id)).where(Dependent.employee_id == employee.id, Dependent.active.is_(True))) or 0
    if active >= config["max_dependents"]: errors.append(f"Limite de {config['max_dependents']} dependentes atingido.")
    if cpf and valid_cpf(cpf) and db.scalar(select(Dependent.id).where(Dependent.employee_id == employee.id, Dependent.cpf == format_cpf(cpf), Dependent.active.is_(True))): errors.append("Dependente já cadastrado.")
    return errors, {"action": "add", "full_name": name, "relationship_type": data.get("relationship_type"), "birth_date": birth.isoformat() if birth else None,
                    "cpf": format_cpf(cpf) if cpf and valid_cpf(cpf) else None, "income_tax_dependent": bool(data.get("income_tax_dependent")), "health_plan_dependent": bool(data.get("health_plan_dependent"))}

def _a_dependents(db, req, actor):
    employee = db.get(Employee, req.employee_id)
    if req.data["action"] == "remove":
        dep = db.get(Dependent, req.data["dependent_id"])
        if dep: dep.active = False
        return {"message": f"Dependente {req.data['full_name']} excluído."}
    dep = Dependent(employee_id=employee.id, full_name=req.data["full_name"], relationship_type=req.data["relationship_type"], birth_date=date.fromisoformat(req.data["birth_date"]),
                    cpf=req.data["cpf"], income_tax_dependent=req.data["income_tax_dependent"], health_plan_dependent=req.data["health_plan_dependent"])
    db.add(dep); db.flush()
    return {"dependent_id": dep.id, "message": f"Dependente {dep.full_name} incluído."}

PERSONAL_FIELDS = {"phone": "allow_phone", "personal_email": "allow_personal_email", "marital_status": "allow_marital_status", "address": "allow_address", "emergency_contact": "allow_emergency_contact"}

def _v_personal(db, company_id, employee, data, config):
    from app.schemas import EmployeePersonalData
    errors, changes = [], {}
    for field, flag in PERSONAL_FIELDS.items():
        if field in data and data[field] not in (None, "") and str(data[field]).strip() != (getattr(employee, field) or ""):
            if not config[flag]: errors.append(f"A alteração de {field} não está habilitada.")
            changes[field] = str(data[field]).strip()
    if not changes and not errors:
        errors.append("Informe ao menos um dado diferente do cadastro atual.")
    try:
        EmployeePersonalData(**changes)
    except Exception as exc:
        errors.append(str(exc).split("\n")[-1] if "\n" in str(exc) else "Dados pessoais inválidos.")
    return errors, {"changes": changes, "before": {k: getattr(employee, k) for k in changes}}

def _a_personal(db, req, actor):
    employee = db.get(Employee, req.employee_id)
    for key, value in req.data["changes"].items():
        setattr(employee, key, value)
    return {"message": "Dados pessoais atualizados.", "fields": list(req.data["changes"])}

def _overlaps(db, employee_id: int, start: date, end: date, exclude_leave: int | None = None) -> list[str]:
    errors = []
    for leave in db.scalars(select(EmployeeLeave).where(EmployeeLeave.employee_id == employee_id)).all():
        if leave.id != exclude_leave and leave.start_date <= end and start <= leave.end_date:
            errors.append(f"Conflita com afastamento já registrado de {leave.start_date.strftime('%d/%m/%Y')} a {leave.end_date.strftime('%d/%m/%Y')}.")
    for vac in db.scalars(select(VacationRequest).where(VacationRequest.employee_id == employee_id, VacationRequest.status.in_(("pending_approval", "approved", "approved_auto")))).all():
        vac_end = vac.start_date + timedelta(days=vac.requested_days - 1)
        if vac.start_date <= end and start <= vac_end:
            errors.append(f"Conflita com férias de {vac.start_date.strftime('%d/%m/%Y')} a {vac_end.strftime('%d/%m/%Y')}.")
    return errors

def _v_leave(db, company_id, employee, data, config):
    errors = []
    kind = data.get("kind")
    rule = LEAVE_KINDS.get(kind)
    if not rule:
        return ["Selecione o tipo de afastamento."], {}
    start, end = _date(data.get("start_date"), "Início"), _date(data.get("end_date"), "Fim")
    days = (end - start).days + 1
    if days < 1: errors.append("A data final deve ser igual ou posterior à inicial.")
    if start < date.today() - timedelta(days=config["max_retroactive_days"]): errors.append(f"Início além do limite retroativo de {config['max_retroactive_days']} dias.")
    if start < employee.admission_date: errors.append("O afastamento não pode começar antes da admissão.")
    if days > config["max_days"]: errors.append(f"Duração acima do máximo de {config['max_days']} dias.")
    if "max" in rule and days > rule["max"]: errors.append(f"{rule['label']}: duração máxima de {rule['max']} dias.")
    if "min" in rule and days < rule["min"]: errors.append(f"{rule['label']}: use este tipo apenas para afastamentos a partir de {rule['min']} dias.")
    if "exact" in rule and days not in rule["exact"]: errors.append(f"{rule['label']}: a duração deve ser de {' ou '.join(map(str, rule['exact']))} dias.")
    note = str(data.get("note") or "").strip()[:500]
    errors += _overlaps(db, employee.id, start, end)
    return errors, {"kind": kind, "kind_label": rule["label"], "start_date": start.isoformat(), "end_date": end.isoformat(), "days": days, "paid": rule["paid"], "note": note or None}

def _a_leave(db, req, actor):
    d = req.data
    errors = _overlaps(db, req.employee_id, date.fromisoformat(d["start_date"]), date.fromisoformat(d["end_date"]))
    if errors:
        raise HTTPException(409, errors)
    leave = EmployeeLeave(company_id=req.company_id, employee_id=req.employee_id, kind=d["kind"], start_date=date.fromisoformat(d["start_date"]), end_date=date.fromisoformat(d["end_date"]), paid=d["paid"], note=d["note"], request_id=req.id)
    db.add(leave); db.flush()
    refresh_employee_status(db, db.get(Employee, req.employee_id))
    return {"leave_id": leave.id, "message": f"{d['kind_label']} registrado ({d['days']} dias)."}

def refresh_employee_status(db: Session, employee: Employee):
    if not employee or employee.status == "Desligado":
        return
    today = date.today()
    on_leave = db.scalar(select(EmployeeLeave.id).where(EmployeeLeave.employee_id == employee.id, EmployeeLeave.start_date <= today, EmployeeLeave.end_date >= today).limit(1))
    if on_leave:
        employee.status = "Afastado"
    elif employee.status == "Afastado":
        employee.status = "Ativo"

def _v_movement(db, company_id, employee, data, config):
    errors, changes = [], {}
    effective = _date(data.get("effective_date"), "Data de efetivação")
    if effective < date.today() - timedelta(days=31): errors.append("A efetivação não pode retroagir mais de 31 dias.")
    if effective < employee.admission_date: errors.append("A efetivação não pode ser anterior à admissão.")
    for field, model in (("department_id", Department), ("position_id", Position), ("group_id", EmployeeGroup)):
        value = _int(data.get(field), field)
        if value and value != getattr(employee, field):
            row = db.get(model, value)
            if not row or row.company_id != employee.company_id: errors.append(f"{field}: item inválido para a empresa.")
            changes[field] = value
    manager = _int(data.get("manager_id"), "Gestor")
    if manager and manager != employee.manager_id:
        row = db.get(Employee, manager)
        if not row or row.company_id != employee.company_id or row.status == "Desligado": errors.append("Gestor imediato inválido.")
        elif manager == employee.id or creates_cycle(db, employee.id, manager): errors.append("O gestor escolhido criaria um ciclo na hierarquia.")
        changes["manager_id"] = manager
    salary = _money(data.get("salary"), "Novo salário")
    if salary is not None and salary != Decimal(str(employee.salary)):
        current = Decimal(str(employee.salary))
        if not config["allow_salary_change"]: errors.append("Alteração salarial não habilitada para movimentações.")
        if salary < current: errors.append("Redução salarial não é permitida (irredutibilidade, art. 7º, VI, da Constituição).")
        if current and (salary - current) / current * 100 > config["max_salary_increase_percent"]: errors.append(f"Aumento acima do limite de {config['max_salary_increase_percent']}%.")
        changes["salary"] = float(salary)
    if not changes:
        errors.append("Informe ao menos uma alteração.")
    position = changes.get("position_id", employee.position_id)
    if config["enforce_salary_table"] and ("salary" in changes or "position_id" in changes):
        errors += salary_range_errors(db, position, changes.get("salary", employee.salary))
    if config["enforce_headcount"] and ("position_id" in changes or "department_id" in changes):
        status = headcount_status(db, employee.company_id, changes.get("department_id", employee.department_id), position)
        if status["planned"] is None or status["occupied"] >= status["planned"]: errors.append("Sem vaga disponível no quadro de vagas para o destino.")
    reason = str(data.get("reason") or "").strip()
    if len(reason) < 5: errors.append("Informe o motivo da movimentação.")
    return errors, {"effective_date": effective.isoformat(), "changes": changes, "reason": reason, "before": {k: (float(getattr(employee, k)) if k == "salary" else getattr(employee, k)) for k in changes}}

def _apply_changes(db, employee: Employee, changes: dict):
    for key, value in changes.items():
        setattr(employee, key, Decimal(str(value)) if key == "salary" else value)
    if "position_id" in changes or "department_id" in changes:
        seat = db.scalar(select(JobPosition).where(JobPosition.employee_id == employee.id))
        if seat: seat.employee_id, seat.status = None, "vacant"

def _a_movement(db, req, actor):
    employee = db.get(Employee, req.employee_id)
    effective = date.fromisoformat(req.data["effective_date"])
    movement = EmployeeMovement(company_id=req.company_id, employee_id=employee.id, kind="movement", effective_date=effective, before_snapshot=employee_snapshot(employee), after_snapshot={**employee_snapshot(employee), **req.data["changes"]}, request_id=req.id)
    if effective <= date.today():
        _apply_changes(db, employee, req.data["changes"])
        movement.status = "applied"
        message = "Movimentação aplicada."
    else:
        movement.status = "scheduled"
        message = f"Movimentação agendada para {effective.strftime('%d/%m/%Y')}."
    db.add(movement)
    if req.data.get("application_id"):
        from app.recruitment_api import on_movement_applied
        on_movement_applied(db, req, actor)
    return {"message": message, "_status": "approved" if movement.status == "applied" else "scheduled"}

def apply_due_movements(db: Session) -> int:
    count = 0
    for movement in db.scalars(select(EmployeeMovement).where(EmployeeMovement.status == "scheduled", EmployeeMovement.effective_date <= date.today())).all():
        employee = db.get(Employee, movement.employee_id)
        changes = {k: v for k, v in movement.after_snapshot.items() if movement.before_snapshot.get(k) != v and k in ("department_id", "position_id", "group_id", "manager_id", "salary")}
        if employee and employee.status != "Desligado":
            _apply_changes(db, employee, changes)
        movement.status = "applied"
        req = db.get(ProcessRequest, movement.request_id) if movement.request_id else None
        if req and req.status == "scheduled": req.status = "approved"
        count += 1
    for employee in db.scalars(select(Employee).where(Employee.status.in_(("Ativo", "Afastado")))).all():
        refresh_employee_status(db, employee)
    return count

def _v_transfer(db, company_id, employee, data, config):
    errors = []
    target = db.get(Company, _int(data.get("target_company_id"), "Empresa de destino") or 0)
    if not target or target.id == employee.company_id or target.status != "Ativa":
        return ["Selecione uma empresa de destino ativa e diferente da atual."], {}
    for field, model in (("target_department_id", Department), ("target_position_id", Position), ("target_group_id", EmployeeGroup)):
        row = db.get(model, _int(data.get(field), field) or 0)
        if not row or row.company_id != target.id: errors.append(f"{field}: selecione um item da empresa de destino.")
    registration = str(data.get("target_registration") or "").strip()
    if not registration: errors.append("Informe a nova matrícula.")
    elif db.scalar(select(Employee.id).where(Employee.company_id == target.id, Employee.registration == registration)): errors.append("Matrícula já usada na empresa de destino.")
    if db.scalar(select(Employee.id).where(Employee.company_id == target.id, Employee.email == employee.email)): errors.append("Já existe colaborador com este e-mail no destino.")
    effective = _date(data.get("effective_date"), "Data da transferência")
    if effective < date.today(): errors.append("A transferência deve ter data atual ou futura.")
    if db.scalar(select(func.count(Employee.id)).where(Employee.manager_id == employee.id, Employee.status != "Desligado")): errors.append("Transfira antes os subordinados para outro gestor.")
    return errors, {"target_company_id": target.id, "target_company_name": target.trade_name, "target_department_id": _int(data.get("target_department_id"), ""), "target_position_id": _int(data.get("target_position_id"), ""),
                    "target_group_id": _int(data.get("target_group_id"), ""), "target_registration": registration, "effective_date": effective.isoformat()}

def _a_transfer(db, req, actor):
    employee = db.get(Employee, req.employee_id)
    before = employee_snapshot(employee)
    d = req.data
    seat = db.scalar(select(JobPosition).where(JobPosition.employee_id == employee.id))
    if seat: seat.employee_id, seat.status = None, "vacant"
    employee.company_id, employee.department_id, employee.position_id, employee.group_id = d["target_company_id"], d["target_department_id"], d["target_position_id"], d["target_group_id"]
    employee.registration, employee.manager_id = d["target_registration"], None
    for account in db.scalars(select(User).where(User.employee_id == employee.id)).all():
        account.company_id, account.access_group_id = d["target_company_id"], None
    db.add(EmployeeMovement(company_id=d["target_company_id"], employee_id=employee.id, kind="transfer", effective_date=date.fromisoformat(d["effective_date"]), before_snapshot=before, after_snapshot=employee_snapshot(employee), request_id=req.id))
    return {"message": f"Colaborador transferido para {d['target_company_name']}."}

def _v_requisition(db, company_id, employee, data, config):
    errors = []
    dept, pos = db.get(Department, _int(data.get("department_id"), "Departamento") or 0), db.get(Position, _int(data.get("position_id"), "Cargo") or 0)
    if not dept or dept.company_id != company_id or not pos or pos.company_id != company_id:
        return ["Departamento e cargo devem pertencer à empresa."], {}
    quantity = _int(data.get("quantity"), "Quantidade") or 0
    if not 1 <= quantity <= 20: errors.append("A quantidade deve ficar entre 1 e 20.")
    reason = data.get("reason")
    if reason not in ("aumento_quadro", "substituicao"): errors.append("Selecione o motivo.")
    replaced = _int(data.get("replaced_employee_id"), "Substituído")
    if reason == "substituicao":
        row = db.get(Employee, replaced or 0)
        if not row or row.company_id != company_id: errors.append("Informe o colaborador substituído.")
    salary = _money(data.get("salary_proposal"), "Salário proposto")
    if salary is None: errors.append("Informe o salário proposto.")
    elif db.scalar(select(SalaryRange.id).where(SalaryRange.position_id == pos.id).limit(1)): errors += salary_range_errors(db, pos.id, salary)
    if config["enforce_headcount"] and reason == "aumento_quadro":
        status = headcount_status(db, company_id, dept.id, pos.id)
        if status["planned"] is None: errors.append("Não há quadro de vagas planejado para este departamento e cargo.")
        elif status["occupied"] + status["pending_requisitions"] + status["open_positions"] + quantity > status["planned"]:
            errors.append(f"O quadro de vagas comporta {status['planned']} pessoas; ocupadas {status['occupied']}, posições vagas {status['open_positions']} e requisições pendentes {status['pending_requisitions']}.")
    justification = str(data.get("justification") or "").strip()
    if len(justification) < 10: errors.append("Detalhe a justificativa (mínimo de 10 caracteres).")
    return errors, {"department_id": dept.id, "department": dept.name, "position_id": pos.id, "position": pos.title, "quantity": quantity, "reason": reason,
                    "replaced_employee_id": replaced if reason == "substituicao" else None, "salary_proposal": float(salary) if salary else None, "justification": justification}

def _a_requisition(db, req, actor):
    d = req.data
    created = []
    if active_config(db, req.company_id, "requisition")["create_positions"]:
        existing = db.scalar(select(func.count(JobPosition.id)).where(JobPosition.company_id == req.company_id)) or 0
        for index in range(d["quantity"]):
            code = f"POS-{existing + index + 1:05d}"
            while db.scalar(select(JobPosition.id).where(JobPosition.company_id == req.company_id, JobPosition.code == code)):
                existing += 1; code = f"POS-{existing + index + 1:05d}"
            seat = JobPosition(company_id=req.company_id, code=code, department_id=d["department_id"], position_id=d["position_id"], status="vacant", requisition_id=req.id)
            db.add(seat); db.flush(); created.append(seat.code)
    from app.recruitment_api import create_vacancy_from_requisition  # recruitment depends on this module
    vacancy = create_vacancy_from_requisition(db, req, actor)
    return {"message": f"Requisição aprovada: {d['quantity']} vaga(s) de {d['position']} em {d['department']}." + (f" Vaga {vacancy.code} criada em rascunho no Recrutamento." if vacancy else ""),
            "positions": created, "vacancy_id": vacancy.id if vacancy else None}

def notice_days(employee: Employee, reason: str, config: dict, when: date) -> int:
    if reason in ("justa_causa", "termino_contrato"):
        return 0
    if reason == "pedido_demissao" or not config["proportional_notice"]:
        return config["base_notice_days"]
    return min(config["max_notice_days"], config["base_notice_days"] + config["extra_days_per_year"] * years_of_service(employee, when))

def _v_termination(db, company_id, employee, data, config):
    errors = []
    reason, notice = data.get("reason"), data.get("notice_type")
    if reason not in TERMINATION_REASONS: errors.append("Selecione o motivo do desligamento.")
    if notice not in NOTICE_TYPES: errors.append("Selecione o tipo de aviso prévio.")
    when = _date(data.get("termination_date"), "Data do aviso")
    if when < employee.admission_date: errors.append("A data não pode ser anterior à admissão.")
    if when < date.today() and not config["allow_retroactive"]: errors.append("Data retroativa não permitida pela configuração de rescisão.")
    if when > date.today() + timedelta(days=90): errors.append("Informe uma data em até 90 dias.")
    if reason in ("justa_causa", "termino_contrato") and notice != "dispensado": errors.append("Neste motivo não há aviso prévio: selecione 'Aviso dispensado'.")
    if reason == "pedido_demissao" and notice == "indenizado": errors.append("No pedido de demissão use aviso trabalhado ou dispensado pelo empregador.")
    if db.scalar(select(func.count(Employee.id)).where(Employee.manager_id == employee.id, Employee.status != "Desligado")): errors.append("Transfira antes os subordinados para outro gestor.")
    if errors:
        return errors, {}
    days = notice_days(employee, reason, config, when)
    last_day = when + timedelta(days=days - 1) if notice == "trabalhado" and days else when
    projected = when + timedelta(days=days) if notice == "indenizado" else last_day
    return errors, {"reason": reason, "reason_label": TERMINATION_REASONS[reason], "notice_type": notice, "notice_label": NOTICE_TYPES[notice], "termination_date": when.isoformat(),
                    "notice_days": days, "notice_paid_ratio": 0.5 if reason == "acordo" and notice == "indenizado" else 1, "last_working_day": last_day.isoformat(),
                    "projected_end_date": projected.isoformat(), "years_of_service": years_of_service(employee, when), "note": (str(data.get("note") or "").strip() or None),
                    "warning": "Cálculo demonstrativo do aviso prévio; não calcula verbas rescisórias, FGTS ou eSocial."}

def _a_termination(db, req, actor):
    employee = db.get(Employee, req.employee_id)
    d = req.data
    db.add(Termination(company_id=req.company_id, employee_id=employee.id, reason=d["reason"], notice_type=d["notice_type"], notice_days=d["notice_days"],
                       termination_date=date.fromisoformat(d["termination_date"]), projected_end_date=date.fromisoformat(d["projected_end_date"]), request_id=req.id))
    employee.status, employee.termination_date = "Desligado", date.fromisoformat(d["last_working_day"])
    seat = db.scalar(select(JobPosition).where(JobPosition.employee_id == employee.id))
    if seat: seat.employee_id, seat.status = None, "vacant"
    if active_config(db, req.company_id, "termination")["deactivate_user_account"]:
        from app.security import revoke_user_sessions
        for account in db.scalars(select(User).where(User.employee_id == employee.id)).all():
            account.active = False; revoke_user_sessions(db, account.id)
    for pending in db.scalars(select(ProcessRequest).where(ProcessRequest.employee_id == employee.id, ProcessRequest.status == "pending_approval", ProcessRequest.id != req.id)).all():
        if HANDLERS.get(pending.process, {}).get("reject"): HANDLERS[pending.process]["reject"](db, pending, actor, "Colaborador desligado")
        pending.status, pending.decided_at = "cancelled", now()
        _audit(db, pending, actor, "cancelled", "Colaborador desligado")
    return {"message": f"Desligamento registrado. Aviso prévio de {d['notice_days']} dias; último dia {date.fromisoformat(d['last_working_day']).strftime('%d/%m/%Y')}."}

def recess_balance(db: Session, employee: Employee, config: dict) -> int:
    months = (date.today().year - employee.admission_date.year) * 12 + date.today().month - employee.admission_date.month - (date.today().day < employee.admission_date.day)
    earned = int(config["days_per_year"] * max(0, months) / 12)
    used = sum(r.days for r in db.scalars(select(RecessRecord).where(RecessRecord.employee_id == employee.id)).all())
    pending = sum(int(r.data.get("days", 0)) for r in db.scalars(select(ProcessRequest).where(ProcessRequest.process == "recess", ProcessRequest.employee_id == employee.id, ProcessRequest.status == "pending_approval")).all())
    return max(0, earned - used - pending)

def _v_recess(db, company_id, employee, data, config):
    errors = []
    if employee.contract_type != "Estágio":
        return ["Recesso remunerado aplica-se a estagiários (Lei 11.788/2008)."], {}
    start, days = _date(data.get("start_date"), "Início"), _int(data.get("days"), "Dias") or 0
    if days < 1: errors.append("Informe a quantidade de dias.")
    if (start - date.today()).days < config["min_advance_days"]: errors.append(f"Antecedência mínima de {config['min_advance_days']} dias.")
    balance = recess_balance(db, employee, config)
    if days > balance: errors.append(f"Saldo de recesso insuficiente ({balance} dias disponíveis).")
    year_count = sum(1 for r in db.scalars(select(RecessRecord).where(RecessRecord.employee_id == employee.id)).all() if r.start_date.year == start.year)
    if year_count >= config["max_installments"]: errors.append(f"Limite de {config['max_installments']} períodos por ano.")
    if days > 0: errors += _overlaps(db, employee.id, start, start + timedelta(days=days - 1))
    return errors, {"start_date": start.isoformat(), "days": days, "balance_before": balance}

def _a_recess(db, req, actor):
    db.add(RecessRecord(company_id=req.company_id, employee_id=req.employee_id, start_date=date.fromisoformat(req.data["start_date"]), days=req.data["days"], request_id=req.id))
    return {"message": f"Recesso de {req.data['days']} dias registrado."}

def transport_cost(db: Session, lines: list[dict], workdays: int) -> Decimal:
    total = Decimal("0")
    for item in lines:
        line = db.get(TransportLine, item["line_id"])
        if line: total += Decimal(str(line.fare)) * item["trips_per_day"] * workdays
    return total.quantize(Decimal("0.01"))

def _v_transport(db, company_id, employee, data, config):
    if data.get("opt_out"):
        return [], {"opt_out": True, "lines": [], "monthly_cost": 0, "employee_discount": 0}
    errors, lines = [], []
    raw = data.get("lines") if isinstance(data.get("lines"), list) else []
    if not raw: errors.append("Informe ao menos uma linha ou marque que não deseja o benefício.")
    for item in raw[:10]:
        line = db.get(TransportLine, _int(item.get("line_id") if isinstance(item, dict) else None, "Linha") or 0)
        trips = _int(item.get("trips_per_day") if isinstance(item, dict) else None, "Viagens") or 0
        if not line or line.company_id != company_id or not line.active: errors.append("Linha de transporte inválida."); continue
        if not 1 <= trips <= 6: errors.append("Viagens por dia devem ficar entre 1 e 6.")
        lines.append({"line_id": line.id, "code": line.code, "description": line.description, "fare": float(line.fare), "trips_per_day": trips})
    cost = transport_cost(db, lines, config["workdays_per_month"])
    discount = min(cost, (Decimal(str(employee.salary)) * Decimal(config["employee_discount_percent"]) / 100).quantize(Decimal("0.01")))
    return errors, {"opt_out": False, "lines": lines, "workdays": config["workdays_per_month"], "monthly_cost": float(cost), "employee_discount": float(discount)}

def _a_transport(db, req, actor):
    for row in db.scalars(select(TransportEnrollment).where(TransportEnrollment.employee_id == req.employee_id, TransportEnrollment.active.is_(True))).all():
        row.active = False
    if req.data["opt_out"]:
        return {"message": "Opção por não receber vale-transporte registrada."}
    db.add(TransportEnrollment(company_id=req.company_id, employee_id=req.employee_id, lines=req.data["lines"], workdays=req.data["workdays"], request_id=req.id))
    return {"message": f"Vale-transporte ativado: custo mensal R$ {req.data['monthly_cost']:.2f}, desconto R$ {req.data['employee_discount']:.2f}."}

def _v_value_entry(db, company_id, employee, data, config):
    errors = []
    competence = str(data.get("competence") or "")
    if len(competence) != 7 or competence[4] != "-" or not competence[:4].isdigit() or not competence[5:].isdigit() or not 1 <= int(competence[5:]) <= 12:
        errors.append("Informe a competência no formato AAAA-MM.")
    event = db.scalar(select(PayrollEvent).where(PayrollEvent.company_id == company_id, PayrollEvent.environment == "production", PayrollEvent.code == str(data.get("event_code") or ""), PayrollEvent.active.is_(True)))
    if not event: errors.append("Selecione um evento de folha publicado em produção.")
    amount = _money(data.get("amount"), "Valor")
    if amount is None: errors.append("Informe o valor.")
    elif amount > config["max_amount"]: errors.append(f"Valor acima do limite de R$ {config['max_amount']:,.2f}.")
    return errors, {"competence": competence, "event_code": event.code if event else None, "event_description": event.description if event else None, "kind": event.kind if event else None,
                    "amount": float(amount) if amount else None, "description": (str(data.get("description") or "").strip() or (event.description if event else ""))[:180]}

def _a_value_entry(db, req, actor):
    d = req.data
    db.add(ValueEntry(company_id=req.company_id, employee_id=req.employee_id, competence=d["competence"], event_code=d["event_code"], description=d["description"], kind=d["kind"], amount=d["amount"], request_id=req.id))
    return {"message": f"Lançamento de R$ {d['amount']:.2f} em {d['event_code']} para {d['competence']}."}

def _a_vacation(db, req, actor):
    from app.vacations import apply_monthly_treatment
    row = db.get(VacationRequest, req.reference_id)
    if row and row.status == "pending_approval":
        note = apply_monthly_treatment(db, row)
        if note:
            _audit(db, req, actor, "note", note)
        row.status, row.approved_by_id, row.decided_at = "approved", actor.id, now()
        actions = db.scalars(select(ProcessAction).where(ProcessAction.request_id == req.id).order_by(ProcessAction.id.desc())).all()
        row.approval_comment = next((a.comment for a in actions if a.action == "approved" and a.comment), None)
    return {"message": "Férias aprovadas."}

def _r_vacation(db, req, actor, comment):
    row = db.get(VacationRequest, req.reference_id)
    if row and row.status == "pending_approval":
        row.status, row.approved_by_id, row.decided_at, row.approval_comment = "rejected", actor.id, now(), comment

def _a_benefit(db, req, actor):
    row = db.get(EmployeeBenefit, req.reference_id)
    if row and row.status == "pending_approval":
        row.status, row.decided_by_id = "active", actor.id
    return {"message": "Benefício ativado."}

def _r_benefit(db, req, actor, comment):
    row = db.get(EmployeeBenefit, req.reference_id)
    if row and row.status == "pending_approval":
        row.status, row.decided_by_id, row.comment = "rejected", actor.id, comment

def _revalidate(process):
    def run(db, req):
        employee = db.get(Employee, req.employee_id) if req.employee_id else None
        config = active_config(db, req.company_id, process)
        errors, _ = VALIDATORS[process](db, req.company_id, employee, req.data, config) if process not in ("dependents", "personal_data", "value_entry", "recess") else ([], None)
        if errors:
            raise HTTPException(409, ["A solicitação não é mais válida:"] + errors)
    return run

VALIDATORS = {"dependents": _v_dependents, "personal_data": _v_personal, "leave": _v_leave, "movement": _v_movement, "transfer": _v_transfer, "requisition": _v_requisition,
              "termination": _v_termination, "recess": _v_recess, "transport": _v_transport, "value_entry": _v_value_entry}
HANDLERS = {"dependents": {"apply": _a_dependents}, "personal_data": {"apply": _a_personal}, "leave": {"apply": _a_leave}, "movement": {"apply": _a_movement},
            "transfer": {"apply": _a_transfer, "revalidate": _revalidate("transfer")}, "requisition": {"apply": _a_requisition}, "termination": {"apply": _a_termination},
            "recess": {"apply": _a_recess}, "transport": {"apply": _a_transport}, "value_entry": {"apply": _a_value_entry},
            "vacations": {"apply": _a_vacation, "reject": _r_vacation}, "benefits": {"apply": _a_benefit, "reject": _r_benefit}}
