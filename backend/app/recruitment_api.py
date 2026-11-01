"""Recrutamento e seleção: vagas abertas a partir da requisição de pessoal,
banco de talentos com consentimento LGPD, etapas configuráveis, avaliações por critério, entrevistas,
vagas internas e contratação integrada à admissão (externo) ou à movimentação (interno)."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (Activity, AdmissionRequest, Application, ApplicationEvent, Candidate, Company, Department, Employee, EmployeeGroup,
    Interview, JobPosition, Position, ProcessRequest, SalaryRange, User, Vacancy)
from app.policies import active_config
from app import processes
from app.schemas import AdmissionRequestInput, ContractType
from app.security import current_user

router = APIRouter(prefix="/api/recruitment", tags=["Recrutamento e seleção"])
MANAGERS = ("system_admin", "hr_manager")
VIEWERS = ("system_admin", "hr_manager", "consultant")
SOURCES = ("site", "indicacao", "linkedin", "agencia", "interno", "outro")
STATUS_LABELS = {"draft": "Rascunho", "open": "Aberta", "paused": "Pausada", "filled": "Preenchida", "closed": "Encerrada", "cancelled": "Cancelada"}
APP_STATUS = {"active": "Em processo", "offer": "Proposta / contratação", "hired": "Contratado", "rejected": "Reprovado", "withdrawn": "Desistiu"}

class VacancyInput(BaseModel):
    company_id: int
    title: str = Field(min_length=3, max_length=180)
    department_id: int
    position_id: int
    requisition_id: int | None = None
    hiring_manager_id: int | None = None
    description: str = Field(default="", max_length=5000)
    requirements: str = Field(default="", max_length=5000)
    contract_type: ContractType = "CLT"
    salary_min: float | None = Field(default=None, gt=0, le=10000000)
    salary_max: float | None = Field(default=None, gt=0, le=10000000)
    quantity: int = Field(default=1, ge=1, le=50)
    recruitment_type: Literal["internal", "external", "mixed"] = "mixed"
    deadline: date | None = None

    @model_validator(mode="after")
    def check(self):
        if self.salary_min and self.salary_max and self.salary_min > self.salary_max:
            raise ValueError("A faixa salarial mínima não pode superar a máxima.")
        if self.deadline and self.deadline < date.today():
            raise ValueError("O prazo de inscrição não pode estar no passado.")
        return self

class StatusInput(BaseModel):
    status: Literal["open", "paused", "closed", "cancelled"]
    reason: str = Field(default="", max_length=500)

class CandidateInput(BaseModel):
    company_id: int
    full_name: str = Field(min_length=3, max_length=180)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=30)
    city: str | None = Field(default=None, max_length=100)
    source: Literal[SOURCES] = "site"
    summary: str = Field(default="", max_length=5000)
    consent: bool

class ApplyInput(BaseModel):
    candidate_id: int

class InternalApplyInput(BaseModel):
    consent: bool
    summary: str = Field(default="", max_length=2000)

class MoveInput(BaseModel):
    to_stage: str = Field(min_length=1, max_length=40)
    comment: str = Field(default="", max_length=1000)

class EvaluateInput(BaseModel):
    criteria: dict[str, int]
    comment: str = Field(default="", max_length=2000)

class RejectInput(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)

class InterviewInput(BaseModel):
    scheduled_at: datetime
    duration_minutes: int = Field(default=60, ge=15, le=480)
    interviewer_id: int
    mode: Literal["online", "presencial", "telefone"] = "online"
    location: str | None = Field(default=None, max_length=300)

class InterviewStatusInput(BaseModel):
    status: Literal["done", "cancelled", "no_show"]

class HireInput(BaseModel):
    planned_date: date
    salary: float = Field(gt=0, le=10000000)
    group_id: int | None = None
    registration: str | None = Field(default=None, min_length=1, max_length=30)
    job_position_id: int | None = None
    documents_complete: bool = False
    comment: str = Field(default="", max_length=1000)

# --------------------------------------------------------------------------- helpers

def now():
    return datetime.now(timezone.utc)

def policy(db: Session, company_id: int) -> dict:
    return active_config(db, company_id, "recruitment")

def company_scope(user: User, company_id: int):
    if user.role in ("hr_manager", "employee") and user.company_id != company_id:
        raise HTTPException(403, "Acesso restrito à empresa vinculada ao seu perfil")

def manager_only(user: User, company_id: int):
    if user.role not in MANAGERS:
        raise HTTPException(403, "Operação restrita ao RH e aos administradores")
    company_scope(user, company_id)

def get_vacancy(db: Session, vacancy_id: int) -> Vacancy:
    row = db.get(Vacancy, vacancy_id)
    if not row:
        raise HTTPException(404, "Vaga não encontrada")
    return row

def is_hiring_manager(user: User, vacancy: Vacancy) -> bool:
    return bool(user.employee_id and vacancy.hiring_manager_id == user.employee_id)

def is_interviewer(db: Session, user: User, application: Application) -> bool:
    return bool(db.scalar(select(Interview.id).where(Interview.application_id == application.id, Interview.interviewer_id == user.id).limit(1)))

def can_see_vacancy_pipeline(user: User, vacancy: Vacancy) -> bool:
    if user.role in VIEWERS:
        return user.role != "hr_manager" or user.company_id == vacancy.company_id
    return is_hiring_manager(user, vacancy)

def application_access(db: Session, user: User, application: Application) -> Literal["full", "candidate"]:
    vacancy = db.get(Vacancy, application.vacancy_id)
    if can_see_vacancy_pipeline(user, vacancy) or is_interviewer(db, user, application):
        return "full"
    candidate = db.get(Candidate, application.candidate_id)
    if user.employee_id and candidate.employee_id == user.employee_id:
        return "candidate"
    raise HTTPException(404, "Candidatura não encontrada")

def log(db: Session, application: Application, user: User | None, kind: str, comment: str | None = None, to_stage: str | None = None, score: int | None = None, criteria: dict | None = None):
    db.add(ApplicationEvent(application_id=application.id, kind=kind, stage=application.stage, to_stage=to_stage, comment=comment, score=score, criteria=criteria,
                            actor_id=user.id if user else None, actor_name=user.full_name if user else "HCM Lab"))

def next_code(db: Session, company_id: int) -> str:
    count = db.scalar(select(func.count(Vacancy.id)).where(Vacancy.company_id == company_id)) or 0
    code = f"VAG-{count + 1:04d}"
    while db.scalar(select(Vacancy.id).where(Vacancy.company_id == company_id, Vacancy.code == code)):
        count += 1; code = f"VAG-{count + 1:04d}"
    return code

def hires(db: Session, vacancy_id: int) -> int:
    return db.scalar(select(func.count(Application.id)).where(Application.vacancy_id == vacancy_id, Application.status == "hired")) or 0

def vacancy_out(db: Session, row: Vacancy, user: User | None = None, detail: bool = False) -> dict:
    department, position = db.get(Department, row.department_id), db.get(Position, row.position_id)
    manager = db.get(Employee, row.hiring_manager_id) if row.hiring_manager_id else None
    apps = db.scalars(select(Application).where(Application.vacancy_id == row.id)).all()
    by_stage = {stage: 0 for stage in row.stages}
    for a in apps:
        if a.status in ("active", "offer"):
            by_stage[a.stage] = by_stage.get(a.stage, 0) + 1
    out = {"id": row.id, "company_id": row.company_id, "code": row.code, "title": row.title, "department_id": row.department_id, "department": department.name if department else None,
           "position_id": row.position_id, "position": position.title if position else None, "requisition_id": row.requisition_id, "hiring_manager_id": row.hiring_manager_id,
           "hiring_manager": manager.full_name if manager else None, "description": row.description, "requirements": row.requirements, "contract_type": row.contract_type,
           "salary_min": float(row.salary_min) if row.salary_min is not None else None, "salary_max": float(row.salary_max) if row.salary_max is not None else None,
           "quantity": row.quantity, "hired": sum(1 for a in apps if a.status == "hired"), "recruitment_type": row.recruitment_type, "status": row.status, "status_label": STATUS_LABELS[row.status],
           "deadline": row.deadline.isoformat() if row.deadline else None, "stages": row.stages, "candidates": len(apps), "active_candidates": sum(by_stage.values()), "by_stage": by_stage,
           "created_at": row.created_at.isoformat() if row.created_at else None}
    if user and user.role == "employee" and not is_hiring_manager(user, row):
        for key in ("candidates", "active_candidates", "by_stage", "hired", "hiring_manager_id"):
            out.pop(key, None)
    if detail:
        out["applications"] = [application_out(db, a, "full") for a in sorted(apps, key=lambda a: (row.stages.index(a.stage) if a.stage in row.stages else 99, -(float(a.score or 0))))]
    return out

def application_out(db: Session, row: Application, access: str, detail: bool = False) -> dict:
    candidate, vacancy = db.get(Candidate, row.candidate_id), db.get(Vacancy, row.vacancy_id)
    out = {"id": row.id, "vacancy_id": row.vacancy_id, "vacancy_title": vacancy.title, "vacancy_code": vacancy.code, "candidate_id": row.candidate_id, "candidate_name": candidate.full_name,
           "internal": bool(candidate.employee_id), "stage": row.stage, "stage_index": vacancy.stages.index(row.stage) if row.stage in vacancy.stages else None, "total_stages": len(vacancy.stages),
           "status": row.status, "status_label": APP_STATUS[row.status], "created_at": row.created_at.isoformat() if row.created_at else None}
    if access == "full":
        out.update({"candidate_email": candidate.email, "candidate_phone": candidate.phone, "source": candidate.source, "score": float(row.score) if row.score is not None else None,
                    "evaluation_criteria": policy(db, row.company_id)["evaluation_criteria"],
                    "rejection_reason": row.rejection_reason, "admission_id": row.admission_id, "stages": vacancy.stages,
                    "department_id": vacancy.department_id, "position_id": vacancy.position_id})
    if detail:
        events = db.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id == row.id).order_by(ApplicationEvent.id)).all()
        interviews = db.scalars(select(Interview).where(Interview.application_id == row.id).order_by(Interview.scheduled_at)).all()
        out["interviews"] = [interview_out(db, i) for i in interviews]
        if access == "full":
            out["summary"] = candidate.summary
            out["events"] = [{"kind": e.kind, "stage": e.stage, "to_stage": e.to_stage, "score": e.score, "criteria": e.criteria, "comment": e.comment, "actor": e.actor_name,
                              "created_at": e.created_at.isoformat() if e.created_at else None} for e in events]
        else:
            out["events"] = [{"kind": e.kind, "stage": e.stage, "to_stage": e.to_stage, "created_at": e.created_at.isoformat() if e.created_at else None} for e in events if e.kind in ("applied", "moved", "rejected", "hired", "withdrawn")]
    return out

def interview_out(db: Session, row: Interview) -> dict:
    interviewer = db.get(User, row.interviewer_id)
    return {"id": row.id, "application_id": row.application_id, "stage": row.stage, "scheduled_at": row.scheduled_at.isoformat(), "duration_minutes": row.duration_minutes,
            "interviewer_id": row.interviewer_id, "interviewer": interviewer.full_name if interviewer else "", "mode": row.mode, "location": row.location, "status": row.status}

def check_vacancy_payload(db: Session, payload: VacancyInput, current: Vacancy | None = None):
    for model, value, label in ((Department, payload.department_id, "Departamento"), (Position, payload.position_id, "Cargo")):
        row = db.get(model, value)
        if not row or row.company_id != payload.company_id:
            raise HTTPException(422, f"{label} deve pertencer à empresa")
    if payload.hiring_manager_id:
        manager = db.get(Employee, payload.hiring_manager_id)
        if not manager or manager.company_id != payload.company_id or manager.status == "Desligado":
            raise HTTPException(422, "Gestor da vaga deve ser um colaborador ativo da empresa")
    config = policy(db, payload.company_id)
    if config["require_requisition"] and not payload.requisition_id:
        raise HTTPException(422, "A parametrização exige uma requisição de pessoal aprovada para abrir a vaga")
    if payload.requisition_id:
        req = db.get(ProcessRequest, payload.requisition_id)
        if not req or req.process != "requisition" or req.company_id != payload.company_id or req.status != "approved":
            raise HTTPException(422, "Informe uma requisição de pessoal aprovada desta empresa")
        if (req.data.get("department_id"), req.data.get("position_id")) != (payload.department_id, payload.position_id):
            raise HTTPException(422, "Departamento e cargo devem ser os da requisição")
        used = db.scalar(select(func.coalesce(func.sum(Vacancy.quantity), 0)).where(Vacancy.requisition_id == req.id, Vacancy.status != "cancelled", Vacancy.id != (current.id if current else 0))) or 0
        if used + payload.quantity > int(req.data.get("quantity", 0)):
            raise HTTPException(422, f"A requisição autoriza {req.data.get('quantity')} vaga(s); {used} já estão em vagas abertas")
    ranges = db.scalars(select(SalaryRange).where(SalaryRange.position_id == payload.position_id)).all()
    if ranges and payload.salary_max:
        top = max(Decimal(str(r.max_salary)) for r in ranges)
        if Decimal(str(payload.salary_max)) > top:
            raise HTTPException(422, f"Faixa acima da tabela salarial do cargo (máximo R$ {float(top):,.2f})")

def create_vacancy_from_requisition(db: Session, req: ProcessRequest, actor: User) -> Vacancy | None:
    """Called when a requisição de pessoal is approved: opens a draft vacancy for the RH to complete."""
    if not policy(db, req.company_id)["enabled"]:
        return None
    d = req.data
    row = Vacancy(company_id=req.company_id, code=next_code(db, req.company_id), title=f"{d.get('position')} - {d.get('department')}", department_id=d["department_id"], position_id=d["position_id"],
                  requisition_id=req.id, description=d.get("justification", ""), salary_min=None, salary_max=d.get("salary_proposal"), quantity=int(d.get("quantity", 1)),
                  status="draft", stages=list(policy(db, req.company_id)["stages"]), created_by_id=actor.id)
    requester = db.get(User, req.requested_by_id)
    if requester and requester.employee_id and db.get(Employee, requester.employee_id):
        row.hiring_manager_id = requester.employee_id
    db.add(row); db.flush()
    return row

def recalc_score(db: Session, application: Application):
    scores = [e.score for e in db.scalars(select(ApplicationEvent).where(ApplicationEvent.application_id == application.id, ApplicationEvent.kind == "evaluation")).all() if e.score is not None]
    application.score = Decimal(str(round(sum(scores) / len(scores), 2))) if scores else None

def hr_users(db: Session, company_id: int) -> list[User]:
    return db.scalars(select(User).where(User.role == "hr_manager", User.company_id == company_id, User.active.is_(True))).all()

def on_admission_decided(db: Session, admission: AdmissionRequest, actor: User):
    """Hook from the admission flow: hired → close the application and the filled vacancy; rejected → back to the pipeline."""
    if not admission.application_id:
        return
    application = db.get(Application, admission.application_id)
    if not application or application.status != "offer":
        return
    if admission.status in ("approved", "approved_auto"):
        application.status = "hired"
        log(db, application, actor, "hired", f"Admissão nº {admission.id} aprovada")
        db.flush()
        vacancy = db.get(Vacancy, application.vacancy_id)
        if hires(db, vacancy.id) >= vacancy.quantity:
            vacancy.status, vacancy.closed_at = "filled", now()
    elif admission.status == "rejected":
        application.status = "active"
        log(db, application, actor, "note", f"Admissão nº {admission.id} rejeitada: {admission.comment or ''}")

def on_movement_applied(db: Session, req: ProcessRequest, actor: User):
    application = db.get(Application, req.data.get("application_id") or 0)
    if application and application.status == "offer":
        application.status = "hired"
        log(db, application, actor, "hired", f"Movimentação interna nº {req.id} aprovada")
        db.flush()
        vacancy = db.get(Vacancy, application.vacancy_id)
        if hires(db, vacancy.id) >= vacancy.quantity:
            vacancy.status, vacancy.closed_at = "filled", now()

# --------------------------------------------------------------------------- vacancies

@router.get("/vacancies")
def list_vacancies(company_id: int, status: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    company_scope(user, company_id)
    query = select(Vacancy).where(Vacancy.company_id == company_id).order_by(Vacancy.created_at.desc(), Vacancy.id.desc())
    if status:
        query = query.where(Vacancy.status == status)
    rows = db.scalars(query).all()
    if user.role == "employee":
        config = policy(db, company_id)
        rows = [r for r in rows if is_hiring_manager(user, r) or (config["allow_internal_applications"] and r.status == "open" and r.recruitment_type in ("internal", "mixed") and (not r.deadline or r.deadline >= date.today()))]
    return [vacancy_out(db, r, user) for r in rows]

@router.post("/vacancies", status_code=201)
def create_vacancy(payload: VacancyInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    manager_only(user, payload.company_id)
    if not policy(db, payload.company_id)["enabled"]:
        raise HTTPException(422, "Recrutamento desabilitado para esta empresa")
    check_vacancy_payload(db, payload)
    row = Vacancy(**payload.model_dump(), code=next_code(db, payload.company_id), status="draft", stages=list(policy(db, payload.company_id)["stages"]), created_by_id=user.id)
    db.add(row); db.flush()
    db.add(Activity(company_id=payload.company_id, actor=user.full_name, message=f"Vaga {row.code} ({row.title}) criada."))
    db.commit(); db.refresh(row)
    return vacancy_out(db, row, user)

@router.get("/vacancies/{vacancy_id}")
def get_vacancy_detail(vacancy_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = get_vacancy(db, vacancy_id)
    company_scope(user, row.company_id)
    if not can_see_vacancy_pipeline(user, row):
        visible = any(v["id"] == row.id for v in list_vacancies(row.company_id, None, db, user))
        if not visible:
            raise HTTPException(404, "Vaga não encontrada")
        return vacancy_out(db, row, user)
    return vacancy_out(db, row, user, detail=True)

@router.put("/vacancies/{vacancy_id}")
def update_vacancy(vacancy_id: int, payload: VacancyInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = get_vacancy(db, vacancy_id)
    manager_only(user, row.company_id)
    if payload.company_id != row.company_id:
        raise HTTPException(422, "A vaga não pode mudar de empresa")
    if row.status in ("filled", "closed", "cancelled"):
        raise HTTPException(409, "Vaga encerrada não pode ser alterada")
    if payload.quantity < hires(db, row.id):
        raise HTTPException(422, "A quantidade não pode ser menor que as contratações já feitas")
    if (payload.department_id, payload.position_id) != (row.department_id, row.position_id) and db.scalar(select(Application.id).where(Application.vacancy_id == row.id).limit(1)):
        raise HTTPException(409, "Departamento e cargo não mudam depois que há candidatos")
    check_vacancy_payload(db, payload, row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit(); db.refresh(row)
    return vacancy_out(db, row, user)

@router.post("/vacancies/{vacancy_id}/status")
def change_vacancy_status(vacancy_id: int, payload: StatusInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = get_vacancy(db, vacancy_id)
    manager_only(user, row.company_id)
    allowed = {"draft": {"open", "cancelled"}, "open": {"paused", "closed", "cancelled"}, "paused": {"open", "closed", "cancelled"}}
    if payload.status not in allowed.get(row.status, set()):
        raise HTTPException(409, f"Não é possível passar de {STATUS_LABELS[row.status]} para {STATUS_LABELS[payload.status]}")
    if payload.status == "open":
        if not row.description.strip() or not row.requirements.strip():
            raise HTTPException(422, "Preencha descrição e requisitos antes de publicar a vaga")
        if row.deadline and row.deadline < date.today():
            raise HTTPException(422, "O prazo de inscrição já passou; ajuste a vaga")
    if payload.status in ("closed", "cancelled"):
        if db.scalar(select(Application.id).where(Application.vacancy_id == row.id, Application.status == "offer").limit(1)):
            raise HTTPException(409, "Há contratação em andamento nesta vaga")
        reason = payload.reason.strip() or "Vaga encerrada"
        for application in db.scalars(select(Application).where(Application.vacancy_id == row.id, Application.status == "active")).all():
            application.status, application.rejection_reason = "rejected", reason
            log(db, application, user, "rejected", reason)
        row.closed_at = now()
    row.status = payload.status
    db.add(Activity(company_id=row.company_id, actor=user.full_name, message=f"Vaga {row.code}: {STATUS_LABELS[row.status]}."))
    db.commit(); db.refresh(row)
    return vacancy_out(db, row, user)

# --------------------------------------------------------------------------- talent pool

@router.get("/candidates")
def list_candidates(company_id: int, q: str = Query(default="", max_length=100), db: Session = Depends(get_db), user: User = Depends(current_user)):
    manager_only(user, company_id) if user.role != "consultant" else company_scope(user, company_id)
    query = select(Candidate).where(Candidate.company_id == company_id, Candidate.anonymized_at.is_(None)).order_by(Candidate.full_name)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.where(or_(Candidate.full_name.ilike(term), Candidate.email.ilike(term), Candidate.summary.ilike(term)))
    retention = policy(db, company_id)["data_retention_days"]
    out = []
    for c in db.scalars(query.limit(300)).all():
        last = db.scalar(select(func.max(Application.updated_at)).where(Application.candidate_id == c.id)) or c.created_at
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        out.append({"id": c.id, "full_name": c.full_name, "email": c.email, "phone": c.phone, "city": c.city, "source": c.source, "summary": c.summary, "internal": bool(c.employee_id),
                    "applications": db.scalar(select(func.count(Application.id)).where(Application.candidate_id == c.id)) or 0,
                    "retention_expired": last < now() - timedelta(days=retention), "consent_at": c.consent_at.isoformat()})
    return out

@router.post("/candidates", status_code=201)
def create_candidate(payload: CandidateInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    manager_only(user, payload.company_id)
    if not payload.consent:
        raise HTTPException(422, "Registre o consentimento do candidato para o tratamento dos dados (LGPD)")
    if db.scalar(select(Candidate.id).where(Candidate.company_id == payload.company_id, func.lower(Candidate.email) == str(payload.email).lower())):
        raise HTTPException(409, "Candidato já cadastrado no banco de talentos")
    row = Candidate(**payload.model_dump(exclude={"consent"}), consent_at=now())
    row.email = str(payload.email).lower()
    db.add(row); db.commit(); db.refresh(row)
    return {"id": row.id, "full_name": row.full_name}

@router.post("/candidates/{candidate_id}/anonymize")
def anonymize_candidate(candidate_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Direito de eliminação (LGPD art. 18): remove os dados pessoais e mantém apenas o histórico estatístico."""
    row = db.get(Candidate, candidate_id)
    if not row or row.anonymized_at:
        raise HTTPException(404, "Candidato não encontrado")
    manager_only(user, row.company_id)
    if db.scalar(select(Application.id).where(Application.candidate_id == row.id, Application.status.in_(("active", "offer"))).limit(1)):
        raise HTTPException(409, "Encerre as candidaturas em andamento antes de anonimizar")
    row.full_name, row.email, row.phone, row.city, row.summary = f"Candidato anonimizado nº {row.id}", f"anonimizado-{row.id}@removido.invalid", None, None, ""
    row.anonymized_at = now()
    for event in db.scalars(select(ApplicationEvent).join(Application, Application.id == ApplicationEvent.application_id).where(Application.candidate_id == row.id)).all():
        event.comment = None
    db.add(Activity(company_id=row.company_id, actor=user.full_name, message=f"Dados do candidato nº {row.id} anonimizados (LGPD)."))
    db.commit()
    return {"ok": True}

# --------------------------------------------------------------------------- applications

def _start_application(db: Session, vacancy: Vacancy, candidate: Candidate, user: User) -> Application:
    if vacancy.status != "open":
        raise HTTPException(409, "A vaga não está aberta para candidaturas")
    if vacancy.deadline and vacancy.deadline < date.today():
        raise HTTPException(409, "O prazo de inscrição da vaga terminou")
    if candidate.anonymized_at:
        raise HTTPException(409, "Candidato anonimizado")
    if db.scalar(select(Application.id).where(Application.vacancy_id == vacancy.id, Application.candidate_id == candidate.id)):
        raise HTTPException(409, "Este candidato já participa desta vaga")
    row = Application(company_id=vacancy.company_id, vacancy_id=vacancy.id, candidate_id=candidate.id, stage=vacancy.stages[0], status="active")
    db.add(row); db.flush()
    log(db, row, user, "applied", "Candidatura interna" if candidate.employee_id else None)
    return row

@router.post("/vacancies/{vacancy_id}/applications", status_code=201)
def add_application(vacancy_id: int, payload: ApplyInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    vacancy = get_vacancy(db, vacancy_id)
    manager_only(user, vacancy.company_id)
    candidate = db.get(Candidate, payload.candidate_id)
    if not candidate or candidate.company_id != vacancy.company_id:
        raise HTTPException(422, "Candidato não encontrado nesta empresa")
    if vacancy.recruitment_type == "internal" and not candidate.employee_id:
        raise HTTPException(422, "Vaga exclusiva para candidatos internos")
    row = _start_application(db, vacancy, candidate, user)
    db.commit(); db.refresh(row)
    return application_out(db, row, "full", detail=True)

@router.post("/vacancies/{vacancy_id}/apply", status_code=201)
def internal_apply(vacancy_id: int, payload: InternalApplyInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Vagas internas: o colaborador se candidata pelo autoatendimento."""
    vacancy = get_vacancy(db, vacancy_id)
    if not user.employee_id or user.company_id != vacancy.company_id:
        raise HTTPException(403, "Candidatura interna disponível aos colaboradores da empresa")
    config = policy(db, vacancy.company_id)
    if not config["allow_internal_applications"] or vacancy.recruitment_type == "external":
        raise HTTPException(403, "Esta vaga não aceita candidaturas internas")
    if not payload.consent:
        raise HTTPException(422, "Confirme o consentimento para o uso dos seus dados no processo seletivo")
    employee = db.get(Employee, user.employee_id)
    months = (date.today().year - employee.admission_date.year) * 12 + date.today().month - employee.admission_date.month - (date.today().day < employee.admission_date.day)
    if months < config["internal_min_months"]:
        raise HTTPException(422, f"Candidaturas internas exigem {config['internal_min_months']} meses de empresa")
    if (employee.department_id, employee.position_id) == (vacancy.department_id, vacancy.position_id):
        raise HTTPException(422, "Você já ocupa este cargo neste departamento")
    candidate = db.scalar(select(Candidate).where(Candidate.company_id == vacancy.company_id, Candidate.employee_id == employee.id))
    if not candidate:
        candidate = db.scalar(select(Candidate).where(Candidate.company_id == vacancy.company_id, func.lower(Candidate.email) == employee.email.lower()))
    if not candidate:
        candidate = Candidate(company_id=vacancy.company_id, full_name=employee.full_name, email=employee.email.lower(), source="interno", employee_id=employee.id, consent_at=now())
        db.add(candidate); db.flush()
    candidate.employee_id, candidate.consent_at = employee.id, now()
    if payload.summary.strip():
        candidate.summary = payload.summary.strip()
    row = _start_application(db, vacancy, candidate, user)
    recipients = hr_users(db, vacancy.company_id)
    if config["notify_internal_manager"] and employee.manager_id:
        recipients += db.scalars(select(User).where(User.employee_id == employee.manager_id, User.active.is_(True))).all()
    processes.notify(db, recipients, f"{employee.full_name} se candidatou à vaga interna {vacancy.code} ({vacancy.title}).")
    db.commit(); db.refresh(row)
    return application_out(db, row, "candidate", detail=True)

@router.get("/applications/mine")
def my_applications(db: Session = Depends(get_db), user: User = Depends(current_user)):
    if not user.employee_id:
        return []
    candidate_ids = db.scalars(select(Candidate.id).where(Candidate.employee_id == user.employee_id)).all()
    rows = db.scalars(select(Application).where(Application.candidate_id.in_(candidate_ids)).order_by(Application.created_at.desc())).all()
    return [application_out(db, r, "candidate") for r in rows]

@router.get("/applications/{application_id}")
def get_application(application_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Candidatura não encontrada")
    return application_out(db, row, application_access(db, user, row), detail=True)

def _active(application: Application):
    if application.status != "active":
        raise HTTPException(409, f"Candidatura com status {APP_STATUS[application.status]}")

@router.post("/applications/{application_id}/move")
def move_application(application_id: int, payload: MoveInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Candidatura não encontrada")
    manager_only(user, row.company_id)
    _active(row)
    vacancy = db.get(Vacancy, row.vacancy_id)
    if payload.to_stage not in vacancy.stages or payload.to_stage == row.stage:
        raise HTTPException(422, "Etapa inválida para esta vaga")
    log(db, row, user, "moved", payload.comment.strip() or None, to_stage=payload.to_stage)
    row.stage = payload.to_stage
    candidate = db.get(Candidate, row.candidate_id)
    if candidate.employee_id:
        processes.notify(db, db.scalars(select(User).where(User.employee_id == candidate.employee_id, User.active.is_(True))).all(), f"Sua candidatura à vaga {vacancy.code} avançou para a etapa {row.stage}.")
    db.commit(); db.refresh(row)
    return application_out(db, row, "full", detail=True)

@router.post("/applications/{application_id}/evaluate")
def evaluate_application(application_id: int, payload: EvaluateInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Candidatura não encontrada")
    if application_access(db, user, row) != "full" or user.role == "consultant":
        raise HTTPException(403, "Somente RH, gestor da vaga ou entrevistador avaliam o candidato")
    _active(row)
    criteria = policy(db, row.company_id)["evaluation_criteria"]
    if set(payload.criteria) != set(criteria) or any(type(v) is not int or not 1 <= v <= 5 for v in payload.criteria.values()):
        raise HTTPException(422, f"Atribua uma nota de 1 a 5 para cada critério: {', '.join(criteria)}")
    if db.scalar(select(ApplicationEvent.id).where(ApplicationEvent.application_id == row.id, ApplicationEvent.kind == "evaluation", ApplicationEvent.stage == row.stage, ApplicationEvent.actor_id == user.id).limit(1)):
        raise HTTPException(409, "Você já avaliou o candidato nesta etapa")
    score = round(sum(payload.criteria.values()) / len(payload.criteria))
    log(db, row, user, "evaluation", payload.comment.strip() or None, score=score, criteria=payload.criteria)
    db.flush()
    recalc_score(db, row)
    db.commit(); db.refresh(row)
    return application_out(db, row, "full", detail=True)

@router.post("/applications/{application_id}/reject")
def reject_application(application_id: int, payload: RejectInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Candidatura não encontrada")
    manager_only(user, row.company_id)
    _active(row)
    row.status, row.rejection_reason = "rejected", payload.reason.strip()
    log(db, row, user, "rejected", row.rejection_reason)
    candidate = db.get(Candidate, row.candidate_id)
    if candidate.employee_id:
        vacancy = db.get(Vacancy, row.vacancy_id)
        processes.notify(db, db.scalars(select(User).where(User.employee_id == candidate.employee_id, User.active.is_(True))).all(), f"Sua candidatura à vaga {vacancy.code} foi encerrada.")
    db.commit(); db.refresh(row)
    return application_out(db, row, "full", detail=True)

@router.post("/applications/{application_id}/withdraw")
def withdraw_application(application_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Candidatura não encontrada")
    access = application_access(db, user, row)
    if access != "candidate" and user.role not in MANAGERS:
        raise HTTPException(403, "Somente o candidato ou o RH registram a desistência")
    _active(row)
    row.status = "withdrawn"
    log(db, row, user, "withdrawn")
    db.commit(); db.refresh(row)
    return application_out(db, row, access, detail=True)

# --------------------------------------------------------------------------- interviews

@router.post("/applications/{application_id}/interviews", status_code=201)
def schedule_interview(application_id: int, payload: InterviewInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Candidatura não encontrada")
    manager_only(user, row.company_id)
    _active(row)
    # Times without offset are Brasília local time; stored in UTC so comparisons are database independent.
    when = (payload.scheduled_at if payload.scheduled_at.tzinfo else payload.scheduled_at.replace(tzinfo=processes.BRT)).astimezone(timezone.utc)
    if when < now():
        raise HTTPException(422, "A entrevista deve ser agendada para uma data futura")
    interviewer = db.get(User, payload.interviewer_id)
    if not interviewer or not interviewer.active or (interviewer.role != "system_admin" and interviewer.company_id != row.company_id):
        raise HTTPException(422, "O entrevistador deve ser um usuário ativo da empresa")
    end = when + timedelta(minutes=payload.duration_minutes)
    for other in db.scalars(select(Interview).where(Interview.interviewer_id == interviewer.id, Interview.status == "scheduled")).all():
        start = other.scheduled_at if other.scheduled_at.tzinfo else other.scheduled_at.replace(tzinfo=timezone.utc)
        if start < end and when < start + timedelta(minutes=other.duration_minutes):
            raise HTTPException(409, "O entrevistador já tem entrevista nesse horário")
    if payload.mode == "presencial" and not (payload.location or "").strip():
        raise HTTPException(422, "Informe o local da entrevista presencial")
    interview = Interview(application_id=row.id, stage=row.stage, scheduled_at=when, duration_minutes=payload.duration_minutes, interviewer_id=interviewer.id, mode=payload.mode, location=payload.location)
    db.add(interview); db.flush()
    vacancy, candidate = db.get(Vacancy, row.vacancy_id), db.get(Candidate, row.candidate_id)
    log(db, row, user, "interview", f"Entrevista agendada para {when.astimezone(processes.BRT).strftime('%d/%m/%Y %H:%M')} com {interviewer.full_name}")
    processes.notify(db, [interviewer], f"Entrevista com {candidate.full_name} ({vacancy.code}) em {when.astimezone(processes.BRT).strftime('%d/%m/%Y %H:%M')}.")
    db.commit()
    return interview_out(db, interview)

@router.get("/interviews/mine")
def my_interviews(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Interview).where(Interview.interviewer_id == user.id).order_by(Interview.scheduled_at)).all()
    out = []
    for i in rows:
        application = db.get(Application, i.application_id)
        out.append({**interview_out(db, i), "candidate_name": db.get(Candidate, application.candidate_id).full_name, "vacancy_title": db.get(Vacancy, application.vacancy_id).title})
    return out

@router.post("/interviews/{interview_id}/status")
def interview_status(interview_id: int, payload: InterviewStatusInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    row = db.get(Interview, interview_id)
    if not row:
        raise HTTPException(404, "Entrevista não encontrada")
    application = db.get(Application, row.application_id)
    if row.interviewer_id != user.id:
        manager_only(user, application.company_id)
    if row.status != "scheduled":
        raise HTTPException(409, "Entrevista já encerrada")
    row.status = payload.status
    log(db, application, user, "interview", {"done": "Entrevista realizada", "cancelled": "Entrevista cancelada", "no_show": "Candidato não compareceu"}[payload.status])
    db.commit()
    return interview_out(db, row)

# --------------------------------------------------------------------------- hiring

@router.post("/applications/{application_id}/hire")
def hire(application_id: int, payload: HireInput, db: Session = Depends(get_db), user: User = Depends(current_user)):
    """Última etapa: candidato externo vira solicitação de admissão; interno vira movimentação."""
    row = db.get(Application, application_id)
    if not row:
        raise HTTPException(404, "Candidatura não encontrada")
    manager_only(user, row.company_id)
    _active(row)
    vacancy, candidate = db.get(Vacancy, row.vacancy_id), db.get(Candidate, row.candidate_id)
    if row.stage != vacancy.stages[-1]:
        raise HTTPException(422, f"A contratação acontece na última etapa ({vacancy.stages[-1]})")
    if hires(db, vacancy.id) + (db.scalar(select(func.count(Application.id)).where(Application.vacancy_id == vacancy.id, Application.status == "offer")) or 0) >= vacancy.quantity:
        raise HTTPException(409, "Todas as posições desta vaga já estão preenchidas ou em contratação")
    if vacancy.salary_min and payload.salary < float(vacancy.salary_min) or vacancy.salary_max and payload.salary > float(vacancy.salary_max):
        raise HTTPException(422, "Salário fora da faixa anunciada na vaga")
    if payload.job_position_id:
        seat = db.get(JobPosition, payload.job_position_id)
        if not seat or seat.company_id != vacancy.company_id or seat.status != "vacant" or (seat.department_id, seat.position_id) != (vacancy.department_id, vacancy.position_id):
            raise HTTPException(422, "Selecione uma posição vaga do departamento e cargo da vaga")
    if candidate.employee_id:
        employee = db.get(Employee, candidate.employee_id)
        if not employee or employee.status == "Desligado" or employee.company_id != vacancy.company_id:
            raise HTTPException(409, "Colaborador interno não está ativo nesta empresa")
        data = {"effective_date": payload.planned_date.isoformat(), "department_id": vacancy.department_id, "position_id": vacancy.position_id, "salary": payload.salary,
                "reason": f"Aprovado no processo seletivo interno da vaga {vacancy.code}"}
        if payload.group_id: data["group_id"] = payload.group_id
        if vacancy.hiring_manager_id and vacancy.hiring_manager_id != employee.id: data["manager_id"] = vacancy.hiring_manager_id
        normalized = processes.validate(db, "movement", vacancy.company_id, employee, data, user)
        normalized["application_id"] = row.id
        row.status = "offer"
        log(db, row, user, "offer", payload.comment.strip() or "Movimentação interna solicitada")
        req = processes.start(db, "movement", vacancy.company_id, employee, user, normalized)
        db.commit(); db.refresh(row)
        return {"kind": "movement", "process_request_id": req.id, "status": req.status, "application": application_out(db, row, "full", detail=True)}
    from app import hr_api
    admission = AdmissionRequestInput(company_id=vacancy.company_id, candidate_name=candidate.full_name, candidate_email=candidate.email, department_id=vacancy.department_id,
                                      position_id=vacancy.position_id, planned_date=payload.planned_date, documents_complete=payload.documents_complete,
                                      group_id=payload.group_id or db.scalar(select(EmployeeGroup.id).where(EmployeeGroup.company_id == vacancy.company_id).order_by(EmployeeGroup.id)),
                                      salary=payload.salary, registration=payload.registration, contract_type=vacancy.contract_type, job_position_id=payload.job_position_id)
    config = active_config(db, vacancy.company_id, "admission")
    errors = hr_api.admission_errors(admission, config) + hr_api.admission_hire_errors(db, admission)
    if errors:
        raise HTTPException(422, errors)
    status = "pending_approval" if config["approval_required"] else "approved_auto"
    record = AdmissionRequest(company_id=vacancy.company_id, candidate_name=candidate.full_name, candidate_email=candidate.email, department_id=vacancy.department_id, position_id=vacancy.position_id,
                              planned_date=payload.planned_date, documents_complete=payload.documents_complete, status=status, requested_by_id=user.id, group_id=admission.group_id,
                              salary=payload.salary, registration=payload.registration.strip() if payload.registration else None, contract_type=vacancy.contract_type,
                              job_position_id=payload.job_position_id, application_id=row.id)
    db.add(record); db.flush()
    row.status, row.admission_id = "offer", record.id
    log(db, row, user, "offer", payload.comment.strip() or f"Solicitação de admissão nº {record.id} criada")
    if status == "approved_auto":
        hr_api.hire_from_admission(db, record, user)
        on_admission_decided(db, record, user)
    db.add(Activity(company_id=vacancy.company_id, actor=user.full_name, message=f"Admissão de {candidate.full_name} solicitada a partir da vaga {vacancy.code}."))
    db.commit(); db.refresh(row)
    return {"kind": "admission", "admission_id": record.id, "status": status, "application": application_out(db, row, "full", detail=True)}

@router.get("/summary")
def summary(company_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role not in VIEWERS:
        raise HTTPException(403, "Indicadores restritos ao RH")
    company_scope(user, company_id)
    vacancies = db.scalars(select(Vacancy).where(Vacancy.company_id == company_id)).all()
    apps = db.scalars(select(Application).where(Application.company_id == company_id)).all()
    hired = [a for a in apps if a.status == "hired"]
    days = []
    for a in hired:
        event = db.scalar(select(ApplicationEvent).where(ApplicationEvent.application_id == a.id, ApplicationEvent.kind == "hired").order_by(ApplicationEvent.id.desc()).limit(1))
        if event and event.created_at and a.created_at:
            days.append((event.created_at - a.created_at).days)
    return {"open_vacancies": sum(1 for v in vacancies if v.status == "open"), "draft_vacancies": sum(1 for v in vacancies if v.status == "draft"),
            "active_applications": sum(1 for a in apps if a.status in ("active", "offer")), "hired": len(hired), "internal_hired": sum(1 for a in hired if db.get(Candidate, a.candidate_id).employee_id),
            "average_days_to_hire": round(sum(days) / len(days), 1) if days else None, "upcoming_interviews": db.scalar(select(func.count(Interview.id)).join(Application, Application.id == Interview.application_id).where(Application.company_id == company_id, Interview.status == "scheduled", Interview.scheduled_at >= now())) or 0}

@router.get("/interviewers")
def interviewers(company_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    manager_only(user, company_id)
    rows = db.scalars(select(User).where(User.active.is_(True), or_(User.company_id == company_id, User.role == "system_admin")).order_by(User.full_name)).all()
    return [{"id": u.id, "name": u.full_name, "role": u.role} for u in rows]
