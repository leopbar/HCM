from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Literal
from app.database import get_db
from app.models import User, AccessGroup, Company, Employee, Activity
from datetime import datetime, timezone
from app.security import current_session, current_user, require_roles, generate_password, hash_password, require_strong_password, revoke_user_sessions, verify_password

router = APIRouter(prefix="/api/access", tags=["access"])
MODULES = {"vacations", "timekeeping", "benefits", "admission", "payroll", "processes", "recruitment"}

class GroupInput(BaseModel):
    company_id: int
    name: str = Field(min_length=2, max_length=120)
    modules: list[str]

    @field_validator("modules")
    @classmethod
    def valid_modules(cls, value):
        if not set(value) <= MODULES:
            raise ValueError("Módulo de acesso inválido")
        return sorted(set(value))

class AccountInput(BaseModel):
    full_name: str = Field(min_length=2, max_length=180)
    email: EmailStr
    role: Literal["employee", "hr_manager", "consultant", "system_admin"]
    company_id: int | None = None
    employee_id: int | None = None
    access_group_id: int | None = None

class AccountUpdate(BaseModel):
    active: bool
    access_group_id: int | None = None
    reset_password: bool = False
    unlock: bool = False

class PasswordInput(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=200)

def account_out(row):
    return {"id": row.id, "full_name": row.full_name, "email": row.email, "role": row.role, "company_id": row.company_id, "employee_id": row.employee_id, "active": row.active, "access_group_id": row.access_group_id, "must_change_password": row.must_change_password, "locked": bool(row.locked_until and (row.locked_until if row.locked_until.tzinfo else row.locked_until.replace(tzinfo=timezone.utc)) > datetime.now(timezone.utc)), "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None}

def check_group(db, group_id, company_id):
    if group_id is not None:
        group = db.get(AccessGroup, group_id)
        if not group or group.company_id != company_id:
            raise HTTPException(422, "O grupo de acesso deve pertencer à empresa do usuário")

def commit(db):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "E-mail ou nome de grupo já cadastrado") from exc

@router.get("/users")
def list_users(db: Session = Depends(get_db), admin: User = Depends(require_roles("system_admin"))):
    return [account_out(row) for row in db.scalars(select(User).order_by(User.full_name))]

@router.get("/groups")
def list_groups(db: Session = Depends(get_db), admin: User = Depends(require_roles("system_admin"))):
    return [{"id": g.id, "company_id": g.company_id, "name": g.name, "modules": g.modules} for g in db.scalars(select(AccessGroup).order_by(AccessGroup.name))]

@router.post("/groups", status_code=201)
def create_group(payload: GroupInput, db: Session = Depends(get_db), admin: User = Depends(require_roles("system_admin"))):
    if not db.get(Company, payload.company_id):
        raise HTTPException(404, "Empresa não encontrada")
    name = payload.name.strip()
    if len(name) < 2:
        raise HTTPException(422, "Informe o nome do grupo")
    row = AccessGroup(company_id=payload.company_id, name=name, modules=payload.modules)
    db.add(row)
    db.add(Activity(company_id=payload.company_id, actor=admin.full_name, message=f"Grupo de acesso {name} criado."))
    commit(db)
    return {"id": row.id}

@router.put("/groups/{group_id}")
def update_group(group_id: int, payload: GroupInput, db: Session = Depends(get_db), admin: User = Depends(require_roles("system_admin"))):
    row = db.get(AccessGroup, group_id)
    if not row or row.company_id != payload.company_id:
        raise HTTPException(404, "Grupo não encontrado nesta empresa")
    if len(payload.name.strip()) < 2:
        raise HTTPException(422, "Informe o nome do grupo")
    row.name, row.modules = payload.name.strip(), payload.modules
    db.add(Activity(company_id=row.company_id, actor=admin.full_name, message=f"Permissões do grupo {row.name} atualizadas."))
    commit(db)
    return {"id": row.id}

@router.post("/users", status_code=201)
def create_user(payload: AccountInput, db: Session = Depends(get_db), admin: User = Depends(require_roles("system_admin"))):
    if db.scalar(select(User.id).where(func.lower(User.email) == str(payload.email).lower())):
        raise HTTPException(409, "Este e-mail já tem uma conta")
    if payload.role in ("employee", "hr_manager"):
        if not payload.company_id or not db.get(Company, payload.company_id):
            raise HTTPException(422, "Selecione a empresa do usuário")
    elif payload.company_id or payload.employee_id or payload.access_group_id:
        raise HTTPException(422, "Administradores e consultores usam acesso global, sem vínculo de empresa ou grupo")
    if payload.role == "employee":
        employee = db.get(Employee, payload.employee_id) if payload.employee_id else None
        if not employee or employee.company_id != payload.company_id:
            raise HTTPException(422, "Selecione um funcionário da empresa")
        if db.scalar(select(User.id).where(User.employee_id == employee.id)):
            raise HTTPException(409, "Este funcionário já tem uma conta")
    elif payload.employee_id:
        raise HTTPException(422, "Vínculo de funcionário permitido apenas no perfil Funcionário")
    check_group(db, payload.access_group_id, payload.company_id)
    # The system generates the temporary password; it is shown once and must be replaced at first sign-in.
    password = generate_password()
    row = User(**payload.model_dump(), password_hash=hash_password(password), must_change_password=True)
    row.email = str(payload.email).lower()
    db.add(row)
    db.add(Activity(company_id=row.company_id, actor=admin.full_name, message=f"Conta {row.email} criada com perfil {row.role}."))
    commit(db)
    return {**account_out(row), "temporary_password": password}

@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: AccountUpdate, db: Session = Depends(get_db), admin: User = Depends(require_roles("system_admin"))):
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(404, "Usuário não encontrado")
    if row.id == admin.id and not payload.active:
        raise HTTPException(422, "Você não pode desativar seu próprio acesso")
    check_group(db, payload.access_group_id, row.company_id)
    row.active, row.access_group_id = payload.active, payload.access_group_id
    password = generate_password() if payload.reset_password else None
    if password:
        row.password_hash = hash_password(password)
        row.must_change_password = True
        row.failed_login_attempts, row.locked_until = 0, None
    if payload.unlock:
        row.failed_login_attempts, row.locked_until = 0, None
    if not row.active or password:
        revoke_user_sessions(db, row.id)
    db.add(Activity(company_id=row.company_id, actor=admin.full_name, message=f"Acesso de {row.email} atualizado."))
    commit(db)
    return {**account_out(row), "temporary_password": password} if password else account_out(row)

@router.post("/password")
def change_password(payload: PasswordInput, db: Session = Depends(get_db), pair=Depends(current_session)):
    user, session = pair
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(422, "Senha atual incorreta")
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(422, "A nova senha deve ser diferente da atual")
    require_strong_password(payload.new_password, user.email, user.full_name)
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.password_changed_at = datetime.now(timezone.utc)
    revoke_user_sessions(db, user.id)
    db.add(Activity(company_id=user.company_id, actor=user.full_name, message="Senha alterada pelo próprio usuário."))
    db.commit()
    return {"message": "Senha alterada. Entre novamente com a nova senha."}
