import re
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.environment_api import _configured_vacation
from app.hr_api import admission_errors, company_access
from app.policies import DEFAULTS, active_config, validate_config
from app.models import (
    Activity, BenefitCatalog, Company, ConfigurationImportPreview, Employee,
    EmployeeGroup, EnvironmentPublication, HRModuleConfiguration,
    HRModuleConfigurationHistory, PayrollEvent, TrainingExerciseProgress,
    User, VacationConfiguration,
)
from app.schemas import ConfigurationImportPreviewInput, TrainingExerciseCheckInput, VacationConfigurationInput
from app.security import require_roles
from app.vacations import CONFIG_FIELDS, config_errors, create_configuration_version, evaluate_vacation, next_valid_start, resolve_effective_config

router = APIRouter(prefix="/api", tags=["Treinamento e configurações"])
ROLES = ("system_admin", "consultant")
FORMAT = "hcm-lab-configuration"
SCHEMA_VERSION = 1
VACATION_ID = "vacation.rules"
EVENT_FIELDS = {"code", "description", "kind", "calculation", "value", "base", "active"}
BENEFIT_FIELDS = {"code", "name", "description", "fixed_value", "percentage_value", "approval_required", "effective_from", "active"}

EXERCISES = [
    {"id": "vacation-publication", "title": "1. Parametrize e publique férias", "module": "Férias", "objective": "Alterar o máximo de parcelas da Empresa ABC de 2 para 3, testar em homologação e publicar sem mudar outra empresa.", "steps": ["Selecione a Empresa ABC e o ambiente Homologação.", "Na Suíte de parametrização, em Autoatendimento → Processos → Férias, altere o máximo de parcelas da regra da empresa para 3 e salve.", "No menu Férias, aba Laboratório, simule três parcelas para um funcionário da ABC.", "Compare homologação e produção; solicite e aprove a publicação.", "Repita a simulação em Produção e confira o limite 3; confirme que a outra empresa não mudou."], "expected": "Homologação e Produção permitem 3 parcelas após a aprovação; as demais empresas mantêm suas próprias regras."},
    {"id": "admission-weekends", "title": "2. Bloqueie admissões no fim de semana", "module": "Admissão", "objective": "Configurar a regra de admissão para não aceitar datas de fim de semana e testar um sábado.", "steps": ["Em Homologação, abra na Suíte Configurações por Empresa → Contratação (Solicitação de admissão).", "Desative admissões aos finais de semana e salve.", "Use a ação Verificar exercício para testar uma data de sábado pelas regras do backend."], "expected": "O backend rejeita o sábado e informa que a data cai em um fim de semana não permitido."},
    {"id": "payroll-bonus", "title": "3. Recalcule o bônus demonstrativo", "module": "Folha", "objective": "Mudar o evento BONUS10 de 10% para 15% em Homologação e conferir o cálculo.", "steps": ["Na Suíte, em Folha de Pagamento → Eventos da folha, localize o evento BONUS10 em Homologação.", "Configure-o como provento percentual de 15% sobre o salário e salve.", "Execute Verificar exercício para calcular o valor de referência com um funcionário ativo."], "expected": "O resultado aplica 15% do salário fictício do funcionário; a folha continua sendo apenas demonstrativa."},
    {"id": "parameter-dependency", "title": "4. Entenda uma dependência inválida", "module": "Parametrizações", "objective": "Tentar salvar uma jornada diária inválida e entender por que o backend a rejeita.", "steps": ["Na Suíte, abra Controle de Ponto em Homologação e observe os limites aceitos.", "Use Verificar exercício para enviar uma jornada diária de 0 hora ao validador do backend.", "Leia a mensagem de validação retornada."], "expected": "O validador rejeita 0 hora e explica que a jornada deve ficar entre 1 e 16 horas; nenhum dado é salvo."},
    {"id": "group-precedence", "title": "5. Configure uma regra por grupo", "module": "Férias", "objective": "Criar uma regra de férias diferente para um grupo e observar que ela prevalece para seus integrantes.", "steps": ["Na Suíte, em Autoatendimento → Processos → Férias, escolha um grupo que tenha funcionários.", "Crie uma regra de grupo com um valor diferente da regra geral da empresa e salve.", "Selecione um funcionário desse grupo e use Verificar exercício."], "expected": "A configuração efetiva aponta para o escopo do grupo e explica a precedência sobre a regra da empresa."},
    {"id": "configuration-migration", "title": "6. Migre configurações por JSON", "module": "Importação e exportação", "objective": "Exportar uma configuração, revisar o arquivo, importar em Homologação, validar, testar e publicar.", "steps": ["Na Suíte, use o Guia Migrador para exportar a empresa ou um módulo em JSON.", "Selecione o arquivo exportado e gere uma prévia; revise alterações e validações.", "Aplique a importação somente depois de uma prévia válida.", "Compare os ambientes e publique a configuração importada com aprovação."], "expected": "A prévia registra as alterações, a importação cria versões em Homologação e uma publicação aprovada leva a configuração a Produção."},
]


def _latest_vacation(db: Session, company_id: int, environment: str):
    return _configured_vacation(db, company_id, environment)


def _check_exercise(db: Session, exercise_id: str, company_id: int, probe: dict):
    if exercise_id == "vacation-publication":
        company = db.get(Company, company_id)
        if not company or company.code != "EMP001":
            return [{"label": "Selecione a Empresa ABC para este roteiro", "passed": False, "details": f"Empresa atual: {company.trade_name if company else 'não encontrada'}"}]
        hom = _latest_vacation(db, company_id, "homologation").get((f"company:{company_id}:default", date.today().isoformat()))
        prod = _latest_vacation(db, company_id, "production").get((f"company:{company_id}:default", date.today().isoformat()))
        # Use the latest currently effective company-level rule if its start date is earlier than today.
        hom_rows = db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id == company_id, VacationConfiguration.group_id.is_(None), VacationConfiguration.config_code.is_(None), VacationConfiguration.environment == "homologation", VacationConfiguration.effective_from <= date.today()).order_by(VacationConfiguration.effective_from.desc(), VacationConfiguration.version.desc())).all()
        prod_rows = db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id == company_id, VacationConfiguration.group_id.is_(None), VacationConfiguration.config_code.is_(None), VacationConfiguration.environment == "production", VacationConfiguration.effective_from <= date.today()).order_by(VacationConfiguration.effective_from.desc(), VacationConfiguration.version.desc())).all()
        hom = hom_rows[0] if hom_rows else hom
        prod = prod_rows[0] if prod_rows else prod
        other_company = db.scalar(select(Company).where(Company.id != company_id).order_by(Company.id))
        other_rule = db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id == other_company.id, VacationConfiguration.group_id.is_(None), VacationConfiguration.config_code.is_(None), VacationConfiguration.environment == "production", VacationConfiguration.effective_from <= date.today()).order_by(VacationConfiguration.effective_from.desc(), VacationConfiguration.version.desc())).first() if other_company else None
        other_hom = db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id == other_company.id, VacationConfiguration.group_id.is_(None), VacationConfiguration.config_code.is_(None), VacationConfiguration.environment == "homologation", VacationConfiguration.effective_from <= date.today()).order_by(VacationConfiguration.effective_from.desc(), VacationConfiguration.version.desc())).first() if other_company else None
        publication = db.scalars(select(EnvironmentPublication).where(EnvironmentPublication.company_id == company_id, EnvironmentPublication.status == "published").order_by(EnvironmentPublication.decided_at.desc())).first()
        vacation_published = bool(publication and any(item.get("scope_key") == f"company:{company_id}:default" and item.get("max_installments") == 3 for item in publication.snapshot.get("vacation", [])))
        other_unchanged = bool(other_rule and other_hom and all(getattr(other_rule, field) == getattr(other_hom, field) for field in CONFIG_FIELDS))
        employee = db.scalar(select(Employee).where(Employee.company_id == company_id, Employee.status == "Ativo").order_by(Employee.id))
        test_start = next_valid_start(date.today() + timedelta(days=max(30, hom.min_advance_days if hom else 7, prod.min_advance_days if prod else 7) + 7))
        sim_payload = SimpleNamespace(employee_id=employee.id if employee else None, start_date=test_start, requested_days=5, installment_count=3, advance_13th_requested=False, requester_type="manager", environment="homologation")
        hom_sim = evaluate_vacation(db, sim_payload, requester_type="manager") if employee else {"allowed": False, "errors": ["Funcionário ativo não encontrado."]}
        sim_payload.environment = "production"
        prod_sim = evaluate_vacation(db, sim_payload, requester_type="manager") if employee else {"allowed": False, "errors": ["Funcionário ativo não encontrado."]}
        return [
            {"label": "Homologação possui regra vigente com 3 parcelas", "passed": bool(hom and hom.max_installments == 3), "details": f"Valor atual: {hom.max_installments if hom else 'sem regra'}"},
            {"label": "Homologação aceita uma simulação de três parcelas", "passed": hom_sim["allowed"], "details": " ".join(hom_sim.get("errors", [])) or f"Simulação permitida na versão {hom_sim.get('effective_configuration', {}).get('version', '—')}"},
            {"label": "Produção recebeu a regra após publicação", "passed": bool(prod and prod.max_installments == 3 and vacation_published), "details": f"Valor atual: {prod.max_installments if prod else 'sem regra'}; publicação da regra de férias: {'sim' if vacation_published else 'não'}"},
            {"label": "Produção aceita três parcelas após publicação", "passed": prod_sim["allowed"], "details": " ".join(prod_sim.get("errors", [])) or "Simulação permitida com as regras publicadas."},
            {"label": "Outra empresa mantém configuração independente", "passed": other_unchanged, "details": f"{other_company.trade_name}: Produção {other_rule.max_installments if other_rule else '—'} / Homologação {other_hom.max_installments if other_hom else '—'} parcela(s)"},
        ]
    if exercise_id == "admission-weekends":
        config = active_config(db, company_id, "admission", "homologation")
        candidate = db.scalar(select(Employee).where(Employee.company_id == company_id, Employee.status == "Ativo").order_by(Employee.id))
        saturday = date.today() + timedelta(days=max(7, config["min_advance_days"]))
        while saturday.weekday() != 5 and (saturday - date.today()).days <= config["max_advance_days"]:
            saturday += timedelta(days=1)
        errors = admission_errors(SimpleNamespace(planned_date=saturday, documents_complete=True), config)
        rejected_for_weekend = any("final de semana não permitido" in error for error in errors)
        return [
            {"label": "Configuração de homologação desabilita fins de semana", "passed": config["allow_weekends"] is False, "details": f"allow_weekends={config['allow_weekends']}"},
            {"label": "Simulação de sábado é rejeitada pela regra do backend", "passed": bool(candidate and rejected_for_weekend), "details": f"Data testada: {saturday.isoformat()}; mensagens: {' '.join(errors) or 'nenhuma'}"},
        ]
    if exercise_id == "payroll-bonus":
        company = db.get(Company, company_id)
        if not company or company.code != "EMP001":
            return [{"label": "Selecione a Empresa ABC, que possui o evento BONUS10 de demonstração", "passed": False, "details": f"Empresa atual: {company.trade_name if company else 'não encontrada'}"}]
        event = db.scalar(select(PayrollEvent).where(PayrollEvent.company_id == company_id, PayrollEvent.environment == "homologation", PayrollEvent.code == "BONUS10", PayrollEvent.active.is_(True)))
        employee = db.scalar(select(Employee).where(Employee.company_id == company_id, Employee.status == "Ativo").order_by(Employee.id))
        calculated = float((Decimal(str(employee.salary)) * Decimal("0.15")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if employee else None
        actual = float((Decimal(str(employee.salary)) * Decimal(str(event.value)) / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if employee and event and event.calculation == "percentage" else None
        return [
            {"label": "Evento BONUS configurado como provento de 15%", "passed": bool(event and event.kind == "earning" and event.calculation == "percentage" and float(event.value) == 15), "details": f"Valor atual: {float(event.value) if event else 'evento ausente'}%"},
            {"label": "Cálculo demonstrativo aplica 15% ao salário", "passed": bool(actual is not None and actual == calculated), "details": f"Valor calculado: R$ {actual:.2f}" if actual is not None else "Evento ou funcionário ativo não encontrado."},
        ]
    if exercise_id == "parameter-dependency":
        invalid = dict(DEFAULTS["timekeeping"])
        invalid["daily_hours"] = 0
        errors = validate_config("timekeeping", invalid)
        return [{"label": "Validador do backend rejeita jornada diária inválida", "passed": any("entre 1 e 16 horas" in error for error in errors), "details": " ".join(errors) if errors else "O validador aceitou a configuração."}]
    if exercise_id == "group-precedence":
        employee = db.scalar(select(Employee).where(Employee.company_id == company_id, Employee.status == "Ativo").order_by(Employee.id))
        config, origin, why = resolve_effective_config(db, employee, environment="homologation") if employee else (None, None, "Funcionário ativo não encontrado.")
        company_rule = db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id == company_id, VacationConfiguration.group_id.is_(None), VacationConfiguration.config_code.is_(None), VacationConfiguration.environment == "homologation", VacationConfiguration.effective_from <= date.today()).order_by(VacationConfiguration.effective_from.desc(), VacationConfiguration.version.desc())).first()
        differs = bool(config and company_rule and any(getattr(config, field) != getattr(company_rule, field) for field in CONFIG_FIELDS))
        return [
            {"label": "Existe regra específica diferente para o grupo do funcionário", "passed": bool(config and config.group_id == employee.group_id and differs), "details": f"Escopo efetivo: {origin['label'] if origin else 'nenhum'}"},
            {"label": "Motor explica a precedência do grupo", "passed": bool(origin and origin["scope"] == "group" and "prevalece" in why), "details": why},
        ]
    if exercise_id == "configuration-migration":
        applied = db.scalars(select(ConfigurationImportPreview).where(ConfigurationImportPreview.company_id == company_id, ConfigurationImportPreview.status == "applied").order_by(ConfigurationImportPreview.applied_at.desc())).all()
        publication = db.scalars(select(EnvironmentPublication).where(EnvironmentPublication.company_id == company_id, EnvironmentPublication.status == "published").order_by(EnvironmentPublication.decided_at.desc())).first()
        migration_published = bool(publication and any(row.applied_at and publication.decided_at and publication.decided_at >= row.applied_at and _imported_parameter_in_snapshot(db, company_id, row, publication.snapshot) for row in applied))
        return [
            {"label": "Prévia de importação válida foi aplicada em homologação", "passed": bool(applied), "details": f"Importação aplicada: {applied[0].applied_at.isoformat() if applied and applied[0].applied_at else 'não'}"},
            {"label": "Configuração importada foi publicada com aprovação", "passed": migration_published, "details": f"Publicação: {publication.status if publication else 'não encontrada'}"},
        ]
    raise HTTPException(status_code=404, detail="Exercício desconhecido")


def _imported_parameter_in_snapshot(db: Session, company_id: int, preview: ConfigurationImportPreview, snapshot: dict):
    for item in preview.package.get("parameters", []):
        module = item.get("module"); parameter_id = item.get("parameter_id"); values = item.get("values", {})
        try: group_id = _map_group(db, company_id, item.get("scope", {}))
        except ValueError: continue
        if parameter_id == VACATION_ID:
            scope_key = f"company:{company_id}:group:{group_id}" if group_id else f"company:{company_id}:default"
            for row in snapshot.get("vacation", []):
                if row.get("scope_key") == scope_key and row.get("effective_from") == item.get("effective_from") and all(row.get(key) == value for key, value in values.items()): return True
        elif isinstance(module, str) and module in DEFAULTS and parameter_id == f"{module}.configuration":
            for row in snapshot.get("modules", {}).get(module, []):
                if row.get("effective_from") == item.get("effective_from") and row.get("values") == values: return True
        elif module == "payroll":
            for row in snapshot.get("payroll_events", []):
                if row.get("code") == values.get("code") and row.get("group_id") == group_id and all(row.get(key) == value for key, value in values.items()): return True
        elif module == "benefits":
            for row in snapshot.get("benefits", []):
                comparable = {key: row.get(key) for key in BENEFIT_FIELDS}
                comparable["effective_from"] = row.get("effective_from")
                comparable["fixed_value"] = float(comparable["fixed_value"]) if comparable["fixed_value"] is not None else None
                comparable["percentage_value"] = float(comparable["percentage_value"]) if comparable["percentage_value"] is not None else None
                normalized = {**values, "fixed_value": float(values.get("fixed_value", 0)), "percentage_value": float(values.get("percentage_value", 0))}
                if row.get("code") == values.get("code") and row.get("group_id") == group_id and comparable == normalized: return True
    return False


@router.get("/training/exercises")
def list_exercises(company_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*ROLES))):
    company_access(user, company_id)
    if not db.get(Company, company_id):
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    rows = db.scalars(select(TrainingExerciseProgress).where(TrainingExerciseProgress.company_id == company_id, TrainingExerciseProgress.user_id == user.id)).all()
    progress = {row.exercise_id: row for row in rows}
    return [{**exercise, "status": progress[exercise["id"]].status if exercise["id"] in progress else "not_started", "last_result": progress[exercise["id"]].last_result if exercise["id"] in progress else None, "completed_at": progress[exercise["id"]].completed_at.isoformat() if exercise["id"] in progress and progress[exercise["id"]].completed_at else None} for exercise in EXERCISES]


@router.post("/training/exercises/{exercise_id}/check")
def check_exercise(exercise_id: str, payload: TrainingExerciseCheckInput, company_id: int = Query(), db: Session = Depends(get_db), user: User = Depends(require_roles(*ROLES))):
    company_access(user, company_id)
    if exercise_id not in {exercise["id"] for exercise in EXERCISES}:
        raise HTTPException(status_code=404, detail="Exercício desconhecido")
    checks = _check_exercise(db, exercise_id, company_id, payload.probe)
    passed = all(check["passed"] for check in checks)
    progress = db.scalar(select(TrainingExerciseProgress).where(TrainingExerciseProgress.company_id == company_id, TrainingExerciseProgress.user_id == user.id, TrainingExerciseProgress.exercise_id == exercise_id))
    now = datetime.now(timezone.utc)
    if not progress:
        progress = TrainingExerciseProgress(company_id=company_id, user_id=user.id, exercise_id=exercise_id)
        db.add(progress)
    if progress.status != "completed":
        progress.status = "completed" if passed else "in_progress"
    progress.last_result = {"passed": passed, "checks": checks, "checked_at": now.isoformat()}
    if passed:
        progress.completed_at = progress.completed_at or now
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"Exercício de treinamento {exercise_id}: {'concluído' if passed else 'verificado'}"))
    db.commit()
    return {"exercise_id": exercise_id, "passed": passed, "checks": checks, "status": progress.status}


def _module_for_id(parameter_id: str):
    if parameter_id == VACATION_ID:
        return "vacations"
    for module in DEFAULTS:
        if parameter_id == f"{module}.configuration":
            return module
    if parameter_id.startswith("payroll.event:"):
        return "payroll"
    if parameter_id.startswith("benefits.catalog:"):
        return "benefits"
    return None


def _group_name(db: Session, group_id: int | None):
    group = db.get(EmployeeGroup, group_id) if group_id else None
    return group.name if group else None


def _export_parameter(db: Session, row, module: str, values: dict, scope_type: str, version: int, effective_from: str, parameter_id: str, scope_name: str | None = None):
    scope = {"type": scope_type}
    if scope_name:
        scope["group_name"] = scope_name
    return {"parameter_id": parameter_id, "module": module, "scope": scope, "version": version, "effective_from": effective_from, "values": values}


def _export_package(db: Session, company: Company, environment: str, module: str):
    if environment not in ("homologation", "production"):
        raise HTTPException(status_code=422, detail="Ambiente inválido")
    if module not in ("all", "vacations", *DEFAULTS.keys()):
        raise HTTPException(status_code=422, detail="Módulo inválido")
    parameters = []
    if module in ("all", "vacations"):
        rows = db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id == company.id, VacationConfiguration.environment == environment).order_by(VacationConfiguration.scope_key, VacationConfiguration.effective_from, VacationConfiguration.version)).all()
        for row in rows:
            item = _export_parameter(db, row, "vacations", {key: getattr(row, key) for key in CONFIG_FIELDS}, "group" if row.group_id else "company", row.version, row.effective_from.isoformat(), VACATION_ID, _group_name(db, row.group_id))
            if row.config_code:
                item["scope"] = {"type": "config", "code": row.config_code, "name": row.name or row.config_code, "simulation_text": row.simulation_text}
            parameters.append(item)
    for name in DEFAULTS:
        if module not in ("all", name):
            continue
        rows = db.scalars(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id == company.id, HRModuleConfiguration.module == name, HRModuleConfiguration.environment == environment, HRModuleConfiguration.revoked.is_(False)).order_by(HRModuleConfiguration.effective_from, HRModuleConfiguration.version)).all()
        for row in rows:
            parameters.append(_export_parameter(db, row, name, row.values, "company", row.version, row.effective_from.isoformat(), f"{name}.configuration"))
    if module in ("all", "payroll"):
        events = db.scalars(select(PayrollEvent).where(PayrollEvent.company_id == company.id, PayrollEvent.environment == environment).order_by(PayrollEvent.code)).all()
        for row in events:
            parameters.append(_export_parameter(db, row, "payroll", {key: getattr(row, key) for key in EVENT_FIELDS}, "group" if row.group_id else "company", row.version, date.today().isoformat(), f"payroll.event:{row.code}", _group_name(db, row.group_id)))
    if module in ("all", "benefits"):
        benefits = db.scalars(select(BenefitCatalog).where(BenefitCatalog.company_id == company.id, BenefitCatalog.environment == environment).order_by(BenefitCatalog.code)).all()
        for row in benefits:
            values = {key: getattr(row, key) for key in BENEFIT_FIELDS}
            values["effective_from"] = row.effective_from.isoformat()
            parameters.append(_export_parameter(db, row, "benefits", values, "group" if row.group_id else "company", row.version, row.effective_from.isoformat(), f"benefits.catalog:{row.code}", _group_name(db, row.group_id)))
    return {"format": FORMAT, "schema_version": SCHEMA_VERSION, "exported_at": datetime.now(timezone.utc).isoformat(), "source_company": {"code": company.code, "name": company.trade_name}, "source_environment": environment, "parameters": parameters}


@router.get("/configurations/export")
def export_configurations(company_id: int, module: str = "all", environment: str = "homologation", db: Session = Depends(get_db), user: User = Depends(require_roles(*ROLES))):
    company_access(user, company_id)
    company = db.get(Company, company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    package = _export_package(db, company, environment, module)
    db.add(Activity(company_id=company_id, actor=user.full_name, message=f"Configurações de {module} exportadas em JSON ({environment})."))
    db.commit()
    return package


def _package_reference_errors(db: Session, company_id: int, module: str, values: dict, package: dict) -> list[str]:
    """Cross-module references: Atribuições de Configurações need their named configurations and groups;
    the Guia Férias needs its calculation groups and sheets. Items carried in the same package count."""
    if module not in ("config_assignments", "vacation_policy") or not isinstance(values, dict):
        return []
    items = package.get("parameters") or []
    in_package_configs = {p["scope"]["code"]: p for p in items if isinstance(p, dict) and isinstance(p.get("scope"), dict) and p["scope"].get("type") == "config" and isinstance(p["scope"].get("code"), str)}
    package_setup = next((p["values"] for p in reversed(items) if isinstance(p, dict) and p.get("module") == "payroll_setup" and isinstance(p.get("values"), dict)), None)
    errors = []
    if module == "config_assignments":
        names = {g.name.lower() for g in db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id == company_id)).all()}
        existing = {c.config_code: c for c in db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id == company_id, VacationConfiguration.environment == "homologation", VacationConfiguration.config_code.is_not(None), VacationConfiguration.revoked.is_(False))).all()}
        for process, label in (("vacations", "Férias"), ("vacations_simulation", "Férias - Simulação")):
            for index, item in enumerate(values.get(process) or [], 1):
                if not isinstance(item, dict):
                    continue
                if str(item.get("group", "")).lower() not in names:
                    errors.append(f"{label}, linha {index}: o grupo '{item.get('group')}' não existe na empresa de destino.")
                code = item.get("config")
                if code not in existing and code not in in_package_configs:
                    errors.append(f"{label}, linha {index}: a configuração {code} não existe no destino nem no arquivo.")
                elif process == "vacations_simulation":
                    simulation_ok = in_package_configs[code]["values"].get("simulation_enabled") if code in in_package_configs else existing[code].simulation_enabled
                    if not simulation_ok:
                        errors.append(f"{label}, linha {index}: a configuração {code} não está disponível para simulação.")
    if module == "vacation_policy":
        setup = package_setup or active_config(db, company_id, "payroll_setup", "homologation")
        groups = {str(g).lower() for g in setup.get("calculation_groups", [])}
        sheets = set(setup.get("sheets", []))
        for index, row in enumerate(values.get("general_configs") or [], 1):
            if not isinstance(row, dict):
                continue
            if str(row.get("calculation_group", "")).lower() not in groups:
                errors.append(f"Configuração geral {index}: o grupo de cálculo {row.get('calculation_group')} não existe em Grupos de cálculo e folhas do destino.")
            for key, value in row.items():
                if key.startswith(("sheet_", "simulation_sheet_")) and value and value not in sheets:
                    errors.append(f"Configuração geral {index}: a folha {value} não existe no destino.")
    return errors


def _map_group(db: Session, company_id: int, scope: dict):
    if scope.get("type") == "config":
        if set(scope) != {"type", "code", "name", "simulation_text"} or not isinstance(scope.get("code"), str) or not re.fullmatch(r"[A-Z0-9_-]{1,20}", scope["code"]) or not isinstance(scope.get("name"), str) or not scope["name"].strip():
            raise ValueError("Configuração nomeada sem código, descrição ou texto de simulação válidos.")
        return None
    if scope.get("type") == "company":
        if set(scope) != {"type"}:
            raise ValueError("Escopo de empresa contém campos desconhecidos.")
        return None
    if scope.get("type") != "group" or set(scope) != {"type", "group_name"} or not isinstance(scope.get("group_name"), str):
        raise ValueError("O escopo deve indicar empresa ou grupo pelo nome.")
    group = db.scalar(select(EmployeeGroup).where(EmployeeGroup.company_id == company_id, EmployeeGroup.name == scope["group_name"]))
    if not group:
        raise ValueError(f"O grupo '{scope['group_name']}' não existe na empresa de destino.")
    return group.id


def _validate_import_package(db: Session, company_id: int, package: dict):
    errors = []
    changes = []
    if not isinstance(package, dict) or set(package) != {"format", "schema_version", "exported_at", "source_company", "source_environment", "parameters"} or package.get("format") != FORMAT or package.get("schema_version") != SCHEMA_VERSION:
        return {"valid": False, "errors": ["Formato ou versão do arquivo não reconhecido; esperado hcm-lab-configuration v1."], "changes": []}
    if not isinstance(package.get("source_company"), dict) or set(package["source_company"]) != {"code", "name"} or not isinstance(package["source_company"].get("code"), str) or not package["source_company"]["code"] or not isinstance(package["source_company"].get("name"), str) or package.get("source_environment") not in ("homologation", "production") or not isinstance(package.get("parameters"), list):
        return {"valid": False, "errors": ["O arquivo precisa identificar a empresa, o ambiente de origem e a lista parameters."], "changes": []}
    try:
        datetime.fromisoformat(package["exported_at"])
    except (TypeError, ValueError):
        return {"valid": False, "errors": ["A data exported_at deve estar no formato ISO 8601."], "changes": []}
    if len(package["parameters"]) == 0:
        errors.append("O arquivo não contém parâmetros para importar.")
    if len(package["parameters"]) > 1000:
        errors.append("O arquivo excede o limite de 1.000 parâmetros.")
    identity_seen = set()
    for index, item in enumerate(package["parameters"]):
        row_errors = []
        if not isinstance(item, dict):
            changes.append({"index": index, "action": "invalid", "errors": ["Cada parâmetro deve ser um objeto JSON."]})
            errors.append(f"Parâmetro {index + 1}: estrutura inválida.")
            continue
        if set(item) != {"parameter_id", "module", "scope", "version", "effective_from", "values"}:
            row_errors.append("O parâmetro contém campos ausentes ou não reconhecidos para o esquema v1.")
        parameter_id = item.get("parameter_id")
        module = item.get("module")
        values = item.get("values")
        scope = item.get("scope")
        try:
            mapped_group = _map_group(db, company_id, scope if isinstance(scope, dict) else {})
        except ValueError as exc:
            mapped_group = None
            row_errors.append(str(exc))
        if not isinstance(parameter_id, str) or not isinstance(module, str) or _module_for_id(parameter_id) != module:
            row_errors.append("Identificador estável do parâmetro incompatível com o módulo.")
        if not isinstance(values, dict):
            row_errors.append("O campo values deve ser um objeto JSON.")
            values = {}
        if type(item.get("version")) is not int or item.get("version", 0) < 1:
            row_errors.append("A versão de origem deve ser um inteiro positivo.")
        try:
            effective_from = date.fromisoformat(item.get("effective_from", ""))
        except (TypeError, ValueError):
            effective_from = None
            row_errors.append("Data de vigência inválida; use AAAA-MM-DD.")
        value_code = values.get("code")
        if not isinstance(value_code, (str, int, float, bool, type(None))):
            value_code = repr(value_code)
        key = (parameter_id, module, item.get("version"), item.get("effective_from"), item.get("scope", {}).get("type") if isinstance(item.get("scope"), dict) else None, item.get("scope", {}).get("group_name") if isinstance(item.get("scope"), dict) else None, item.get("scope", {}).get("code") if isinstance(item.get("scope"), dict) else None, value_code)
        if key in identity_seen:
            row_errors.append("Parâmetro duplicado no arquivo.")
        identity_seen.add(key)
        if parameter_id == VACATION_ID:
            if set(values) != set(CONFIG_FIELDS):
                row_errors.append("A regra de férias não contém exatamente os campos suportados.")
            else:
                bool_fields = set(CONFIG_FIELDS) - {"max_installments", "min_advance_days"}
                if any(type(values[field]) is not bool for field in bool_fields): row_errors.append("Os parâmetros de férias booleanos devem usar true ou false no JSON.")
                if type(values["max_installments"]) is not int or type(values["min_advance_days"]) is not int:
                    row_errors.append("Máximo de parcelas e antecedência devem ser números inteiros.")
                else:
                    row_errors.extend(config_errors(values))
        elif isinstance(module, str) and module in DEFAULTS and parameter_id == f"{module}.configuration":
            row_errors.extend(validate_config(module, values))
            if not row_errors:
                row_errors.extend(_package_reference_errors(db, company_id, module, values, package))
        elif parameter_id and parameter_id.startswith("payroll.event:"):
            if set(values) != EVENT_FIELDS:
                row_errors.append("O evento de folha não contém exatamente os campos suportados.")
            else:
                if not isinstance(values.get("code"), str) or values.get("code") != parameter_id.split(":", 1)[1] or not values.get("code") or len(values["code"]) > 30: row_errors.append("Código do evento incompatível com o identificador estável.")
                if values.get("kind") not in ("earning", "deduction") or values.get("calculation") not in ("fixed", "percentage") or values.get("base") != "salary": row_errors.append("Tipo, cálculo ou base do evento de folha inválidos.")
                if not isinstance(values.get("description"), str) or not values["description"].strip() or type(values.get("active")) is not bool: row_errors.append("Descrição e status do evento são obrigatórios.")
                if type(values.get("value")) not in (int, float) or not math.isfinite(values["value"]) or not 0 <= values["value"] <= 10000000: row_errors.append("Valor do evento deve estar entre 0 e 10.000.000.")
        elif parameter_id and parameter_id.startswith("benefits.catalog:"):
            if set(values) != BENEFIT_FIELDS:
                row_errors.append("O benefício não contém exatamente os campos suportados.")
            else:
                if not isinstance(values.get("code"), str) or values.get("code") != parameter_id.split(":", 1)[1] or not values.get("code") or len(values["code"]) > 30: row_errors.append("Código do benefício incompatível com o identificador estável.")
                if not isinstance(values.get("name"), str) or not values["name"].strip() or len(values["name"]) > 180: row_errors.append("Nome do benefício inválido.")
                if not isinstance(values.get("description"), str) or type(values.get("approval_required")) is not bool or type(values.get("active")) is not bool: row_errors.append("Descrição ou indicadores do benefício inválidos.")
                for numeric in ("fixed_value", "percentage_value"):
                    value = values.get(numeric)
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (numeric == "percentage_value" and value > 100): row_errors.append(f"{numeric} está fora dos limites permitidos.")
                try: date.fromisoformat(values.get("effective_from", ""))
                except (TypeError, ValueError): row_errors.append("Vigência do benefício inválida.")
        else:
            row_errors.append("Identificador do parâmetro não suportado por esta versão do importador.")
        if row_errors:
            errors.extend([f"Parâmetro {index + 1}: {error}" for error in row_errors])
            changes.append({"index": index, "parameter_id": parameter_id, "module": module, "action": "invalid", "errors": row_errors})
            continue
        current = None
        current_scope = None
        if parameter_id == VACATION_ID:
            named_code = scope.get("code") if isinstance(scope, dict) and scope.get("type") == "config" else None
            scope_filter = (VacationConfiguration.config_code == named_code) if named_code else ((VacationConfiguration.group_id == mapped_group) if mapped_group else (VacationConfiguration.group_id.is_(None) & VacationConfiguration.config_code.is_(None)))
            current = db.scalar(select(VacationConfiguration).where(VacationConfiguration.company_id == company_id, scope_filter, VacationConfiguration.effective_from == effective_from, VacationConfiguration.environment == "homologation").order_by(VacationConfiguration.version.desc()))
            current_values = {field: getattr(current, field) for field in CONFIG_FIELDS} if current else None
            current_scope = {"type": "group", "group_name": _group_name(db, current.group_id)} if current and current.group_id else {"type": "company"} if current else None
        elif module in DEFAULTS:
            current = db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id == company_id, HRModuleConfiguration.module == module, HRModuleConfiguration.effective_from == effective_from, HRModuleConfiguration.environment == "homologation", HRModuleConfiguration.revoked.is_(False)).order_by(HRModuleConfiguration.version.desc()))
            current_values = current.values if current else None
            current_scope = {"type": "company"} if current else None
        elif module == "payroll":
            current = db.scalar(select(PayrollEvent).where(PayrollEvent.company_id == company_id, PayrollEvent.environment == "homologation", PayrollEvent.code == values["code"]))
            current_values = {field: getattr(current, field) for field in EVENT_FIELDS} if current else None
            if current_values:
                current_values["value"] = float(current_values["value"])
                current_scope = {"type": "group", "group_name": _group_name(db, current.group_id)} if current.group_id else {"type": "company"}
        else:
            current = db.scalar(select(BenefitCatalog).where(BenefitCatalog.company_id == company_id, BenefitCatalog.environment == "homologation", BenefitCatalog.code == values["code"]))
            current_values = {field: getattr(current, field) for field in BENEFIT_FIELDS} if current else None
            if current_values:
                current_values["fixed_value"] = float(current_values["fixed_value"])
                current_values["percentage_value"] = float(current_values["percentage_value"])
                current_values["effective_from"] = current_values["effective_from"].isoformat()
                current_scope = {"type": "group", "group_name": _group_name(db, current.group_id)} if current.group_id else {"type": "company"}
        current_snapshot = {"scope": current_scope, "values": current_values} if current is not None else None
        proposed_snapshot = {"scope": scope, "values": values}
        action = "new" if current is None else ("unchanged" if current_snapshot == proposed_snapshot else "modified")
        changes.append({"index": index, "parameter_id": parameter_id, "module": module, "scope": scope, "action": action, "values": values, "current_snapshot": current_snapshot, "source_version": item["version"]})
    return {"valid": not errors, "errors": errors, "changes": changes}


@router.post("/configurations/import/preview")
def preview_configuration_import(payload: ConfigurationImportPreviewInput, db: Session = Depends(get_db), user: User = Depends(require_roles(*ROLES))):
    company = db.get(Company, payload.company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Empresa de destino não encontrada")
    report = _validate_import_package(db, company.id, payload.package)
    row = ConfigurationImportPreview(company_id=company.id, requested_by_id=user.id, package=payload.package, report=report, status="ready" if report["valid"] else "invalid")
    db.add(row)
    db.add(Activity(company_id=company.id, actor=user.full_name, message=f"Prévia de importação JSON {row.status} para homologação."))
    db.commit(); db.refresh(row)
    return {"preview_id": row.id, "company_id": company.id, "target_environment": "homologation", **report}


@router.post("/configurations/import/{preview_id}/apply")
def apply_configuration_import(preview_id: int, db: Session = Depends(get_db), user: User = Depends(require_roles(*ROLES))):
    row = db.get(ConfigurationImportPreview, preview_id)
    if not row:
        raise HTTPException(status_code=404, detail="Prévia de importação não encontrada")
    company_access(user, row.company_id)
    if row.status != "ready":
        raise HTTPException(status_code=409, detail="Somente uma prévia válida e ainda não aplicada pode ser importada")
    db.scalar(select(Company).where(Company.id == row.company_id).with_for_update())
    report = _validate_import_package(db, row.company_id, row.package)
    if not report["valid"]:
        row.status = "invalid"; row.report = report; db.commit()
        raise HTTPException(status_code=422, detail={"message": "O arquivo deixou de passar na validação.", "report": report})
    original_snapshots = [item.get("current_snapshot") for item in row.report["changes"]]
    current_snapshots = [item.get("current_snapshot") for item in report["changes"]]
    if original_snapshots != current_snapshots:
        row.status = "stale"; row.report = report; db.commit()
        raise HTTPException(status_code=409, detail="As configurações mudaram depois da prévia. Gere uma nova prévia antes de importar.")
    for item in row.package["parameters"]:
        parameter_id = item["parameter_id"]; module = item["module"]; values = item["values"]
        group_id = _map_group(db, row.company_id, item["scope"])
        eff = date.fromisoformat(item["effective_from"])
        if parameter_id == VACATION_ID:
            named = item["scope"] if item["scope"].get("type") == "config" else None
            payload = VacationConfigurationInput(company_id=row.company_id, environment="homologation", group_id=None if named else group_id, effective_from=eff,
                                                 config_code=named["code"] if named else None, name=named["name"] if named else None, simulation_text=named["simulation_text"] if named else None, **values)
            config, errors = create_configuration_version(db, payload, user.id, user.full_name)
            if errors: raise HTTPException(status_code=422, detail=errors)
        elif module in DEFAULTS:
            latest = db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id == row.company_id, HRModuleConfiguration.module == module, HRModuleConfiguration.environment == "homologation").order_by(HRModuleConfiguration.version.desc()).limit(1))
            version = latest.version + 1 if latest else 1
            config = HRModuleConfiguration(company_id=row.company_id, module=module, environment="homologation", version=version, effective_from=eff, values=values, created_by=user.id)
            db.add(config); db.flush()
            db.add(HRModuleConfigurationHistory(company_id=row.company_id, module=module, environment="homologation", version=version, actor_id=user.id, actor_name=user.full_name, snapshot={"source": "json_import", "source_version": item["version"], "effective_from": eff.isoformat(), "values": values}))
        elif module == "payroll":
            event = db.scalar(select(PayrollEvent).where(PayrollEvent.company_id == row.company_id, PayrollEvent.environment == "homologation", PayrollEvent.code == values["code"]))
            mapped = {**values, "group_id": group_id}
            if event:
                if any(getattr(event, key) != value for key, value in mapped.items()):
                    for key, value in mapped.items(): setattr(event, key, value)
                    event.version += 1
            else: db.add(PayrollEvent(company_id=row.company_id, environment="homologation", **mapped))
        else:
            benefit = db.scalar(select(BenefitCatalog).where(BenefitCatalog.company_id == row.company_id, BenefitCatalog.environment == "homologation", BenefitCatalog.code == values["code"]))
            mapped = {**values, "effective_from": date.fromisoformat(values["effective_from"]), "group_id": group_id}
            if benefit:
                if any(getattr(benefit, key) != value for key, value in mapped.items()):
                    for key, value in mapped.items(): setattr(benefit, key, value)
                    benefit.version += 1
            else: db.add(BenefitCatalog(company_id=row.company_id, environment="homologation", **mapped))
    row.status = "applied"; row.applied_at = datetime.now(timezone.utc)
    db.add(Activity(company_id=row.company_id, actor=user.full_name, message=f"Prévia de importação JSON {row.id} aplicada em Homologação."))
    db.commit()
    return {"preview_id": row.id, "status": row.status, "target_environment": "homologation", "applied_at": row.applied_at.isoformat()}

