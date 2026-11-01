from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Activity, BenefitCatalog, Company, Department, Employee, EmployeeGroup, HRModuleConfiguration, HRModuleConfigurationHistory, PayrollEvent, Position, SalaryRange, TransportLine, VacationConfiguration, VacationConfigurationHistory

COMPANY_SEEDS = [
    {"code":"EMP001","legal_name":"Empresa ABC Tecnologia Ltda.","trade_name":"ABC Tecnologia","cnpj":"00.000.000/0001-01","city":"São Paulo","state":"SP","sector":"Tecnologia","departments":["Administrativo","Financeiro","Tecnologia","Comercial"]},
    {"code":"EMP002","legal_name":"Indústria Brasil S.A.","trade_name":"Indústria Brasil","cnpj":"00.000.000/0002-02","city":"Belo Horizonte","state":"MG","sector":"Indústria","departments":["Produção","Recursos Humanos","Operações","Financeiro"]},
]

def seed_demo_data(db: Session):
    """Fictitious companies and employees only. No login is ever created here: the single
    administrator account comes from `python -m app.bootstrap_admin`."""
    if db.scalar(select(Company.id).limit(1)):
        seed_vacation_configurations(db)
        seed_hr_modules(db)
        seed_company_setup(db)
        return
    for company_index, data in enumerate(COMPANY_SEEDS):
        company = Company(**{key: data[key] for key in ("code","legal_name","trade_name","cnpj","city","state","sector")}, status="Ativa")
        db.add(company)
        db.flush()
        departments = [Department(company_id=company.id, name=name) for name in data["departments"]]
        positions = [Position(company_id=company.id, title=title) for title in ["Analista","Assistente","Especialista","Coordenador"]]
        groups = [EmployeeGroup(company_id=company.id, name=name) for name in ["Administrativo","Operacional","Gestores"]]
        db.add_all(departments + positions + groups)
        db.flush()
        total = 30 if company_index == 0 else 50
        employees = []
        for i in range(total):
            employees.append(Employee(
                company_id=company.id, department_id=departments[i % len(departments)].id,
                position_id=positions[i % len(positions)].id, group_id=groups[2 if i == 0 else i % 2].id,
                registration=f"{company.code[-3:]}-{i + 1:04d}",
                full_name=("João Silva" if i == 0 and company_index == 0 else f"Colaborador {i + 1:02d} - {company.trade_name}"),
                email=("joao.silva@empresaabc.example.com" if i == 0 and company_index == 0 else f"colaborador{i+1:02d}@{company.code.lower()}.example.com"),
                admission_date=date.today() - timedelta(days=365 + i * 17),
                salary=5000 + (i % 10) * 350, status="Ativo"))
        db.add_all(employees)
        db.flush()
        for employee in employees[1:]:
            employee.manager_id = employees[0].id
        db.add(Activity(company_id=company.id, actor="HCM Lab", message=f"Dados fictícios iniciais carregados para {company.trade_name}."))
    db.add(Activity(company_id=None, actor="HCM Lab", message="Ambiente demonstrativo iniciado com duas empresas fictícias."))
    db.commit()
    seed_vacation_configurations(db)
    seed_hr_modules(db)
    seed_company_setup(db)

def seed_company_setup(db: Session):
    """Fictitious salary table and transport lines so the new processes can be exercised."""
    for company in db.scalars(select(Company).where(Company.code.in_(("EMP001", "EMP002")))).all():
        if not db.scalar(select(TransportLine.id).where(TransportLine.company_id == company.id).limit(1)):
            db.add_all([TransportLine(company_id=company.id, code="ONIBUS-MUN", description="Ônibus municipal (fictício)", fare=5.00),
                        TransportLine(company_id=company.id, code="METRO", description="Metrô/trem (fictício)", fare=5.20)])
        if not db.scalar(select(SalaryRange.id).where(SalaryRange.company_id == company.id).limit(1)):
            for position in db.scalars(select(Position).where(Position.company_id == company.id)).all():
                db.add_all([SalaryRange(company_id=company.id, position_id=position.id, level="Júnior", min_salary=3000, max_salary=6000),
                            SalaryRange(company_id=company.id, position_id=position.id, level="Pleno", min_salary=6000.01, max_salary=10000)])
    db.commit()

def seed_vacation_configurations(db: Session):
    system_values={
        "process_enabled":True,"employee_request_enabled":True,"manager_request_enabled":True,
        "manager_approval_required":True,"simulation_enabled":True,"installments_allowed":True,
        "max_installments":3,"min_advance_days":15,"allow_13th_advance":False,
        "show_balance_to_employee":True,
    }
    if not db.scalar(select(VacationConfiguration.id).where(VacationConfiguration.scope_key=="system",VacationConfiguration.environment=="production").limit(1)):
        rules=[VacationConfiguration(scope_key="system",environment="production",company_id=None,group_id=None,version=1,effective_from=date.today(),**system_values)]
        for code,values in [
            ("EMP001",{**system_values,"max_installments":2,"min_advance_days":30,"employee_request_enabled":True}),
            ("EMP002",{**system_values,"max_installments":3,"min_advance_days":45,"employee_request_enabled":False}),
        ]:
            company=db.scalar(select(Company).where(Company.code==code))
            if company: rules.append(VacationConfiguration(scope_key=f"company:{company.id}:default",environment="production",company_id=company.id,group_id=None,version=1,effective_from=date.today(),**values))
        db.add_all(rules);db.flush()
        for rule in rules:
            db.add(VacationConfigurationHistory(company_id=rule.company_id,group_id=rule.group_id,config_id=rule.id,version=rule.version,environment="production",actor_id=None,actor_name="HCM Lab — dados iniciais",before_snapshot=None,after_snapshot={"scope_key":rule.scope_key,"version":rule.version,"environment":"production","effective_from":rule.effective_from.isoformat(),**{key:getattr(rule,key) for key in system_values}}))
    # Every company starts with its own rule copied from the system default, so it can be
    # parameterized and published like the demonstration companies.
    system=db.scalar(select(VacationConfiguration).where(VacationConfiguration.scope_key=="system",VacationConfiguration.environment=="production").order_by(VacationConfiguration.version.desc()).limit(1))
    if system:
        for company in db.scalars(select(Company)).all():
            scope=f"company:{company.id}:default"
            if db.scalar(select(VacationConfiguration.id).where(VacationConfiguration.scope_key==scope,VacationConfiguration.environment=="production").limit(1)): continue
            values={key:getattr(system,key) for key in system_values}
            rule=VacationConfiguration(scope_key=scope,environment="production",company_id=company.id,group_id=None,version=1,effective_from=date.today(),**values)
            db.add(rule);db.flush()
            db.add(VacationConfigurationHistory(company_id=company.id,group_id=None,config_id=rule.id,version=1,environment="production",actor_id=None,actor_name="HCM Lab — padrão do sistema",before_snapshot=None,after_snapshot={"scope_key":scope,"version":1,"environment":"production","effective_from":rule.effective_from.isoformat(),**values}))
    production=db.scalars(select(VacationConfiguration).where(VacationConfiguration.environment=="production").order_by(VacationConfiguration.scope_key,VacationConfiguration.version.desc())).all()
    production_by_scope={}
    for row in production: production_by_scope.setdefault(row.scope_key,row)
    for scope,source in production_by_scope.items():
        if db.scalar(select(VacationConfiguration.id).where(VacationConfiguration.scope_key==scope,VacationConfiguration.environment=="homologation").limit(1)): continue
        values={key:getattr(source,key) for key in system_values}
        clone=VacationConfiguration(scope_key=source.scope_key,environment="homologation",company_id=source.company_id,group_id=source.group_id,version=1,effective_from=source.effective_from,active=source.active,**values)
        db.add(clone);db.flush()
        db.add(VacationConfigurationHistory(company_id=clone.company_id,group_id=clone.group_id,config_id=clone.id,version=clone.version,environment="homologation",actor_id=None,actor_name="HCM Lab — baseline de homologação",before_snapshot=None,after_snapshot={"scope_key":clone.scope_key,"version":clone.version,"environment":"homologation","effective_from":clone.effective_from.isoformat(),**values}))
    db.commit()

def seed_hr_modules(db: Session):
    defaults={
        "admission":{"enabled":True,"min_advance_days":5,"max_advance_days":90,"allow_weekends":False,"allow_holidays":False,"approval_required":True,"documents_required":True,"allow_retroactive":False},
        "timekeeping":{"daily_hours":8,"min_break_minutes":60,"late_tolerance_minutes":10,"allow_overtime":True,"max_overtime_hours":2,"manager_approval_required":True,"allow_manual_adjustment":True,"allow_weekends":False},
        "payroll":{"enabled":True,"currency":"BRL","show_demo_warning":True},
        "benefits":{"enabled":True,"allow_employee_requests":True},
    }
    for company in db.scalars(select(Company)).all():
        for module,values in defaults.items():
            prod=db.scalar(select(HRModuleConfiguration).where(HRModuleConfiguration.company_id==company.id,HRModuleConfiguration.module==module,HRModuleConfiguration.environment=="production").order_by(HRModuleConfiguration.version.desc()).limit(1))
            if not prod:
                prod=HRModuleConfiguration(company_id=company.id,module=module,environment="production",version=1,effective_from=date.today(),values=values)
                db.add(prod);db.flush()
                db.add(HRModuleConfigurationHistory(company_id=company.id,module=module,environment="production",version=1,actor_id=None,actor_name="HCM Lab — dados iniciais",snapshot={"effective_from":date.today().isoformat(),"values":values}))
            if not db.scalar(select(HRModuleConfiguration.id).where(HRModuleConfiguration.company_id==company.id,HRModuleConfiguration.module==module,HRModuleConfiguration.environment=="homologation").limit(1)):
                clone=HRModuleConfiguration(company_id=company.id,module=module,environment="homologation",version=1,effective_from=prod.effective_from,values=prod.values)
                db.add(clone);db.flush()
                db.add(HRModuleConfigurationHistory(company_id=company.id,module=module,environment="homologation",version=1,actor_id=None,actor_name="HCM Lab — baseline de homologação",snapshot={"effective_from":clone.effective_from.isoformat(),"values":clone.values}))
    abc=db.scalar(select(Company).where(Company.code=="EMP001"))
    industry=db.scalar(select(Company).where(Company.code=="EMP002"))
    if abc and not db.scalar(select(PayrollEvent.id).where(PayrollEvent.company_id==abc.id,PayrollEvent.environment=="production").limit(1)):
        db.add_all([
            PayrollEvent(company_id=abc.id,environment="production",code="BONUS10",description="Bônus demonstrativo 10%",kind="earning",calculation="percentage",value=10,base="salary",active=True),
            PayrollEvent(company_id=abc.id,environment="production",code="DEMO-DESC",description="Desconto demonstrativo fixo",kind="deduction",calculation="fixed",value=50,base="salary",active=True),
        ])
    db.flush()
    if abc:
        operational_group=db.scalar(select(EmployeeGroup).where(EmployeeGroup.company_id==abc.id,EmployeeGroup.name=="Operacional"))
        manager_group=db.scalar(select(EmployeeGroup).where(EmployeeGroup.company_id==abc.id,EmployeeGroup.name=="Gestores"))
        if not db.scalar(select(PayrollEvent.id).where(PayrollEvent.company_id==abc.id,PayrollEvent.environment=="production",PayrollEvent.code=="COMISSAO-OPS").limit(1)):
            db.add(PayrollEvent(company_id=abc.id,environment="production",code="COMISSAO-OPS",description="Comissão didática Operacional 5%",kind="earning",calculation="percentage",value=5,base="salary",group_id=operational_group.id if operational_group else None,active=True))
        if not db.scalar(select(BenefitCatalog.id).where(BenefitCatalog.company_id==abc.id,BenefitCatalog.environment=="production",BenefitCatalog.code=="EDUCACAO-GESTORES").limit(1)):
            db.add(BenefitCatalog(company_id=abc.id,environment="production",code="EDUCACAO-GESTORES",name="Auxílio-educação demonstrativo",description="Cenário didático de benefício elegível ao grupo Gestores.",fixed_value=180,percentage_value=0,group_id=manager_group.id if manager_group else None,approval_required=True,effective_from=date.today(),active=True))
        group=db.scalar(select(EmployeeGroup).where(EmployeeGroup.company_id==abc.id,EmployeeGroup.name=="Administrativo"))
        if not db.scalar(select(BenefitCatalog.id).where(BenefitCatalog.company_id==abc.id,BenefitCatalog.environment=="production",BenefitCatalog.code=="ALIMENTACAO").limit(1)):
            db.add(BenefitCatalog(company_id=abc.id,environment="production",code="ALIMENTACAO",name="Auxílio-alimentação demonstrativo",description="Valor mensal fictício elegível ao grupo Administrativo.",fixed_value=700,percentage_value=0,group_id=group.id if group else None,approval_required=True,effective_from=date.today(),active=True))
        if not db.scalar(select(BenefitCatalog.id).where(BenefitCatalog.company_id==abc.id,BenefitCatalog.environment=="production",BenefitCatalog.code=="MOBILIDADE").limit(1)):
            db.add(BenefitCatalog(company_id=abc.id,environment="production",code="MOBILIDADE",name="Auxílio-mobilidade demonstrativo",description="Benefício fictício disponível a todos os grupos.",fixed_value=350,percentage_value=0,group_id=None,approval_required=True,effective_from=date.today(),active=True))
    if industry and not db.scalar(select(BenefitCatalog.id).where(BenefitCatalog.company_id==industry.id,BenefitCatalog.environment=="production",BenefitCatalog.code=="TRANSPORTE").limit(1)):
        db.add(BenefitCatalog(company_id=industry.id,environment="production",code="TRANSPORTE",name="Auxílio-transporte demonstrativo",description="Valor mensal fictício para treinamento.",fixed_value=250,percentage_value=0,group_id=None,approval_required=False,effective_from=date.today(),active=True))
    db.flush()
    for company in db.scalars(select(Company)).all():
        prod_events=db.scalars(select(PayrollEvent).where(PayrollEvent.company_id==company.id,PayrollEvent.environment=="production")).all()
        for event in prod_events:
            if not db.scalar(select(PayrollEvent.id).where(PayrollEvent.company_id==company.id,PayrollEvent.environment=="homologation",PayrollEvent.code==event.code).limit(1)):
                db.add(PayrollEvent(company_id=company.id,environment="homologation",version=event.version,code=event.code,description=event.description,kind=event.kind,calculation=event.calculation,value=event.value,base=event.base,group_id=event.group_id,active=event.active))
        prod_benefits=db.scalars(select(BenefitCatalog).where(BenefitCatalog.company_id==company.id,BenefitCatalog.environment=="production")).all()
        for benefit in prod_benefits:
            if not db.scalar(select(BenefitCatalog.id).where(BenefitCatalog.company_id==company.id,BenefitCatalog.environment=="homologation",BenefitCatalog.code==benefit.code).limit(1)):
                db.add(BenefitCatalog(company_id=company.id,environment="homologation",version=benefit.version,code=benefit.code,name=benefit.name,description=benefit.description,fixed_value=benefit.fixed_value,percentage_value=benefit.percentage_value,group_id=benefit.group_id,approval_required=benefit.approval_required,effective_from=benefit.effective_from,active=benefit.active))
    db.commit()
