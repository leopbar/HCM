"""Integration contract v2: catalog-driven, every parametrizable module, named vacation
configurations, ordered assignments and multi-group membership. The v1 routes stay untouched
until the ERP AI migrates; both share credentials, idempotency and the operation ledger."""
from datetime import date, datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.database import get_db
from app.hr_api import cross_errors
from app.integration_api import LABELS as VACATION_LABELS, allowed_company, integration_auth, search_companies
from app.integration_execution import RollbackInput, digest, execution_auth, operation_out, policy_checks
from app.integration_hr import report_for as module_report, values_from
from app.models import (Activity, Company, Employee, EmployeeGroup, EmployeeGroupMembership, HRModuleConfiguration,
    HRModuleConfigurationHistory, IntegrationOperation, VacationConfiguration, VacationConfigurationHistory)
from app.policies import ALL_STEPS, BOUNDS, DEFAULTS, FIELD_LABELS, LABELS, field_schema, normalize, validate_config
from app.schemas import VacationConfigurationInput
from app.vacations import CONFIG_FIELDS, config_errors, config_snapshot, create_configuration_version, latest_scope_config, resolve_rule

router = APIRouter(prefix="/api/integration/v2", tags=["Integração consultiva v2"], dependencies=[Depends(integration_auth)])
CONTRACT = 2
ERP_VERSION = "0.2.0"
NAMED_FIELDS = ("config_code", "name", "simulation_text")
INTEGRATION_MODULES = tuple(DEFAULTS)
HASHED_KEYS = ("version", "module", "fields", "scopes", "precedence", "rules")

def contract_hash(catalog):
    """Fingerprint of the semantic part of a catalog; the ERP stores it with each case."""
    return digest({key: catalog.get(key) for key in HASHED_KEYS})

def snapshot_hash(snapshot):
    keys = ("schedules", "as_of", "catalog_version", "erp_version", "groups", "company", "assignments")
    return digest({key: snapshot.get(key) for key in keys})

def plan_hash(expected_fingerprint, configurations, assignments):
    return digest({"expected_fingerprint": expected_fingerprint, "configurations": configurations, "assignments": assignments})

MODULE_DESCRIPTIONS = {
    "vacations": "Regras de solicitação de férias: processo habilitado, quem pode solicitar, aprovação, simulação, parcelas, antecedência mínima, adiantamento do 13º e saldo visível. Por empresa, grupo ou configuração nomeada.",
    "vacation_policy": "Política de férias da empresa: abono pecuniário, regras CLT de parcelamento, bloqueio antes de feriado e DSR, aviso, ciência do colaborador, prazo mínimo de aprovação e Guia Férias (folhas por grupo de cálculo e tipo de cargo).",
    "config_assignments": "Atribuições de Configurações: lista ordenada que liga grupos de colaboradores a configurações nomeadas de férias, para Férias e Férias - Simulação.",
    "workflow": "Fluxos de aprovação e SLA de todos os processos: etapas (gestor imediato, RH, administrador, RH de destino), prazos em horas úteis, expediente e exceções por grupo.",
    "assignments": "Atribuições de Seleção de Colaboradores: para quem cada processo pode ser aberto (próprio solicitante, subordinados diretos, indiretos ou por visibilidade) e de onde vem a configuração.",
    "process_fields": "Informações adicionais: campos extras por processo (texto, número, data, sim/não, lista) gravados na solicitação.",
    "admission": "Admissão: antecedência mínima e máxima, fins de semana e feriados, aprovação, documentos obrigatórios e retroatividade.",
    "contracting": "Contratação: contrato de experiência, criação do funcionário na aprovação, exigência da tabela salarial, do quadro de vagas e controle por posição.",
    "timekeeping": "Controle de ponto: jornada diária, intervalo mínimo, tolerância de atraso, horas extras e seu limite, aprovação e ajuste manual.",
    "work_schedule": "Jornada e escala: horários previstos de entrada e saída e dias de trabalho.",
    "payroll": "Folha demonstrativa: processo habilitado, moeda de apresentação e aviso de cálculo demonstrativo.",
    "payroll_setup": "Grupos de cálculo e folhas de pagamento usados pela Guia Férias e pelo cadastro de colaboradores.",
    "benefits": "Benefícios: processo habilitado e solicitação pelo próprio colaborador.",
    "termination": "Rescisão e aviso prévio: aviso proporcional, dias base, adicionais por ano, máximo, desativação do acesso e retroatividade.",
    "transport": "Vale-transporte: processo, solicitação pelo colaborador, desconto percentual e dias úteis por mês.",
    "dependents": "Dependentes: processo, solicitação pelo colaborador, máximo de dependentes e exigência de CPF.",
    "personal_data": "Alteração de dados pessoais: quais dados o colaborador pode alterar.",
    "leave": "Afastamento e ausência: processo, solicitação, limite retroativo e duração máxima.",
    "movement": "Movimentação: alteração salarial, aumento máximo, tabela salarial e quadro de vagas.",
    "transfer": "Cessão e busca de colaborador entre empresas.",
    "requisition": "Requisição de pessoal: quadro de vagas e criação de posições.",
    "recess": "Recesso de estágio: dias por ano, antecedência mínima e períodos.",
    "value_entry": "Lançamento de valores: valor máximo por lançamento.",
    "recruitment": "Recrutamento e seleção: etapas, critérios de avaliação, candidaturas internas, requisição obrigatória e retenção de dados.",
}

def available_modules():
    return [{"id": "vacations", "label": "Férias", "scopes": ["company", "group", "config"], "description": MODULE_DESCRIPTIONS["vacations"]},
            *[{"id": module, "label": LABELS[module], "scopes": ["company"], "description": MODULE_DESCRIPTIONS.get(module, "")} for module in INTEGRATION_MODULES]]

CAPABILITIES = {"apply_homologation": True, "automatic_tests": True, "rollback": True, "production_write": False,
                "named_configurations": True, "assignments": True, "contract_hash": True}

# ---------------------------------------------------------------- catalogs

VACATION_RULES = [
    "Precedência por colaborador: 1) Atribuições de Configurações, na ordem da lista; 2) regra própria de um dos grupos do colaborador; 3) regra da empresa; 4) padrão do sistema.",
    "Um colaborador pode participar de vários grupos (principal e participações adicionais). Com atribuições, a primeira linha cujo grupo inclui o colaborador vence.",
    "Regra de grupo, de empresa ou nomeada é completa: não há mesclagem por campo entre níveis.",
    "Configurações nomeadas têm código e descrição, não são ligadas diretamente a um grupo e só valem quando atribuídas em Atribuições de Configurações.",
    "Sem parcelamento, max_installments deve ser 1; com parcelamento, deve ser 2 ou 3.",
    "Com processo desabilitado, solicitações por funcionário e gestor devem estar desabilitadas.",
    "Aprovação atual é feita pelo perfil Gestor de RH ou administrador; etapas e SLA ficam no módulo workflow (processes.vacations).",
    "Parcelas são um limite demonstrativo; não há agendamento de períodos separados.",
    "A simulação operacional resolve a configuração na data atual, não na data inicial das férias.",
    "Abono pecuniário, regras CLT de parcelamento, aviso, ciência e Guia Férias ficam no módulo vacation_policy.",
]
VACATION_RELATED = [
    {"id": "config_assignments", "label": LABELS["config_assignments"], "description": "Lista ordenada grupo → configuração nomeada para Férias e Férias - Simulação. Pode ser alterada no mesmo plano de Férias (campo assignments)."},
    {"id": "vacation_policy", "label": LABELS["vacation_policy"], "description": "Abono pecuniário, regras CLT de parcelamento, bloqueio antes de feriado/DSR, aviso, ciência, prazo mínimo de aprovação e Guia Férias."},
    {"id": "workflow", "label": LABELS["workflow"], "description": "Etapas de aprovação, SLA e exceções por grupo do processo de férias (processes.vacations)."},
]
MODULE_RULES = {
    "admission": ["A antecedência mínima não pode ser maior que a máxima."],
    "timekeeping": ["Jornada de 1 a 16 horas, intervalo de 0 a 300 minutos, tolerância de 0 a 120 minutos e horas extras de 0 a 8 por dia."],
    "termination": ["O aviso prévio base não pode superar o máximo."],
    "work_schedule": ["Horários no formato HH:MM com entrada antes da saída; dias de 0 (segunda) a 6 (domingo)."],
    "workflow": [f"Até 5 etapas por processo; aprovadores válidos: {', '.join(ALL_STEPS)}; prazo de 1 a 720 horas úteis.", "group_overrides mapeia o nome de um grupo de colaboradores para etapas próprias.", "Todos os processos precisam constar em processes."],
    "config_assignments": ["Grupo e configuração devem existir em homologação; em vacations_simulation só entram configurações com simulação habilitada.", "A ordem da lista define a prioridade quando o colaborador participa de mais de um grupo."],
    "vacation_policy": ["Grupos de cálculo e folhas de general_configs devem existir em payroll_setup.", "Com by_job_type habilitado cada configuração geral informa tipos de cargo; sem ele, nenhuma."],
    "payroll_setup": ["De 1 a 20 nomes distintos em cada lista; são referenciados por vacation_policy e pelo cadastro de colaboradores."],
    "recruitment": ["De 2 a 8 etapas e de 1 a 8 critérios, distintos, com 2 a 40 caracteres."],
    "process_fields": ["Até 10 campos por processo; nome técnico em minúsculas; campos de seleção exigem opções."],
    "assignments": ["Ao menos uma regra; process pode ser * ou um processo; targets entre self, direct, indirect e visibility."],
}
MODULE_RELATED = {
    "config_assignments": [{"id": "vacations", "label": "Férias", "description": "Configurações nomeadas referenciadas pelas atribuições."}],
    "vacation_policy": [{"id": "payroll_setup", "label": LABELS["payroll_setup"], "description": "Grupos de cálculo e folhas usados pelas configurações gerais."}],
    "workflow": [{"id": "assignments", "label": LABELS["assignments"], "description": "Para quem cada processo pode ser aberto."}],
}
GENERIC_RULES = ["Configuração completa por empresa, em homologação, sem exceção por grupo neste módulo.",
                 "O plano deve informar todos os parâmetros do módulo; parâmetros não citados mantêm o valor atual.",
                 "A produção só muda pelo processo de publicação aprovado no HCM."]

def kind_of(value):
    return "boolean" if type(value) is bool else "integer" if type(value) is int else "string" if isinstance(value, str) else "array" if isinstance(value, list) else "object"

def vacation_catalog():
    fields = []
    for key in CONFIG_FIELDS:
        integer = key in ("max_installments", "min_advance_days")
        schema = {"type": "integer", "minimum": 1 if key == "max_installments" else 0, "maximum": 3 if key == "max_installments" else 365} if integer else {"type": "boolean"}
        fields.append({"id": key, "label": VACATION_LABELS[key], "type": "integer" if integer else "boolean", "schema": schema,
                       "minimum": schema.get("minimum"), "maximum": schema.get("maximum")})
    named_fields = [{"id": "config_code", "label": "Código da configuração nomeada", "schema": {"type": "string", "pattern": r"^[A-Z0-9_-]{1,20}$"}},
                    {"id": "name", "label": "Descrição da configuração nomeada", "schema": {"type": "string", "minLength": 1, "maxLength": 120}},
                    {"id": "simulation_text", "label": "Texto exibido na simulação", "schema": {"type": ["string", "null"], "maxLength": 2000}}]
    data = {"version": "vacations-2", "contract": CONTRACT, "erp_version": ERP_VERSION, "module": "vacations", "module_label": "Férias", "fields": fields,
            "scopes": [{"type": "company", "label": "Regra geral da empresa", "id_field": None},
                       {"type": "group", "label": "Regra própria de um grupo de colaboradores", "id_field": "group_id", "source": "snapshot.groups"},
                       {"type": "config", "label": "Configuração nomeada, atribuída a grupos em Atribuições de Configurações", "id_field": "config_code", "source": "snapshot.named_configurations", "fields": named_fields}],
            "precedence": [{"level": 1, "scope": "assignment", "description": "Atribuições de Configurações (módulo config_assignments), na ordem da lista: a primeira linha cujo grupo inclui o colaborador vence."},
                           {"level": 2, "scope": "group", "description": "Regra própria do primeiro grupo do colaborador que tiver regra (grupo principal primeiro)."},
                           {"level": 3, "scope": "company", "description": "Regra geral da empresa."},
                           {"level": 4, "scope": "system", "description": "Padrão do sistema."}],
            "rules": VACATION_RULES, "related_modules": VACATION_RELATED, "available_modules": available_modules(), "capabilities": CAPABILITIES,
            "plan": {"configurations": "Lista de regras completas; escopo pelo par group_id/config_code.", "assignments": "Opcional: nova lista completa de Atribuições de Configurações (vacations e vacations_simulation).",
                     "approval_hash": "sha256 de {expected_fingerprint, configurations (objetos completos, inclusive campos nulos), assignments (ou null)} com chaves ordenadas."},
            "effective_basis": "snapshot.effective traz a regra resolvida para um colaborador que participa apenas do grupo indicado; overlap_count mostra quantos colaboradores do grupo também estão em outros grupos."}
    data["contract_hash"] = contract_hash(data)
    return data

def check_module(module):
    if module not in DEFAULTS:
        raise HTTPException(404, "Módulo de parametrização não disponível no HCM")

def module_catalog(module):
    check_module(module)
    fields = []
    for key, value in DEFAULTS[module].items():
        field = {"id": key, "label": FIELD_LABELS.get(key, key), "type": kind_of(value), "schema": field_schema(module, key)}
        if key in BOUNDS and type(value) is int:
            field.update(minimum=BOUNDS[key][0], maximum=BOUNDS[key][1])
        fields.append(field)
    data = {"version": f"{module}-2", "contract": CONTRACT, "erp_version": ERP_VERSION, "module": module, "module_label": LABELS[module], "fields": fields,
            "scopes": [{"type": "company", "label": "Configuração da empresa", "id_field": None}],
            "precedence": [{"level": 1, "scope": "company", "description": "Versão vigente salva para a empresa em homologação."},
                           {"level": 2, "scope": "default", "description": "Padrão do sistema quando a empresa nunca salvou o módulo ou para parâmetros criados depois."}],
            "rules": [*MODULE_RULES.get(module, []), *GENERIC_RULES], "related_modules": MODULE_RELATED.get(module, []),
            "available_modules": available_modules(), "capabilities": {**CAPABILITIES, "named_configurations": False, "assignments": False}}
    data["contract_hash"] = contract_hash(data)
    return data

@router.get("/modules")
def modules():
    return {"contract": CONTRACT, "items": available_modules()}

@router.get("/catalog")
def catalog(module: str = Query(default="vacations", max_length=40)):
    return vacation_catalog() if module == "vacations" else module_catalog(module)

@router.get("/modules/{module}/catalog")
def catalog_for_module(module: str):
    return catalog(module)

router.add_api_route("/companies", search_companies, methods=["GET"])

# ---------------------------------------------------------------- vacations snapshot

def module_values_as_of(db, company_id, module, environment, as_of):
    row = db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id == company_id, HRModuleConfiguration.module == module,
        HRModuleConfiguration.environment == environment, HRModuleConfiguration.revoked.is_(False), HRModuleConfiguration.effective_from <= as_of)
        .order_by(HRModuleConfiguration.effective_from.desc(), HRModuleConfiguration.version.desc()).limit(1))
    return (normalize(module, row.values) if row else dict(DEFAULTS[module])), row

def group_stats(db, company, groups):
    employees = db.scalars(select(Employee).where(Employee.company_id == company.id)).all()
    memberships = db.scalars(select(EmployeeGroupMembership).join(Employee, Employee.id == EmployeeGroupMembership.employee_id).where(Employee.company_id == company.id)).all()
    by_employee = {e.id: ({e.group_id} if e.group_id else set()) for e in employees}
    for membership in memberships:
        by_employee.setdefault(membership.employee_id, set()).add(membership.group_id)
    stats = []
    for group in groups:
        members = {employee_id for employee_id, ids in by_employee.items() if group.id in ids}
        primary = sum(1 for e in employees if e.group_id == group.id)
        stats.append({"id": group.id, "name": group.name, "employee_count": primary, "primary_count": primary, "member_count": len(members),
                      "overlap_count": sum(1 for employee_id in members if len(by_employee[employee_id]) > 1)})
    return stats

def vacation_snapshot(db, company, as_of):
    groups = db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id == company.id).order_by(EmployeeGroup.id)).all()
    stats = group_stats(db, company, groups)
    schedules, named, assignments, effective = {}, {}, {}, {}
    for environment in ("homologation", "production"):
        rows = db.scalars(select(VacationConfiguration).where(
            (VacationConfiguration.company_id == company.id) | (VacationConfiguration.scope_key == "system"),
            VacationConfiguration.environment == environment, VacationConfiguration.revoked.is_(False),
        ).order_by(VacationConfiguration.scope_key, VacationConfiguration.effective_from, VacationConfiguration.version)).all()
        schedules[environment] = [config_snapshot(row) for row in rows]
        latest_named = {}
        for row in rows:
            if row.config_code and row.effective_from <= as_of:
                latest_named[row.config_code] = config_snapshot(row)
        named[environment] = list(latest_named.values())
        values, row = module_values_as_of(db, company.id, "config_assignments", environment, as_of)
        assignments[environment] = {"vacations": values["vacations"], "vacations_simulation": values["vacations_simulation"],
                                    "version": row.version if row else 0, "effective_from": row.effective_from.isoformat() if row else None}
        def lookup(scope_key, environment=environment):
            found = latest_scope_config(db, scope_key, as_of, environment)
            return config_snapshot(found) if found else None
        effective[environment] = []
        for group in [None, *stats]:
            config, origin, why = resolve_rule(lookup, company.id, [group] if group else [], assignments[environment]["vacations"], company.trade_name)
            effective[environment].append({"group_id": group["id"] if group else None, "group_name": group["name"] if group else "Regra geral",
                                           "configuration": config, "origin": origin, "why": why, "overlap_count": group["overlap_count"] if group else 0})
    data = {"contract": CONTRACT, "company": {"id": company.id, "code": company.code, "name": company.trade_name},
            "as_of": as_of.isoformat(), "consulted_at": datetime.now(timezone.utc).isoformat(),
            "catalog_version": "vacations-2", "contract_hash": vacation_catalog()["contract_hash"], "erp_version": ERP_VERSION,
            "groups": stats, "schedules": schedules, "named_configurations": named, "assignments": assignments, "effective": effective,
            "effective_basis": vacation_catalog()["effective_basis"]}
    data["fingerprint"] = snapshot_hash(data)
    return data

@router.get("/vacations/snapshot")
def snapshot(company_code: str = Query(min_length=1, max_length=20), as_of: date = Query(default_factory=date.today), db: Session = Depends(get_db)):
    return vacation_snapshot(db, allowed_company(db, company_code), as_of)

# ---------------------------------------------------------------- vacations plan

class AssignmentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vacations: list[dict] = Field(default_factory=list, max_length=100)
    vacations_simulation: list[dict] = Field(default_factory=list, max_length=100)

class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    company_code: str = Field(min_length=1, max_length=20)
    effective_from: date
    configurations: list[VacationConfigurationInput] = Field(default_factory=list, max_length=20)
    assignments: AssignmentsInput | None = None

class ApplyInput(PlanInput):
    operation_id: UUID
    approved_by: str = Field(min_length=1, max_length=100)
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

def scope_key_for(company_id, item):
    code = item.get("config_code") if isinstance(item, dict) else item.config_code
    group_id = item.get("group_id") if isinstance(item, dict) else item.group_id
    return f"company:{company_id}:config:{code}" if code else f"company:{company_id}:group:{group_id}" if group_id is not None else f"company:{company_id}:default"

def canonical(plan: PlanInput):
    return [item.model_dump(mode="json") for item in plan.configurations], (plan.assignments.model_dump() if plan.assignments else None)

def assignment_errors(db, company, assignments, planned):
    """Planned-aware version of hr_api.cross_errors: named configurations created in the same plan count as existing."""
    errors = []
    names = {g.name.lower() for g in db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id == company.id)).all()}
    existing = {}
    for row in db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id == company.id, VacationConfiguration.environment == "homologation",
                                                              VacationConfiguration.config_code.is_not(None), VacationConfiguration.revoked.is_(False)).order_by(VacationConfiguration.version)).all():
        existing[row.config_code] = row.simulation_enabled
    for item in planned:
        if item.config_code:
            existing[item.config_code] = item.simulation_enabled
    for process, label in (("vacations", "Férias"), ("vacations_simulation", "Férias - Simulação")):
        for index, item in enumerate(assignments.get(process) or [], 1):
            if not isinstance(item, dict):
                continue
            if str(item.get("group", "")).lower() not in names:
                errors.append(f"{label}, linha {index}: grupo de colaboradores {item.get('group')} não existe.")
            if item.get("config") not in existing:
                errors.append(f"{label}, linha {index}: configuração {item.get('config')} não existe em homologação nem no plano.")
            elif process == "vacations_simulation" and not existing[item.get("config")]:
                errors.append(f"{label}, linha {index}: a configuração {item.get('config')} não está disponível para simulação.")
    return errors

def plan_problems(db, company, plan: PlanInput, before):
    """Returns (scope_violations, validation_errors). Scope violations are refused with 403."""
    scope, errors, seen = [], [], set()
    groups = {item["id"] for item in before["groups"]}
    for index, item in enumerate(plan.configurations, 1):
        if item.company_id != company.id or item.environment != "homologation" or item.effective_from != plan.effective_from:
            scope.append(f"Configuração {index}: o plano deve conter somente a empresa, o ambiente de homologação e a vigência aprovados.")
        if item.group_id is not None and item.group_id not in groups:
            scope.append(f"Configuração {index}: grupo fora da empresa autorizada.")
        key = scope_key_for(company.id, item)
        if key in seen:
            errors.append(f"Configuração {index}: escopo repetido no plano ({key}).")
        seen.add(key)
        errors.extend(f"Configuração {index}: {text}" for text in config_errors(item.model_dump()))
    if plan.assignments is not None:
        values = normalize("config_assignments", plan.assignments.model_dump())
        errors.extend(validate_config("config_assignments", values))
        errors.extend(assignment_errors(db, company, values, plan.configurations))
    return scope, errors

@router.post("/vacations/validate")
def validate(payload: PlanInput, db: Session = Depends(get_db)):
    company = allowed_company(db, payload.company_code)
    before = vacation_snapshot(db, company, payload.effective_from)
    scope, errors = plan_problems(db, company, payload, before)
    if scope:
        raise HTTPException(403, scope)
    return {"valid": not errors, "errors": errors, "persisted": False, "fingerprint": before["fingerprint"], "contract_hash": before["contract_hash"]}

def schedule_index(rows):
    latest = {}
    for row in sorted(rows, key=lambda value: value["version"]):
        latest[(row["scope_key"], row["effective_from"])] = {"scope_key": row["scope_key"], "effective_from": row["effective_from"], "version": row["version"],
                                                              **{key: row.get(key) for key in NAMED_FIELDS}, **{key: row[key] for key in CONFIG_FIELDS}}
    return latest

def test_result(before, after, configurations, assignments):
    company_id = after["company"]["id"]
    expected = schedule_index(before["schedules"]["homologation"])
    for item in configurations:
        key = scope_key_for(company_id, item)
        previous = [value for (scope, _), value in expected.items() if scope == key]
        expected[(key, item["effective_from"])] = {"scope_key": key, "effective_from": item["effective_from"], "version": max((v["version"] for v in previous), default=0) + 1,
                                                   **{field: item.get(field) or None for field in NAMED_FIELDS}, **{field: item[field] for field in CONFIG_FIELDS}}
    strip = lambda index: {k: {f: v for f, v in row.items() if f != "version"} for k, row in index.items()}
    planned_assignments = normalize("config_assignments", assignments) if assignments else {k: before["assignments"]["homologation"][k] for k in ("vacations", "vacations_simulation")}
    actual_assignments = {k: after["assignments"]["homologation"][k] for k in ("vacations", "vacations_simulation")}
    checks = [{"name": "Parâmetros, escopos e vigências correspondem ao plano aprovado", "passed": strip(expected) == strip(schedule_index(after["schedules"]["homologation"])), "origin": "API HCM"},
              {"name": "Atribuições de Configurações correspondem ao plano", "passed": planned_assignments == actual_assignments, "origin": "API HCM"},
              {"name": "Produção permanece preservada", "passed": before["schedules"]["production"] == after["schedules"]["production"] and before["assignments"]["production"] == after["assignments"]["production"], "origin": "API HCM"}]
    def expected_lookup(scope_key):
        candidates = [value for (key, eff), value in expected.items() if key == scope_key and eff <= after["as_of"]]
        return max(candidates, key=lambda value: (value["effective_from"], value["version"])) if candidates else None
    for row in after["effective"]["homologation"]:
        config = row["configuration"]
        if config is None:
            checks.append({"name": f"Regra disponível: {row['group_name']}", "passed": False, "origin": "API HCM"})
            continue
        groups = [{"id": row["group_id"], "name": row["group_name"]}] if row["group_id"] is not None else []
        target, origin, _ = resolve_rule(expected_lookup, company_id, groups, planned_assignments["vacations"], after["company"]["name"])
        passed = target is not None and origin["scope"] == (row.get("origin") or {}).get("scope") and all(target[key] == config[key] for key in CONFIG_FIELDS)
        checks.append({"name": f"Precedência e valores efetivos: {row['group_name']}", "passed": passed, "origin": "Resolução real por vigência e atribuições"})
        checks.extend(policy_checks(row["group_name"], config, after["as_of"]))
    named_codes = {item.get("config_code") for item in configurations if item.get("config_code")}
    for item in configurations:
        if item.get("group_id") is not None and not item.get("config_code"):
            row = next((r for r in after["effective"]["homologation"] if r["group_id"] == item["group_id"]), None)
            key = scope_key_for(company_id, item)
            shadowed = row is None or not row.get("origin") or row["origin"]["scope_key"] != key
            checks.append({"name": f"Regra planejada é a efetiva: {row['group_name'] if row else item['group_id']}", "passed": not shadowed, "origin": "Resolução real por vigência e atribuições",
                           "detail": None if not shadowed else f"A regra do grupo é sobreposta por {row['origin']['label']} ({row['origin']['scope']}); alterar o grupo não muda a regra efetiva de ninguém."})
    for code in sorted(named_codes):
        assigned = [item for item in actual_assignments["vacations"] if item.get("config") == code]
        checks.append({"name": f"Configuração nomeada {code} atribuída", "passed": True, "origin": "API HCM",
                       "detail": f"Atribuída a {len(assigned)} grupo(s) em Férias." if assigned else "Sem atribuição em Férias: não vale para nenhum colaborador até ser atribuída."})
    groups = {g["name"].lower() for g in after["groups"]}
    codes = {row["config_code"] for row in after["schedules"]["homologation"] if row.get("config_code")}
    dangling = [f"{process}: {item.get('group')} → {item.get('config')}" for process in ("vacations", "vacations_simulation") for item in actual_assignments[process]
                if str(item.get("group", "")).lower() not in groups or item.get("config") not in codes]
    checks.append({"name": "Atribuições referenciam grupos e configurações existentes", "passed": not dangling, "origin": "API HCM", "detail": "; ".join(dangling) or None})
    return {"passed": all(check["passed"] for check in checks), "checks": checks,
            "limits": "Testes automáticos de configuração, precedência (inclusive atribuições), política de férias e integridade das atribuições. Não criam solicitações reais nem comprovam requisitos ambíguos ou cálculos trabalhistas."}

def get_operation(db, operation_id, module):
    row = db.get(IntegrationOperation, str(operation_id))
    if not row or row.payload.get("contract") != CONTRACT or row.payload.get("module") != module:
        raise HTTPException(404, "Operação não encontrada neste contrato")
    return row

def add_assignments_version(db, company, effective_from, values, actor, operation_id):
    previous = db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id == company.id, HRModuleConfiguration.module == "config_assignments",
                                                             HRModuleConfiguration.environment == "homologation").order_by(HRModuleConfiguration.version.desc()).limit(1))
    row = HRModuleConfiguration(company_id=company.id, module="config_assignments", environment="homologation", version=previous.version + 1 if previous else 1,
                                effective_from=effective_from, values=values, created_by=None)
    db.add(row); db.flush()
    db.add(HRModuleConfigurationHistory(company_id=company.id, module="config_assignments", environment="homologation", version=row.version, actor_id=None, actor_name=actor,
                                        snapshot={"effective_from": effective_from.isoformat(), "values": values, "operation_id": operation_id}))
    return row

@router.post("/vacations/apply", dependencies=[Depends(execution_auth)])
def apply(payload: ApplyInput, db: Session = Depends(get_db)):
    company = allowed_company(db, payload.company_code)
    db.scalar(select(Company).where(Company.id == company.id).with_for_update())
    configurations, assignments = canonical(payload)
    packet = {**payload.model_dump(mode="json"), "configurations": configurations, "assignments": assignments, "module": "vacations", "contract": CONTRACT}
    request_hash = digest(packet)
    existing = db.get(IntegrationOperation, str(payload.operation_id))
    if existing:
        if existing.company_id != company.id or existing.request_hash != request_hash:
            raise HTTPException(409, "Identificador da operação já utilizado por outro plano")
        return operation_out(existing)
    if plan_hash(payload.expected_fingerprint, configurations, assignments) != payload.approval_hash:
        raise HTTPException(409, "O plano não corresponde à aprovação recebida")
    before = vacation_snapshot(db, company, payload.effective_from)
    if before["fingerprint"] != payload.expected_fingerprint:
        raise HTTPException(409, "As configurações mudaram após a análise. Faça nova análise antes de aplicar.")
    scope, errors = plan_problems(db, company, payload, before)
    if scope:
        raise HTTPException(403, scope)
    if errors:
        raise HTTPException(422, errors)
    row = IntegrationOperation(id=str(payload.operation_id), company_id=company.id, request_hash=request_hash, status="applying",
                               approved_by=payload.approved_by, payload=packet, before_snapshot=before, after_snapshot={}, report={}, created_ids=[])
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Identificador da operação já utilizado por outro plano") from exc
    actor = f"ERP AI · {payload.approved_by}"
    report = {}
    try:
        with db.begin_nested():
            created = []
            for item in payload.configurations:
                config, item_errors = create_configuration_version(db, item, None, actor)
                if item_errors:
                    raise ValueError("Validação rejeitada: " + "; ".join(item_errors))
                created.append({"kind": "vacation", "id": config.id})
            if assignments is not None:
                values = normalize("config_assignments", assignments)
                if conflicts := cross_errors(db, company.id, "config_assignments", values):
                    raise ValueError("Atribuições rejeitadas: " + "; ".join(conflicts))
                created.append({"kind": "module", "id": add_assignments_version(db, company, payload.effective_from, values, actor, row.id).id})
            after = vacation_snapshot(db, company, payload.effective_from)
            report = test_result(before, after, configurations, assignments)
            if not report["passed"]:
                raise ValueError("Os testes automáticos não passaram; alterações desfeitas")
        row.created_ids = created; row.after_snapshot = after; row.report = report; row.status = "applied"
    except ValueError as exc:
        row.status = "failed"; row.report = {**report, "passed": False, "error": str(exc), "rolled_back": True}; row.after_snapshot = before
    db.add(Activity(company_id=company.id, actor=actor, message=f"Operação {row.id} (contrato v2): {row.status}; homologação de férias; aprovação {payload.approval_hash}."))
    db.commit(); db.refresh(row)
    return operation_out(row)

@router.get("/vacations/operations/{operation_id}")
def operation(operation_id: UUID, db: Session = Depends(get_db)):
    return operation_out(get_operation(db, operation_id, "vacations"))

@router.get("/vacations/operations/{operation_id}/verify")
def verify(operation_id: UUID, db: Session = Depends(get_db)):
    row = get_operation(db, operation_id, "vacations")
    company = db.get(Company, row.company_id)
    current = vacation_snapshot(db, company, date.fromisoformat(row.payload["effective_from"]))
    return {"operation_id": row.id, "status": row.status, "report": test_result(row.before_snapshot, current, row.payload["configurations"], row.payload.get("assignments")), "current_snapshot": current}

@router.post("/vacations/operations/{operation_id}/rollback", dependencies=[Depends(execution_auth)])
def rollback(operation_id: UUID, payload: RollbackInput, db: Session = Depends(get_db)):
    row = get_operation(db, operation_id, "vacations")
    company = db.scalar(select(Company).where(Company.id == row.company_id).with_for_update())
    db.refresh(row)
    if row.status == "reverted":
        return operation_out(row)
    if row.status != "applied":
        raise HTTPException(409, "Somente uma aplicação concluída pode ser revertida")
    effective_from = date.fromisoformat(row.payload["effective_from"])
    current = vacation_snapshot(db, company, effective_from)
    if current["fingerprint"] != payload.expected_fingerprint or current["fingerprint"] != row.after_snapshot["fingerprint"]:
        raise HTTPException(409, "O HCM mudou após a aplicação. Reversão bloqueada para preservar alterações posteriores.")
    for item in row.created_ids:
        db.get(VacationConfiguration if item["kind"] == "vacation" else HRModuleConfiguration, item["id"]).revoked = True
    db.flush()
    restored = vacation_snapshot(db, company, effective_from)
    if restored["fingerprint"] != row.before_snapshot["fingerprint"]:
        db.rollback()
        raise HTTPException(409, "Não foi possível restaurar exatamente a configuração anterior")
    for item in row.created_ids:
        if item["kind"] == "vacation":
            config = db.get(VacationConfiguration, item["id"])
            db.add(VacationConfigurationHistory(company_id=company.id, group_id=config.group_id, config_id=config.id, version=config.version, environment="homologation", actor_id=None,
                                                actor_name="ERP AI · Reversão", before_snapshot=config_snapshot(config), after_snapshot={"action": "rollback", "operation_id": row.id, "reason": payload.reason}))
        else:
            config = db.get(HRModuleConfiguration, item["id"])
            db.add(HRModuleConfigurationHistory(company_id=company.id, module="config_assignments", environment="homologation", version=config.version, actor_id=None,
                                                actor_name="ERP AI · Reversão", snapshot={"operation_id": row.id, "action": "rollback", "reason": payload.reason, "restored": restored["assignments"]["homologation"]}))
    row.status = "reverted"
    row.report = {**row.report, "rollback": {"passed": True, "reason": payload.reason, "restored_snapshot": restored}}
    db.add(Activity(company_id=company.id, actor=f"ERP AI · {row.approved_by}", message=f"Operação {row.id} (contrato v2) revertida em homologação: {payload.reason}"))
    db.commit(); db.refresh(row)
    return operation_out(row)

# ---------------------------------------------------------------- generic modules

def flat(row, company, module, environment):
    values = normalize(module, row.values) if row else dict(DEFAULTS[module])
    return {"id": row.id if row else None, "scope_key": f"company:{company.id}:default", "company_id": company.id, "group_id": None,
            "version": row.version if row else 0, "effective_from": row.effective_from.isoformat() if row else "0001-01-01", "environment": environment, "active": True, **values}

def module_snapshot(db, module, company, as_of):
    schedules, effective = {}, {}
    for environment in ("homologation", "production"):
        rows = db.scalars(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id == company.id, HRModuleConfiguration.module == module,
                                                              HRModuleConfiguration.environment == environment, HRModuleConfiguration.revoked.is_(False)).order_by(HRModuleConfiguration.version)).all()
        schedules[environment] = [flat(None, company, module, environment), *[flat(r, company, module, environment) for r in rows]]
        current = max((r for r in rows if r.effective_from <= as_of), key=lambda r: (r.effective_from, r.version), default=None)
        effective[environment] = [{"group_id": None, "group_name": "Regra geral", "configuration": flat(current, company, module, environment),
                                   "origin": {"scope": "company" if current else "default", "version": current.version if current else 0}}]
    data = {"contract": CONTRACT, "company": {"id": company.id, "code": company.code, "name": company.trade_name}, "module": module, "as_of": as_of.isoformat(),
            "consulted_at": datetime.now(timezone.utc).isoformat(), "catalog_version": f"{module}-2", "contract_hash": module_catalog(module)["contract_hash"], "erp_version": ERP_VERSION,
            "groups": [], "schedules": schedules, "effective": effective, "assignments": None}
    data["fingerprint"] = snapshot_hash(data)
    return data

@router.get("/modules/{module}/snapshot")
def snapshot_for_module(module: str, company_code: str = Query(min_length=1, max_length=20), as_of: date = Query(default_factory=date.today), db: Session = Depends(get_db)):
    check_module(module)
    return module_snapshot(db, module, allowed_company(db, company_code), as_of)

def module_values(db, module, company, item, effective_from=None):
    values = values_from(module, item, company, effective_from)
    if errors := cross_errors(db, company.id, module, values):
        raise HTTPException(422, errors)
    return values

@router.post("/modules/{module}/validate")
def validate_module(module: str, payload: dict, db: Session = Depends(get_db)):
    check_module(module)
    company = db.get(Company, payload.get("company_id"))
    if not company:
        raise HTTPException(404, "Empresa não encontrada")
    try:
        module_values(db, module, company, payload)
    except HTTPException as exc:
        if exc.status_code != 422:
            raise
        return {"valid": False, "errors": exc.detail if isinstance(exc.detail, list) else [exc.detail], "persisted": False}
    return {"valid": True, "errors": [], "persisted": False}

class ModuleApplyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    approved_by: str = Field(min_length=1, max_length=100)
    company_code: str = Field(min_length=1, max_length=20)
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    effective_from: date
    configurations: list[dict] = Field(max_length=1)

@router.post("/modules/{module}/apply", dependencies=[Depends(execution_auth)])
def apply_module(module: str, payload: ModuleApplyInput, db: Session = Depends(get_db)):
    check_module(module)
    company = allowed_company(db, payload.company_code)
    db.scalar(select(Company).where(Company.id == company.id).with_for_update())
    packet = {**payload.model_dump(mode="json"), "module": module, "contract": CONTRACT}
    request_hash = digest(packet)
    existing = db.get(IntegrationOperation, str(payload.operation_id))
    if existing:
        if existing.company_id != company.id or existing.request_hash != request_hash:
            raise HTTPException(409, "Identificador utilizado por outro plano")
        return operation_out(existing)
    if plan_hash(payload.expected_fingerprint, payload.configurations, None) != payload.approval_hash:
        raise HTTPException(409, "Plano diferente da aprovação")
    before = module_snapshot(db, module, company, payload.effective_from)
    if before["fingerprint"] != payload.expected_fingerprint:
        raise HTTPException(409, "O HCM mudou; faça nova análise")
    values_list = [module_values(db, module, company, item, payload.effective_from) for item in payload.configurations]
    row = IntegrationOperation(id=str(payload.operation_id), company_id=company.id, request_hash=request_hash, status="applying", approved_by=payload.approved_by,
                               payload=packet, before_snapshot=before, after_snapshot={}, report={}, created_ids=[])
    db.add(row); db.flush()
    actor = "ERP AI · " + payload.approved_by
    try:
        with db.begin_nested():
            created = []
            for values in values_list:
                previous = db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id == company.id, HRModuleConfiguration.module == module,
                                                                         HRModuleConfiguration.environment == "homologation").order_by(HRModuleConfiguration.version.desc()).limit(1))
                config = HRModuleConfiguration(company_id=company.id, module=module, environment="homologation", version=previous.version + 1 if previous else 1,
                                               effective_from=payload.effective_from, values=values, created_by=None)
                db.add(config); db.flush(); created.append({"kind": "module", "id": config.id})
                db.add(HRModuleConfigurationHistory(company_id=company.id, module=module, environment="homologation", version=config.version, actor_id=None, actor_name=actor,
                                                    snapshot={"effective_from": payload.effective_from.isoformat(), "values": config.values, "operation_id": row.id}))
            after = module_snapshot(db, module, company, payload.effective_from)
            report = module_report(module, before, after, payload.configurations)
            if not report["passed"]:
                raise ValueError("Verificações automáticas não passaram")
        row.created_ids = created; row.after_snapshot = after; row.report = report; row.status = "applied"
    except ValueError as exc:
        row.status = "failed"; row.after_snapshot = before; row.report = {"passed": False, "error": str(exc), "rolled_back": True, "checks": []}
    db.add(Activity(company_id=company.id, actor=actor, message=f"Configuração de {module} (contrato v2): {row.status}; operação {row.id}."))
    db.commit(); db.refresh(row)
    return operation_out(row)

@router.get("/modules/{module}/operations/{operation_id}")
def module_operation(module: str, operation_id: UUID, db: Session = Depends(get_db)):
    check_module(module)
    return operation_out(get_operation(db, operation_id, module))

@router.get("/modules/{module}/operations/{operation_id}/verify")
def verify_module(module: str, operation_id: UUID, db: Session = Depends(get_db)):
    check_module(module)
    row = get_operation(db, operation_id, module)
    company = db.get(Company, row.company_id)
    current = module_snapshot(db, module, company, date.fromisoformat(row.payload["effective_from"]))
    return {"operation_id": row.id, "status": row.status, "report": module_report(module, row.before_snapshot, current, row.payload["configurations"]), "current_snapshot": current}

@router.post("/modules/{module}/operations/{operation_id}/rollback", dependencies=[Depends(execution_auth)])
def rollback_module(module: str, operation_id: UUID, payload: RollbackInput, db: Session = Depends(get_db)):
    check_module(module)
    row = get_operation(db, operation_id, module)
    company = db.scalar(select(Company).where(Company.id == row.company_id).with_for_update()); db.refresh(row)
    if row.status == "reverted":
        return operation_out(row)
    if row.status != "applied":
        raise HTTPException(409, "Somente uma aplicação concluída pode ser revertida")
    effective_from = date.fromisoformat(row.payload["effective_from"])
    current = module_snapshot(db, module, company, effective_from)
    if current["fingerprint"] != payload.expected_fingerprint or current["fingerprint"] != row.after_snapshot["fingerprint"]:
        raise HTTPException(409, "O HCM mudou depois da aplicação")
    for item in row.created_ids:
        db.get(HRModuleConfiguration, item["id"]).revoked = True
    db.flush()
    restored = module_snapshot(db, module, company, effective_from)
    if restored["fingerprint"] != row.before_snapshot["fingerprint"]:
        db.rollback(); raise HTTPException(409, "A reversão não restaurou o estado anterior")
    for item in row.created_ids:
        config = db.get(HRModuleConfiguration, item["id"])
        db.add(HRModuleConfigurationHistory(company_id=company.id, module=module, environment="homologation", version=config.version, actor_id=None, actor_name="ERP AI · Reversão",
                                            snapshot={"operation_id": row.id, "action": "rollback", "reason": payload.reason, "restored": restored["effective"]}))
    row.status = "reverted"; row.report = {**row.report, "rollback": {"passed": True, "reason": payload.reason, "restored_snapshot": restored}}
    db.add(Activity(company_id=company.id, actor="ERP AI · " + row.approved_by, message=f"Reversão de {module} (contrato v2): operação {row.id}."))
    db.commit(); db.refresh(row)
    return operation_out(row)
