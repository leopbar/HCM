import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (Activity, AdmissionRequest, Company, Department, Employee, EmployeeGroup, EmployeeMovement, HeadcountPlan, JobPosition,
    PayrollEvent, PayrollSimulation, Position, ProcessRequest, SalaryRange, TimeRecord, User, VacationRequest, BenefitCatalog, EnvironmentPublication)
from app.schemas import CompanyCatalogItemInput, CompanyInput, CompanyOut, EmployeeInput, EmployeeOut, EmployeeUpdate, LoginInput
from app.security import (MAX_FAILED_LOGINS, LOCK_MINUTES, clear_session_cookie, create_session, current_session, current_user, hash_password, password_expired,
    require_roles, set_session_cookie, verify_password, _aware)
from app.validators import format_cnpj, valid_cnpj
from app.policies import active_config
from app.processes import apply_due_movements, contracting_errors, creates_cycle
from app.process_api import router as process_router
from app.setup_api import router as setup_router
from app.recruitment_api import router as recruitment_router
from app.seed import seed_demo_data, seed_vacation_configurations
from app.vacation_api import router as vacation_router
from app.hr_api import router as hr_router
from app.environment_api import router as environment_router
from app.training_api import router as training_router
from app.access_api import router as access_router
from app.integration_v2 import router as integration_v2_router  # contract v2; the v1 modules remain as helpers only

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db = next(get_db())
    try:
        seed_demo_data(db)
        apply_due_movements(db)
        db.commit()
    finally:
        db.close()
    yield

DOCS = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"
app = FastAPI(title="HCM Lab API", description="Simulador de gestão de pessoas com processos parametrizáveis.", version="0.2.0", lifespan=lifespan,
              docs_url="/docs" if DOCS else None, redoc_url=None, openapi_url="/openapi.json" if DOCS else None)
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-HCM-Execution-Key"])

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if not request.url.path.startswith("/docs"):
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response
app.include_router(vacation_router)
app.include_router(hr_router)
app.include_router(environment_router)
app.include_router(training_router)
app.include_router(access_router)
app.include_router(integration_v2_router)
app.include_router(process_router)
app.include_router(setup_router)
app.include_router(recruitment_router)

def ensure_company_access(user: User, company_id: int):
    if user.role in ("system_admin", "consultant"):
        return
    if user.company_id != company_id:
        raise HTTPException(status_code=403, detail="Acesso restrito à empresa vinculada ao seu perfil")

def company_out(db: Session, company: Company):
    count = db.scalar(select(func.count(Employee.id)).where(Employee.company_id == company.id)) or 0
    return CompanyOut(id=company.id, code=company.code, legal_name=company.legal_name, trade_name=company.trade_name, cnpj=company.cnpj, city=company.city, state=company.state, sector=company.sector, status=company.status, employee_count=count)

def employee_out(db: Session, employee: Employee):
    company = db.get(Company, employee.company_id)
    department = db.get(Department, employee.department_id)
    position = db.get(Position, employee.position_id)
    group = db.get(EmployeeGroup, employee.group_id)
    manager = db.get(Employee, employee.manager_id) if employee.manager_id else None
    return EmployeeOut(id=employee.id, company_id=employee.company_id, company_name=company.trade_name, department=department.name, position=position.title, group_name=group.name, registration=employee.registration, full_name=employee.full_name, email=employee.email, admission_date=employee.admission_date, salary=float(employee.salary), status=employee.status,
                       manager_id=employee.manager_id, manager_name=manager.full_name if manager else None, contract_type=employee.contract_type, department_id=employee.department_id, position_id=employee.position_id, group_id=employee.group_id, termination_date=employee.termination_date, calculation_group=employee.calculation_group)

PERSONAL = ("cpf", "birth_date", "phone", "personal_email", "marital_status", "address", "emergency_contact")

def employee_detail(db: Session, employee: Employee, user: User):
    out = employee_out(db, employee).model_dump(mode="json")
    if user.role != "employee" or user.employee_id == employee.id:
        out.update({key: (getattr(employee, key).isoformat() if key == "birth_date" and getattr(employee, key) else getattr(employee, key)) for key in PERSONAL})
    out["movements"] = [{"kind": m.kind, "effective_date": m.effective_date.isoformat(), "status": m.status, "before": m.before_snapshot, "after": m.after_snapshot} for m in db.scalars(select(EmployeeMovement).where(EmployeeMovement.employee_id == employee.id).order_by(EmployeeMovement.id.desc()).limit(20))]
    seat = db.scalar(select(JobPosition).where(JobPosition.employee_id == employee.id))
    out["job_position"] = seat.code if seat else None
    return out

def check_manager(db: Session, company_id: int, manager_id: int | None, employee_id: int | None = None):
    if manager_id is None:
        return
    manager = db.get(Employee, manager_id)
    if not manager or manager.company_id != company_id or manager.status == "Desligado":
        raise HTTPException(422, "O gestor imediato deve ser um colaborador ativo da mesma empresa")
    if employee_id and (manager_id == employee_id or creates_cycle(db, employee_id, manager_id)):
        raise HTTPException(422, "O gestor escolhido criaria um ciclo na hierarquia")

def check_calculation_group(db: Session, company_id: int, value: str):
    groups = active_config(db, company_id, "payroll_setup")["calculation_groups"]
    if value not in groups:
        raise HTTPException(422, f"Grupo de cálculo inválido. Use um de: {', '.join(groups)} (Configuração › Folha de Pagamento).")

def check_cnpj(cnpj: str, current: Company | None = None) -> str:
    if current and cnpj == current.cnpj:
        return cnpj  # legacy demonstration identifiers stay editable
    if not valid_cnpj(cnpj):
        raise HTTPException(422, "CNPJ inválido: confira os dígitos verificadores.")
    return format_cnpj(cnpj)

LOGIN_WINDOW = 300
LOGIN_PER_IP = int(os.getenv("LOGIN_ATTEMPTS_PER_IP", "100"))
_login_hits: dict[str, deque] = defaultdict(deque)

def throttle_login(request: Request):
    key = request.client.host if request.client else "unknown"
    hits, current = _login_hits[key], time.monotonic()
    while hits and current - hits[0] > LOGIN_WINDOW:
        hits.popleft()
    if len(hits) >= LOGIN_PER_IP:
        raise HTTPException(429, "Muitas tentativas de login. Aguarde alguns minutos.")
    hits.append(current)

@app.get("/health")
def health():
    return {"status": "ok", "application": "HCM Lab"}

GENERIC_LOGIN_ERROR = "E-mail ou senha inválidos"
_DUMMY_HASH = hash_password("timing-equalizer-not-a-password")

@app.post("/api/auth/login")
def login(payload: LoginInput, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle_login(request)
    user = db.scalar(select(User).where(func.lower(User.email) == payload.email.lower()).with_for_update())
    now = datetime.now(timezone.utc)
    if not user:
        verify_password(payload.password, _DUMMY_HASH)
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    if user.locked_until and _aware(user.locked_until) > now:
        raise HTTPException(status_code=423, detail=f"Conta bloqueada temporariamente por tentativas inválidas. Tente novamente em até {LOCK_MINUTES} minutos ou peça o desbloqueio ao administrador.")
    if not verify_password(payload.password, user.password_hash):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        if user.failed_login_attempts >= MAX_FAILED_LOGINS:
            user.locked_until, user.failed_login_attempts = now + timedelta(minutes=LOCK_MINUTES), 0
            db.add(Activity(company_id=user.company_id, actor="HCM Lab", message=f"Conta {user.email} bloqueada após {MAX_FAILED_LOGINS} tentativas inválidas."))
        db.commit()
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    if not user.active:
        raise HTTPException(status_code=401, detail=GENERIC_LOGIN_ERROR)
    if not user.password_hash.startswith("pbkdf2$"):
        user.password_hash = hash_password(payload.password)  # upgrade legacy hashes transparently
    user.failed_login_attempts, user.locked_until, user.last_login_at = 0, None, now
    token = create_session(db, user, request)
    db.commit()
    set_session_cookie(response, token)
    return {"access_token": token, "token_type": "bearer", "user": user_payload(user, db)}

@app.post("/api/auth/logout")
def logout(response: Response, pair=Depends(current_session), db: Session = Depends(get_db)):
    pair[1].revoked_at = datetime.now(timezone.utc)
    db.commit()
    clear_session_cookie(response)
    return {"ok": True}

def user_payload(user: User, db: Session):
    from app.models import AccessGroup
    group = db.get(AccessGroup, user.access_group_id) if user.access_group_id else None
    has_team = bool(user.employee_id and db.scalar(select(Employee.id).where(Employee.manager_id == user.employee_id, Employee.status != "Desligado").limit(1)))
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role, "company_id": user.company_id, "employee_id": user.employee_id, "allowed_modules": group.modules if group else None,
            "must_change_password": bool(user.must_change_password or password_expired(user)), "has_team": has_team}

@app.get("/api/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return user_payload(user, db)

@app.get("/api/companies", response_model=list[CompanyOut])
def list_companies(db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Company).order_by(Company.code)
    if user.role not in ("system_admin", "consultant"):
        query = query.where(Company.id == user.company_id)
    return [company_out(db, row) for row in db.scalars(query).all()]

@app.post("/api/companies", response_model=CompanyOut, status_code=201)
def create_company(payload: CompanyInput, db: Session = Depends(get_db), user: User = Depends(require_roles("system_admin"))):
    company = Company(**{**payload.model_dump(), "cnpj": check_cnpj(payload.cnpj), "code": payload.code.strip().upper()})
    db.add(company)
    db.add(Activity(actor=user.full_name, message=f"Empresa {payload.trade_name} cadastrada."))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Código ou CNPJ já cadastrado") from exc
    db.refresh(company)
    seed_vacation_configurations(db)  # company default rule in production and homologation
    return company_out(db, company)

@app.patch("/api/companies/{company_id}", response_model=CompanyOut)
def update_company(company_id: int, payload: CompanyInput, db: Session = Depends(get_db), user: User = Depends(require_roles("system_admin"))):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    values = {**payload.model_dump(), "cnpj": check_cnpj(payload.cnpj, company), "code": payload.code.strip().upper()}
    if payload.status == "Inativa" and db.scalar(select(func.count(Employee.id)).where(Employee.company_id == company.id, Employee.status != "Desligado")):
        raise HTTPException(status_code=409, detail="Desligue ou transfira os colaboradores antes de inativar a empresa")
    for key, value in values.items():
        setattr(company, key, value)
    db.add(Activity(company_id=company.id, actor=user.full_name, message=f"Cadastro da empresa {company.trade_name} atualizado."))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Código ou CNPJ já cadastrado") from exc
    db.refresh(company)
    return company_out(db, company)

@app.get("/api/employees", response_model=list[EmployeeOut])
def list_employees(company_id: int | None = None, q: str = Query(default="", max_length=100), status: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    query = select(Employee).order_by(Employee.registration)
    if user.role in ("employee", "hr_manager") and company_id and company_id != user.company_id:
        raise HTTPException(status_code=403, detail="Acesso restrito à empresa vinculada ao seu perfil")
    if user.role == "employee":
        query = query.where(Employee.id == user.employee_id)
    elif user.role == "hr_manager":
        query = query.where(Employee.company_id == user.company_id)
    elif company_id:
        query = query.where(Employee.company_id == company_id)
    if company_id and user.role not in ("employee", "hr_manager"):
        query = query.where(Employee.company_id == company_id)
    if q:
        term = f"%{q.strip()}%"
        query = query.where(or_(Employee.full_name.ilike(term), Employee.registration.ilike(term), Employee.email.ilike(term)))
    if status:
        query = query.where(Employee.status == status)
    return [employee_out(db, row) for row in db.scalars(query).all()]

@app.post("/api/employees", response_model=EmployeeOut, status_code=201)
def create_employee(payload: EmployeeInput, db: Session = Depends(get_db), user: User = Depends(require_roles("system_admin", "consultant", "hr_manager"))):
    ensure_company_access(user, payload.company_id)
    company = db.get(Company, payload.company_id)
    department = db.get(Department, payload.department_id)
    position = db.get(Position, payload.position_id)
    group = db.get(EmployeeGroup, payload.group_id)
    if not company or not department or not position or not group or any(x.company_id != payload.company_id for x in (department, position, group)):
        raise HTTPException(status_code=422, detail="Empresa, departamento, cargo e grupo devem ser válidos e pertencer à mesma empresa")
    if company.status != "Ativa":
        raise HTTPException(status_code=422, detail="Empresa inativa não aceita novos colaboradores")
    check_manager(db, company.id, payload.manager_id)
    check_calculation_group(db, company.id, payload.calculation_group)
    errors = contracting_errors(db, company.id, payload.department_id, payload.position_id, payload.salary, payload.job_position_id)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    if db.scalar(select(Employee.id).where(Employee.company_id == company.id, (Employee.registration == payload.registration.strip()) | (func.lower(Employee.email) == str(payload.email).lower()))):
        raise HTTPException(status_code=409, detail="Matrícula ou e-mail já cadastrado nesta empresa")
    employee = Employee(**payload.model_dump(exclude={"job_position_id"}))
    employee.registration, employee.email = payload.registration.strip(), str(payload.email).lower()
    try:
        db.add(employee)
        db.flush()
        occupy_position(db, employee, payload.job_position_id)
        db.add(Activity(company_id=company.id, actor=user.full_name, message=f"Funcionário {employee.full_name} cadastrado."))
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Matrícula ou e-mail já cadastrado nesta empresa") from exc
    db.refresh(employee)
    return employee_out(db, employee)

def occupy_position(db: Session, employee: Employee, job_position_id: int | None):
    if job_position_id:
        seat = db.get(JobPosition, job_position_id)
        seat.employee_id, seat.status = employee.id, "occupied"

@app.get("/api/employees/{employee_id}")
def get_employee(employee_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    if user.role == "employee":
        from app.processes import allowed_target_ids
        allowed = allowed_target_ids(db, user, "movement") or set()
        if employee.id != user.employee_id and employee.id not in allowed:
            raise HTTPException(status_code=403, detail="Funcionários só podem consultar o próprio cadastro e a própria equipe")
    else:
        ensure_company_access(user, employee.company_id)
    return employee_detail(db, employee, user)

@app.patch("/api/employees/{employee_id}")
def update_employee(employee_id: int, payload: EmployeeUpdate, db: Session = Depends(get_db), user: User = Depends(require_roles("system_admin", "hr_manager"))):
    """Correção cadastral pelo RH. Mudanças de cargo, salário, área e desligamento seguem os processos de Movimentação e Rescisão."""
    employee = db.get(Employee, employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="Funcionário não encontrado")
    ensure_company_access(user, employee.company_id)
    if employee.status == "Desligado" and payload.status != "Desligado":
        raise HTTPException(status_code=409, detail="Para readmitir, registre uma nova admissão")
    if payload.status == "Desligado" and employee.status != "Desligado":
        raise HTTPException(status_code=422, detail="Use o processo de Rescisão para desligar o colaborador")
    check_manager(db, employee.company_id, payload.manager_id, employee.id)
    if db.scalar(select(Employee.id).where(Employee.company_id == employee.company_id, func.lower(Employee.email) == str(payload.email).lower(), Employee.id != employee.id)):
        raise HTTPException(status_code=409, detail="E-mail já cadastrado nesta empresa")
    check_calculation_group(db, employee.company_id, payload.calculation_group)
    before = {key: getattr(employee, key) for key in ("full_name", "email", "manager_id", "status", "contract_type", "calculation_group", *PERSONAL)}
    for key, value in payload.model_dump().items():
        setattr(employee, key, str(value).lower() if key == "email" else value)
    changed = [key for key, value in before.items() if getattr(employee, key) != value]
    db.add(Activity(company_id=employee.company_id, actor=user.full_name, message=f"Cadastro de {employee.full_name} corrigido ({', '.join(changed) or 'sem alterações'})."))
    db.commit()
    return employee_detail(db, employee, user)

@app.get("/api/companies/{company_id}/options")
def company_options(company_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    ensure_company_access(user, company_id)
    if not db.get(Company, company_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    return {
        "departments": [{"id": x.id, "name": x.name} for x in db.scalars(select(Department).where(Department.company_id == company_id).order_by(Department.name))],
        "positions": [{"id": x.id, "title": x.title} for x in db.scalars(select(Position).where(Position.company_id == company_id).order_by(Position.title))],
        "groups": [{"id": x.id, "name": x.name} for x in db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id == company_id).order_by(EmployeeGroup.name))],
    }

@app.post("/api/companies/{company_id}/structure/{kind}", status_code=201)
def create_company_structure_item(company_id: int, kind: Literal["departments", "positions", "groups"], payload: CompanyCatalogItemInput, db: Session = Depends(get_db), user: User = Depends(require_roles("system_admin"))):
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    catalogs = {
        "departments": (Department, "name", "Departamento"),
        "positions": (Position, "title", "Cargo"),
        "groups": (EmployeeGroup, "name", "Grupo de funcionários"),
    }
    model, field, label = catalogs[kind]
    name = payload.name
    column = getattr(model, field)
    exists = db.scalar(select(model.id).where(model.company_id == company_id, func.lower(column) == name.lower()))
    if exists:
        raise HTTPException(status_code=409, detail=f"{label} já cadastrado nesta empresa")
    row = model(company_id=company_id, **{field: name})
    db.add(row)
    db.add(Activity(company_id=company.id, actor=user.full_name, message=f"{label} {name} cadastrado para {company.trade_name}."))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{label} já cadastrado nesta empresa") from exc
    db.refresh(row)
    return {"id": row.id, "name": name, "title": name}

STRUCTURE = {"departments": (Department, "name", "Departamento", "department_id"), "positions": (Position, "title", "Cargo", "position_id"), "groups": (EmployeeGroup, "name", "Grupo de funcionários", "group_id")}

@app.patch("/api/companies/{company_id}/structure/{kind}/{item_id}")
def rename_company_structure_item(company_id: int, kind: Literal["departments", "positions", "groups"], item_id: int, payload: CompanyCatalogItemInput, db: Session = Depends(get_db), user: User = Depends(require_roles("system_admin"))):
    model, field, label, _ = STRUCTURE[kind]
    row = db.get(model, item_id)
    if not row or row.company_id != company_id:
        raise HTTPException(status_code=404, detail=f"{label} não encontrado")
    if db.scalar(select(model.id).where(model.company_id == company_id, func.lower(getattr(model, field)) == payload.name.lower(), model.id != item_id)):
        raise HTTPException(status_code=409, detail=f"{label} já cadastrado nesta empresa")
    old = getattr(row, field)
    setattr(row, field, payload.name)
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"{label} {old} renomeado para {payload.name}."))
    db.commit()
    return {"id": row.id, "name": payload.name, "title": payload.name}

@app.delete("/api/companies/{company_id}/structure/{kind}/{item_id}", status_code=204)
def delete_company_structure_item(company_id: int, kind: Literal["departments", "positions", "groups"], item_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles("system_admin"))):
    model, field, label, column = STRUCTURE[kind]
    row = db.get(model, item_id)
    if not row or row.company_id != company_id:
        raise HTTPException(status_code=404, detail=f"{label} não encontrado")
    in_use = db.scalar(select(func.count(Employee.id)).where(getattr(Employee, column) == item_id)) or 0
    in_use += db.scalar(select(func.count(AdmissionRequest.id)).where(getattr(AdmissionRequest, column) == item_id)) or 0
    if kind != "groups":
        in_use += db.scalar(select(func.count(JobPosition.id)).where(getattr(JobPosition, column) == item_id)) or 0
        in_use += db.scalar(select(func.count(HeadcountPlan.id)).where(getattr(HeadcountPlan, column) == item_id)) or 0
    if kind == "positions":
        in_use += db.scalar(select(func.count(SalaryRange.id)).where(SalaryRange.position_id == item_id)) or 0
    if kind == "groups":
        in_use += db.scalar(select(func.count(PayrollEvent.id)).where(PayrollEvent.group_id == item_id)) or 0
        in_use += db.scalar(select(func.count(BenefitCatalog.id)).where(BenefitCatalog.group_id == item_id)) or 0
    if in_use:
        raise HTTPException(status_code=409, detail=f"{label} em uso por {in_use} registro(s); mova-os antes de excluir")
    db.delete(row)
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"{label} {getattr(row, field)} excluído."))
    db.commit()

@app.get("/api/dashboard")
def dashboard(company_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)):
    if user.role not in ("system_admin", "consultant"):
        company_id = user.company_id
    employee_query = select(Employee)
    company_query = select(Company)
    activity_query = select(Activity).order_by(Activity.created_at.desc()).limit(8)
    vacation_query = select(VacationRequest).where(VacationRequest.status == "pending_approval")
    if company_id:
        employee_query = employee_query.where(Employee.company_id == company_id)
        company_query = company_query.where(Company.id == company_id)
        activity_query = activity_query.where((Activity.company_id == company_id) | (Activity.company_id.is_(None)))
        vacation_query = vacation_query.where(VacationRequest.company_id == company_id)
    if user.role == "employee":
        employee_query = employee_query.where(Employee.id == user.employee_id)
        vacation_query = vacation_query.where(VacationRequest.employee_id == user.employee_id)
    companies = db.scalars(company_query).all()
    total_employees = db.scalar(select(func.count()).select_from(employee_query.subquery())) or 0
    active_employees = db.scalar(select(func.count()).select_from(employee_query.where(Employee.status == "Ativo").subquery())) or 0
    vacation_requests_pending = db.scalar(select(func.count()).select_from(vacation_query.subquery())) or 0
    activities = [{"id": x.id, "actor": x.actor, "message": x.message, "created_at": x.created_at.isoformat() if x.created_at else None} for x in db.scalars(activity_query).all()]
    admission_query = select(AdmissionRequest).where(AdmissionRequest.status == "pending_approval")
    time_query = select(TimeRecord).where(TimeRecord.status == "pending_approval")
    if company_id:
        admission_query = admission_query.where(AdmissionRequest.company_id == company_id)
        time_query = time_query.where(TimeRecord.company_id == company_id)
    if user.role == "employee":
        admission_query = admission_query.where(AdmissionRequest.id == -1)
        time_query = time_query.where(TimeRecord.employee_id == user.employee_id)
    admissions_pending = db.scalar(select(func.count()).select_from(admission_query.subquery())) or 0
    timekeeping_pending = db.scalar(select(func.count()).select_from(time_query.subquery())) or 0
    payroll_query=select(PayrollSimulation).order_by(PayrollSimulation.created_at.desc(),PayrollSimulation.id.desc())
    if company_id: payroll_query=payroll_query.where(PayrollSimulation.company_id==company_id)
    payroll_latest=db.scalar(payroll_query.limit(1))
    payroll_status=f"Última simulação: {payroll_latest.competence}" if payroll_latest else "Sem simulações registradas"
    process_query = select(func.count(ProcessRequest.id)).where(ProcessRequest.status == "pending_approval")
    if company_id: process_query = process_query.where(ProcessRequest.company_id == company_id)
    if user.role == "employee": process_query = process_query.where((ProcessRequest.employee_id == user.employee_id) | (ProcessRequest.requested_by_id == user.id))
    processes_pending = db.scalar(process_query) or 0
    publications_pending = None
    if user.role in ("system_admin", "consultant"):
        publication_query = select(func.count(EnvironmentPublication.id)).where(EnvironmentPublication.status == "pending_approval")
        if company_id: publication_query = publication_query.where(EnvironmentPublication.company_id == company_id)
        publications_pending = db.scalar(publication_query) or 0
    return {"publications_pending": publications_pending, "processes_pending": processes_pending, "total_employees": total_employees, "active_employees": active_employees, "company_count": len(companies), "vacation_requests_pending": vacation_requests_pending, "admissions_pending": admissions_pending, "timekeeping_pending": timekeeping_pending, "payroll_status": payroll_status, "recent_activities": activities, "companies": [{"id": c.id, "code": c.code, "trade_name": c.trade_name} for c in companies]}
