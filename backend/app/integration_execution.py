"""Atomic vacation execution scoped to the company in the approved plan."""
import hashlib
import hmac
import json
import os
from datetime import date, timedelta
from uuid import UUID
from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.database import get_db
from app.models import Activity, Company, IntegrationOperation, VacationConfiguration, VacationConfigurationHistory
from app.schemas import VacationConfigurationInput
from app.integration_api import router, allowed_company, snapshot, catalog_data
from app.vacations import CONFIG_FIELDS, config_errors, config_snapshot, create_configuration_version, vacation_policy_errors, vacation_timing_errors

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def snapshot_hash(value):
    return digest({key: value[key] for key in ("schedules", "as_of", "catalog_version", "erp_version", "groups", "company")})

NAMED_FIELDS = {"config_code", "name", "simulation_text"}

def plan_hash(expected, configurations):
    return digest({"expected_fingerprint": expected, "configurations": configurations})

def execution_auth(x_hcm_execution_key: str = Header(default="")):
    expected = os.getenv("HCM_EXECUTION_TOKEN_SHA256", "")
    if not expected or not hmac.compare_digest(hashlib.sha256(x_hcm_execution_key.encode()).hexdigest(), expected):
        raise HTTPException(403, "A credencial de consulta não autoriza execução")

class ExecutionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    approved_by: str = Field(min_length=1, max_length=100)
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_from: date
    configurations: list[VacationConfigurationInput] = Field(max_length=20)
    company_code: str | None = Field(default=None, min_length=1, max_length=20)

class RollbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=5, max_length=2000)

def schedules(rows):
    latest = {}
    for row in sorted(rows, key=lambda value: value["version"]):
        latest[(row["scope_key"], row["effective_from"])] = {"scope_key": row["scope_key"], "effective_from": row["effective_from"], **{key: row[key] for key in CONFIG_FIELDS}}
    return latest

def test_result(before, after, payloads):
    expected = schedules(before["schedules"]["homologation"])
    for item in payloads:
        scope = f"company:{item['company_id']}:default" if item["group_id"] is None else f"company:{item['company_id']}:group:{item['group_id']}"
        expected[(scope, item["effective_from"])] = {"scope_key": scope, "effective_from": item["effective_from"], **{key: item[key] for key in CONFIG_FIELDS}}
    checks = [{"name": "Parâmetros e vigências correspondem ao plano aprovado", "passed": expected == schedules(after["schedules"]["homologation"]), "origin": "API HCM"},
              {"name": "Produção permanece preservada", "passed": before["schedules"]["production"] == after["schedules"]["production"], "origin": "API HCM"}]
    for row in after["effective"]["homologation"]:
        config = row["configuration"]
        if config is None:
            checks.append({"name": f"Regra disponível: {row['group_name']}", "passed": False, "origin": "API HCM"})
            continue
        # Independently resolve the planned precedence for each group/date.
        company_scope = f"company:{after['company']['id']}:default"
        scopes = [f"company:{after['company']['id']}:group:{row['group_id']}", company_scope, "system"] if row["group_id"] is not None else [company_scope, "system"]
        target = None
        for scope in scopes:
            candidates = [value for (key, eff), value in expected.items() if key == scope and eff <= after["as_of"]]
            if candidates:
                target = max(candidates, key=lambda value: value["effective_from"])
                break
        checks.append({"name": f"Precedência e valores efetivos: {row['group_name']}", "passed": target is not None and all(target[key] == config[key] for key in CONFIG_FIELDS), "origin": "Resolução real por vigência"})
        checks.append({"name": f"Dependências válidas: {row['group_name']}", "passed": not config_errors(config), "origin": "Validador oficial"})
        probes = []
        for who in ("employee", "manager"):
            for installments in (1, 2, 3, 4):
                for advance in (False, True):
                    errors = vacation_policy_errors(config, who, installments, advance, True)
                    expected_allowed = config["process_enabled"] and config["simulation_enabled"] and config[who + "_request_enabled"] and installments <= (config["max_installments"] if config["installments_allowed"] else 1) and (not advance or config["allow_13th_advance"])
                    probes.append({"requester": who, "installments": installments, "advance_13th": advance, "allowed": not errors, "errors": errors, "passed": (not errors) == expected_allowed})
        checks.append({"name": f"Limites, solicitações e adiantamento: {row['group_name']}", "passed": all(probe["passed"] for probe in probes), "origin": "Motor de regras usado na simulação operacional", "probes": probes})
        reference = date.fromisoformat(after["as_of"])
        earliest = reference + timedelta(days=config["min_advance_days"])
        timing = [{"start_date": (earliest + timedelta(days=offset)).isoformat(), "allowed": not vacation_timing_errors(config, earliest + timedelta(days=offset), 1, 30, reference), "expected_allowed": offset >= 0} for offset in (-1, 0, 1)]
        checks.append({"name": f"Antecedência e saldo fictício: {row['group_name']}", "passed": all(t["allowed"] == t["expected_allowed"] for t in timing) and bool(vacation_timing_errors(config, earliest, 31, 30, reference)), "origin": "Motor de datas e saldo com cenário sintético na vigência aprovada", "timing_probes": timing})
    return {"passed": all(check["passed"] for check in checks), "checks": checks,
            "limits": "Testes automáticos de configuração, precedência e política de férias. Não criam solicitações reais nem comprovam requisitos ambíguos ou cálculos trabalhistas."}

def operation_out(row):
    return {"operation_id": row.id, "status": row.status, "approved_by": row.approved_by,
            "report": row.report, "before_snapshot": row.before_snapshot, "after_snapshot": row.after_snapshot,
            "created_config_ids": row.created_ids, "approval_hash": row.payload["approval_hash"]}

@router.get("/vacations/operations/{operation_id}")
def operation(operation_id: UUID, db: Session = Depends(get_db)):
    row = db.get(IntegrationOperation, str(operation_id))
    if not row or row.payload.get("module") not in (None, "vacations"):
        raise HTTPException(404, "Operação não encontrada")
    return operation_out(row)

@router.post("/vacations/apply", dependencies=[Depends(execution_auth)])
def apply(payload: ExecutionInput, db: Session = Depends(get_db)):
    existing = db.get(IntegrationOperation, str(payload.operation_id))
    if payload.company_code:
        company = allowed_company(db, payload.company_code)
    elif payload.configurations:
        company = db.get(Company, payload.configurations[0].company_id)
    elif existing:
        company = db.get(Company, existing.company_id)
    else:
        raise HTTPException(422, "Informe a empresa do plano, inclusive quando não houver alterações")
    if not company:
        raise HTTPException(404, "Empresa não encontrada no HCM")
    db.scalar(select(Company).where(Company.id == company.id).with_for_update())
    if any(item.config_code or item.name or item.simulation_text for item in payload.configurations):
        raise HTTPException(403, "Configurações nomeadas não fazem parte do escopo da integração")
    packet = payload.model_dump(mode="json", exclude_none=True)
    # Keep the complete configuration keys (group_id can legitimately be null); named-configuration fields are outside the pilot contract.
    packet["configurations"] = [item.model_dump(mode="json", exclude=NAMED_FIELDS) for item in payload.configurations]
    request_hash = digest(packet)
    existing = db.get(IntegrationOperation, str(payload.operation_id))
    if existing:
        if existing.company_id != company.id or existing.request_hash != request_hash:
            raise HTTPException(409, "Identificador da operação já utilizado por outro plano")
        return operation_out(existing)
    configurations = [item.model_dump(mode="json", exclude=NAMED_FIELDS) for item in payload.configurations]
    if plan_hash(payload.expected_fingerprint, configurations) != payload.approval_hash:
        raise HTTPException(409, "O plano não corresponde à aprovação recebida")
    before = snapshot(company.code, payload.effective_from, db)
    if snapshot_hash(before) != payload.expected_fingerprint:
        raise HTTPException(409, "As configurações mudaram após a análise. Faça nova análise antes de aplicar.")
    seen = set()
    groups = {item["id"] for item in before["groups"]}
    for item in payload.configurations:
        if item.company_id != company.id or item.environment != "homologation" or item.effective_from != payload.effective_from:
            raise HTTPException(403, "O plano deve conter somente a empresa, o ambiente de homologação e a vigência aprovados")
        if item.group_id is not None and item.group_id not in groups:
            raise HTTPException(403, "Grupo fora da empresa autorizada")
        if item.group_id in seen:
            raise HTTPException(422, "Escopo repetido no plano")
        seen.add(item.group_id)
        if errors := config_errors(item.model_dump()):
            raise HTTPException(422, errors)
    row = IntegrationOperation(id=str(payload.operation_id), company_id=company.id, request_hash=request_hash, status="applying",
        approved_by=payload.approved_by, payload=packet, before_snapshot=before, after_snapshot={}, report={}, created_ids=[])
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Identificador da operação já utilizado por outro plano") from exc
    report = {}
    try:
        with db.begin_nested():
            created = []
            for item in payload.configurations:
                config, errors = create_configuration_version(db, item, None, f"ERP AI · {payload.approved_by}")
                if errors:
                    raise ValueError("Validação rejeitada")
                created.append(config.id)
            after = snapshot(company.code, payload.effective_from, db)
            report = test_result(before, after, configurations)
            if not report["passed"]:
                raise ValueError("Os testes automáticos não passaram; alterações desfeitas")
        row.created_ids = created; row.after_snapshot = after; row.report = report; row.status = "applied"
    except ValueError as exc:
        row.status = "failed"; row.report = {**report, "passed": False, "error": str(exc), "rolled_back": True}; row.after_snapshot = before
    db.add(Activity(company_id=company.id, actor=f"ERP AI · {payload.approved_by}", message=f"Operação {row.id}: {row.status}; homologação de férias; aprovação {payload.approval_hash}."))
    db.commit(); db.refresh(row)
    return operation_out(row)

@router.get("/vacations/operations/{operation_id}/verify")
def verify(operation_id: UUID, db: Session = Depends(get_db)):
    row = db.get(IntegrationOperation, str(operation_id))
    if not row or row.payload.get("module") not in (None, "vacations"):
        raise HTTPException(404, "Operação não encontrada")
    company = db.get(Company, row.company_id)
    current = snapshot(company.code, date.fromisoformat(row.payload["effective_from"]), db)
    report = test_result(row.before_snapshot, current, row.payload["configurations"])
    return {"operation_id": row.id, "status": row.status, "report": report, "current_snapshot": current}

@router.post("/vacations/operations/{operation_id}/rollback", dependencies=[Depends(execution_auth)])
def rollback(operation_id: UUID, payload: RollbackInput, db: Session = Depends(get_db)):
    row = db.get(IntegrationOperation, str(operation_id))
    if not row or row.payload.get("module") not in (None, "vacations"):
        raise HTTPException(404, "Operação não encontrada")
    company = db.scalar(select(Company).where(Company.id == row.company_id).with_for_update())
    db.refresh(row)
    if row.status == "reverted":
        return operation_out(row)
    if row.status != "applied":
        raise HTTPException(409, "Somente uma aplicação concluída pode ser revertida")
    current = snapshot(company.code, date.fromisoformat(row.payload["effective_from"]), db)
    if snapshot_hash(current) != payload.expected_fingerprint or snapshot_hash(current) != snapshot_hash(row.after_snapshot):
        raise HTTPException(409, "O HCM mudou após a aplicação. Reversão bloqueada para preservar alterações posteriores.")
    for config_id in row.created_ids:
        config = db.get(VacationConfiguration, config_id)
        config.revoked = True
    db.flush()
    restored = snapshot(company.code, date.fromisoformat(row.payload["effective_from"]), db)
    if snapshot_hash(restored) != snapshot_hash(row.before_snapshot):
        db.rollback()
        raise HTTPException(409, "Não foi possível restaurar exatamente a configuração anterior")
    for config_id in row.created_ids:
        config = db.get(VacationConfiguration, config_id)
        restored_rule = next((item["configuration"] for item in restored["effective"]["homologation"] if item["group_id"] == config.group_id), None)
        db.add(VacationConfigurationHistory(company_id=company.id, group_id=config.group_id, config_id=config.id,
            version=config.version, environment="homologation", actor_id=None, actor_name="ERP AI · Reversão",
            before_snapshot=config_snapshot(config), after_snapshot={**(restored_rule or {}), "action": "rollback", "operation_id": row.id, "reason": payload.reason}))
    row.status = "reverted"
    row.report = {**row.report, "rollback": {"passed": True, "reason": payload.reason, "restored_snapshot": restored}}
    db.add(Activity(company_id=company.id, actor=f"ERP AI · {row.approved_by}", message=f"Operação {row.id} revertida em homologação: {payload.reason}"))
    db.commit(); db.refresh(row)
    return operation_out(row)
