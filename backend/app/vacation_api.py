from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Activity, Company, Employee, EmployeeGroup, ProcessRequest, User, VacationConfiguration, VacationConfigurationHistory, VacationRequest
from app.schemas import VacationConfigurationInput, VacationDecisionInput, VacationRequestInput, VacationSimulationInput
from app.security import current_user, require_roles
from app.vacations import apply_monthly_treatment, config_snapshot, create_configuration_version, current_period, evaluate_vacation, remaining_vacation_balance, resolve_effective_config
from app import processes
from app.policies import active_config

router=APIRouter(prefix="/api/vacations",tags=["Férias"])

def ensure_company_access(user: User, company_id: int):
    if user.role not in ("system_admin","consultant") and user.company_id!=company_id:
        raise HTTPException(status_code=403,detail="Acesso restrito à empresa vinculada ao seu perfil")

def ensure_employee_access(user: User, employee: Employee, requester_type: str | None=None, db: Session | None=None):
    if user.role=="employee":
        if user.employee_id==employee.id and requester_type in (None,"employee"):
            return
        # Leaders act for their team as configured in the selection assignments (Atribuições de Seleção).
        allowed=processes.allowed_target_ids(db,user,"vacations") if db is not None else set()
        if user.employee_id!=employee.id and employee.id in (allowed or set()) and requester_type in (None,"manager"):
            return
        raise HTTPException(status_code=403,detail="Funcionários só podem consultar ou solicitar as próprias férias ou as da própria equipe, conforme as atribuições de seleção")
    elif user.role=="hr_manager":
        if user.company_id!=employee.company_id or requester_type not in (None,"manager"):
            raise HTTPException(status_code=403,detail="O gestor de RH só pode solicitar férias para funcionários da própria empresa")
    elif user.role not in ("system_admin","consultant"):
        raise HTTPException(status_code=403,detail="Perfil sem acesso ao processo de férias")

def group_payload(db: Session, config: VacationConfiguration):
    group=db.get(EmployeeGroup,config.group_id) if config.group_id else None
    company=db.get(Company,config.company_id) if config.company_id else None
    return {**config_snapshot(config),"company_name":company.trade_name if company else "Todas as empresas","group_name":group.name if group else None}

def request_payload(db: Session, row: VacationRequest):
    employee=db.get(Employee,row.employee_id)
    company=db.get(Company,row.company_id)
    requester=db.get(User,row.requested_by_id)
    approver=db.get(User,row.approved_by_id) if row.approved_by_id else None
    return {
        "id":row.id,"company_id":row.company_id,"company_name":company.trade_name if company else "",
        "employee_id":row.employee_id,"employee_name":employee.full_name if employee else "",
        "requested_by_id":row.requested_by_id,"requested_by":requester.full_name if requester else "",
        "approved_by":approver.full_name if approver else None,"start_date":row.start_date.isoformat(),
        "requested_days":row.requested_days,"installment_count":row.installment_count,
        "advance_13th_requested":row.advance_13th_requested,"status":row.status,
        "approval_comment":row.approval_comment,"config_version":row.config_version,
        "config_scope":row.config_scope,"rules_snapshot":row.rules_snapshot,
        "created_at":row.created_at.isoformat() if row.created_at else None,
        "cash_bonus_days":row.cash_bonus_days or 0,"acquisition_start":row.acquisition_start.isoformat() if row.acquisition_start else None,
        "acknowledged_at":row.acknowledged_at.isoformat() if row.acknowledged_at else None,
        "end_date":(row.start_date+timedelta(days=row.requested_days-1)).isoformat(),
        "return_date":(row.start_date+timedelta(days=row.requested_days)).isoformat(),
        **_workflow_state(db,row),
    }

def _workflow_state(db: Session, row: VacationRequest):
    req=db.scalar(select(ProcessRequest).where(ProcessRequest.process=="vacations",ProcessRequest.reference_id==row.id).order_by(ProcessRequest.id.desc()).limit(1))
    if not req:
        return {"process_request_id":None,"current_approver":None,"due_at":None}
    out=processes.request_out(db,req)
    return {"process_request_id":req.id,"current_approver":out["current_approver"],"due_at":out["due_at"],"overdue":out["overdue"]}

@router.get("/configurations")
def list_configurations(company_id: int, environment:str="production", db: Session=Depends(get_db), user: User=Depends(require_roles("system_admin","consultant","hr_manager"))):
    ensure_company_access(user,company_id)
    if environment not in ("homologation","production"):
        raise HTTPException(status_code=422,detail="Ambiente inválido")
    company=db.get(Company,company_id)
    if not company:
        raise HTTPException(status_code=404,detail="Empresa não encontrada")
    system=db.scalar(select(VacationConfiguration).where(VacationConfiguration.scope_key=="system",VacationConfiguration.environment==environment,VacationConfiguration.revoked.is_(False)).order_by(VacationConfiguration.version.desc()).limit(1))
    company_rules=db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id==company_id,VacationConfiguration.environment==environment,VacationConfiguration.revoked.is_(False)).order_by(VacationConfiguration.group_id,VacationConfiguration.effective_from.desc(),VacationConfiguration.version.desc())).all()
    seen=set();latest=[]
    for rule in company_rules:
        if rule.scope_key not in seen:
            seen.add(rule.scope_key);latest.append(group_payload(db,rule))
    groups=db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id==company_id).order_by(EmployeeGroup.name)).all()
    named=[c for c in latest if c.get("config_code")]
    latest=[c for c in latest if not c.get("config_code")]
    return {"named_configurations":named,"company":{"id":company.id,"code":company.code,"name":company.trade_name},"environment":environment,"system_default":group_payload(db,system) if system else None,"configurations":latest,"groups":[{"id":g.id,"name":g.name} for g in groups]}

@router.get("/effective")
def effective_configuration(employee_id: int | None=None, environment:str="production", db: Session=Depends(get_db), user: User=Depends(current_user)):
    if environment not in ("homologation","production"):
        raise HTTPException(status_code=422,detail="Ambiente inválido")
    if user.role=="employee":
        employee_id=user.employee_id
        environment="production"
    if not employee_id:
        raise HTTPException(status_code=422,detail="Selecione um funcionário")
    employee=db.get(Employee,employee_id)
    if not employee:
        raise HTTPException(status_code=404,detail="Funcionário não encontrado")
    ensure_employee_access(user,employee,db=db)
    config,origin,why=resolve_effective_config(db,employee,environment=environment)
    if not config:
        raise HTTPException(status_code=404,detail=why)
    return {"employee":{"id":employee.id,"name":employee.full_name,"group_id":employee.group_id,"company_id":employee.company_id},"environment":environment,"configuration":config_snapshot(config),"origin":origin,"why":why,"balance_days":remaining_vacation_balance(db,employee) if config.show_balance_to_employee or user.role!="employee" else None,
            "acquisition_period":_period_out(current_period(db,employee)) if config.show_balance_to_employee or user.role!="employee" else None,
            "policy":active_config(db,employee.company_id,"vacation_policy",environment)}

def _period_out(period):
    return {"start":period["start"].isoformat(),"end":period["end"].isoformat(),"concession_end":period["concession_end"].isoformat(),"remaining":period["remaining"],"parcels":period["parcels"],"cash_bonus":period["cash_bonus"]} if period else None

@router.put("/configurations")
def save_configuration(payload: VacationConfigurationInput, db: Session=Depends(get_db), user: User=Depends(require_roles("system_admin","consultant"))):
    company=db.scalar(select(Company).where(Company.id==payload.company_id).with_for_update())
    if not company:
        raise HTTPException(status_code=404,detail="Empresa não encontrada")
    if payload.environment!="homologation":
        raise HTTPException(status_code=403,detail="Regras de produção só mudam pelo fluxo de publicação aprovado")
    if payload.group_id is not None:
        group=db.get(EmployeeGroup,payload.group_id)
        if not group or group.company_id!=payload.company_id:
            raise HTTPException(status_code=422,detail="O grupo deve pertencer à empresa selecionada")
    config,errors=create_configuration_version(db,payload,user.id,user.full_name)
    if errors:
        raise HTTPException(status_code=422,detail=errors)
    db.add(Activity(company_id=company.id,actor=user.full_name,message=f"Regra de férias v{config.version} salva para {company.trade_name}."))
    db.commit();db.refresh(config)
    return group_payload(db,config)

@router.get("/history")
def configuration_history(company_id: int, environment:str="production", db: Session=Depends(get_db), user: User=Depends(require_roles("system_admin","consultant","hr_manager"))):
    ensure_company_access(user,company_id)
    if environment not in ("homologation","production"):
        raise HTTPException(status_code=422,detail="Ambiente inválido")
    rows=db.scalars(select(VacationConfigurationHistory).where(VacationConfigurationHistory.company_id==company_id,VacationConfigurationHistory.environment==environment).order_by(VacationConfigurationHistory.created_at.desc(),VacationConfigurationHistory.id.desc()).limit(100)).all()
    return [{"id":r.id,"company_id":r.company_id,"group_id":r.group_id,"config_id":r.config_id,"version":r.version,"environment":r.environment,"actor":r.actor_name,"before":r.before_snapshot,"after":r.after_snapshot,"created_at":r.created_at.isoformat() if r.created_at else None} for r in rows]

def prepare_evaluation(payload: VacationSimulationInput, user: User, db: Session, simulation: bool=True):
    employee=db.get(Employee,payload.employee_id)
    if not employee:
        raise HTTPException(status_code=404,detail="Funcionário não encontrado")
    ensure_employee_access(user,employee,payload.requester_type,db)
    if user.role in ("employee","hr_manager"):
        payload.environment="production"
    return evaluate_vacation(db,payload,simulation=simulation)

@router.post("/simulate")
def simulate_vacation(payload: VacationSimulationInput, db: Session=Depends(get_db), user: User=Depends(current_user)):
    result=prepare_evaluation(payload,user,db)
    return result

@router.get("/requests")
def list_vacation_requests(company_id: int | None=None, status: str | None=Query(default=None,pattern="^(pending_approval|approved|approved_auto|rejected)$"), db: Session=Depends(get_db), user: User=Depends(current_user)):
    query=select(VacationRequest).order_by(VacationRequest.created_at.desc(),VacationRequest.id.desc())
    if user.role=="employee":
        team=processes.allowed_target_ids(db,user,"vacations") or set()
        query=query.where(VacationRequest.employee_id.in_(team|{user.employee_id or 0}))
    elif user.role=="hr_manager":
        query=query.where(VacationRequest.company_id==user.company_id)
    elif company_id:
        query=query.where(VacationRequest.company_id==company_id)
    if company_id and user.role not in ("employee","hr_manager"):
        query=query.where(VacationRequest.company_id==company_id)
    if status:
        query=query.where(VacationRequest.status==status)
    return [request_payload(db,row) for row in db.scalars(query).all()]

@router.post("/requests",status_code=201)
def create_vacation_request(payload: VacationRequestInput, db: Session=Depends(get_db), user: User=Depends(current_user)):
    if user.role not in ("employee","hr_manager"):
        raise HTTPException(status_code=403,detail="Solicitações operacionais são feitas pelo funcionário ou gestor de RH")
    payload.environment="production"
    result=prepare_evaluation(payload,user,db,simulation=False)
    if not result["allowed"]:
        raise HTTPException(status_code=422,detail=" ".join(result["errors"]))
    employee=db.get(Employee,payload.employee_id)
    config=result["effective_configuration"]
    status="pending_approval" if config["manager_approval_required"] else "approved_auto"
    period=current_period(db,employee)
    row=VacationRequest(
        company_id=employee.company_id,employee_id=employee.id,requested_by_id=user.id,
        start_date=payload.start_date,requested_days=payload.requested_days,
        installment_count=payload.installment_count,advance_13th_requested=payload.advance_13th_requested,
        status=status,config_id=config["id"],config_version=config["version"],config_scope=config["scope_key"],
        rules_snapshot={**config,"recording":result.get("recording")},cash_bonus_days=payload.cash_bonus_days,acquisition_start=period["start"] if period else None,
    )
    db.add(row);db.flush()
    if status=="approved_auto":
        apply_monthly_treatment(db,row)
    if status=="pending_approval":
        processes.start(db,"vacations",employee.company_id,employee,user,{"vacation_request_id":row.id,"start_date":row.start_date.isoformat(),"requested_days":row.requested_days,"cash_bonus_days":row.cash_bonus_days},reference_id=row.id,auto_advance=False)
    db.add(Activity(company_id=employee.company_id,actor=user.full_name,message=f"Solicitação de férias criada para {employee.full_name}; status {status}."))
    db.commit();db.refresh(row)
    return request_payload(db,row)

@router.post("/requests/{request_id}/decision")
def decide_vacation_request(request_id: int, payload: VacationDecisionInput, db: Session=Depends(get_db), user: User=Depends(current_user)):
    """Decides the current workflow step (gestor imediato, RH or administrator, as configured)."""
    row=db.get(VacationRequest,request_id)
    if not row:
        raise HTTPException(status_code=404,detail="Solicitação não encontrada")
    if user.role=="hr_manager": ensure_company_access(user,row.company_id)
    if row.status!="pending_approval":
        raise HTTPException(status_code=409,detail="A solicitação já foi analisada")
    req=db.scalar(select(ProcessRequest).where(ProcessRequest.process=="vacations",ProcessRequest.reference_id==row.id,ProcessRequest.status=="pending_approval").limit(1))
    if not req:  # requests created before the workflow engine existed
        req=processes.start(db,"vacations",row.company_id,db.get(Employee,row.employee_id),db.get(User,row.requested_by_id) or user,{"vacation_request_id":row.id},reference_id=row.id,auto_advance=False)
    processes.decide(db,req,user,payload.approved,payload.comment)
    employee=db.get(Employee,row.employee_id)
    db.add(Activity(company_id=row.company_id,actor=user.full_name,message=f"Solicitação de férias de {employee.full_name if employee else 'funcionário'}: {row.status}."))
    db.commit();db.refresh(row)
    return request_payload(db,row)

@router.post("/requests/{request_id}/acknowledge")
def acknowledge_vacation_notice(request_id: int, db: Session=Depends(get_db), user: User=Depends(current_user)):
    """Ciência do aviso de férias pelo colaborador (art. 135 da CLT)."""
    row=db.get(VacationRequest,request_id)
    if not row or user.employee_id!=row.employee_id:
        raise HTTPException(status_code=404,detail="Aviso de férias não encontrado")
    if row.status not in ("approved","approved_auto"):
        raise HTTPException(status_code=409,detail="O aviso só fica disponível após a aprovação")
    if row.acknowledged_at:
        raise HTTPException(status_code=409,detail="Ciência já registrada")
    row.acknowledged_at=datetime.now(timezone.utc)
    db.add(Activity(company_id=row.company_id,actor=user.full_name,message=f"Ciência do aviso de férias nº {row.id} registrada."))
    db.commit();db.refresh(row)
    return request_payload(db,row)
