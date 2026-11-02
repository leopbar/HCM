"""Versioned company parameters (homologation → production), organised as
"Configurações por Empresa" and "Configurações Gerais (Processos Parametrizáveis)" tabs."""
import re
from datetime import date
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import HRModuleConfiguration

ALL_STEPS = ("direct_manager", "hr", "system_admin", "target_hr")
TARGETS = ("self", "direct", "indirect", "visibility")
JOB_TYPES = ("Mensal", "Quinzenal", "Semanal", "Diário", "Horário", "Tarefa", "Outros")
MONTHLY_TREATMENTS = ("none", "block", "delete_monthly")
LEGACY_MODULES = ("admission", "timekeeping", "payroll", "benefits")  # exact key sets shared with the ERP-AI integration
GENERAL_CONFIG_KEYS = ("calculation_group", "job_types", "sheet_normal", "sheet_cash_bonus", "sheet_13th", "sheet_cash_bonus_13th",
                       "sheet_occurrence_1", "sheet_occurrence_2", "sheet_occurrence_3", "monthly_values_treatment",
                       "simulation_sheet_1", "simulation_sheet_2", "simulation_sheet_3")

def _general_config(group: str) -> dict:
    return {"calculation_group": group, "job_types": [], "sheet_normal": "Folha de férias", "sheet_cash_bonus": "Folha de férias", "sheet_13th": "Folha de férias",
            "sheet_cash_bonus_13th": "Folha de férias", "sheet_occurrence_1": "Folha de férias", "sheet_occurrence_2": "Folha complementar",
            "sheet_occurrence_3": "Folha complementar", "monthly_values_treatment": "none", "simulation_sheet_1": "", "simulation_sheet_2": "", "simulation_sheet_3": ""}
PROCESS_KEYS = ("vacations", "benefits", "dependents", "personal_data", "leave", "movement", "transfer", "requisition", "termination", "recess", "transport", "value_entry")
FORM_PROCESSES = ("dependents", "personal_data", "leave", "movement", "transfer", "requisition", "termination", "recess", "transport", "value_entry")
FIELD_TYPES = ("text", "number", "date", "boolean", "select")

def _steps(*approvers, hours=16):
    return [{"approver": a, "sla_hours": hours} for a in approvers]

DEFAULTS = {
 # Phase 4 modules (the ERP-AI integration relies on these exact keys; do not add keys here).
 "admission": {"enabled": True, "min_advance_days": 5, "max_advance_days": 90, "allow_weekends": False, "allow_holidays": False, "approval_required": True, "documents_required": True, "allow_retroactive": False},
 "timekeeping": {"daily_hours": 8, "min_break_minutes": 60, "late_tolerance_minutes": 10, "allow_overtime": True, "max_overtime_hours": 2, "manager_approval_required": True, "allow_manual_adjustment": True, "allow_weekends": False},
 "payroll": {"enabled": True, "currency": "BRL", "show_demo_warning": True},
 "benefits": {"enabled": True, "allow_employee_requests": True},
 # Configurações por Empresa
 "contracting": {"probation_days": 45, "probation_extension_days": 45, "create_employee_on_approval": True, "enforce_salary_table": False, "enforce_headcount": False, "position_control": False},
 "vacation_policy": {"cash_bonus_allowed": True, "clt_installment_rules": True, "block_start_before_rest_days": True, "notice_min_days": 30, "require_acknowledgement": True, "approval_min_days_before_start": 0,
                     # Guia Férias (Configurações Gerais): gravação da ocorrência por grupo de cálculo e, opcionalmente, por tipo de cargo
                     "by_job_type": False, "general_configs": [_general_config("Mensalistas"), _general_config("Horistas"), _general_config("Diretores")]},
 # Grupos de cálculo e folhas (vêm da Folha de Pagamento)
 "payroll_setup": {"calculation_groups": ["Mensalistas", "Horistas", "Estagiários", "Diretores"], "sheets": ["Folha mensal", "Folha de férias", "Folha complementar", "Adiantamento de 13º", "Folha de rescisão"]},
 # Atribuições de Configurações: lista ordenada grupo de colaboradores -> configuração nomeada; a primeira que se aplica vence
 "config_assignments": {"vacations": [], "vacations_simulation": []},
 "termination": {"enabled": True, "proportional_notice": True, "base_notice_days": 30, "extra_days_per_year": 3, "max_notice_days": 90, "deactivate_user_account": True, "allow_retroactive": False},
 "transport": {"enabled": True, "allow_employee_requests": True, "employee_discount_percent": 6, "workdays_per_month": 22},
 "work_schedule": {"expected_start_time": "09:00", "expected_end_time": "18:00", "workdays": [0, 1, 2, 3, 4]},
 # Processos parametrizáveis
 "dependents": {"enabled": True, "allow_employee_requests": True, "max_dependents": 10, "require_cpf": True},
 "personal_data": {"enabled": True, "allow_employee_requests": True, "allow_phone": True, "allow_personal_email": True, "allow_marital_status": True, "allow_address": True, "allow_emergency_contact": True},
 "leave": {"enabled": True, "allow_employee_requests": True, "max_retroactive_days": 30, "max_days": 730},
 "movement": {"enabled": True, "allow_salary_change": True, "max_salary_increase_percent": 30, "enforce_salary_table": False, "enforce_headcount": False},
 "transfer": {"enabled": True},
 "requisition": {"enabled": True, "enforce_headcount": True, "create_positions": True},
 "recess": {"enabled": True, "allow_employee_requests": True, "days_per_year": 30, "min_advance_days": 15, "max_installments": 3},
 "value_entry": {"enabled": True, "max_amount": 100000},
 # Recrutamento e seleção
 "recruitment": {"enabled": True, "stages": ["Triagem", "Entrevista RH", "Avaliação técnica", "Entrevista com gestor", "Proposta"],
                 "evaluation_criteria": ["Conhecimento técnico", "Comunicação", "Aderência à cultura"], "allow_internal_applications": True,
                 "internal_min_months": 6, "notify_internal_manager": True, "require_requisition": False, "data_retention_days": 365},
 # Gerenciador de processos: etapas de aprovação, SLA e calendário (Guia SLA)
 "workflow": {
   "sla_enabled": True, "business_start": "08:00", "business_end": "18:00", "business_days": [0, 1, 2, 3, 4],
   "skip_step_when_requester_is_approver": True,
   "processes": {
     "vacations": {"steps": _steps("hr"), "group_overrides": {}},
     "benefits": {"steps": _steps("hr"), "group_overrides": {}},
     "dependents": {"steps": _steps("hr"), "group_overrides": {}},
     "personal_data": {"steps": _steps("hr"), "group_overrides": {}},
     "leave": {"steps": _steps("direct_manager", "hr"), "group_overrides": {}},
     "movement": {"steps": _steps("direct_manager", "hr"), "group_overrides": {}},
     "transfer": {"steps": _steps("hr", "target_hr"), "group_overrides": {}},
     "requisition": {"steps": _steps("hr", "system_admin", hours=24), "group_overrides": {}},
     "termination": {"steps": _steps("hr"), "group_overrides": {}},
     "recess": {"steps": _steps("direct_manager", "hr"), "group_overrides": {}},
     "transport": {"steps": _steps("hr"), "group_overrides": {}},
     "value_entry": {"steps": _steps("hr"), "group_overrides": {}},
   },
 },
 # Informações adicionais: campos extras por processo (Configurações Gerais → Informações Adicionais)
 "process_fields": {process: [] for process in FORM_PROCESSES},
 # Atribuições de Seleção de Colaboradores: para quem cada processo pode ser aberto
 "assignments": {"rules": [
   {"process": "*", "requester_group": None, "targets": ["self", "direct"], "config_source": "target"},
 ]},
}

LABELS = {
 "contracting": "Contratação", "vacation_policy": "Férias / aviso e abono", "termination": "Rescisão / Aviso prévio", "transport": "Vale-transporte",
 "work_schedule": "Jornada e escala", "dependents": "Dependentes", "personal_data": "Alteração de dados pessoais", "leave": "Afastamento / Ausência",
 "movement": "Movimentação", "transfer": "Cessão / Busca de colaborador", "requisition": "Requisição de pessoal", "recess": "Recesso de estágio",
 "value_entry": "Lançamento de valores", "recruitment": "Recrutamento e seleção", "payroll_setup": "Grupos de cálculo e folhas", "config_assignments": "Atribuições de Configurações", "process_fields": "Informações adicionais dos processos", "workflow": "Fluxos de aprovação e SLA", "assignments": "Atribuições de seleção",
 "admission": "Admissão", "timekeeping": "Controle de ponto", "payroll": "Folha demonstrativa", "benefits": "Benefícios",
}

FIELD_LABELS = {
 "enabled": "Processo habilitado", "allow_employee_requests": "Permitir solicitação pelo próprio colaborador", "probation_days": "Contrato de experiência (dias)",
 "probation_extension_days": "Prorrogação da experiência (dias)", "create_employee_on_approval": "Criar funcionário ao aprovar a admissão",
 "enforce_salary_table": "Exigir salário dentro da tabela salarial", "enforce_headcount": "Respeitar o quadro de vagas", "position_control": "Controle por posição",
 "cash_bonus_allowed": "Permitir abono pecuniário (venda de 1/3)", "clt_installment_rules": "Aplicar regras de parcelamento da CLT (art. 134)",
 "block_start_before_rest_days": "Bloquear início nos 2 dias que antecedem feriado ou DSR", "notice_min_days": "Antecedência do aviso de férias (dias)",
 "require_acknowledgement": "Exigir ciência do aviso pelo colaborador", "proportional_notice": "Aviso prévio proporcional (Lei 12.506/2011)",
 "base_notice_days": "Aviso prévio base (dias)", "extra_days_per_year": "Dias adicionais por ano trabalhado", "max_notice_days": "Aviso prévio máximo (dias)",
 "deactivate_user_account": "Desativar o acesso do desligado", "allow_retroactive": "Permitir data retroativa", "employee_discount_percent": "Desconto do colaborador (% do salário)",
 "workdays_per_month": "Dias úteis por mês", "expected_start_time": "Entrada prevista", "expected_end_time": "Saída prevista", "workdays": "Dias de trabalho (0=segunda)",
 "max_dependents": "Máximo de dependentes", "require_cpf": "Exigir CPF do dependente", "allow_phone": "Permitir alterar telefone", "allow_personal_email": "Permitir alterar e-mail pessoal",
 "allow_marital_status": "Permitir alterar estado civil", "allow_address": "Permitir alterar endereço", "allow_emergency_contact": "Permitir alterar contato de emergência",
 "max_retroactive_days": "Limite retroativo (dias)", "max_days": "Duração máxima (dias)", "allow_salary_change": "Permitir alteração salarial",
 "max_salary_increase_percent": "Aumento máximo por movimentação (%)", "create_positions": "Criar posições vagas ao aprovar", "days_per_year": "Dias por ano",
 "min_advance_days": "Antecedência mínima (dias)", "max_installments": "Máximo de períodos", "max_amount": "Valor máximo por lançamento (R$)",
 "sla_enabled": "Utiliza prazo de atendimento (SLA)", "business_start": "Início do expediente do SLA", "business_end": "Fim do expediente do SLA",
 "business_days": "Dias úteis do SLA", "skip_step_when_requester_is_approver": "Aprovar automaticamente a etapa quando o solicitante é o aprovador",
 "approval_min_days_before_start": "Prazo mínimo para aprovação (dias antes do início; 0 = sem bloqueio)",
 "by_job_type": "Habilitar configurações gerais de férias por tipo de cargo do colaborador", "general_configs": "Configurações gerais de férias",
 "calculation_groups": "Grupos de cálculo", "sheets": "Folhas de pagamento", "vacations": "Férias", "vacations_simulation": "Férias - Simulação",
 "stages": "Etapas do processo seletivo (na ordem)", "evaluation_criteria": "Critérios de avaliação (notas de 1 a 5)",
 "allow_internal_applications": "Permitir candidaturas internas (vagas internas)", "internal_min_months": "Tempo mínimo de casa para candidatura interna (meses)",
 "notify_internal_manager": "Avisar o gestor imediato quando um colaborador se candidatar", "require_requisition": "Exigir requisição aprovada para abrir vaga",
 "data_retention_days": "Retenção dos dados de candidatos (dias, LGPD)",
 "max_advance_days": "Antecedência máxima (dias)", "allow_weekends": "Permitir fins de semana", "allow_holidays": "Permitir feriados",
 "daily_hours": "Jornada diária (horas)", "min_break_minutes": "Intervalo mínimo (minutos)", "late_tolerance_minutes": "Tolerância de atraso (minutos)",
 "allow_overtime": "Permitir horas extras", "max_overtime_hours": "Limite diário de horas extras", "manager_approval_required": "Exigir aprovação",
 "allow_manual_adjustment": "Permitir ajuste manual (com justificativa)", "show_demo_warning": "Exibir aviso de cálculo demonstrativo",
 "approval_required": "Exigir aprovação", "documents_required": "Exigir documentos completos", "currency": "Moeda",
}

BOUNDS = {
 "probation_days": (1, 90), "probation_extension_days": (0, 90), "notice_min_days": (0, 60), "base_notice_days": (0, 90), "extra_days_per_year": (0, 10),
 "max_notice_days": (0, 90), "employee_discount_percent": (0, 6), "workdays_per_month": (1, 31), "max_dependents": (1, 30), "max_retroactive_days": (0, 365),
 "max_days": (1, 1095), "max_salary_increase_percent": (0, 300), "days_per_year": (1, 30), "min_advance_days": (0, 365), "max_installments": (1, 3),
 "max_amount": (1, 10000000), "approval_min_days_before_start": (0, 120), "max_advance_days": (1, 730), "daily_hours": (1, 16), "min_break_minutes": (0, 300), "late_tolerance_minutes": (0, 120), "max_overtime_hours": (0, 8), "internal_min_months": (0, 60), "data_retention_days": (30, 3650),
}

def _hhmm(value) -> bool:
    return isinstance(value, str) and len(value) == 5 and value[2] == ":" and value[:2].isdigit() and value[3:].isdigit() and int(value[:2]) < 24 and int(value[3:]) < 60

def _validate_steps(label: str, steps) -> list[str]:
    if not isinstance(steps, list) or len(steps) > 5:
        return [f"{label}: informe até 5 etapas."]
    errors = []
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or not {"approver", "sla_hours"} <= set(step) <= {"approver", "sla_hours", "finish"} or step["approver"] not in ALL_STEPS:
            errors.append(f"{label}: etapa {index} deve ter aprovador válido ({', '.join(ALL_STEPS)}) e prazo.")
        elif type(step["sla_hours"]) is not int or not 1 <= step["sla_hours"] <= 720:
            errors.append(f"{label}: o prazo da etapa {index} deve ficar entre 1 e 720 horas úteis.")
        elif "finish" in step and type(step["finish"]) is not bool:
            errors.append(f"{label}: o redirecionamento da etapa {index} deve ser sim ou não.")
    return errors

def normalize(module: str, values: dict) -> dict:
    """Parameters added after a module was created take their default until saved again."""
    if module in LEGACY_MODULES or not isinstance(values, dict):
        return values
    return {**{k: v for k, v in DEFAULTS[module].items() if k not in values}, **values}

def validate_config(module: str, values: dict) -> list[str]:
    values = normalize(module, values)
    defaults = DEFAULTS[module]
    if not isinstance(values, dict) or set(values) != set(defaults):
        return ["Informe todos os parâmetros do módulo usando os identificadores apresentados pela API."]
    errors = []
    for key, default in defaults.items():
        if type(values[key]) != type(default):
            errors.append(f"O parâmetro {key} deve ser {type(default).__name__}.")
    if errors:
        return errors
    for key, (low, high) in BOUNDS.items():
        if key in values and not low <= values[key] <= high:
            errors.append(f"{FIELD_LABELS.get(key, key)} deve ficar entre {low} e {high}.")
    if module == "admission" and (values["min_advance_days"] < 0 or values["max_advance_days"] < 1 or values["min_advance_days"] > values["max_advance_days"]):
        errors.append("A antecedência mínima deve ser não negativa e não pode superar a máxima.")
    if module == "timekeeping":
        if not 1 <= values["daily_hours"] <= 16: errors.append("A jornada diária deve ficar entre 1 e 16 horas.")
        if not 0 <= values["min_break_minutes"] <= 300: errors.append("O intervalo mínimo deve ficar entre 0 e 300 minutos.")
        if not 0 <= values["late_tolerance_minutes"] <= 120: errors.append("A tolerância deve ficar entre 0 e 120 minutos.")
        if not 0 <= values["max_overtime_hours"] <= 8: errors.append("O limite de horas extras deve ficar entre 0 e 8 horas.")
    if module == "termination" and values["base_notice_days"] > values["max_notice_days"]:
        errors.append("O aviso prévio base não pode superar o máximo.")
    if module == "work_schedule":
        if not _hhmm(values["expected_start_time"]) or not _hhmm(values["expected_end_time"]) or values["expected_start_time"] >= values["expected_end_time"]:
            errors.append("Informe entrada e saída no formato HH:MM, com a entrada antes da saída.")
        if not values["workdays"] or any(type(d) is not int or not 0 <= d <= 6 for d in values["workdays"]):
            errors.append("Dias de trabalho devem ser números de 0 (segunda) a 6 (domingo).")
    if module == "recruitment":
        for key, low, high in (("stages", 2, 8), ("evaluation_criteria", 1, 8)):
            items = values[key]
            if not low <= len(items) <= high or any(not isinstance(x, str) or not 2 <= len(x.strip()) <= 40 for x in items) or len({x.strip().lower() for x in items}) != len(items):
                errors.append(f"{FIELD_LABELS[key]}: informe de {low} a {high} itens distintos, com 2 a 40 caracteres.")
    if module == "payroll_setup":
        for key in ("calculation_groups", "sheets"):
            items = values[key]
            if not 1 <= len(items) <= 20 or any(not isinstance(x, str) or not 2 <= len(x.strip()) <= 60 for x in items) or len({x.strip().lower() for x in items}) != len(items):
                errors.append(f"{FIELD_LABELS[key]}: informe de 1 a 20 nomes distintos.")
    if module == "vacation_policy":
        rows = values["general_configs"]
        if not isinstance(rows, list) or len(rows) > 30:
            errors.append("Informe até 30 configurações gerais de férias.")
        else:
            seen = []
            for index, row in enumerate(rows, 1):
                if not isinstance(row, dict) or set(row) != set(GENERAL_CONFIG_KEYS):
                    errors.append(f"Configuração geral {index}: campos incompletos."); continue
                if not isinstance(row["calculation_group"], str) or not row["calculation_group"].strip():
                    errors.append(f"Configuração geral {index}: informe o grupo de cálculo.")
                types = row["job_types"]
                if not isinstance(types, list) or not set(types) <= set(JOB_TYPES):
                    errors.append(f"Configuração geral {index}: tipos de cargo inválidos.")
                elif values["by_job_type"] and not types:
                    errors.append(f"Configuração geral {index}: com a configuração por tipo de cargo habilitada, selecione ao menos um tipo de cargo.")
                elif not values["by_job_type"] and types:
                    errors.append(f"Configuração geral {index}: habilite a configuração por tipo de cargo para informar tipos de cargo.")
                for key in GENERAL_CONFIG_KEYS[2:9]:
                    if not isinstance(row[key], str) or not row[key].strip():
                        errors.append(f"Configuração geral {index}: informe a folha de {FIELD_LABELS.get(key, key)}."); break
                if row["monthly_values_treatment"] not in MONTHLY_TREATMENTS:
                    errors.append(f"Configuração geral {index}: tratamento para valores mensais inválido.")
                for key in ("simulation_sheet_1", "simulation_sheet_2", "simulation_sheet_3"):
                    if not isinstance(row[key], str):
                        errors.append(f"Configuração geral {index}: folha de simulação inválida."); break
                group = str(row["calculation_group"]).strip().lower()
                for other_group, other_types in seen:
                    if other_group == group and (not values["by_job_type"] or set(other_types) & set(types or [])):
                        errors.append(f"Configuração geral {index}: o grupo de cálculo {row['calculation_group']} já está configurado" + (" para estes tipos de cargo." if values["by_job_type"] else "; habilite a configuração por tipo de cargo para diferenciá-los."))
                seen.append((group, types if isinstance(types, list) else []))
    if module == "process_fields":
        if set(values) != set(FORM_PROCESSES):
            return ["Informe os campos adicionais de todos os processos (lista vazia quando não houver)."]
        for process, fields in values.items():
            if not isinstance(fields, list) or len(fields) > 10:
                errors.append(f"{process}: informe até 10 campos adicionais."); continue
            names = set()
            for index, field in enumerate(fields, 1):
                label = f"{process}, campo {index}"
                if not isinstance(field, dict) or set(field) != {"name", "label", "type", "required", "options", "help"}:
                    errors.append(f"{label}: informe nome, rótulo, tipo, obrigatoriedade, opções e ajuda."); continue
                if not isinstance(field["name"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", field["name"]):
                    errors.append(f"{label}: o nome técnico usa letras minúsculas, números e _ (2 a 31 caracteres).")
                elif field["name"] in names:
                    errors.append(f"{label}: nome técnico repetido.")
                names.add(field.get("name"))
                if not isinstance(field["label"], str) or not 2 <= len(field["label"].strip()) <= 60: errors.append(f"{label}: rótulo com 2 a 60 caracteres.")
                if field["type"] not in FIELD_TYPES: errors.append(f"{label}: tipo deve ser um de {', '.join(FIELD_TYPES)}.")
                if type(field["required"]) is not bool: errors.append(f"{label}: obrigatório deve ser sim ou não.")
                if not isinstance(field["options"], list) or any(not isinstance(o, str) or not o.strip() for o in field["options"]) or len(field["options"]) > 20:
                    errors.append(f"{label}: opções devem ser textos (até 20).")
                elif field["type"] == "select" and not field["options"]: errors.append(f"{label}: campo de seleção precisa de opções.")
                elif field["type"] != "select" and field["options"]: errors.append(f"{label}: só campos de seleção têm opções.")
                if not isinstance(field["help"], str) or len(field["help"]) > 200: errors.append(f"{label}: ajuda com até 200 caracteres.")
    if module == "config_assignments":
        for process in ("vacations", "vacations_simulation"):
            items = values[process]
            if not isinstance(items, list) or len(items) > 100:
                errors.append(f"{FIELD_LABELS[process]}: lista inválida."); continue
            for index, item in enumerate(items, 1):
                if not isinstance(item, dict) or set(item) != {"group", "config"} or not all(isinstance(item[k], str) and item[k].strip() for k in ("group", "config")):
                    errors.append(f"{FIELD_LABELS[process]}, linha {index}: informe grupo e configuração.")
            pairs = [(i.get("group"), i.get("config")) for i in items if isinstance(i, dict)]
            if len(pairs) != len(set(pairs)):
                errors.append(f"{FIELD_LABELS[process]}: há atribuições repetidas.")
    if module == "workflow":
        if not _hhmm(values["business_start"]) or not _hhmm(values["business_end"]) or values["business_start"] >= values["business_end"]:
            errors.append("Informe o expediente do SLA no formato HH:MM.")
        if not values["business_days"] or any(type(d) is not int or not 0 <= d <= 6 for d in values["business_days"]):
            errors.append("Dias úteis do SLA devem ser números de 0 a 6.")
        if set(values["processes"]) != set(PROCESS_KEYS):
            errors.append("Configure as etapas de todos os processos.")
        else:
            for process, cfg in values["processes"].items():
                if not isinstance(cfg, dict) or set(cfg) != {"steps", "group_overrides"} or not isinstance(cfg["group_overrides"], dict):
                    errors.append(f"{process}: informe steps e group_overrides."); continue
                errors += _validate_steps(process, cfg["steps"])
                for group_name, override in cfg["group_overrides"].items():
                    errors += _validate_steps(f"{process}/{group_name}", override)
    if module == "assignments":
        rules = values["rules"]
        if not isinstance(rules, list) or not rules or len(rules) > 100:
            errors.append("Informe ao menos uma atribuição de seleção.")
        else:
            for index, rule in enumerate(rules, 1):
                if not isinstance(rule, dict) or set(rule) != {"process", "requester_group", "targets", "config_source"}:
                    errors.append(f"Atribuição {index}: campos process, requester_group, targets e config_source são obrigatórios."); continue
                if rule["process"] != "*" and rule["process"] not in PROCESS_KEYS: errors.append(f"Atribuição {index}: processo desconhecido.")
                if rule["requester_group"] is not None and not isinstance(rule["requester_group"], str): errors.append(f"Atribuição {index}: grupo do solicitante inválido.")
                if not isinstance(rule["targets"], list) or not rule["targets"] or not set(rule["targets"]) <= set(TARGETS): errors.append(f"Atribuição {index}: alvos devem estar entre {', '.join(TARGETS)}.")
                if rule["config_source"] not in ("requester", "target"): errors.append(f"Atribuição {index}: obtenção da configuração deve ser requester ou target.")
    return errors

def active_config(db: Session, company_id: int, module: str, environment: str = "production") -> dict:
    row = db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id == company_id, HRModuleConfiguration.module == module, HRModuleConfiguration.environment == environment, HRModuleConfiguration.revoked.is_(False), HRModuleConfiguration.effective_from <= date.today()).order_by(HRModuleConfiguration.effective_from.desc(), HRModuleConfiguration.version.desc()).limit(1))
    return normalize(module, row.values) if row else DEFAULTS[module]

HHMM_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
_STEP_SCHEMA = {"type": "object", "properties": {"approver": {"type": "string", "enum": list(ALL_STEPS)}, "sla_hours": {"type": "integer", "minimum": 1, "maximum": 720}, "finish": {"type": "boolean"}},
                "required": ["approver", "sla_hours"], "additionalProperties": False}
_STEPS_SCHEMA = {"type": "array", "maxItems": 5, "items": _STEP_SCHEMA}
_GENERAL_CONFIG_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(GENERAL_CONFIG_KEYS), "properties": {
    "calculation_group": {"type": "string", "minLength": 1, "maxLength": 60}, "job_types": {"type": "array", "items": {"type": "string", "enum": list(JOB_TYPES)}, "uniqueItems": True},
    **{key: {"type": "string", "minLength": 1} for key in GENERAL_CONFIG_KEYS[2:9]}, "monthly_values_treatment": {"type": "string", "enum": list(MONTHLY_TREATMENTS)},
    **{key: {"type": "string"} for key in ("simulation_sheet_1", "simulation_sheet_2", "simulation_sheet_3")}}}
_PROCESS_FIELD_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["name", "label", "type", "required", "options", "help"], "properties": {
    "name": {"type": "string", "pattern": r"^[a-z][a-z0-9_]{1,30}$"}, "label": {"type": "string", "minLength": 2, "maxLength": 60}, "type": {"type": "string", "enum": list(FIELD_TYPES)},
    "required": {"type": "boolean"}, "options": {"type": "array", "maxItems": 20, "items": {"type": "string", "minLength": 1}}, "help": {"type": "string", "maxLength": 200}}}
_ASSIGNMENT_ITEM_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["group", "config"], "properties": {"group": {"type": "string", "minLength": 1}, "config": {"type": "string", "minLength": 1}}}
SPECIAL_SCHEMAS = {
    ("work_schedule", "expected_start_time"): {"type": "string", "pattern": HHMM_PATTERN}, ("work_schedule", "expected_end_time"): {"type": "string", "pattern": HHMM_PATTERN},
    ("work_schedule", "workdays"): {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 0, "maximum": 6}},
    ("workflow", "business_start"): {"type": "string", "pattern": HHMM_PATTERN}, ("workflow", "business_end"): {"type": "string", "pattern": HHMM_PATTERN},
    ("workflow", "business_days"): {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "integer", "minimum": 0, "maximum": 6}},
    ("workflow", "processes"): {"type": "object", "additionalProperties": False, "required": list(PROCESS_KEYS), "properties": {
        process: {"type": "object", "additionalProperties": False, "required": ["steps", "group_overrides"], "properties": {"steps": _STEPS_SCHEMA, "group_overrides": {"type": "object", "additionalProperties": _STEPS_SCHEMA}}} for process in PROCESS_KEYS}},
    ("vacation_policy", "general_configs"): {"type": "array", "maxItems": 30, "items": _GENERAL_CONFIG_SCHEMA},
    ("payroll_setup", "calculation_groups"): {"type": "array", "minItems": 1, "maxItems": 20, "uniqueItems": True, "items": {"type": "string", "minLength": 2, "maxLength": 60}},
    ("payroll_setup", "sheets"): {"type": "array", "minItems": 1, "maxItems": 20, "uniqueItems": True, "items": {"type": "string", "minLength": 2, "maxLength": 60}},
    ("config_assignments", "vacations"): {"type": "array", "maxItems": 100, "items": _ASSIGNMENT_ITEM_SCHEMA},
    ("config_assignments", "vacations_simulation"): {"type": "array", "maxItems": 100, "items": _ASSIGNMENT_ITEM_SCHEMA},
    ("recruitment", "stages"): {"type": "array", "minItems": 2, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "minLength": 2, "maxLength": 40}},
    ("recruitment", "evaluation_criteria"): {"type": "array", "minItems": 1, "maxItems": 8, "uniqueItems": True, "items": {"type": "string", "minLength": 2, "maxLength": 40}},
    ("process_fields", None): {"type": "array", "maxItems": 10, "items": _PROCESS_FIELD_SCHEMA},
    ("assignments", "rules"): {"type": "array", "minItems": 1, "maxItems": 100, "items": {"type": "object", "additionalProperties": False, "required": ["process", "requester_group", "targets", "config_source"], "properties": {
        "process": {"type": "string", "enum": ["*", *PROCESS_KEYS]}, "requester_group": {"type": ["string", "null"]}, "targets": {"type": "array", "minItems": 1, "uniqueItems": True, "items": {"type": "string", "enum": list(TARGETS)}},
        "config_source": {"type": "string", "enum": ["requester", "target"]}}}},
    ("payroll", "currency"): {"type": "string", "minLength": 3, "maxLength": 3},
}

def field_schema(module: str, key: str) -> dict:
    """JSON Schema do valor de um parâmetro, derivado da estrutura de DEFAULTS e das regras de validate_config.
    O ERP-AI monta a saída estruturada do modelo e a validação determinística a partir daqui."""
    if (module, key) in SPECIAL_SCHEMAS:
        return SPECIAL_SCHEMAS[(module, key)]
    if module == "process_fields":
        return SPECIAL_SCHEMAS[("process_fields", None)]
    value = DEFAULTS[module][key]
    if type(value) is bool:
        return {"type": "boolean"}
    if type(value) is int:
        schema = {"type": "integer"}
        if key in BOUNDS:
            schema.update(minimum=BOUNDS[key][0], maximum=BOUNDS[key][1])
        return schema
    if isinstance(value, str):
        return {"type": "string"}
    if isinstance(value, list):
        return {"type": "array", "items": {"type": "string"}} if all(isinstance(v, str) for v in value) else {"type": "array"}
    return {"type": "object"}

def field_catalog(module: str) -> list[dict]:
    """Metadata so the UI can render a generic editor for every module."""
    fields = []
    for key, value in DEFAULTS[module].items():
        kind = "boolean" if type(value) is bool else "number" if type(value) is int else "text" if type(value) is str else "days" if isinstance(value, list) and value and all(type(v) is int for v in value) else "tags" if isinstance(value, list) and value and all(isinstance(v, str) for v in value) else "json"
        field = {"key": key, "label": FIELD_LABELS.get(key, key), "type": kind}
        if key in BOUNDS: field.update(min=BOUNDS[key][0], max=BOUNDS[key][1])
        fields.append(field)
    return fields
