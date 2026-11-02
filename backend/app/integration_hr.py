"""Catalog-driven, company-scoped integration for existing HR settings."""
import copy
from datetime import date, datetime, timezone
from uuid import UUID
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Company, HRModuleConfiguration as Config, HRModuleConfigurationHistory as History, IntegrationOperation, Activity
from app.hr_api import DEFAULTS, validate_config, time_evaluation
from app.schemas import TimeSimulationInput
from app.integration_api import router, allowed_company, catalog_data
from app.integration_execution import execution_auth, digest, snapshot_hash, plan_hash, operation_out, RollbackInput

NAMES = {"admission":"Admissão", "timekeeping":"Controle de Ponto", "payroll":"Folha demonstrativa", "benefits":"Benefícios"}
LABELS = {"enabled":"Processo habilitado", "min_advance_days":"Antecedência mínima em dias", "max_advance_days":"Antecedência máxima em dias", "allow_weekends":"Permitir finais de semana", "allow_holidays":"Permitir feriados", "approval_required":"Aprovação obrigatória", "documents_required":"Documentos obrigatórios", "allow_retroactive":"Permitir solicitações retroativas", "daily_hours":"Jornada diária em horas", "min_break_minutes":"Intervalo mínimo em minutos", "late_tolerance_minutes":"Tolerância de atraso em minutos", "allow_overtime":"Permitir horas extras", "max_overtime_hours":"Limite diário de horas extras", "manager_approval_required":"Aprovação do RH obrigatória", "allow_manual_adjustment":"Permitir ajuste manual de ponto", "currency":"Moeda de apresentação", "show_demo_warning":"Exibir aviso de folha demonstrativa", "allow_employee_requests":"Permitir solicitação pelo funcionário"}
BOUNDS = {"daily_hours":(1,16),"min_break_minutes":(0,300),"late_tolerance_minutes":(0,120),"max_overtime_hours":(0,8)}

def check_module(module):
    if module not in NAMES:
        raise HTTPException(404, "Módulo de parametrização não disponível no HCM")

@router.get("/modules/{module}/catalog")
def catalog(module: str):
    check_module(module)
    fields=[]
    for key, value in DEFAULTS[module].items():
        kind="boolean" if type(value) is bool else "integer" if type(value) is int else "string"
        field={"id":key,"label":LABELS.get(key,key),"type":kind}
        if kind=="integer":
            if key in BOUNDS: field.update(minimum=BOUNDS[key][0],maximum=BOUNDS[key][1])
            else: field["minimum"]=0 if key=="min_advance_days" else 1
        fields.append(field)
    return {"version":module+"-1","erp_version":"0.1.0","module":module,"module_label":NAMES[module],"fields":fields,
        "rules":["Configuração completa por empresa, sem exceção por grupo neste módulo.", "A produção só muda pelo processo de publicação aprovado.", "Admissão: a antecedência mínima não pode ser maior que a máxima."],
        "available_modules":catalog_data()["available_modules"],
        "capabilities":{"apply_homologation":True,"automatic_tests":True,"rollback":True,"production_write":False}}

def flat(row, company, module, environment):
    return {"id":row.id if row else None,"scope_key":f"company:{company.id}:default","company_id":company.id,"group_id":None,
        "version":row.version if row else 0,"effective_from":row.effective_from.isoformat() if row else "0001-01-01",
        "environment":environment,"active":True,**(row.values if row else DEFAULTS[module])}

@router.get("/modules/{module}/snapshot")
def snapshot(module: str, company_code: str, as_of: date = Query(default_factory=date.today), db: Session = Depends(get_db)):
    check_module(module); company=allowed_company(db,company_code)
    schedules={}; effective={}
    for env in ("homologation","production"):
        rows=db.scalars(select(Config).where(Config.company_id==company.id,Config.module==module,Config.environment==env,Config.revoked.is_(False)).order_by(Config.version)).all()
        schedules[env]=[flat(None,company,module,env),*[flat(r,company,module,env) for r in rows]]
        current=max((r for r in rows if r.effective_from<=as_of),key=lambda r:(r.effective_from,r.version),default=None)
        effective[env]=[{"group_id":None,"group_name":"Regra geral","configuration":flat(current,company,module,env)}]
    return {"company":{"id":company.id,"code":company.code,"name":company.trade_name},"module":module,"as_of":as_of.isoformat(),"consulted_at":datetime.now(timezone.utc).isoformat(),"catalog_version":module+"-1","erp_version":"0.1.0","groups":[],"schedules":schedules,"effective":effective}

def values_from(module, item, company=None, effective=None):
    keys=set(DEFAULTS[module]); metadata={"company_id","environment","group_id","effective_from"}
    if set(item)!=keys|metadata:
        raise HTTPException(422,"O plano deve informar todos os parâmetros do catálogo, sem campos extras")
    if item["environment"]!="homologation" or item["group_id"] is not None:
        raise HTTPException(403,"Este módulo é configurado por empresa, em homologação")
    if company and item["company_id"]!=company.id:
        raise HTTPException(403,"O plano contém outra empresa")
    if effective and item["effective_from"]!=effective.isoformat():
        raise HTTPException(403,"A vigência difere da aprovada")
    values={k:item[k] for k in keys}
    errors=validate_config(module,values)
    if errors: raise HTTPException(422,errors)
    return values

@router.post("/modules/{module}/validate")
def validate(module: str, payload: dict, db: Session = Depends(get_db)):
    check_module(module)
    company=db.get(Company,payload.get("company_id"))
    if not company: raise HTTPException(404,"Empresa não encontrada")
    values_from(module,payload,company)
    return {"valid":True,"errors":[],"persisted":False}

class ApplyInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    operation_id: UUID
    approved_by: str=Field(min_length=1,max_length=100)
    company_code: str=Field(min_length=1,max_length=20)
    expected_fingerprint: str=Field(pattern=r"^[a-f0-9]{64}$")
    approval_hash: str=Field(pattern=r"^[a-f0-9]{64}$")
    effective_from: date
    configurations: list[dict]=Field(max_length=1)

def report_for(module, before, after, payloads):
    fields=list(DEFAULTS[module])
    def schedules(snap):
        latest={}
        for row in snap["schedules"]["homologation"]:
            latest[(row["scope_key"],row["effective_from"]) ]={k:row[k] for k in fields}
        return latest
    expected=schedules(before)
    for item in payloads: expected[(f"company:{item['company_id']}:default",item["effective_from"]) ]={k:item[k] for k in fields}
    current=after["effective"]["homologation"][0]["configuration"]
    values={k:current[k] for k in fields}
    eligible=[(day,v) for (scope,day),v in expected.items() if day<=after["as_of"]]
    effective=max(eligible,key=lambda item:item[0])[1]
    checks=[{"name":"Valores e vigências correspondem à proposta","passed":expected==schedules(after)},
            {"name":"Regra efetiva corresponde à proposta","passed":values==effective},
            {"name":"Produção preservada","passed":before["schedules"]["production"]==after["schedules"]["production"]},
            {"name":"Validador oficial do módulo","passed":not validate_config(module,values)}]
    if module=="timekeeping":
        # The same evaluator used by the real timekeeping simulator, with synthetic inputs.
        probes=[]
        for hours in (0,1):
            start=date(2026,10,26)
            end_minutes=values["daily_hours"]*60+values["min_break_minutes"]+hours*60
            if end_minutes>=24*60: continue
            payload=TimeSimulationInput(employee_id=1,environment="homologation",work_date=start,start_time="00:00",end_time=f"{end_minutes//60:02}:{end_minutes%60:02}",break_minutes=values["min_break_minutes"])
            result=time_evaluation(payload,values)
            expected_allowed=hours==0 or (values["allow_overtime"] and hours<=values["max_overtime_hours"])
            probes.append({"overtime_hours":hours,"allowed":result["allowed"],"expected_allowed":expected_allowed})
        checks.append({"name":"Regra de horas extras no motor do HCM","passed":all(p["allowed"]==p["expected_allowed"] for p in probes),"probes":probes})
    return {"passed":all(c["passed"] for c in checks),"checks":[{**c,"origin":"API e validador oficial do HCM"} for c in checks],"limits":"Verificações dos parâmetros e regras implementadas no módulo; sem criar registros pessoais ou publicar produção."}

def get_operation(db, module, operation_id):
    check_module(module)
    row=db.get(IntegrationOperation,str(operation_id))
    if not row or row.payload.get("module")!=module or row.payload.get("contract") not in (None,1): raise HTTPException(404,"Operação não encontrada neste módulo")
    return row

@router.post("/modules/{module}/apply", dependencies=[Depends(execution_auth)])
def apply(module: str, payload: ApplyInput, db: Session = Depends(get_db)):
    check_module(module); company=allowed_company(db,payload.company_code)
    db.scalar(select(Company).where(Company.id==company.id).with_for_update())
    packet={**payload.model_dump(mode="json"),"module":module}; request_hash=digest(packet)
    existing=db.get(IntegrationOperation,str(payload.operation_id))
    if existing:
        if existing.company_id!=company.id or existing.request_hash!=request_hash: raise HTTPException(409,"Identificador utilizado por outro plano")
        return operation_out(existing)
    if plan_hash(payload.expected_fingerprint,payload.configurations)!=payload.approval_hash: raise HTTPException(409,"Plano diferente da aprovação")
    before=snapshot(module,company.code,payload.effective_from,db)
    if snapshot_hash(before)!=payload.expected_fingerprint: raise HTTPException(409,"O HCM mudou; faça nova análise")
    for item in payload.configurations: values_from(module,item,company,payload.effective_from)
    row=IntegrationOperation(id=str(payload.operation_id),company_id=company.id,request_hash=request_hash,status="applying",approved_by=payload.approved_by,payload=packet,before_snapshot=before,after_snapshot={},report={},created_ids=[])
    db.add(row);db.flush()
    try:
        with db.begin_nested():
            created=[]
            for item in payload.configurations:
                previous=db.scalar(select(Config).where(Config.company_id==company.id,Config.module==module,Config.environment=="homologation").order_by(Config.version.desc()).limit(1))
                config=Config(company_id=company.id,module=module,environment="homologation",version=previous.version+1 if previous else 1,effective_from=payload.effective_from,values=values_from(module,item),created_by=None)
                db.add(config);db.flush();created.append(config.id)
                db.add(History(company_id=company.id,module=module,environment="homologation",version=config.version,actor_id=None,actor_name="ERP AI · "+payload.approved_by,snapshot={"effective_from":payload.effective_from.isoformat(),"values":config.values,"operation_id":row.id}))
            after=snapshot(module,company.code,payload.effective_from,db)
            report=report_for(module,before,after,payload.configurations)
            if not report["passed"]: raise ValueError("Verificações automáticas não passaram")
        row.created_ids=created;row.after_snapshot=after;row.report=report;row.status="applied"
    except ValueError as exc:
        row.status="failed";row.after_snapshot=before;row.report={"passed":False,"error":str(exc),"rolled_back":True,"checks":[]}
    db.add(Activity(company_id=company.id,actor="ERP AI · "+payload.approved_by,message=f"Configuração de {module}: {row.status}; operação {row.id}."))
    db.commit();db.refresh(row);return operation_out(row)

@router.get("/modules/{module}/operations/{operation_id}")
def operation(module: str, operation_id: UUID, db: Session=Depends(get_db)):
    return operation_out(get_operation(db,module,operation_id))

@router.get("/modules/{module}/operations/{operation_id}/verify")
def verify(module: str, operation_id: UUID, db: Session=Depends(get_db)):
    row=get_operation(db,module,operation_id);company=db.get(Company,row.company_id)
    current=snapshot(module,company.code,date.fromisoformat(row.payload["effective_from"]),db)
    return {"operation_id":row.id,"status":row.status,"report":report_for(module,row.before_snapshot,current,row.payload["configurations"]),"current_snapshot":current}

@router.post("/modules/{module}/operations/{operation_id}/rollback", dependencies=[Depends(execution_auth)])
def rollback(module: str, operation_id: UUID, payload: RollbackInput, db: Session=Depends(get_db)):
    row=get_operation(db,module,operation_id)
    company=db.scalar(select(Company).where(Company.id==row.company_id).with_for_update());db.refresh(row)
    if row.status=="reverted": return operation_out(row)
    if row.status!="applied": raise HTTPException(409,"Somente uma aplicação concluída pode ser revertida")
    current=snapshot(module,company.code,date.fromisoformat(row.payload["effective_from"]),db)
    if snapshot_hash(current)!=payload.expected_fingerprint or snapshot_hash(current)!=snapshot_hash(row.after_snapshot): raise HTTPException(409,"O HCM mudou depois da aplicação")
    for config_id in row.created_ids:
        config=db.get(Config,config_id);config.revoked=True
    db.flush()
    restored=snapshot(module,company.code,date.fromisoformat(row.payload["effective_from"]),db)
    if snapshot_hash(restored)!=snapshot_hash(row.before_snapshot):
        db.rollback();raise HTTPException(409,"A reversão não restaurou o estado anterior")
    for config_id in row.created_ids:
        config=db.get(Config,config_id)
        db.add(History(company_id=company.id,module=module,environment="homologation",version=config.version,actor_id=None,actor_name="ERP AI · Reversão",snapshot={"operation_id":row.id,"action":"rollback","reason":payload.reason,"restored":restored["effective"]}))
    row.status="reverted";row.report={**row.report,"rollback":{"passed":True,"reason":payload.reason,"restored_snapshot":restored}}
    db.add(Activity(company_id=company.id,actor="ERP AI · "+row.approved_by,message=f"Reversão de {module}: operação {row.id}."))
    db.commit();db.refresh(row);return operation_out(row)
