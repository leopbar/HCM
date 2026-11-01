from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.hr_api import company_access
from app.policies import DEFAULTS, normalize, validate_config
from app.models import (Activity, BenefitCatalog, Company, EmployeeGroup, EnvironmentPublication,
    HRModuleConfiguration, HRModuleConfigurationHistory, PayrollEvent, User,
    VacationConfiguration, VacationConfigurationHistory)
from app.schemas import PublicationDecisionInput, PublicationRequestInput
from app.security import require_roles
from app.vacations import CONFIG_FIELDS, config_errors, config_snapshot, latest_scope_config

router=APIRouter(prefix="/api/environments",tags=["Ambientes e publicação"])
MODULES=tuple(DEFAULTS)
VACATION_FIELDS=("process_enabled","employee_request_enabled","manager_request_enabled","manager_approval_required","simulation_enabled","installments_allowed","max_installments","min_advance_days","allow_13th_advance","show_balance_to_employee")

def _effective_vacation(db:Session,company_id:int,environment:str):
    rows=db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id==company_id,VacationConfiguration.environment==environment,VacationConfiguration.revoked.is_(False),VacationConfiguration.effective_from<=date.today()).order_by(VacationConfiguration.effective_from.desc(),VacationConfiguration.version.desc())).all()
    found={}
    for row in rows: found.setdefault(row.scope_key,row)
    return found

def _effective_module_configs(db:Session,company_id:int,environment:str):
    rows=db.scalars(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id==company_id,HRModuleConfiguration.environment==environment,HRModuleConfiguration.revoked.is_(False),HRModuleConfiguration.effective_from<=date.today()).order_by(HRModuleConfiguration.effective_from.desc(),HRModuleConfiguration.version.desc())).all()
    found={}
    for row in rows: found.setdefault(row.module,row)
    return found

def _configured_vacation(db:Session,company_id:int,environment:str):
    rows=db.scalars(select(VacationConfiguration).where(VacationConfiguration.company_id==company_id,VacationConfiguration.environment==environment,VacationConfiguration.revoked.is_(False)).order_by(VacationConfiguration.scope_key,VacationConfiguration.effective_from.desc(),VacationConfiguration.version.desc())).all()
    found={}
    for row in rows: found.setdefault((row.scope_key,row.effective_from.isoformat()),row)
    return found

def _configured_module_configs(db:Session,company_id:int,environment:str):
    rows=db.scalars(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id==company_id,HRModuleConfiguration.environment==environment,HRModuleConfiguration.revoked.is_(False)).order_by(HRModuleConfiguration.module,HRModuleConfiguration.effective_from.desc(),HRModuleConfiguration.version.desc())).all()
    found={}
    for row in rows: found.setdefault((row.module,row.effective_from.isoformat()),row)
    return found

def _snapshot(db:Session,company_id:int):
    vacation=[config_snapshot(r) for r in _configured_vacation(db,company_id,"homologation").values()]
    modules={}
    for (module,_),row in _configured_module_configs(db,company_id,"homologation").items():
        modules.setdefault(module,[]).append({"effective_from":row.effective_from.isoformat(),"version":row.version,"values":row.values})
    events=db.scalars(select(PayrollEvent).where(PayrollEvent.company_id==company_id,PayrollEvent.environment=="homologation").order_by(PayrollEvent.code)).all()
    benefits=db.scalars(select(BenefitCatalog).where(BenefitCatalog.company_id==company_id,BenefitCatalog.environment=="homologation").order_by(BenefitCatalog.code)).all()
    return {
        "company_id":company_id,"source_environment":"homologation","created_at":datetime.now(timezone.utc).isoformat(),
        "vacation":vacation,"modules":modules,
        "payroll_events":[{"code":e.code,"description":e.description,"kind":e.kind,"calculation":e.calculation,"value":float(e.value),"base":e.base,"group_id":e.group_id,"active":e.active} for e in events],
        "benefits":[{"code":b.code,"name":b.name,"description":b.description,"fixed_value":float(b.fixed_value),"percentage_value":float(b.percentage_value),"group_id":b.group_id,"approval_required":b.approval_required,"effective_from":b.effective_from.isoformat(),"active":b.active} for b in benefits],
    }

def _validate_snapshot(snapshot:dict):
    report=[]
    vacation_checks=[]
    for row in snapshot["vacation"]:
        values={key:row[key] for key in CONFIG_FIELDS}
        errors=config_errors(values)
        vacation_checks.append({"scope":row["scope_key"],"passed":not errors,"errors":errors})
    report.append({"module":"vacations","passed":all(x["passed"] for x in vacation_checks) and bool(vacation_checks),"checks":vacation_checks or [{"passed":False,"errors":["Não há regras de férias de empresa ou grupo em homologação."]}]})
    for module in MODULES:
        rows=snapshot["modules"].get(module,[])
        checks=[]
        for row in rows or [{"effective_from":None,"values":DEFAULTS[module]}]:
            errors=validate_config(module,row["values"])
            checks.append({"effective_from":row["effective_from"],"passed":not errors,"errors":errors or ["Parâmetros e dependências válidos."]})
        report.append({"module":module,"passed":all(c["passed"] for c in checks),"checks":checks})
    event_codes=[e["code"] for e in snapshot["payroll_events"]]
    event_errors=["Há códigos de eventos de folha duplicados."] if len(event_codes)!=len(set(event_codes)) else []
    report.append({"module":"payroll_events","passed":not event_errors,"checks":[{"passed":not event_errors,"errors":event_errors or [f"{len(event_codes)} evento(s) verificado(s)."]}]})
    benefit_codes=[b["code"] for b in snapshot["benefits"]]
    benefit_errors=["Há códigos de benefícios duplicados."] if len(benefit_codes)!=len(set(benefit_codes)) else []
    report.append({"module":"benefits_catalog","passed":not benefit_errors,"checks":[{"passed":not benefit_errors,"errors":benefit_errors or [f"{len(benefit_codes)} benefício(s) verificado(s)."]}]})
    return report

def _diff(label:str,before:dict,after:dict,sources:tuple[str,str]):
    """One row per field of the configuration in force; only fields whose values differ are marked as changed."""
    rows=[]
    for key in sorted(set(before)|set(after)):
        h=before.get(key);p=after.get(key)
        rows.append({"parameter":f"{label}.{key}","homologation":h,"production":p,"changed":h!=p,"homologation_source":sources[0],"production_source":sources[1]})
    return rows

def _since(row)->str:
    return f"v{row.version} · vigente desde {row.effective_from.strftime('%d/%m/%Y')}"

def _vacation_in_force(db:Session,company_id:int,scope_key:str,environment:str):
    """Rule in force today for the scope, following the same fallback as resolve_effective_config:
    named configuration → nothing; group → company → system default; company → system default."""
    today=date.today()
    own=latest_scope_config(db,scope_key,today,environment)
    if own: return own,_since(own)
    if ":config:" in scope_key: return None,"Não cadastrada neste ambiente"
    if ":group:" in scope_key:
        company=latest_scope_config(db,f"company:{company_id}:default",today,environment)
        if company: return company,f"Herdada da regra da empresa ({_since(company)})"
    system=latest_scope_config(db,"system",today,environment)
    if system: return system,f"Padrão do sistema ({_since(system)})"
    return None,"Sem regra vigente"

def _vacation_label(db:Session,scope_key:str)->str:
    if scope_key.endswith(":default"): return "férias.empresa"
    kind,ref=scope_key.split(":")[2:4]
    if kind=="group":
        group=db.get(EmployeeGroup,int(ref))
        return f"férias.grupo {group.name if group else ref}"
    return f"férias.configuração {ref}"

def _comparison(db:Session,company_id:int):
    rows=[]
    scopes=set(db.scalars(select(VacationConfiguration.scope_key).where(VacationConfiguration.company_id==company_id,VacationConfiguration.revoked.is_(False))).all())
    scopes.add(f"company:{company_id}:default")
    for scope in sorted(scopes,key=lambda s:(not s.endswith(":default"),s)):
        h,h_src=_vacation_in_force(db,company_id,scope,"homologation");p,p_src=_vacation_in_force(db,company_id,scope,"production")
        fields=VACATION_FIELDS+(("name","simulation_text") if ":config:" in scope else ())
        rows.extend(_diff(_vacation_label(db,scope),{k:getattr(h,k) for k in fields} if h else {k:None for k in fields},{k:getattr(p,k) for k in fields} if p else {k:None for k in fields},(h_src,p_src)))
    hom_m=_effective_module_configs(db,company_id,"homologation");prod_m=_effective_module_configs(db,company_id,"production")
    for module in MODULES:
        h=hom_m.get(module);p=prod_m.get(module)
        # Nothing in force means the module runs with the system defaults (same rule as active_config).
        h_values=normalize(module,h.values) if h else DEFAULTS[module];p_values=normalize(module,p.values) if p else DEFAULTS[module]
        rows.extend(_diff(module,h_values,p_values,(_since(h) if h else "Padrão do sistema",_since(p) if p else "Padrão do sistema")))
    for model,label in ((PayrollEvent,"eventos_folha"),(BenefitCatalog,"benefícios")):
        hom={r.code:r for r in db.scalars(select(model).where(model.company_id==company_id,model.environment=="homologation")).all()}
        prod={r.code:r for r in db.scalars(select(model).where(model.company_id==company_id,model.environment=="production")).all()}
        # version is a per-environment counter (publication bumps it in production), not a parameter.
        columns=[c for c in model.__table__.columns.keys() if c not in ("id","company_id","environment","version")]
        def data(row):
            return {c:(getattr(row,c).isoformat() if isinstance(getattr(row,c),date) else getattr(row,c)) for c in columns} if row else {c:None for c in columns}
        for code in sorted(set(hom)|set(prod)):
            h=hom.get(code);p=prod.get(code)
            rows.extend(_diff(f"{label}.{code}",data(h),data(p),(f"v{h.version}" if h else "Não cadastrado neste ambiente",f"v{p.version}" if p else "Não cadastrado neste ambiente")))
    return rows

@router.get("/compare")
def compare_environments(company_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant","hr_manager"))):
    company_access(user,company_id)
    company=db.get(Company,company_id)
    if not company: raise HTTPException(status_code=404,detail="Empresa não encontrada")
    changes=_comparison(db,company_id)
    return {"company":{"id":company.id,"code":company.code,"name":company.trade_name},"homologation":"Homologação","production":"Produção","changes":changes,"has_changes":any(row["changed"] for row in changes),"changed_count":sum(row["changed"] for row in changes)}

@router.post("/validate")
def validate_environment(company_id:int,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant"))):
    company_access(user,company_id)
    if not db.get(Company,company_id): raise HTTPException(status_code=404,detail="Empresa não encontrada")
    snapshot=_snapshot(db,company_id);report=_validate_snapshot(snapshot)
    return {"company_id":company_id,"environment":"homologation","passed":all(r["passed"] for r in report),"report":report}

def _publication_out(db,row):
    requester=db.get(User,row.requested_by_id);decision=db.get(User,row.decided_by_id) if row.decided_by_id else None
    return {"id":row.id,"company_id":row.company_id,"company_name":db.get(Company,row.company_id).trade_name,"status":row.status,"requested_by":requester.full_name if requester else "","decided_by":decision.full_name if decision else None,"validation_report":row.validation_report,"decision_comment":row.decision_comment,"created_at":row.created_at.isoformat() if row.created_at else None,"decided_at":row.decided_at.isoformat() if row.decided_at else None}

@router.post("/publications",status_code=201)
def request_publication(payload:PublicationRequestInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant"))):
    company=db.get(Company,payload.company_id)
    if not company: raise HTTPException(status_code=404,detail="Empresa não encontrada")
    snapshot=_snapshot(db,company.id);report=_validate_snapshot(snapshot)
    if not all(r["passed"] for r in report): raise HTTPException(status_code=422,detail={"message":"A homologação possui validações pendentes.","report":report})
    row=EnvironmentPublication(company_id=company.id,requested_by_id=user.id,status="pending_approval",snapshot=snapshot,validation_report=report)
    db.add(row);db.add(Activity(company_id=company.id,actor=user.full_name,message="Publicação de homologação solicitada para produção."));db.flush()
    from app.processes import notify
    admins=db.scalars(select(User).where(User.role=="system_admin",User.active.is_(True),User.id!=user.id)).all()
    notify(db,admins,f"Publicação nº {row.id} de {company.trade_name} aguarda sua aprovação em Ambientes e Publicação (solicitada por {user.full_name}).")
    db.commit();db.refresh(row)
    return _publication_out(db,row)

def _copy_to_production(db:Session,publication:EnvironmentPublication,actor:User):
    db.scalar(select(Company).where(Company.id==publication.company_id).with_for_update())
    snapshot=publication.snapshot;company_id=publication.company_id
    for source in snapshot["vacation"]:
        latest=db.scalar(select(VacationConfiguration).where(VacationConfiguration.scope_key==source["scope_key"],VacationConfiguration.environment=="production").order_by(VacationConfiguration.version.desc()).limit(1))
        same=db.scalar(select(VacationConfiguration).where(VacationConfiguration.scope_key==source["scope_key"],VacationConfiguration.environment=="production",VacationConfiguration.effective_from==date.fromisoformat(source["effective_from"])).order_by(VacationConfiguration.version.desc()).limit(1))
        if same and all(getattr(same,key)==source[key] for key in VACATION_FIELDS): continue
        config=VacationConfiguration(scope_key=source["scope_key"],environment="production",company_id=company_id,group_id=source.get("group_id"),version=(latest.version+1 if latest else 1),effective_from=date.fromisoformat(source["effective_from"]),created_by=actor.id,config_code=source.get("config_code"),name=source.get("name"),simulation_text=source.get("simulation_text"),**{key:source[key] for key in VACATION_FIELDS})
        db.add(config);db.flush()
        db.add(VacationConfigurationHistory(company_id=company_id,group_id=config.group_id,config_id=config.id,version=config.version,environment="production",actor_id=actor.id,actor_name=actor.full_name,before_snapshot=config_snapshot(latest) if latest else None,after_snapshot=config_snapshot(config)))
    for module,schedules in snapshot["modules"].items():
      for data in schedules:
        latest=db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id==company_id,HRModuleConfiguration.module==module,HRModuleConfiguration.environment=="production").order_by(HRModuleConfiguration.version.desc()).limit(1))
        same=db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id==company_id,HRModuleConfiguration.module==module,HRModuleConfiguration.environment=="production",HRModuleConfiguration.effective_from==date.fromisoformat(data["effective_from"])).order_by(HRModuleConfiguration.version.desc()).limit(1))
        if same and same.values==data["values"]: continue
        row=HRModuleConfiguration(company_id=company_id,module=module,environment="production",version=(latest.version+1 if latest else 1),effective_from=date.fromisoformat(data["effective_from"]),values=data["values"],created_by=actor.id)
        db.add(row);db.flush()
        db.add(HRModuleConfigurationHistory(company_id=company_id,module=module,environment="production",version=row.version,actor_id=actor.id,actor_name=actor.full_name,snapshot={"published_from_homologation_version":data["version"],"effective_from":data["effective_from"],"values":data["values"]}))
    published_event_codes=set()
    for item in snapshot["payroll_events"]:
        published_event_codes.add(item["code"])
        row=db.scalar(select(PayrollEvent).where(PayrollEvent.company_id==company_id,PayrollEvent.environment=="production",PayrollEvent.code==item["code"]))
        if row:
            if any(getattr(row,key)!=value for key,value in item.items()):
                for key,value in item.items(): setattr(row,key,value)
                row.version+=1
        else: db.add(PayrollEvent(company_id=company_id,environment="production",**item))
    for row in db.scalars(select(PayrollEvent).where(PayrollEvent.company_id==company_id,PayrollEvent.environment=="production")).all():
        if row.code not in published_event_codes: row.active=False
    published_benefit_codes=set()
    for item in snapshot["benefits"]:
        published_benefit_codes.add(item["code"])
        row=db.scalar(select(BenefitCatalog).where(BenefitCatalog.company_id==company_id,BenefitCatalog.environment=="production",BenefitCatalog.code==item["code"]))
        values={**item,"effective_from":date.fromisoformat(item["effective_from"])}
        if row:
            if any(getattr(row,key)!=value for key,value in values.items()):
                for key,value in values.items(): setattr(row,key,value)
                row.version+=1
        else: db.add(BenefitCatalog(company_id=company_id,environment="production",**values))
    for row in db.scalars(select(BenefitCatalog).where(BenefitCatalog.company_id==company_id,BenefitCatalog.environment=="production")).all():
        if row.code not in published_benefit_codes: row.active=False

@router.post("/publications/{publication_id}/decision")
def decide_publication(publication_id:int,payload:PublicationDecisionInput,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin"))):
    row=db.get(EnvironmentPublication,publication_id)
    if not row: raise HTTPException(status_code=404,detail="Solicitação de publicação não encontrada")
    if row.status!="pending_approval": raise HTTPException(status_code=409,detail="A solicitação já foi decidida")
    if not payload.approved and not payload.comment.strip(): raise HTTPException(status_code=422,detail="Informe o motivo da rejeição")
    if payload.approved:
        report=_validate_snapshot(row.snapshot)
        if not all(r["passed"] for r in report): raise HTTPException(status_code=422,detail={"message":"A versão enviada não passou nas validações.","report":report})
        try:
            _copy_to_production(db,row,user)
            row.status="published";row.validation_report=report;row.decided_by_id=user.id;row.decided_at=datetime.now(timezone.utc);row.decision_comment=payload.comment.strip() or None
            db.add(Activity(company_id=row.company_id,actor=user.full_name,message=f"Publicação {row.id} aprovada e aplicada em transação à produção."))
            db.commit()
        except Exception:
            db.rollback();raise
    else:
        row.status="rejected";row.decided_by_id=user.id;row.decided_at=datetime.now(timezone.utc);row.decision_comment=payload.comment.strip()
        db.add(Activity(company_id=row.company_id,actor=user.full_name,message=f"Publicação {row.id} rejeitada: {payload.comment.strip()}"));db.commit()
    db.refresh(row);return _publication_out(db,row)

@router.get("/publications")
def list_publications(company_id:int|None=None,db:Session=Depends(get_db),user:User=Depends(require_roles("system_admin","consultant","hr_manager"))):
    q=select(EnvironmentPublication).order_by(EnvironmentPublication.created_at.desc(),EnvironmentPublication.id.desc())
    if user.role=="hr_manager":
        if company_id and company_id!=user.company_id: raise HTTPException(status_code=403,detail="Acesso restrito à empresa vinculada ao seu perfil")
        q=q.where(EnvironmentPublication.company_id==user.company_id)
    elif company_id:
        company_access(user,company_id);q=q.where(EnvironmentPublication.company_id==company_id)
    return [_publication_out(db,row) for row in db.scalars(q.limit(100)).all()]
