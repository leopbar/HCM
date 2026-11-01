"""Narrow, read-only ERP adapter. Its credential is never a normal user token."""
import hashlib
import hmac
import os
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Company, Employee, EmployeeGroup, VacationConfiguration, VacationConfigurationHistory
from app.schemas import VacationConfigurationInput
from app.vacations import CONFIG_FIELDS, config_errors, latest_scope_config
from app.vacations import config_snapshot as _full_snapshot

def config_snapshot(row):
    """Integration contract v1: company/group scopes only, without named-configuration fields."""
    data = _full_snapshot(row)
    for key in ("config_code", "name", "simulation_text"):
        data.pop(key, None)
    return data


def integration_auth(authorization: str = Header(default="")):
    expected = os.getenv("HCM_INTEGRATION_TOKEN_SHA256", "")
    scheme, _, token = authorization.partition(" ")
    if not expected or scheme.lower() != "bearer" or not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected):
        raise HTTPException(401, "Credencial de integração inválida ou desabilitada")


router = APIRouter(prefix="/api/integration/v1", tags=["Integração consultiva"], dependencies=[Depends(integration_auth)])


def allowed_company(db: Session, code: str):
    company = db.scalar(select(Company).where(Company.code == code))
    if not company:
        raise HTTPException(404, "Empresa não encontrada no HCM")
    return company


LABELS = {
    "process_enabled": "Processo de férias habilitado",
    "employee_request_enabled": "Solicitação pelo funcionário",
    "manager_request_enabled": "Solicitação pelo Gestor de RH",
    "manager_approval_required": "Aprovação pelo RH ou administrador obrigatória",
    "simulation_enabled": "Simulação habilitada",
    "installments_allowed": "Parcelamento habilitado",
    "max_installments": "Máximo de parcelas",
    "min_advance_days": "Antecedência mínima em dias",
    "allow_13th_advance": "Adiantamento fictício do 13º permitido",
    "show_balance_to_employee": "Exibição do saldo ao funcionário",
}


def catalog_data():
    return {
        "version": "vacations-1", "erp_version": "0.1.0", "module": "vacations",
        "fields": [{"id": key, "label": LABELS[key], "type": "integer" if key in ("max_installments", "min_advance_days") else "boolean",
                    "minimum": 1 if key == "max_installments" else 0 if key == "min_advance_days" else None,
                    "maximum": 3 if key == "max_installments" else 365 if key == "min_advance_days" else None} for key in CONFIG_FIELDS],
        "rules": [
            "Regra completa de grupo prevalece sobre empresa; empresa prevalece sobre sistema. Não há mesclagem por campo.",
            "Sem parcelamento, max_installments deve ser 1; com parcelamento, deve ser 2 ou 3.",
            "Com processo desabilitado, solicitações por funcionário e gestor devem estar desabilitadas.",
            "Aprovação atual é feita pelo perfil Gestor de RH ou administrador, não pelo gestor imediato.",
            "Parcelas são um limite demonstrativo; não há agendamento de períodos separados.",
            "A simulação operacional atual resolve a configuração na data atual, não na data inicial das férias.",
        ],
        "available_modules": [{"id": "vacations", "label": "Férias"}, {"id": "time", "label": "Controle de Ponto"}, {"id": "admission", "label": "Admissão"}, {"id": "payroll", "label": "Folha demonstrativa"}, {"id": "benefits", "label": "Benefícios"}],
        "capabilities": {"apply_homologation": bool(os.getenv("HCM_EXECUTION_TOKEN_SHA256")), "automatic_tests": True, "rollback": True, "production_write": False},
        "input_schema": VacationConfigurationInput.model_json_schema(),
    }


@router.get("/catalog")
def catalog():
    return catalog_data()


def company_search_text(value):
    value = unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", value))


@router.get("/companies")
def search_companies(query: str = Query(default="", max_length=180), db: Session = Depends(get_db)):
    """Company identity lookup; finding a company never grants write access."""
    needle = company_search_text(query)
    tokens = needle.split()
    if tokens[:1] == ["empresa"] and len(tokens) > 1:
        tokens = tokens[1:]
        needle = " ".join(tokens)
    ranked = []
    for company in db.scalars(select(Company).order_by(Company.trade_name, Company.code)).all():
        names = [company_search_text(v) for v in (company.trade_name, company.legal_name, company.code)]
        if not needle or any(all(token in name.split() or token in name for token in tokens) for name in names):
            score = 0 if needle in names else 1 if any(name.startswith(needle) for name in names) else 2
            ranked.append((score, company))
    ranked.sort(key=lambda item: (item[0], item[1].trade_name.casefold(), item[1].code))
    return {"items": [{"id": c.id, "code": c.code, "name": c.trade_name, "legal_name": c.legal_name,
                      "city": c.city, "state": c.state, "status": c.status,
                      "configuration_allowed": True}
                     for _, c in ranked[:50]], "has_more": len(ranked) > 50,
            "scope_note": "Parametrização disponível para todas as empresas cadastradas, em homologação."}


@router.get("/vacations/snapshot")
def snapshot(company_code: str = Query(min_length=1, max_length=20), as_of: date = Query(default_factory=date.today), db: Session = Depends(get_db)):
    company = allowed_company(db, company_code)
    groups = db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id == company.id).order_by(EmployeeGroup.id)).all()
    schedules = {}
    effective = {}
    for environment in ("homologation", "production"):
        rows = db.scalars(select(VacationConfiguration).where(
            (VacationConfiguration.company_id == company.id) | (VacationConfiguration.scope_key == "system"),
            VacationConfiguration.environment == environment, VacationConfiguration.revoked.is_(False),
        ).order_by(VacationConfiguration.scope_key, VacationConfiguration.effective_from, VacationConfiguration.version)).all()
        schedules[environment] = [config_snapshot(row) for row in rows if not row.config_code]
        system = latest_scope_config(db, "system", as_of, environment)
        base = latest_scope_config(db, f"company:{company.id}:default", as_of, environment) or system
        effective[environment] = [{"group_id": None, "group_name": "Regra geral", "configuration": config_snapshot(base) if base else None}]
        for group in groups:
            selected = latest_scope_config(db, f"company:{company.id}:group:{group.id}", as_of, environment) or base
            effective[environment].append({"group_id": group.id, "group_name": group.name, "configuration": config_snapshot(selected) if selected else None})
    return {"company": {"id": company.id, "code": company.code, "name": company.trade_name},
            "as_of": as_of.isoformat(), "consulted_at": datetime.now(timezone.utc).isoformat(),
            "catalog_version": "vacations-1", "erp_version": "0.1.0",
            "groups": [{"id": g.id, "name": g.name, "employee_count": db.scalar(select(func.count(Employee.id)).where(Employee.group_id == g.id, Employee.company_id == company.id))} for g in groups],
            "schedules": schedules, "effective": effective}


@router.get("/vacations/history")
def history(company_code: str = Query(min_length=1, max_length=20), environment: Literal["homologation", "production"] = "homologation", before_id: int | None = None, limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db)):
    company = allowed_company(db, company_code)
    query = select(VacationConfigurationHistory).where(VacationConfigurationHistory.company_id == company.id, VacationConfigurationHistory.environment == environment)
    if before_id is not None:
        query = query.where(VacationConfigurationHistory.id < before_id)
    rows = db.scalars(query.order_by(VacationConfigurationHistory.id.desc()).limit(limit)).all()
    return {"items": [{"id": r.id, "actor": r.actor_name, "before": r.before_snapshot, "after": r.after_snapshot, "created_at": r.created_at.isoformat()} for r in rows], "next_before_id": rows[-1].id if len(rows) == limit else None}


@router.post("/vacations/validate")
def validate(payload: VacationConfigurationInput, db: Session = Depends(get_db)):
    company = db.get(Company, payload.company_id)
    if not company:
        raise HTTPException(404, "Empresa não encontrada no HCM")
    if payload.environment != "homologation":
        raise HTTPException(403, "A configuração deve ser aplicada em homologação")
    if payload.group_id is not None:
        group = db.get(EmployeeGroup, payload.group_id)
        if not group or group.company_id != company.id:
            raise HTTPException(403, "Grupo fora do escopo autorizado")
    errors = config_errors(payload.model_dump())
    return {"valid": not errors, "errors": errors, "persisted": False}
