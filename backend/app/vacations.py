from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Company, Employee, EmployeeGroup, Position, VacationConfiguration, VacationConfigurationHistory, VacationRequest, ValueEntry
from app.groups import employee_groups
from app.schemas import VacationConfigurationInput, VacationSimulationInput

CONFIG_FIELDS = (
    "process_enabled", "employee_request_enabled", "manager_request_enabled",
    "manager_approval_required", "simulation_enabled", "installments_allowed",
    "max_installments", "min_advance_days", "allow_13th_advance",
    "show_balance_to_employee",
)

def config_snapshot(config: VacationConfiguration) -> dict:
    return {
        "id": config.id, "scope_key": config.scope_key, "company_id": config.company_id,
        "environment": config.environment,
        "group_id": config.group_id, "version": config.version,
        "effective_from": config.effective_from.isoformat(), "active": config.active,
        "config_code": config.config_code, "name": config.name, "simulation_text": config.simulation_text,
        **{field: getattr(config, field) for field in CONFIG_FIELDS},
    }

def config_errors(values: dict) -> list[str]:
    errors=[]
    if not values["process_enabled"] and (values["employee_request_enabled"] or values["manager_request_enabled"]):
        errors.append("Desative as solicitações pelo funcionário e pelo gestor quando o processo estiver desabilitado.")
    if not values["installments_allowed"] and values["max_installments"] != 1:
        errors.append("Com o parcelamento desabilitado, o máximo de parcelas deve ser 1.")
    if values["installments_allowed"] and values["max_installments"] < 2:
        errors.append("Quando o parcelamento estiver habilitado, defina o máximo entre 2 e 3 parcelas.")
    if not 0 <= values["min_advance_days"] <= 365:
        errors.append("A antecedência mínima deve estar entre 0 e 365 dias.")
    return errors

def latest_scope_config(db: Session, scope_key: str, as_of: date, environment: str="production") -> VacationConfiguration | None:
    return db.scalar(
        select(VacationConfiguration)
        .where(VacationConfiguration.scope_key == scope_key, VacationConfiguration.environment == environment, VacationConfiguration.effective_from <= as_of, VacationConfiguration.revoked.is_(False))
        .order_by(VacationConfiguration.effective_from.desc(), VacationConfiguration.version.desc())
        .limit(1)
    )

def resolve_effective_config(db: Session, employee: Employee, as_of: date | None = None, environment: str="production", simulation: bool=False):
    """1) Atribuições de Configurações, na ordem parametrizada (a primeira atribuição cujo grupo inclui o colaborador vence);
    2) regra própria de um dos grupos do colaborador; 3) regra da empresa; 4) padrão do sistema."""
    from app.policies import active_config
    today=as_of or date.today()
    groups=employee_groups(db, employee)
    names={g.name.lower(): g for g in groups}
    assignments=active_config(db, employee.company_id, "config_assignments", environment)
    ordered=assignments["vacations_simulation"] if simulation and assignments["vacations_simulation"] else assignments["vacations"]
    process_label="Férias - Simulação" if ordered is assignments["vacations_simulation"] and simulation else "Férias"
    for index, item in enumerate(ordered, 1):
        group=names.get(str(item["group"]).lower())
        if not group:
            continue
        config=latest_scope_config(db, f"company:{employee.company_id}:config:{item['config']}", today, environment)
        if config:
            origin={"scope":"assignment", "label":f"Configuração {config.config_code} - {config.name or ''}".strip(" -"), "scope_key":config.scope_key, "version":config.version,
                    "group":group.name, "priority":index, "process":process_label}
            why=(f"O colaborador participa do grupo {group.name}, atribuído à configuração {config.config_code} na posição {index} das Atribuições de Configurações ({process_label}). "
                 "A ordenação define a prioridade quando o colaborador faz parte de mais de um grupo.")
            return config, origin, why
    system=latest_scope_config(db, "system", today, environment)
    company=latest_scope_config(db, f"company:{employee.company_id}:default", today, environment)
    group=None; group_record=None
    for candidate in groups:
        group=latest_scope_config(db, f"company:{employee.company_id}:group:{candidate.id}", today, environment)
        if group:
            group_record=candidate; break
    selected=group or company or system
    company_label=f"Empresa {employee.company.trade_name}" if employee.company else "Empresa"
    if group:
        origin={"scope":"group", "label":f"Grupo {group_record.name if group_record else employee.group_id}", "scope_key":group.scope_key, "version":group.version}
        why=f"Para este funcionário, a regra efetiva vem do grupo {group_record.name if group_record else employee.group_id}, que prevalece sobre a configuração geral da empresa."
    elif company:
        origin={"scope":"company", "label":company_label, "scope_key":company.scope_key, "version":company.version}
        why=f"A configuração da empresa {employee.company.trade_name if employee.company else employee.company_id} prevalece sobre o padrão do sistema."
    elif system:
        origin={"scope":"system", "label":"Padrão do sistema", "scope_key":system.scope_key, "version":system.version}
        why="Nenhuma regra vigente foi encontrada para o grupo ou para a empresa; foi aplicado o padrão do sistema."
    else:
        return None, None, "Não existe configuração de férias vigente."
    return selected, origin, why

HOLIDAYS = {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)}
ENTITLEMENT_DAYS = 30
OPEN_PERIODS = 2  # CLT art. 134/137: after the concession period the férias are paid in double; the demo keeps the two latest periods open

def _add_years(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29/02
        return day.replace(year=day.year + years, day=28)

def acquisition_periods(db: Session, employee: Employee, today: date | None = None, exclude_request_id: int | None = None) -> list[dict]:
    """Períodos aquisitivos completos (12 meses de contrato) ainda abertos, com dias usados e parcelas."""
    today = today or date.today()
    starts = []
    k = 0
    while _add_years(employee.admission_date, k + 1) <= today + timedelta(days=0) and k < 80:
        starts.append(_add_years(employee.admission_date, k))
        k += 1
    starts = starts[-OPEN_PERIODS:]
    periods = [{"start": s, "end": _add_years(s, 1) - timedelta(days=1), "concession_end": _add_years(s, 2) - timedelta(days=1), "entitlement": ENTITLEMENT_DAYS, "used": 0, "cash_bonus": 0, "parcels": []} for s in starts]
    requests = db.scalars(select(VacationRequest).where(VacationRequest.employee_id == employee.id, VacationRequest.status.in_(("pending_approval", "approved", "approved_auto"))).order_by(VacationRequest.id)).all()
    for row in requests:
        if row.id == exclude_request_id:
            continue
        target = next((p for p in periods if p["start"] == row.acquisition_start), None)
        if target is None:  # legacy requests: consume the oldest open period
            target = next((p for p in periods if p["used"] + p["cash_bonus"] < p["entitlement"]), None)
        if target is None:
            continue
        target["used"] += row.requested_days
        target["cash_bonus"] += row.cash_bonus_days or 0
        target["parcels"].append(row.requested_days)
    for p in periods:
        p["remaining"] = max(0, p["entitlement"] - p["used"] - p["cash_bonus"])
    return periods

def current_period(db: Session, employee: Employee) -> dict | None:
    return next((p for p in acquisition_periods(db, employee) if p["remaining"] > 0), None)

def remaining_vacation_balance(db: Session, employee: Employee) -> int:
    """Saldo do período aquisitivo mais antigo ainda com dias a gozar."""
    period = current_period(db, employee)
    return period["remaining"] if period else 0

def clt_errors(period: dict | None, requested_days: int, cash_bonus_days: int, policy: dict, max_installments: int) -> list[str]:
    errors = []
    if period is None:
        return ["O colaborador ainda não completou um período aquisitivo de 12 meses (art. 130 da CLT)."]
    if cash_bonus_days:
        if not policy["cash_bonus_allowed"]:
            errors.append("O abono pecuniário não está habilitado para esta empresa.")
        if cash_bonus_days > period["entitlement"] // 3:
            errors.append(f"O abono pecuniário é limitado a 1/3 do período ({period['entitlement'] // 3} dias, art. 143 da CLT).")
        if period["cash_bonus"]:
            errors.append("O abono pecuniário já foi solicitado neste período aquisitivo.")
    if requested_days + cash_bonus_days > period["remaining"]:
        errors.append(f"O pedido excede o saldo de {period['remaining']} dias do período aquisitivo {period['start'].strftime('%d/%m/%Y')} a {period['end'].strftime('%d/%m/%Y')}.")
    if policy["clt_installment_rules"]:
        parcels = period["parcels"]
        if requested_days < 5:
            errors.append("Nenhum período de férias pode ser inferior a 5 dias corridos (art. 134, §1º, da CLT).")
        if len(parcels) + 1 > min(3, max_installments):
            errors.append(f"O período aquisitivo já possui {len(parcels)} parcela(s); o limite é {min(3, max_installments)}.")
        remaining_after = period["remaining"] - requested_days - cash_bonus_days
        has_long = any(x >= 14 for x in parcels) or requested_days >= 14
        if not has_long and remaining_after < 14:
            errors.append("Um dos períodos deve ter no mínimo 14 dias corridos (art. 134, §1º, da CLT); ajuste a quantidade de dias.")
        if remaining_after and 0 < remaining_after < 5 and len(parcels) + 1 >= min(3, max_installments):
            errors.append("O saldo restante ficaria inferior a 5 dias sem possibilidade de nova parcela.")
    return errors

def rest_day_errors(start: date) -> list[str]:
    """Art. 134, §3º: o início não pode ocorrer nos dois dias que antecedem feriado ou o repouso semanal (domingo)."""
    for offset in (1, 2):
        day = start + timedelta(days=offset)
        if day.weekday() == 6 or (day.month, day.day) in HOLIDAYS:
            return [f"As férias não podem começar nos dois dias que antecedem feriado ou o descanso semanal remunerado ({'domingo' if day.weekday() == 6 else 'feriado'} em {day.strftime('%d/%m/%Y')})."]
    return []

def create_configuration_version(db: Session, payload: VacationConfigurationInput, actor_id: int, actor_name: str):
    db.scalar(select(Company).where(Company.id == payload.company_id).with_for_update())
    code=getattr(payload, "config_code", None)
    scope_key=(f"company:{payload.company_id}:config:{code}" if code else f"company:{payload.company_id}:group:{payload.group_id}" if payload.group_id is not None else f"company:{payload.company_id}:default")
    latest=db.scalar(select(VacationConfiguration).where(VacationConfiguration.scope_key==scope_key,VacationConfiguration.environment==payload.environment).order_by(VacationConfiguration.version.desc()).limit(1))
    values=payload.model_dump(exclude={"company_id", "group_id", "effective_from", "environment", "config_code", "name", "simulation_text"})
    errors=config_errors(values)
    if errors:
        return None, errors
    config=VacationConfiguration(
        scope_key=scope_key, company_id=payload.company_id, group_id=payload.group_id,
        environment=payload.environment, version=(latest.version+1 if latest else 1), effective_from=payload.effective_from,
        created_by=actor_id, config_code=code, name=(getattr(payload, "name", None) or None), simulation_text=(getattr(payload, "simulation_text", None) or None), **values,
    )
    db.add(config)
    db.flush()
    db.add(VacationConfigurationHistory(
        company_id=payload.company_id, group_id=payload.group_id, config_id=config.id,
        version=config.version, environment=config.environment, actor_id=actor_id, actor_name=actor_name,
        before_snapshot=config_snapshot(latest) if latest else None,
        after_snapshot=config_snapshot(config),
    ))
    return config, []

def evaluate_vacation(db: Session, payload: VacationSimulationInput, requester_type: str | None = None, simulation: bool = True):
    employee=db.get(Employee, payload.employee_id)
    if not employee:
        return {"allowed":False, "errors":["Funcionário não encontrado."], "effective_configuration":None, "origin":None, "balance_days":None}
    config,origin,why=resolve_effective_config(db, employee, environment=payload.environment, simulation=simulation)
    if not config:
        return {"allowed":False, "errors":[why], "effective_configuration":None, "origin":None, "balance_days":None}
    from app.policies import active_config
    who=requester_type or payload.requester_type
    errors=vacation_policy_errors(config_snapshot(config), who, payload.installment_count, payload.advance_13th_requested, simulation)
    policy=active_config(db, employee.company_id, "vacation_policy", payload.environment)
    period=current_period(db, employee)
    balance=period["remaining"] if period else 0
    cash=getattr(payload, "cash_bonus_days", 0) or 0
    errors.extend(vacation_timing_errors(config_snapshot(config), payload.start_date, payload.requested_days + cash, balance))
    errors=[e for e in errors if not e.startswith("O pedido excede o saldo")]
    errors.extend(clt_errors(period, payload.requested_days, cash, policy, config.max_installments if config.installments_allowed else 1))
    if policy["block_start_before_rest_days"]:
        errors.extend(rest_day_errors(payload.start_date))
    if payload.start_date < date.today() + timedelta(days=policy["notice_min_days"]):
        errors.append(f"O aviso de férias deve ser comunicado com pelo menos {policy['notice_min_days']} dias de antecedência (art. 135 da CLT).")
    if employee.contract_type == "Estágio":
        errors.append("Estagiários não têm férias CLT; use o processo de Recesso de estágio.")
    recording, recording_errors = vacation_recording(db, employee, policy, payload.start_date, cash, payload.advance_13th_requested, simulation)
    errors.extend(recording_errors)
    candidate_end=payload.start_date+timedelta(days=payload.requested_days-1)
    existing=db.scalars(select(VacationRequest).where(
        VacationRequest.employee_id==employee.id,
        VacationRequest.status.in_(("pending_approval","approved","approved_auto")),
    )).all()
    if any(payload.start_date<=row.start_date+timedelta(days=row.requested_days-1) and row.start_date<=candidate_end for row in existing):
        errors.append("O período informado se sobrepõe a outra solicitação pendente ou aprovada.")
    return {
        "allowed":not errors, "errors":errors,
        "effective_configuration":config_snapshot(config), "origin":origin, "why":why,
        "balance_days":balance if config.show_balance_to_employee or who!="employee" else None,
        "acquisition_period":({"start":period["start"].isoformat(),"end":period["end"].isoformat(),"concession_end":period["concession_end"].isoformat(),"remaining":period["remaining"],"parcels":period["parcels"]} if period and (config.show_balance_to_employee or who!="employee") else None),
        "cash_bonus_days":cash,
        "recording":recording,
        "simulation_text":config.simulation_text if simulation else None,
        "employee":{"id":employee.id,"full_name":employee.full_name,"company_id":employee.company_id,"group_id":employee.group_id},
    }


def vacation_policy_errors(values, requester_type, installments, advance_13th=False, simulation=True):
    """Policy used by both operational simulations and integration probes."""
    errors=[]
    if not values["process_enabled"]:
        errors.append("O processo de férias está desabilitado para esta empresa e grupo.")
    if requester_type=="employee" and not values["employee_request_enabled"]:
        errors.append("Esta configuração não permite que o funcionário solicite férias diretamente.")
    if requester_type=="manager" and not values["manager_request_enabled"]:
        errors.append("Esta configuração não permite solicitações de férias pelo gestor.")
    if simulation and not values["simulation_enabled"]:
        errors.append("A simulação de férias está desabilitada para esta empresa e grupo.")
    if not values["installments_allowed"] and installments>1:
        errors.append("O parcelamento de férias está desabilitado para este funcionário.")
    elif values["installments_allowed"] and installments>values["max_installments"]:
        errors.append(f"A configuração vigente permite no máximo {values['max_installments']} parcelas.")
    if advance_13th and not values["allow_13th_advance"]:
        errors.append("O adiantamento do 13º salário não está habilitado para este funcionário.")
    return errors

def vacation_timing_errors(values, start_date, requested_days, balance, today=None):
    today = today or date.today()
    errors = []
    if start_date < today:
        errors.append("A data de início não pode estar no passado.")
    if start_date < today + timedelta(days=values["min_advance_days"]):
        errors.append(f"Esta empresa exige antecedência mínima de {values['min_advance_days']} dias para solicitações de férias.")
    if requested_days > balance:
        errors.append(f"O pedido excede o saldo fictício disponível de {balance} dias.")
    return errors

def next_valid_start(day: date) -> date:
    """First date on or after `day` that is not in the two days before a rest day or holiday."""
    while rest_day_errors(day):
        day += timedelta(days=1)
    return day


MONTHLY_LABELS = {"none": "Não há manipulação referente aos valores mensais", "block": "Não efetivar as férias caso existam valores mensais calculados", "delete_monthly": "Excluir valores das folhas tipo Mensal"}

def vacation_recording(db: Session, employee: Employee, policy: dict, start: date, cash_bonus_days: int, advance_13th: bool, simulation: bool) -> tuple[dict | None, list[str]]:
    """Guia Férias: escolhe a configuração geral pelo grupo de cálculo (e tipo de cargo, se habilitado),
    a folha da ocorrência no mês e aplica o tratamento para valores mensais já calculados."""
    rows = policy.get("general_configs") or []
    if not rows:
        return None, []
    position = db.get(Position, employee.position_id)
    job_type = position.job_type if position else "Mensal"
    group = (employee.calculation_group or "").lower()
    row = next((r for r in rows if r["calculation_group"].lower() == group and (not policy.get("by_job_type") or job_type in r["job_types"])), None)
    if not row:
        detail = f"grupo de cálculo {employee.calculation_group}" + (f" e tipo de cargo {job_type}" if policy.get("by_job_type") else "")
        return None, [f"Não há configuração geral de férias (Guia Férias) para o {detail}."]
    month_start = start.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1)
    occurrence = 1 + sum(1 for r in db.scalars(select(VacationRequest).where(VacationRequest.employee_id == employee.id, VacationRequest.status.in_(("pending_approval", "approved", "approved_auto")),
                                                                         VacationRequest.start_date >= month_start, VacationRequest.start_date < month_end)).all())
    errors = []
    if occurrence > 3:
        errors.append("O mês de início já possui 3 ocorrências de férias gravadas.")
    kind = "sheet_cash_bonus_13th" if cash_bonus_days and advance_13th else "sheet_cash_bonus" if cash_bonus_days else "sheet_13th" if advance_13th else "sheet_normal"
    occurrence_sheet = row.get(f"sheet_occurrence_{min(occurrence, 3)}")
    if simulation and row.get(f"simulation_sheet_{min(occurrence, 3)}"):
        occurrence_sheet = row[f"simulation_sheet_{min(occurrence, 3)}"]
    competence = start.strftime("%Y-%m")
    monthly = db.scalars(select(ValueEntry).where(ValueEntry.employee_id == employee.id, ValueEntry.competence == competence)).all()
    treatment = row["monthly_values_treatment"]
    if monthly and treatment == "block":
        errors.append(f"Existem {len(monthly)} valor(es) mensal(is) calculado(s) na competência {competence}; a configuração do Guia Férias impede efetivar as férias.")
    return {"calculation_group": row["calculation_group"], "job_type": job_type, "kind_sheet": row[kind], "occurrence": occurrence, "occurrence_sheet": occurrence_sheet,
            "competence": competence, "monthly_values_treatment": treatment, "monthly_values_treatment_label": MONTHLY_LABELS[treatment], "monthly_values": len(monthly)}, errors

def apply_monthly_treatment(db: Session, request: VacationRequest) -> str | None:
    """Executed when the férias are effectively approved."""
    recording = (request.rules_snapshot or {}).get("recording") or {}
    if not recording:
        return None
    entries = db.scalars(select(ValueEntry).where(ValueEntry.employee_id == request.employee_id, ValueEntry.competence == request.start_date.strftime("%Y-%m"))).all()
    if recording.get("monthly_values_treatment") == "block" and entries:
        from fastapi import HTTPException
        raise HTTPException(409, "Existem valores mensais calculados na competência das férias; a configuração do Guia Férias impede efetivá-las.")
    if recording.get("monthly_values_treatment") == "delete_monthly" and entries:
        for entry in entries:
            db.delete(entry)
        return f"{len(entries)} valor(es) mensal(is) excluído(s) da competência {request.start_date.strftime('%Y-%m')}."
    return None
