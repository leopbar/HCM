from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import (Activity, AdmissionRequest, BenefitCatalog, Company, Department, Employee,
    EmployeeBenefit, EmployeeGroup, HRModuleConfiguration, HRModuleConfigurationHistory,
    PayrollEvent, PayrollSimulation, Position, TimeRecord, User)
from app.schemas import (AdmissionDecisionInput, AdmissionRequestInput, AdmissionSimulationInput, BenefitDecisionInput,
    BenefitInput, BenefitRequestInput, HRModuleConfigurationInput, PayrollEventInput,
    PayrollSimulationInput, TimeSimulationInput)
from app.security import current_user, require_roles
from app.policies import DEFAULTS, active_config, normalize, validate_config
from app.models import JobPosition, HeadcountPlan, SalaryRange, TimeRecordAdjustment, TransportEnrollment, TransportLine, ValueEntry
from app.schemas import TimeAdjustmentInput
from app import processes
from app.groups import employee_group_ids
from sqlalchemy import func

router=APIRouter(prefix="/api/hr",tags=["Módulos de RH"])
HOLIDAYS={(1,1),(4,21),(5,1),(9,7),(10,12),(11,2),(11,15),(11,20),(12,25)}
EMPLOYEE_VISIBLE_MODULES=("timekeeping","benefits")  # what the self-service screens need; everything else is parametrization

def company_access(user:User, company_id:int):
 if user.role not in ("system_admin","consultant") and user.company_id!=company_id:
  raise HTTPException(status_code=403,detail="Acesso restrito à empresa vinculada ao perfil")

def employee_access(user:User, employee:Employee):
 if user.role=="employee" and user.employee_id!=employee.id:
  raise HTTPException(status_code=403,detail="Funcionários só podem consultar os próprios dados")
 if user.role=="hr_manager" and user.company_id!=employee.company_id:
  raise HTTPException(status_code=403,detail="Acesso restrito à empresa vinculada ao perfil")

def audit(db:Session, company_id:int, actor:User, message:str):
 db.add(Activity(company_id=company_id,actor=actor.full_name,message=message))

def config_out(row):
 return {"id":row.id,"company_id":row.company_id,"module":row.module,"environment":row.environment,"version":row.version,"effective_from":row.effective_from.isoformat(),"values":normalize(row.module,row.values),"created_at":row.created_at.isoformat() if row.created_at else None}

@router.get("/configurations/{module}")
def get_configuration(module:str,company_id:int,environment:str="production",db:Session=Depends(get_db),user:User=Depends(current_user)):
 if module not in DEFAULTS: raise HTTPException(status_code=404,detail="Módulo desconhecido")
 if user.role=="employee" and module not in EMPLOYEE_VISIBLE_MODULES: raise HTTPException(status_code=403,detail="Parametrização restrita às equipes de RH e consultoria")
 company_access(user,company_id)
 if environment not in ("homologation","production"): raise HTTPException(status_code=422,detail="Ambiente inválido")
 if user.role=="employee": environment="production"
 rows=db.scalars(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id==company_id,HRModuleConfiguration.module==module,HRModuleConfiguration.environment==environment,HRModuleConfiguration.revoked.is_(False)).order_by(HRModuleConfiguration.version.desc())).all()
 current=max((r for r in rows if r.effective_from<=date.today()),key=lambda r:(r.effective_from,r.version),default=None)
 return {"module":module,"environment":environment,"defaults":DEFAULTS[module],"effective":config_out(current) if current else {"version":0,"effective_from":None,"values":DEFAULTS[module]},"versions":[config_out(r) for r in rows]}

@router.put("/configurations")
def save_configuration(payload:HRModuleConfigurationInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant"))):
 company=db.get(Company,payload.company_id)
 if not company: raise HTTPException(status_code=404,detail="Empresa não encontrada")
 if payload.environment!="homologation": raise HTTPException(status_code=403,detail="Configurações de produção só mudam pelo fluxo de publicação aprovado")
 payload.values=normalize(payload.module,payload.values)
 errors=validate_config(payload.module,payload.values)+cross_errors(db,payload.company_id,payload.module,payload.values)
 if errors: raise HTTPException(status_code=422,detail=errors)
 db.scalar(select(Company).where(Company.id==company.id).with_for_update())
 previous=db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id==company.id,HRModuleConfiguration.module==payload.module,HRModuleConfiguration.environment==payload.environment).order_by(HRModuleConfiguration.version.desc()).limit(1))
 row=HRModuleConfiguration(company_id=company.id,module=payload.module,environment=payload.environment,version=(previous.version+1 if previous else 1),effective_from=payload.effective_from,values=payload.values,created_by=user.id)
 db.add(row);db.flush()
 db.add(HRModuleConfigurationHistory(company_id=company.id,module=payload.module,environment=payload.environment,version=row.version,actor_id=user.id,actor_name=user.full_name,snapshot={"effective_from":payload.effective_from.isoformat(),"values":payload.values}))
 audit(db,company.id,user,f"Parâmetros de {payload.module} salvos na versão {row.version}.")
 db.commit();db.refresh(row)
 return config_out(row)

def cross_errors(db:Session,company_id:int,module:str,values:dict)->list[str]:
 """Checks that depend on other company data (catalogs, groups and named configurations in homologation)."""
 errors=[]
 if module=="vacation_policy" and isinstance(values.get("general_configs"),list):
  catalog=active_config(db,company_id,"payroll_setup","homologation")
  groups={g.lower() for g in catalog["calculation_groups"]};sheets=set(catalog["sheets"])
  for index,row in enumerate(values["general_configs"],1):
   if not isinstance(row,dict): continue
   if str(row.get("calculation_group","")).strip().lower() not in groups: errors.append(f"Configuração geral {index}: grupo de cálculo {row.get('calculation_group')} não existe em Grupos de cálculo e folhas.")
   for key,value in row.items():
    if key.startswith(("sheet_","simulation_sheet_")) and value and value not in sheets: errors.append(f"Configuração geral {index}: a folha {value} não existe em Grupos de cálculo e folhas.")
 if module=="config_assignments":
  from app.models import VacationConfiguration
  names={g.name.lower() for g in db.scalars(select(EmployeeGroup).where(EmployeeGroup.company_id==company_id)).all()}
  configs={c.config_code:c for c in db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id==company_id,VacationConfiguration.environment=="homologation",VacationConfiguration.config_code.is_not(None),VacationConfiguration.revoked.is_(False)).order_by(VacationConfiguration.version)).all()}
  for process,label in (("vacations","Férias"),("vacations_simulation","Férias - Simulação")):
   for index,item in enumerate(values.get(process) or [],1):
    if not isinstance(item,dict): continue
    if str(item.get("group","")).lower() not in names: errors.append(f"{label}, linha {index}: grupo de colaboradores {item.get('group')} não existe.")
    config=configs.get(item.get("config"))
    if not config: errors.append(f"{label}, linha {index}: configuração {item.get('config')} não existe em homologação.")
    elif process=="vacations_simulation" and not config.simulation_enabled: errors.append(f"{label}, linha {index}: a configuração {item.get('config')} não está disponível para simulação.")
 return errors

def admission_errors(payload,config):
 errors=[]; delta=(payload.planned_date-date.today()).days
 if not config["enabled"]: errors.append("Admissões estão desabilitadas para esta empresa.")
 if delta<config["min_advance_days"] and not (delta<0 and config["allow_retroactive"]): errors.append(f"Esta empresa exige antecedência mínima de {config['min_advance_days']} dias para solicitações de admissão.")
 if delta>config["max_advance_days"]: errors.append(f"A antecedência máxima permitida é de {config['max_advance_days']} dias.")
 if delta<0 and not config["allow_retroactive"]: errors.append("Admissões retroativas estão desabilitadas.")
 if payload.planned_date.weekday()>=5 and not config["allow_weekends"]: errors.append("A data prevista cai em um final de semana não permitido.")
 if (payload.planned_date.month,payload.planned_date.day) in HOLIDAYS and not config["allow_holidays"]: errors.append("A data prevista coincide com um feriado demonstrativo não permitido.")
 if config["documents_required"] and not payload.documents_complete: errors.append("Os documentos obrigatórios precisam ser marcados como completos.")
 return errors

def admission_out(db,row):
 company=db.get(Company,row.company_id); department=db.get(Department,row.department_id); position=db.get(Position,row.position_id); user=db.get(User,row.requested_by_id); group=db.get(EmployeeGroup,row.group_id) if row.group_id else None
 seat=db.get(JobPosition,row.job_position_id) if row.job_position_id else None
 return {"id":row.id,"company_id":row.company_id,"company_name":company.trade_name,"candidate_name":row.candidate_name,"candidate_email":row.candidate_email,"department":department.name,"position":position.title,"planned_date":row.planned_date.isoformat(),"documents_complete":row.documents_complete,"status":row.status,"responsible":user.full_name if user else "","comment":row.comment,
  "group":group.name if group else None,"salary":float(row.salary) if row.salary is not None else None,"registration":row.registration,"contract_type":row.contract_type,"job_position":seat.code if seat else None,"employee_id":row.employee_id}

def admission_hire_errors(db:Session,payload)->list[str]:
 """Guia Contratação: dados para gerar o funcionário, tabela salarial, quadro de vagas e posição."""
 config=active_config(db,payload.company_id,"contracting");errors=[]
 if config["create_employee_on_approval"]:
  if not payload.group_id or not payload.salary or not payload.registration: errors.append("Informe grupo, salário e matrícula: o funcionário será criado na aprovação.")
 if payload.group_id:
  group=db.get(EmployeeGroup,payload.group_id)
  if not group or group.company_id!=payload.company_id: errors.append("O grupo deve pertencer à empresa selecionada.")
 if payload.registration and db.scalar(select(Employee.id).where(Employee.company_id==payload.company_id,Employee.registration==payload.registration.strip())): errors.append("Matrícula já usada nesta empresa.")
 if db.scalar(select(Employee.id).where(Employee.company_id==payload.company_id,func.lower(Employee.email)==str(payload.candidate_email).lower(),Employee.status!="Desligado")): errors.append("Já existe colaborador ativo com este e-mail.")
 if db.scalar(select(AdmissionRequest.id).where(AdmissionRequest.company_id==payload.company_id,func.lower(AdmissionRequest.candidate_email)==str(payload.candidate_email).lower(),AdmissionRequest.status.in_(("pending_approval","approved","approved_auto")),AdmissionRequest.employee_id.is_(None)).limit(1)): errors.append("Já existe admissão em andamento para este candidato.")
 errors+=processes.contracting_errors(db,payload.company_id,payload.department_id,payload.position_id,payload.salary or 0,payload.job_position_id)
 return errors

def hire_from_admission(db:Session,row:AdmissionRequest,actor:User):
 if not active_config(db,row.company_id,"contracting")["create_employee_on_approval"] or row.employee_id: return
 employee=Employee(company_id=row.company_id,department_id=row.department_id,position_id=row.position_id,group_id=row.group_id,registration=row.registration,full_name=row.candidate_name,email=row.candidate_email.lower(),admission_date=row.planned_date,salary=row.salary,status="Ativo",contract_type=row.contract_type,
  calculation_group="Estagiários" if row.contract_type=="Estágio" and "Estagiários" in active_config(db,row.company_id,"payroll_setup")["calculation_groups"] else "Mensalistas")
 db.add(employee);db.flush();row.employee_id=employee.id
 if row.job_position_id:
  seat=db.get(JobPosition,row.job_position_id)
  if seat and seat.status=="vacant": seat.employee_id,seat.status=employee.id,"occupied"
 audit(db,row.company_id,actor,f"Funcionário {employee.full_name} criado a partir da admissão nº {row.id}.")

def validate_admission_scope(db:Session,company_id:int,department_id:int,position_id:int):
 department=db.get(Department,department_id);position=db.get(Position,position_id)
 if not department or not position or department.company_id!=company_id or position.company_id!=company_id:
  raise HTTPException(status_code=422,detail="Departamento e cargo devem pertencer à empresa selecionada")

@router.post("/admissions/simulate")
def simulate_admission(payload:AdmissionSimulationInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant","hr_manager"))):
 company_access(user,payload.company_id)
 validate_admission_scope(db,payload.company_id,payload.department_id,payload.position_id)
 config=active_config(db,payload.company_id,"admission",payload.environment)
 errors=admission_errors(payload,config)+admission_hire_errors(db,payload)
 return {"allowed":not errors,"errors":errors,"environment":payload.environment,"configuration":config,"candidate_name":payload.candidate_name,"planned_date":payload.planned_date.isoformat()}

@router.get("/admissions")
def list_admissions(company_id:int|None=None,db:Session=Depends(get_db),user:User=Depends(current_user)):
 q=select(AdmissionRequest).order_by(AdmissionRequest.created_at.desc(),AdmissionRequest.id.desc())
 if user.role=="employee": raise HTTPException(status_code=403,detail="Módulo de admissão disponível à equipe de RH")
 if user.role=="hr_manager": q=q.where(AdmissionRequest.company_id==user.company_id)
 elif company_id: q=q.where(AdmissionRequest.company_id==company_id)
 if company_id: company_access(user,company_id)
 return [admission_out(db,r) for r in db.scalars(q).all()]

@router.post("/admissions",status_code=201)
def create_admission(payload:AdmissionRequestInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant","hr_manager"))):
 company_access(user,payload.company_id); config=active_config(db,payload.company_id,"admission")
 validate_admission_scope(db,payload.company_id,payload.department_id,payload.position_id)
 errors=admission_errors(payload,config)+admission_hire_errors(db,payload)
 if errors: raise HTTPException(status_code=422,detail=errors)
 row=AdmissionRequest(company_id=payload.company_id,candidate_name=payload.candidate_name,candidate_email=str(payload.candidate_email).lower(),department_id=payload.department_id,position_id=payload.position_id,planned_date=payload.planned_date,documents_complete=payload.documents_complete,status="pending_approval" if config["approval_required"] else "approved_auto",requested_by_id=user.id,
  group_id=payload.group_id,salary=payload.salary,registration=payload.registration.strip() if payload.registration else None,contract_type=payload.contract_type,job_position_id=payload.job_position_id)
 db.add(row);db.flush()
 if row.status=="approved_auto": hire_from_admission(db,row,user)
 audit(db,payload.company_id,user,f"Solicitação de admissão criada para {payload.candidate_name}.");db.commit();db.refresh(row)
 return admission_out(db,row)

@router.post("/admissions/{request_id}/decision")
def decide_admission(request_id:int,payload:AdmissionDecisionInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","hr_manager"))):
 row=db.get(AdmissionRequest,request_id)
 if not row: raise HTTPException(status_code=404,detail="Solicitação não encontrada")
 company_access(user,row.company_id)
 if row.status!="pending_approval": raise HTTPException(status_code=409,detail="A solicitação já foi analisada")
 if not payload.approved and not payload.comment.strip(): raise HTTPException(status_code=422,detail="Informe o motivo da rejeição")
 row.status="approved" if payload.approved else "rejected";row.decided_by_id=user.id;row.comment=payload.comment.strip() or None
 if payload.approved:
  try:
   hire_from_admission(db,row,user)
  except Exception as exc:
   db.rollback();raise HTTPException(status_code=409,detail="Não foi possível gerar o funcionário: matrícula ou e-mail já usados") from exc
 from app.recruitment_api import on_admission_decided
 on_admission_decided(db,row,user)
 audit(db,row.company_id,user,f"Admissão de {row.candidate_name} {row.status}.");db.commit();db.refresh(row)
 return admission_out(db,row)

def time_evaluation(payload,config,schedule=None):
 schedule=schedule or DEFAULTS["work_schedule"]
 start=datetime.combine(payload.work_date,time.fromisoformat(payload.start_time));end=datetime.combine(payload.work_date,time.fromisoformat(payload.end_time))
 if end<=start: return {"allowed":False,"errors":["O horário de saída deve ser posterior ao horário de entrada."]}
 errors=[];gross=int((end-start).total_seconds()//60);worked=gross-payload.break_minutes;target=config["daily_hours"]*60;overtime=max(0,worked-target)
 if payload.break_minutes>=gross: errors.append("O intervalo deve ser menor que o período entre entrada e saída.")
 if payload.break_minutes<config["min_break_minutes"]: errors.append(f"O intervalo informado é menor que o mínimo configurado de {config['min_break_minutes']} minutos.")
 if payload.work_date.weekday() not in schedule["workdays"] and not config["allow_weekends"]: errors.append("O registro fora dos dias da escala de trabalho está desabilitado.")
 if (payload.work_date.month,payload.work_date.day) in HOLIDAYS: errors.append("A data coincide com um feriado demonstrativo; confirme o calendário antes de registrar.")
 if overtime and not config["allow_overtime"]: errors.append("Horas extras estão desabilitadas para esta empresa.")
 max_extra=config["max_overtime_hours"]*60
 if overtime>max_extra: errors.append(f"Horas extras excedem o limite diário demonstrativo de {config['max_overtime_hours']} horas.")
 expected=int(schedule["expected_start_time"][:2])*60+int(schedule["expected_start_time"][3:])
 start_minutes=start.hour*60+start.minute;late=max(0,start_minutes-expected-config["late_tolerance_minutes"])
 return {"allowed":not errors,"errors":errors,"environment":payload.environment,"work_date":payload.work_date.isoformat(),"start_time":payload.start_time,"end_time":payload.end_time,"break_minutes":payload.break_minutes,"worked_minutes":worked,"worked_hours":round(worked/60,2),"overtime_minutes":overtime,"overtime_hours":round(overtime/60,2),"late_minutes":late,"requires_approval":bool(overtime and config["manager_approval_required"]),"configuration":config}

def time_out(db,row):
 emp=db.get(Employee,row.employee_id)
 adjustments=[{"actor":a.actor_name,"justification":a.justification,"before":a.before_snapshot,"after":a.after_snapshot,"created_at":a.created_at.isoformat() if a.created_at else None} for a in db.scalars(select(TimeRecordAdjustment).where(TimeRecordAdjustment.record_id==row.id).order_by(TimeRecordAdjustment.id))]
 return {"adjustments":adjustments,"id":row.id,"employee_id":row.employee_id,"employee_name":emp.full_name,"work_date":row.work_date.isoformat(),"start_time":row.start_time,"end_time":row.end_time,"break_minutes":row.break_minutes,"worked_minutes":row.worked_minutes,"worked_hours":round(row.worked_minutes/60,2),"overtime_minutes":row.overtime_minutes,"overtime_hours":round(row.overtime_minutes/60,2),"status":row.status}

@router.post("/timekeeping/simulate")
def simulate_time(payload:TimeSimulationInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
 employee=db.get(Employee,payload.employee_id)
 if not employee: raise HTTPException(status_code=404,detail="Funcionário não encontrado")
 employee_access(user,employee)
 result=time_evaluation(payload,active_config(db,employee.company_id,"timekeeping",payload.environment),active_config(db,employee.company_id,"work_schedule",payload.environment))
 return result

@router.post("/timekeeping/records",status_code=201)
def record_time(payload:TimeSimulationInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
 employee=db.get(Employee,payload.employee_id)
 if not employee: raise HTTPException(status_code=404,detail="Funcionário não encontrado")
 employee_access(user,employee)
 if user.role not in ("employee","hr_manager","system_admin"): raise HTTPException(status_code=403,detail="Perfil sem permissão para registrar ponto")
 if payload.environment!="production": raise HTTPException(status_code=403,detail="Registros operacionais só podem ser gravados em produção")
 if payload.work_date>date.today(): raise HTTPException(status_code=422,detail="Não é possível registrar ponto em data futura")
 if payload.work_date<employee.admission_date or employee.status=="Desligado": raise HTTPException(status_code=422,detail="Data fora do período de contrato do colaborador")
 result=time_evaluation(payload,active_config(db,employee.company_id,"timekeeping","production"),active_config(db,employee.company_id,"work_schedule"))
 if not result["allowed"]: raise HTTPException(status_code=422,detail=result["errors"])
 row=TimeRecord(company_id=employee.company_id,employee_id=employee.id,work_date=payload.work_date,start_time=payload.start_time,end_time=payload.end_time,break_minutes=payload.break_minutes,worked_minutes=result["worked_minutes"],overtime_minutes=result["overtime_minutes"],status="pending_approval" if result["requires_approval"] else "recorded")
 db.add(row);audit(db,employee.company_id,user,f"Ponto registrado para {employee.full_name} em {payload.work_date.isoformat()}.")
 try: db.commit()
 except Exception as exc: db.rollback();raise HTTPException(status_code=409,detail="Já existe um registro de ponto para este funcionário nesta data") from exc
 db.refresh(row);return time_out(db,row)

@router.get("/timekeeping/records")
def list_time_records(company_id:int|None=None,db:Session=Depends(get_db),user:User=Depends(current_user)):
 q=select(TimeRecord).order_by(TimeRecord.work_date.desc(),TimeRecord.id.desc())
 if user.role=="employee": q=q.where(TimeRecord.employee_id==user.employee_id)
 elif user.role=="hr_manager": q=q.where(TimeRecord.company_id==user.company_id)
 elif company_id: q=q.where(TimeRecord.company_id==company_id)
 if company_id: company_access(user,company_id)
 return [time_out(db,r) for r in db.scalars(q.limit(200)).all()]

@router.post("/timekeeping/records/{record_id}/decision")
def decide_time_record(record_id:int,payload:AdmissionDecisionInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","hr_manager"))):
 row=db.get(TimeRecord,record_id)
 if not row: raise HTTPException(status_code=404,detail="Registro de ponto não encontrado")
 company_access(user,row.company_id)
 if row.status!="pending_approval": raise HTTPException(status_code=409,detail="O registro não está aguardando aprovação")
 if not payload.approved and not payload.comment.strip(): raise HTTPException(status_code=422,detail="Informe o motivo da rejeição")
 row.status="approved" if payload.approved else "rejected"
 audit(db,row.company_id,user,f"Registro de ponto {row.status} para {row.work_date.isoformat()}.");db.commit();db.refresh(row)
 return time_out(db,row)

@router.patch("/timekeeping/records/{record_id}")
def adjust_time_record(record_id:int,payload:TimeAdjustmentInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","hr_manager"))):
 row=db.get(TimeRecord,record_id)
 if not row: raise HTTPException(status_code=404,detail="Registro de ponto não encontrado")
 company_access(user,row.company_id)
 config=active_config(db,row.company_id,"timekeeping")
 if not config["allow_manual_adjustment"]: raise HTTPException(status_code=403,detail="Ajustes manuais estão desabilitados para esta empresa")
 if payload.employee_id!=row.employee_id or payload.work_date!=row.work_date: raise HTTPException(status_code=422,detail="O ajuste deve manter o mesmo funcionário e data")
 result=time_evaluation(payload,config,active_config(db,row.company_id,"work_schedule"))
 if not result["allowed"]: raise HTTPException(status_code=422,detail=result["errors"])
 before={"start_time":row.start_time,"end_time":row.end_time,"break_minutes":row.break_minutes,"status":row.status}
 row.start_time=payload.start_time;row.end_time=payload.end_time;row.break_minutes=payload.break_minutes;row.worked_minutes=result["worked_minutes"];row.overtime_minutes=result["overtime_minutes"]
 # Any adjustment of an already decided record goes back to approval when the rule requires it (Portaria 671/2021: adjustments must be traceable).
 row.status="pending_approval" if result["requires_approval"] or before["status"]=="approved" else "recorded"
 db.add(TimeRecordAdjustment(record_id=row.id,actor_id=user.id,actor_name=user.full_name,justification=payload.justification.strip(),before_snapshot=before,after_snapshot={"start_time":row.start_time,"end_time":row.end_time,"break_minutes":row.break_minutes,"status":row.status}))
 audit(db,row.company_id,user,f"Registro de ponto ajustado para {row.work_date.isoformat()}: {payload.justification.strip()}");db.commit();db.refresh(row)
 return time_out(db,row)

def event_out(event):
 return {"id":event.id,"company_id":event.company_id,"code":event.code,"description":event.description,"kind":event.kind,"calculation":event.calculation,"value":float(event.value),"base":event.base,"group_id":event.group_id,"active":event.active,"version":event.version}

@router.get("/payroll/events")
def list_payroll_events(company_id:int,environment:str="production",db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant","hr_manager"))):
 company_access(user,company_id)
 if environment not in ("homologation","production"): raise HTTPException(status_code=422,detail="Ambiente inválido")
 return [event_out(r) for r in db.scalars(select(PayrollEvent).where(PayrollEvent.company_id==company_id,PayrollEvent.environment==environment).order_by(PayrollEvent.code)).all()]

@router.post("/payroll/events",status_code=201)
def save_payroll_event(payload:PayrollEventInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant"))):
 company_access(user,payload.company_id)
 if payload.environment!="homologation": raise HTTPException(status_code=403,detail="Eventos de produção só mudam pelo fluxo de publicação aprovado")
 if payload.group_id:
  group=db.get(EmployeeGroup,payload.group_id)
  if not group or group.company_id!=payload.company_id: raise HTTPException(status_code=422,detail="O grupo deve pertencer à empresa selecionada")
 event=db.scalar(select(PayrollEvent).where(PayrollEvent.company_id==payload.company_id,PayrollEvent.environment==payload.environment,PayrollEvent.code==payload.code))
 if event:
  values=payload.model_dump(exclude={"company_id","environment"})
  if any(getattr(event,key)!=value for key,value in values.items()):
   for key,value in values.items(): setattr(event,key,value)
   event.version+=1
 else: event=PayrollEvent(**payload.model_dump());db.add(event)
 audit(db,payload.company_id,user,f"Evento demonstrativo de folha {payload.code} atualizado.");db.commit();db.refresh(event)
 return event_out(event)

@router.post("/payroll/simulate")
def simulate_payroll(payload:PayrollSimulationInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant","hr_manager"))):
 company_access(user,payload.company_id)
 if user.role=="hr_manager": payload.environment="production"  # operations always use the published configuration
 if not active_config(db,payload.company_id,"payroll",payload.environment)["enabled"]: raise HTTPException(status_code=422,detail="A simulação de folha está desabilitada para esta empresa")
 q=select(Employee).where(Employee.company_id==payload.company_id,Employee.status=="Ativo").order_by(Employee.registration)
 if payload.employee_id: q=q.where(Employee.id==payload.employee_id)
 employees=db.scalars(q).all()
 if payload.employee_id and not employees: raise HTTPException(status_code=404,detail="Funcionário não encontrado nesta empresa")
 events=db.scalars(select(PayrollEvent).where(PayrollEvent.company_id==payload.company_id,PayrollEvent.environment==payload.environment,PayrollEvent.active.is_(True))).all()
 results=[]
 for employee in employees:
  base=Decimal(str(employee.salary)); earnings=[]; deductions=[]
  groups=employee_group_ids(db,employee)
  for event in sorted((e for e in events if e.group_id is None or e.group_id in groups),key=lambda e:(e.code,e.id)):
   amount=Decimal(str(event.value)) if event.calculation=="fixed" else (base*Decimal(str(event.value))/Decimal("100"))
   amount=amount.quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
   (earnings if event.kind=="earning" else deductions).append({"code":event.code,"description":event.description,"amount":float(amount)})
  for entry in db.scalars(select(ValueEntry).where(ValueEntry.employee_id==employee.id,ValueEntry.competence==payload.competence)).all():
   (earnings if entry.kind=="earning" else deductions).append({"code":entry.event_code,"description":f"{entry.description} (lançamento)","amount":float(entry.amount)})
  enrollment=db.scalar(select(TransportEnrollment).where(TransportEnrollment.employee_id==employee.id,TransportEnrollment.active.is_(True)))
  if enrollment:
   transport=active_config(db,payload.company_id,"transport",payload.environment)
   cost=processes.transport_cost(db,enrollment.lines,enrollment.workdays)
   discount=min(cost,(base*Decimal(transport["employee_discount_percent"])/Decimal("100")).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP))
   deductions.append({"code":"VT","description":f"Desconto vale-transporte ({transport['employee_discount_percent']}%; custo R$ {float(cost):.2f})","amount":float(discount)})
  benefits=[{"code":b.code,"name":b.name,"value":float(b.fixed_value)+float(base*Decimal(str(b.percentage_value))/Decimal("100"))} for eb,b in db.execute(select(EmployeeBenefit,BenefitCatalog).join(BenefitCatalog,BenefitCatalog.id==EmployeeBenefit.benefit_id).where(EmployeeBenefit.employee_id==employee.id,EmployeeBenefit.status=="active")).all()]
  gross=base+sum((Decimal(str(e["amount"])) for e in earnings),Decimal("0"));total_deductions=sum((Decimal(str(e["amount"])) for e in deductions),Decimal("0"))
  net=gross-total_deductions
  results.append({"employee_id":employee.id,"employee_name":employee.full_name,"registration":employee.registration,"salary_base":float(base),"earnings":earnings,"deductions":deductions,"benefits":benefits,"gross":float(gross),"net_demo":float(net),"status":"negative_net" if net<0 else "simulated","warnings":(["Líquido negativo: revise descontos e lançamentos."] if net<0 else [])})
 simulation=PayrollSimulation(company_id=payload.company_id,environment=payload.environment,competence=payload.competence,results=results,simulated_by_id=user.id);db.add(simulation)
 audit(db,payload.company_id,user,f"Folha demonstrativa da competência {payload.competence} simulada para {len(results)} funcionário(s).");db.commit();db.refresh(simulation)
 return {"id":simulation.id,"company_id":simulation.company_id,"environment":simulation.environment,"competence":simulation.competence,"warning":"Cálculo exclusivamente demonstrativo; não calcula encargos trabalhistas ou tributários.","results":simulation.results,"created_at":simulation.created_at.isoformat() if simulation.created_at else None}

@router.get("/payroll/simulations")
def list_payroll_simulations(company_id:int,environment:str="production",db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant","hr_manager"))):
 company_access(user,company_id)
 return [{"id":r.id,"company_id":r.company_id,"environment":r.environment,"competence":r.competence,"results":r.results,"created_at":r.created_at.isoformat() if r.created_at else None} for r in db.scalars(select(PayrollSimulation).where(PayrollSimulation.company_id==company_id,PayrollSimulation.environment==environment).order_by(PayrollSimulation.created_at.desc()).limit(30)).all()]

def benefit_out(db,row,user:User|None=None):
 group=db.get(EmployeeGroup,row.group_id) if row.group_id else None
 return {"id":row.id,"company_id":row.company_id,"environment":row.environment,"code":row.code,"name":row.name,"description":row.description,"fixed_value":float(row.fixed_value),"percentage_value":float(row.percentage_value),"group_id":row.group_id,"group_name":group.name if group else "Todos os grupos","approval_required":row.approval_required,"effective_from":row.effective_from.isoformat(),"active":row.active,"version":row.version}

@router.get("/benefits/catalog")
def list_benefits(company_id:int,environment:str="production",db:Session=Depends(get_db),user:User=Depends(current_user)):
 company_access(user,company_id)
 if environment not in ("homologation","production"): raise HTTPException(status_code=422,detail="Ambiente inválido")
 if user.role=="employee": environment="production"
 q=select(BenefitCatalog).where(BenefitCatalog.company_id==company_id,BenefitCatalog.environment==environment).order_by(BenefitCatalog.name)
 if user.role=="employee":
  employee=db.get(Employee,user.employee_id);q=q.where(BenefitCatalog.active.is_(True),BenefitCatalog.effective_from<=date.today())
  rows=db.scalars(q).all();groups=employee_group_ids(db,employee);rows=[r for r in rows if r.group_id is None or r.group_id in groups]
 else: rows=db.scalars(q).all()
 return [benefit_out(db,r,user) for r in rows]

@router.post("/benefits/catalog",status_code=201)
def save_benefit(payload:BenefitInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant"))):
 company_access(user,payload.company_id)
 if payload.environment!="homologation": raise HTTPException(status_code=403,detail="Catálogo de produção só muda pelo fluxo de publicação aprovado")
 if payload.group_id:
  group=db.get(EmployeeGroup,payload.group_id)
  if not group or group.company_id!=payload.company_id: raise HTTPException(status_code=422,detail="O grupo deve pertencer à empresa selecionada")
 row=db.scalar(select(BenefitCatalog).where(BenefitCatalog.company_id==payload.company_id,BenefitCatalog.environment==payload.environment,BenefitCatalog.code==payload.code))
 if row:
  values=payload.model_dump(exclude={"company_id","environment"})
  if any(getattr(row,key)!=value for key,value in values.items()):
   for key,value in values.items(): setattr(row,key,value)
   row.version+=1
 else: row=BenefitCatalog(**payload.model_dump());db.add(row)
 audit(db,payload.company_id,user,f"Benefício {payload.name} salvo no catálogo.");db.commit();db.refresh(row)
 return benefit_out(db,row)

def employee_benefit_out(db,row):
 benefit=db.get(BenefitCatalog,row.benefit_id);employee=db.get(Employee,row.employee_id);requester=db.get(User,row.requested_by_id)
 return {"id":row.id,"employee_id":row.employee_id,"employee_name":employee.full_name,"benefit_id":row.benefit_id,"benefit_name":benefit.name,"fixed_value":float(benefit.fixed_value),"percentage_value":float(benefit.percentage_value),"status":row.status,"requested_by":requester.full_name,"comment":row.comment,"created_at":row.created_at.isoformat() if row.created_at else None}

@router.get("/benefits/requests")
def list_benefit_requests(company_id:int|None=None,db:Session=Depends(get_db),user:User=Depends(current_user)):
 q=select(EmployeeBenefit).order_by(EmployeeBenefit.created_at.desc(),EmployeeBenefit.id.desc())
 if user.role=="employee": q=q.where(EmployeeBenefit.employee_id==user.employee_id)
 elif user.role=="hr_manager": q=q.where(EmployeeBenefit.company_id==user.company_id)
 elif company_id: q=q.where(EmployeeBenefit.company_id==company_id)
 if company_id: company_access(user,company_id)
 return [employee_benefit_out(db,r) for r in db.scalars(q.limit(200)).all()]

@router.post("/benefits/requests",status_code=201)
def request_benefit(payload:BenefitRequestInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
 employee=db.get(Employee,payload.employee_id);benefit=db.get(BenefitCatalog,payload.benefit_id)
 if not employee or not benefit: raise HTTPException(status_code=404,detail="Funcionário ou benefício não encontrado")
 employee_access(user,employee)
 if user.role not in ("employee","hr_manager"): raise HTTPException(status_code=403,detail="Use o perfil de funcionário ou RH para associar um benefício")
 if employee.company_id!=benefit.company_id or benefit.environment!="production": raise HTTPException(status_code=422,detail="A solicitação deve usar um benefício publicado em produção para a mesma empresa")
 if not benefit.active or benefit.effective_from>date.today() or (benefit.group_id and benefit.group_id not in employee_group_ids(db,employee)): raise HTTPException(status_code=422,detail="Benefício indisponível ou funcionário fora do grupo elegível")
 if not active_config(db,employee.company_id,"benefits","production")["enabled"]: raise HTTPException(status_code=422,detail="Benefícios estão desabilitados para esta empresa")
 if user.role=="employee" and not active_config(db,employee.company_id,"benefits","production")["allow_employee_requests"]: raise HTTPException(status_code=403,detail="Solicitações diretas de benefícios estão desabilitadas")
 existing=db.scalar(select(EmployeeBenefit).where(EmployeeBenefit.employee_id==employee.id,EmployeeBenefit.benefit_id==benefit.id))
 if existing: raise HTTPException(status_code=409,detail="Já existe uma associação ou solicitação para este benefício")
 row=EmployeeBenefit(company_id=employee.company_id,employee_id=employee.id,benefit_id=benefit.id,status="pending_approval" if benefit.approval_required else "active",requested_by_id=user.id)
 db.add(row);db.flush()
 if row.status=="pending_approval": processes.start(db,"benefits",employee.company_id,employee,user,{"benefit_id":benefit.id,"benefit_name":benefit.name},reference_id=row.id,auto_advance=False)
 audit(db,employee.company_id,user,f"{employee.full_name} solicitou o benefício {benefit.name}.");db.commit();db.refresh(row)
 return employee_benefit_out(db,row)

@router.post("/benefits/requests/{request_id}/decision")
def decide_benefit(request_id:int,payload:BenefitDecisionInput,db:Session=Depends(get_db),user:User=Depends(current_user)):
 row=db.get(EmployeeBenefit,request_id)
 if not row: raise HTTPException(status_code=404,detail="Solicitação não encontrada")
 if user.role=="hr_manager": company_access(user,row.company_id)
 if row.status!="pending_approval": raise HTTPException(status_code=409,detail="A solicitação já foi analisada")
 req=db.scalar(select(processes.ProcessRequest).where(processes.ProcessRequest.process=="benefits",processes.ProcessRequest.reference_id==row.id,processes.ProcessRequest.status=="pending_approval").limit(1))
 if not req: req=processes.start(db,"benefits",row.company_id,db.get(Employee,row.employee_id),db.get(User,row.requested_by_id) or user,{"benefit_id":row.benefit_id},reference_id=row.id,auto_advance=False)
 processes.decide(db,req,user,payload.approved,payload.comment)
 audit(db,row.company_id,user,f"Solicitação de benefício {row.status}.");db.commit();db.refresh(row)
 return employee_benefit_out(db,row)
