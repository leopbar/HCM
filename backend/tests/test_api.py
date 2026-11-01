from datetime import date, timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import get_db
from app.main import app
from app.models import Base, Company, Department, Employee, EmployeeGroup, Position, User, VacationConfiguration
from app.security import hash_password
from app.vacations import next_valid_start

@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = TestingSession()
    companies = [
        Company(code="EMP001", legal_name="ABC Ltda", trade_name="ABC", cnpj="00.000.000/0001-01", city="São Paulo", state="SP", sector="Tecnologia", status="Ativa"),
        Company(code="EMP002", legal_name="Indústria S.A.", trade_name="Indústria", cnpj="00.000.000/0002-02", city="Belo Horizonte", state="MG", sector="Indústria", status="Ativa"),
    ]
    db.add_all(companies); db.flush()
    refs=[]
    for company in companies:
        department=Department(company_id=company.id,name="Administrativo")
        position=Position(company_id=company.id,title="Analista")
        group=EmployeeGroup(company_id=company.id,name="Gestores")
        db.add_all([department,position,group]);db.flush()
        employee=Employee(company_id=company.id,department_id=department.id,position_id=position.id,group_id=group.id,registration=f"{company.code}-1",full_name=f"Pessoa {company.code}",email=f"pessoa@{company.code.lower()}.example.com",admission_date=date(2020,1,1),salary=5000,status="Ativo")
        db.add(employee);db.flush();refs.append((company,employee))
        db.add(User(email=f"rh@{company.code.lower()}.example.com",full_name="Gestor RH",password_hash=hash_password("hcm123"),role="hr_manager",company_id=company.id))
    config_values={"process_enabled":True,"employee_request_enabled":True,"manager_request_enabled":True,"manager_approval_required":True,"simulation_enabled":True,"installments_allowed":True,"allow_13th_advance":False,"show_balance_to_employee":True,"created_by":None}
    db.add(VacationConfiguration(scope_key="system",environment="production",company_id=None,group_id=None,version=1,effective_from=date.today(),max_installments=3,min_advance_days=15,**config_values))
    db.add(VacationConfiguration(scope_key="system",environment="homologation",company_id=None,group_id=None,version=1,effective_from=date.today(),max_installments=3,min_advance_days=15,**config_values))
    for index,(company,employee) in enumerate(refs):
        base={"max_installments":(2 if index==0 else 3),"min_advance_days":(30 if index==0 else 45),"employee_request_enabled":(index==0),**{k:v for k,v in config_values.items() if k!="employee_request_enabled"}}
        db.add(VacationConfiguration(scope_key=f"company:{company.id}:default",environment="production",company_id=company.id,group_id=None,version=1,effective_from=date.today(),**base))
        db.add(VacationConfiguration(scope_key=f"company:{company.id}:default",environment="homologation",company_id=company.id,group_id=None,version=1,effective_from=date.today(),**base))
    db.add(User(email="consultor@hcmlab.example.com",full_name="Consultor",password_hash=hash_password("hcm123"),role="consultant"))
    db.add(User(email="admin@hcmlab.example.com",full_name="Administrador",password_hash=hash_password("hcm123"),role="system_admin"))
    db.add(User(email="pessoa@emp001.example.com",full_name=refs[0][1].full_name,password_hash=hash_password("hcm123"),role="employee",company_id=refs[0][0].id,employee_id=refs[0][1].id))
    db.commit()
    def override_get_db():
        session=TestingSession()
        try: yield session
        finally: session.close()
    app.dependency_overrides[get_db]=override_get_db
    yield TestClient(app), refs
    app.dependency_overrides.clear();db.close();Base.metadata.drop_all(engine);engine.dispose()

def auth(client, email):
    result=client.post("/api/auth/login",json={"email":email,"password":"hcm123"})
    assert result.status_code==200
    client.cookies.clear()  # each helper call acts only through its explicit bearer token
    return {"Authorization":f"Bearer {result.json()['access_token']}"}

def first_login(client, email, temporary, final):
    """Accounts created by the administrator must replace the temporary password first."""
    login = client.post("/api/auth/login", json={"email": email, "password": temporary})
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is True
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    client.cookies.clear()
    assert client.get("/api/employees", headers=headers).status_code == 403
    assert client.post("/api/access/password", headers=headers, json={"current_password": temporary, "new_password": final}).status_code == 200
    login = client.post("/api/auth/login", json={"email": email, "password": final})
    assert login.status_code == 200 and login.json()["user"]["must_change_password"] is False
    client.cookies.clear()
    return login, {"Authorization": "Bearer " + login.json()["access_token"]}

def test_individual_accounts_groups_and_password_revocation(client):
    http, refs = client
    admin = auth(http, "admin@hcmlab.example.com")
    company, employee = refs[1]
    group = http.post("/api/access/groups", headers=admin, json={"company_id": company.id, "name": "Somente férias", "modules": ["vacations"]})
    assert group.status_code == 201
    payload = {"full_name": employee.full_name, "email": "individual@example.com", "role": "employee", "company_id": company.id, "employee_id": employee.id, "access_group_id": group.json()["id"]}
    created = http.post("/api/access/users", headers=admin, json={**payload, "password": "Ignorada#Pelo2026"})
    assert created.status_code == 201
    assert "password" not in created.json() and "password_hash" not in created.json()
    payload["password"] = created.json()["temporary_password"]  # generated by the system, never by the admin
    assert http.post("/api/auth/login", json={"email": payload["email"], "password": "Ignorada#Pelo2026"}).status_code == 401
    login, headers = first_login(http, payload["email"], payload["password"], "Ferias#Segura2026x")
    payload["password"] = "Ferias#Segura2026x"
    assert login.json()["user"]["allowed_modules"] == ["vacations"]
    assert http.get("/api/hr/benefits/catalog", params={"company_id": company.id}, headers=headers).status_code == 403
    assert http.get("/api/access/users", headers=headers).status_code == 403
    assert http.get("/api/companies/999/options", headers=headers).status_code == 403
    assert len(http.get("/api/employees", headers=headers).json()) == 1
    assert http.post("/api/access/password", headers=headers, json={"current_password": "wrong", "new_password": "Alterada#Senha2026"}).status_code == 422
    assert http.post("/api/access/password", headers=headers, json={"current_password": payload["password"], "new_password": "Alterada#Senha2026"}).status_code == 200
    assert http.get("/api/auth/me", headers=headers).status_code == 401
    assert http.post("/api/auth/login", json={"email": payload["email"], "password": payload["password"]}).status_code == 401
    assert http.post("/api/auth/login", json={"email": payload["email"], "password": "Alterada#Senha2026"}).status_code == 200
    reset = http.patch(f"/api/access/users/{created.json()['id']}", headers=admin, json={"active": True, "access_group_id": group.json()["id"], "reset_password": True})
    assert reset.status_code == 200 and reset.json()["must_change_password"]
    assert http.post("/api/auth/login", json={"email": payload["email"], "password": "Alterada#Senha2026"}).status_code == 401
    first_login(http, payload["email"], reset.json()["temporary_password"], "Redefinida#Senha2026")
    assert "temporary_password" not in http.patch(f"/api/access/users/{created.json()['id']}", headers=admin, json={"active": True, "access_group_id": group.json()["id"]}).json()

def test_access_group_company_binding_and_disabled_accounts(client):
    http, refs = client
    admin = auth(http, "admin@hcmlab.example.com")
    group = http.post("/api/access/groups", headers=admin, json={"company_id": refs[0][0].id, "name": "Empresa A", "modules": []}).json()
    payload = {"full_name": "Gestor novo", "email": "new-manager@example.com", "role": "hr_manager", "company_id": refs[1][0].id, "access_group_id": group["id"]}
    assert http.post("/api/access/users", headers=admin, json=payload).status_code == 422
    payload["access_group_id"] = None
    created = http.post("/api/access/users", headers=admin, json=payload)
    assert created.status_code == 201
    _, headers = first_login(http, payload["email"], created.json()["temporary_password"], "Gestao#Definitiva26")
    assert http.get("/api/auth/me", headers=headers).status_code == 200
    assert http.patch(f"/api/access/users/{created.json()['id']}", headers=admin, json={"active": False, "access_group_id": None}).status_code == 200
    assert http.get("/api/auth/me", headers=headers).status_code == 401

def test_login_and_protected_routes(client):
    http,_=client
    assert http.get("/api/companies").status_code==401
    headers=auth(http,"consultor@hcmlab.example.com")
    assert len(http.get("/api/companies",headers=headers).json())==2

def test_company_isolation_for_hr_manager(client):
    http,refs=client
    headers=auth(http,"rh@emp001.example.com")
    assert len(http.get("/api/companies",headers=headers).json())==1
    assert http.get("/api/employees",headers=headers).json()[0]["company_id"]==refs[0][0].id
    response=http.get(f"/api/companies/{refs[1][0].id}/options",headers=headers)
    assert response.status_code==403

def test_company_creation_requires_system_admin(client):
    http,_=client
    headers=auth(http,"consultor@hcmlab.example.com")
    payload={"code":"EMP003","legal_name":"Nova Ltda","trade_name":"Nova","cnpj":"11.222.333/0001-81","city":"Curitiba","state":"PR","sector":"Serviços","status":"Ativa"}
    assert http.post("/api/companies",json=payload,headers=headers).status_code==403

def test_admin_can_create_company_and_hr_can_create_employee(client):
    http,refs=client
    admin=auth(http,"admin@hcmlab.example.com")
    company_payload={"code":"EMP003","legal_name":"Nova Ltda","trade_name":"Nova","cnpj":"11.222.333/0001-81","city":"Curitiba","state":"PR","sector":"Serviços","status":"Ativa"}
    created_company=http.post("/api/companies",json=company_payload,headers=admin)
    assert created_company.status_code==201
    assert created_company.json()["employee_count"]==0

    hr=auth(http,"rh@emp001.example.com")
    company_id=refs[0][0].id
    options=http.get(f"/api/companies/{company_id}/options",headers=hr).json()
    employee_payload={"company_id":company_id,"department_id":options["departments"][0]["id"],"position_id":options["positions"][0]["id"],"group_id":options["groups"][0]["id"],"registration":"NEW-0001","full_name":"Pessoa de Teste","email":"pessoa.teste@example.com","admission_date":"2026-01-15","salary":4500,"status":"Ativo"}
    created_employee=http.post("/api/employees",json=employee_payload,headers=hr)
    assert created_employee.status_code==201
    assert created_employee.json()["full_name"]=="Pessoa de Teste"
    employee_payload["company_id"]=refs[1][0].id
    assert http.post("/api/employees",json=employee_payload,headers=hr).status_code==403

def test_vacation_simulation_uses_company_rules(client):
    http,refs=client
    consultant=auth(http,"consultor@hcmlab.example.com")
    payload={"employee_id":refs[0][1].id,"start_date":next_valid_start(date.today()+timedelta(days=60)).isoformat(),"requested_days":10,"installment_count":3,"advance_13th_requested":False,"requester_type":"manager"}
    abc=http.post("/api/vacations/simulate",json=payload,headers=consultant).json()
    assert abc["allowed"] is False
    assert "no máximo 2 parcelas" in " ".join(abc["errors"])
    assert abc["origin"]["scope"]=="company"
    payload["employee_id"]=refs[1][1].id
    industry=http.post("/api/vacations/simulate",json=payload,headers=consultant).json()
    assert industry["allowed"] is True
    assert industry["effective_configuration"]["max_installments"]==3

def test_vacation_request_keeps_the_configuration_version_snapshot(client):
    http,refs=client
    consultant=auth(http,"consultor@hcmlab.example.com")
    company_id=refs[0][0].id
    base={"company_id":company_id,"group_id":None,"effective_from":date.today().isoformat(),"process_enabled":True,"employee_request_enabled":True,"manager_request_enabled":True,"manager_approval_required":True,"simulation_enabled":True,"installments_allowed":True,"max_installments":3,"min_advance_days":30,"allow_13th_advance":False,"show_balance_to_employee":True}
    saved=http.put("/api/vacations/configurations",json=base,headers=consultant)
    assert saved.status_code==200 and saved.json()["version"]==2
    manager=auth(http,"rh@emp001.example.com")
    request_payload={"employee_id":refs[0][1].id,"start_date":next_valid_start(date.today()+timedelta(days=60)).isoformat(),"requested_days":10,"installment_count":2,"advance_13th_requested":False,"requester_type":"manager"}
    created=http.post("/api/vacations/requests",json=request_payload,headers=manager)
    assert created.status_code==201
    assert created.json()["status"]=="pending_approval"
    assert created.json()["config_version"]==1
    base["max_installments"]=1;base["installments_allowed"]=False
    later=http.put("/api/vacations/configurations",json=base,headers=consultant)
    assert later.status_code==200 and later.json()["version"]==3
    listed=http.get("/api/vacations/requests",headers=manager).json()
    assert listed[0]["rules_snapshot"]["max_installments"]==2
    history=http.get(f"/api/vacations/history?company_id={company_id}&environment=homologation",headers=consultant).json()
    assert [row["version"] for row in history[:2]]==[3,2]

def test_employee_vacation_flow_requires_and_records_manager_approval(client):
    http,refs=client
    employee=auth(http,"pessoa@emp001.example.com")
    payload={"employee_id":refs[1][1].id,"start_date":next_valid_start(date.today()+timedelta(days=60)).isoformat(),"requested_days":5,"installment_count":1,"advance_13th_requested":False,"requester_type":"employee"}
    forbidden=http.post("/api/vacations/requests",json=payload,headers=employee)
    assert forbidden.status_code==403
    payload["employee_id"]=refs[0][1].id
    created=http.post("/api/vacations/requests",json=payload,headers=employee)
    assert created.status_code==201 and created.json()["status"]=="pending_approval"
    manager=auth(http,"rh@emp001.example.com")
    approved=http.post(f"/api/vacations/requests/{created.json()['id']}/decision",json={"approved":True,"comment":"Validado na demonstração"},headers=manager)
    assert approved.status_code==200 and approved.json()["status"]=="approved"

def test_group_rule_precedes_company_rule_and_invalid_dependencies_are_rejected(client):
    http,refs=client
    consultant=auth(http,"consultor@hcmlab.example.com")
    group_id=refs[0][1].group_id
    group_rule={"company_id":refs[0][0].id,"group_id":group_id,"effective_from":date.today().isoformat(),"process_enabled":True,"employee_request_enabled":True,"manager_request_enabled":True,"manager_approval_required":True,"simulation_enabled":True,"installments_allowed":False,"max_installments":1,"min_advance_days":30,"allow_13th_advance":False,"show_balance_to_employee":True}
    group_rule["environment"]="homologation"
    saved=http.put("/api/vacations/configurations",json=group_rule,headers=consultant)
    assert saved.status_code==200
    effective=http.get(f"/api/vacations/effective?employee_id={refs[0][1].id}&environment=homologation",headers=consultant).json()
    assert effective["origin"]["scope"]=="group"
    assert "prevalece" in effective["why"]
    group_rule["installments_allowed"]=False;group_rule["max_installments"]=3
    invalid=http.put("/api/vacations/configurations",json=group_rule,headers=consultant)
    assert invalid.status_code==422

def test_future_vacation_configuration_does_not_apply_early(client):
    http,refs=client
    consultant=auth(http,"consultor@hcmlab.example.com")
    future={"company_id":refs[0][0].id,"environment":"homologation","group_id":None,"effective_from":(date.today()+timedelta(days=30)).isoformat(),"process_enabled":True,"employee_request_enabled":True,"manager_request_enabled":True,"manager_approval_required":True,"simulation_enabled":True,"installments_allowed":True,"max_installments":3,"min_advance_days":30,"allow_13th_advance":False,"show_balance_to_employee":True}
    response=http.put("/api/vacations/configurations",json=future,headers=consultant)
    assert response.status_code==200
    current=http.get(f"/api/vacations/effective?employee_id={refs[0][1].id}&environment=homologation",headers=consultant).json()
    assert current["configuration"]["max_installments"]==2
    assert current["configuration"]["version"]==1

def test_pending_vacation_reserves_balance_and_rejection_releases_it(client):
    http,refs=client
    employee=auth(http,"pessoa@emp001.example.com")
    start=next_valid_start(date.today()+timedelta(days=60)).isoformat()
    payload={"employee_id":refs[0][1].id,"start_date":start,"requested_days":10,"installment_count":1,"advance_13th_requested":False,"requester_type":"employee"}
    created=http.post("/api/vacations/requests",json=payload,headers=employee)
    assert created.status_code==201
    effective=http.get(f"/api/vacations/effective?employee_id={refs[0][1].id}",headers=employee).json()
    assert effective["balance_days"]==20
    overlap=http.post("/api/vacations/simulate",json=payload,headers=employee).json()
    assert overlap["allowed"] is False
    assert any("sobrepõe" in message for message in overlap["errors"])
    manager=auth(http,"rh@emp001.example.com")
    decision=http.post(f"/api/vacations/requests/{created.json()['id']}/decision",json={"approved":False,"comment":"Conflito de escala"},headers=manager)
    assert decision.status_code==200 and decision.json()["status"]=="rejected"
    effective_after=http.get(f"/api/vacations/effective?employee_id={refs[0][1].id}",headers=employee).json()
    assert effective_after["balance_days"]==30

def test_admission_rules_reject_short_notice_then_allow_and_approve(client):
    http,refs=client
    consultant=auth(http,"consultor@hcmlab.example.com")
    company_id=refs[0][0].id
    values={"enabled":True,"min_advance_days":5,"max_advance_days":90,"allow_weekends":False,"allow_holidays":False,"approval_required":True,"documents_required":True,"allow_retroactive":False}
    saved=http.put("/api/hr/configurations",json={"company_id":company_id,"module":"admission","effective_from":date.today().isoformat(),"values":values},headers=consultant)
    assert saved.status_code==200 and saved.json()["version"]==1
    options=http.get(f"/api/companies/{company_id}/options",headers=consultant).json()
    payload={"company_id":company_id,"candidate_name":"Candidato Demonstrativo","candidate_email":"candidato@example.com","department_id":options["departments"][0]["id"],"position_id":options["positions"][0]["id"],"planned_date":(date.today()+timedelta(days=2)).isoformat(),"documents_complete":True,"group_id":options["groups"][0]["id"],"salary":4000,"registration":"ADM-0001"}
    short=http.post("/api/hr/admissions",json=payload,headers=auth(http,"rh@emp001.example.com"))
    assert short.status_code==422 and "antecedência mínima de 5 dias" in str(short.json()["detail"])
    payload["planned_date"]=(date.today()+timedelta(days=10)).isoformat()
    created=http.post("/api/hr/admissions",json=payload,headers=auth(http,"rh@emp001.example.com"))
    assert created.status_code==201 and created.json()["status"]=="pending_approval"
    approved=http.post(f"/api/hr/admissions/{created.json()['id']}/decision",json={"approved":True,"comment":"Documentos conferidos"},headers=auth(http,"rh@emp001.example.com"))
    assert approved.status_code==200 and approved.json()["status"]=="approved"

def test_timekeeping_simulation_persists_manual_adjustment_and_approval(client):
    http,refs=client
    employee_id=refs[0][1].id
    employee=auth(http,"pessoa@emp001.example.com")
    manager=auth(http,"rh@emp001.example.com")
    payload={"employee_id":employee_id,"work_date":"2026-10-27","start_time":"09:15","end_time":"19:00","break_minutes":60}
    result=http.post("/api/hr/timekeeping/simulate",json=payload,headers=employee)
    assert result.status_code==200 and result.json()["allowed"] is True
    assert result.json()["late_minutes"]==5 and result.json()["overtime_hours"]==0.75
    created=http.post("/api/hr/timekeeping/records",json=payload,headers=employee)
    assert created.status_code==201 and created.json()["status"]=="pending_approval"
    decision=http.post(f"/api/hr/timekeeping/records/{created.json()['id']}/decision",json={"approved":True},headers=manager)
    assert decision.status_code==200 and decision.json()["status"]=="approved"
    unjustified=http.patch(f"/api/hr/timekeeping/records/{created.json()['id']}",json={**payload,"start_time":"09:00","end_time":"18:00"},headers=manager)
    assert unjustified.status_code==422
    adjusted=http.patch(f"/api/hr/timekeeping/records/{created.json()['id']}",json={**payload,"start_time":"09:00","end_time":"18:00","justification":"Marcação corrigida conforme registro do relógio"},headers=manager)
    # An already approved record goes back to approval and keeps the adjustment trail.
    assert adjusted.status_code==200 and adjusted.json()["status"]=="pending_approval" and adjusted.json()["worked_hours"]==8
    assert adjusted.json()["adjustments"][0]["before"]["status"]=="approved"
    weekend={**payload,"work_date":"2026-10-31"}
    assert http.post("/api/hr/timekeeping/simulate",json=weekend,headers=employee).json()["allowed"] is False

def test_payroll_events_are_applied_to_demo_simulation(client):
    http,refs=client
    company_id=refs[0][0].id;employee_id=refs[0][1].id
    hr=auth(http,"consultor@hcmlab.example.com")
    event={"company_id":company_id,"code":"BONUS10","description":"Bônus demonstrativo","kind":"earning","calculation":"percentage","value":10,"base":"salary","group_id":None,"active":True}
    assert http.post("/api/hr/payroll/events",json=event,headers=hr).status_code==201
    run=http.post("/api/hr/payroll/simulate",json={"company_id":company_id,"competence":"2026-10","employee_id":employee_id},headers=hr)
    assert run.status_code==200
    assert run.json()["results"][0]["gross"]==5500
    assert "não calcula encargos" in run.json()["warning"]

def test_benefit_group_eligibility_request_and_approval(client):
    http,refs=client
    company_id=refs[0][0].id;employee_id=refs[0][1].id;group_id=refs[0][1].group_id
    consultant=auth(http,"consultor@hcmlab.example.com")
    benefit={"company_id":company_id,"code":"ALIM-DEMO","name":"Auxílio Alimentação Demo","description":"Valor fictício mensal","fixed_value":700,"percentage_value":0,"group_id":group_id,"approval_required":True,"effective_from":date.today().isoformat(),"active":True}
    created_benefit=http.post("/api/hr/benefits/catalog",json=benefit,headers=consultant)
    assert created_benefit.status_code==201
    assert created_benefit.json()["environment"]=="homologation"
    publication=http.post("/api/environments/publications",json={"company_id":company_id},headers=consultant)
    assert publication.status_code==201 and publication.json()["status"]=="pending_approval"
    admin=auth(http,"admin@hcmlab.example.com")
    published=http.post(f"/api/environments/publications/{publication.json()['id']}/decision",json={"approved":True},headers=admin)
    assert published.status_code==200 and published.json()["status"]=="published"
    employee=auth(http,"pessoa@emp001.example.com")
    published_benefits=http.get(f"/api/hr/benefits/catalog?company_id={company_id}&environment=production",headers=employee).json()
    production_benefit=next(row for row in published_benefits if row["code"]=="ALIM-DEMO")
    requested=http.post("/api/hr/benefits/requests",json={"employee_id":employee_id,"benefit_id":production_benefit["id"]},headers=employee)
    assert requested.status_code==201 and requested.json()["status"]=="pending_approval"
    manager=auth(http,"rh@emp001.example.com")
    decision=http.post(f"/api/hr/benefits/requests/{requested.json()['id']}/decision",json={"approved":True},headers=manager)
    assert decision.status_code==200 and decision.json()["status"]=="active"
    duplicate=http.post("/api/hr/benefits/requests",json={"employee_id":employee_id,"benefit_id":production_benefit["id"]},headers=employee)
    assert duplicate.status_code==409

def test_hr_module_configuration_and_employee_data_remain_company_scoped(client):
    http,refs=client
    employee=auth(http,"pessoa@emp001.example.com")
    visible=http.get(f"/api/hr/configurations/timekeeping?company_id={refs[0][0].id}",headers=employee)
    assert visible.status_code==200
    forbidden=http.get(f"/api/hr/configurations/timekeeping?company_id={refs[1][0].id}",headers=employee)
    assert forbidden.status_code==403
    assert http.get(f"/api/hr/benefits/catalog?company_id={refs[1][0].id}",headers=employee).status_code==403

def test_homologation_publication_changes_phase4_admission_only_after_admin_approval(client):
    http,refs=client
    company_id=refs[0][0].id;consultant=auth(http,"consultor@hcmlab.example.com")
    values={"enabled":True,"min_advance_days":10,"max_advance_days":90,"allow_weekends":False,"allow_holidays":False,"approval_required":True,"documents_required":True,"allow_retroactive":False}
    payload={"company_id":company_id,"module":"admission","environment":"homologation","effective_from":date.today().isoformat(),"values":values}
    saved=http.put("/api/hr/configurations",json=payload,headers=consultant)
    assert saved.status_code==200 and saved.json()["environment"]=="homologation"
    production_write=http.put("/api/hr/configurations",json={**payload,"environment":"production"},headers=consultant)
    assert production_write.status_code==403
    options=http.get(f"/api/companies/{company_id}/options",headers=consultant).json()
    planned=date.today()+timedelta(days=6)
    while planned.weekday()>=5 or (planned.month,planned.day) in {(1,1),(4,21),(5,1),(9,7),(10,12),(11,2),(11,15),(11,20),(12,25)}:
        planned+=timedelta(days=1)
    sim={"company_id":company_id,"candidate_name":"Teste Ambiente","candidate_email":"teste.ambiente@example.com","department_id":options["departments"][0]["id"],"position_id":options["positions"][0]["id"],"planned_date":planned.isoformat(),"documents_complete":True,"group_id":options["groups"][0]["id"],"salary":4000,"registration":"ADM-0002"}
    hom=http.post("/api/hr/admissions/simulate",json={**sim,"environment":"homologation"},headers=consultant).json()
    prod=http.post("/api/hr/admissions/simulate",json={**sim,"environment":"production"},headers=consultant).json()
    assert hom["allowed"] is False and prod["allowed"] is True
    comparison=http.get(f"/api/environments/compare?company_id={company_id}",headers=consultant).json()
    assert any(row["parameter"]=="admission.min_advance_days" and row["changed"] for row in comparison["changes"])
    validation=http.post(f"/api/environments/validate?company_id={company_id}",headers=consultant)
    assert validation.status_code==200 and validation.json()["passed"] is True
    publication=http.post("/api/environments/publications",json={"company_id":company_id},headers=consultant)
    assert publication.status_code==201 and publication.json()["status"]=="pending_approval"
    changed_after_request={**values,"min_advance_days":20}
    later={**payload,"values":changed_after_request}
    assert http.put("/api/hr/configurations",json=later,headers=consultant).status_code==200
    denied=http.post(f"/api/environments/publications/{publication.json()['id']}/decision",json={"approved":True},headers=consultant)
    assert denied.status_code==403
    admin=auth(http,"admin@hcmlab.example.com")
    decision=http.post(f"/api/environments/publications/{publication.json()['id']}/decision",json={"approved":True,"comment":"Homologação validada"},headers=admin)
    assert decision.status_code==200 and decision.json()["status"]=="published"
    active=http.get(f"/api/hr/configurations/admission?company_id={company_id}&environment=production",headers=consultant).json()
    assert active["effective"]["values"]["min_advance_days"]==10
    hom_after=http.get(f"/api/hr/configurations/admission?company_id={company_id}&environment=homologation",headers=consultant).json()
    assert hom_after["effective"]["values"]["min_advance_days"]==20
    manager=auth(http,"rh@emp001.example.com")
    rejected=http.post("/api/hr/admissions",json=sim,headers=manager)
    assert rejected.status_code==422 and "antecedência mínima de 10 dias" in str(rejected.json()["detail"])
    planned=date.today()+timedelta(days=20)
    while planned.weekday()>=5 or (planned.month,planned.day) in {(1,1),(4,21),(5,1),(9,7),(10,12),(11,2),(11,15),(11,20),(12,25)}:
        planned+=timedelta(days=1)
    sim["planned_date"]=planned.isoformat()
    created=http.post("/api/hr/admissions",json=sim,headers=manager)
    assert created.status_code==201 and created.json()["status"]=="pending_approval"

def test_json_configuration_preview_and_import_only_change_homologation(client):
    http,refs=client
    headers=auth(http,"consultor@hcmlab.example.com")
    company_id=refs[0][0].id
    package=http.get(f"/api/configurations/export?company_id={company_id}&module=vacations&environment=homologation",headers=headers).json()
    assert package["format"]=="hcm-lab-configuration"
    assert package["schema_version"]==1
    assert package["parameters"][0]["parameter_id"]=="vacation.rules"
    package["parameters"][0]["values"]["max_installments"]=3
    preview=http.post("/api/configurations/import/preview",headers=headers,json={"company_id":company_id,"package":package})
    assert preview.status_code==200
    assert preview.json()["valid"] is True
    assert any(row["action"]=="modified" for row in preview.json()["changes"])
    applied=http.post(f"/api/configurations/import/{preview.json()['preview_id']}/apply",headers=headers)
    assert applied.status_code==200
    hom=http.get(f"/api/vacations/configurations?company_id={company_id}&environment=homologation",headers=headers).json()
    prod=http.get(f"/api/vacations/configurations?company_id={company_id}&environment=production",headers=headers).json()
    assert hom["configurations"][0]["max_installments"]==3
    assert prod["configurations"][0]["max_installments"]==2
    assert http.post(f"/api/configurations/import/{preview.json()['preview_id']}/apply",headers=headers).status_code==409

def test_json_import_rejects_invalid_configuration_and_employee_cannot_import(client):
    http,refs=client
    headers=auth(http,"consultor@hcmlab.example.com")
    company_id=refs[0][0].id
    package=http.get(f"/api/configurations/export?company_id={company_id}&module=vacations&environment=homologation",headers=headers).json()
    package["parameters"][0]["values"]["max_installments"]=1
    preview=http.post("/api/configurations/import/preview",headers=headers,json={"company_id":company_id,"package":package})
    assert preview.status_code==200
    assert preview.json()["valid"] is False
    assert preview.json()["changes"][0]["action"]=="invalid"
    assert http.post(f"/api/configurations/import/{preview.json()['preview_id']}/apply",headers=headers).status_code==409
    employee=auth(http,"pessoa@emp001.example.com")
    assert http.get(f"/api/configurations/export?company_id={company_id}",headers=employee).status_code==403

def test_json_import_requires_a_fresh_preview_if_homologation_changed(client):
    http,refs=client
    headers=auth(http,"consultor@hcmlab.example.com")
    company_id=refs[0][0].id
    package=http.get(f"/api/configurations/export?company_id={company_id}&module=vacations&environment=homologation",headers=headers).json()
    preview=http.post("/api/configurations/import/preview",headers=headers,json={"company_id":company_id,"package":package}).json()
    changed={"company_id":company_id,"environment":"homologation","group_id":None,"effective_from":date.today().isoformat(),"process_enabled":True,"employee_request_enabled":True,"manager_request_enabled":True,"manager_approval_required":True,"simulation_enabled":True,"installments_allowed":True,"max_installments":3,"min_advance_days":25,"allow_13th_advance":False,"show_balance_to_employee":True}
    assert http.put("/api/vacations/configurations",headers=headers,json=changed).status_code==200
    applied=http.post(f"/api/configurations/import/{preview['preview_id']}/apply",headers=headers)
    assert applied.status_code==409
    assert "depois da prévia" in applied.json()["detail"]

def test_training_exercise_checks_real_rules_and_saves_progress(client):
    http,refs=client
    headers=auth(http,"consultor@hcmlab.example.com")
    company_id=refs[0][0].id
    invalid=http.post(f"/api/training/exercises/parameter-dependency/check?company_id={company_id}",headers=headers,json={})
    assert invalid.status_code==200
    assert invalid.json()["passed"] is True
    assert "1 e 16 horas" in invalid.json()["checks"][0]["details"]
    admission=http.post(f"/api/training/exercises/admission-weekends/check?company_id={company_id}",headers=headers,json={})
    assert admission.status_code==200
    assert admission.json()["passed"] is True
    progress=http.get(f"/api/training/exercises?company_id={company_id}",headers=headers).json()
    assert next(row for row in progress if row["id"]=="parameter-dependency")["status"]=="completed"

def test_full_json_package_includes_all_modules_and_passes_preview(client):
    http,refs=client
    headers=auth(http,"consultor@hcmlab.example.com")
    company_id=refs[0][0].id
    module_values={
        "admission":{"enabled":True,"min_advance_days":5,"max_advance_days":90,"allow_weekends":False,"allow_holidays":False,"approval_required":True,"documents_required":True,"allow_retroactive":False},
        "timekeeping":{"daily_hours":8,"min_break_minutes":60,"late_tolerance_minutes":10,"allow_overtime":True,"max_overtime_hours":2,"manager_approval_required":True,"allow_manual_adjustment":True,"allow_weekends":False},
        "payroll":{"enabled":True,"currency":"BRL","show_demo_warning":True},
        "benefits":{"enabled":True,"allow_employee_requests":True},
    }
    for module,values in module_values.items():
        response=http.put("/api/hr/configurations",headers=headers,json={"company_id":company_id,"environment":"homologation","module":module,"effective_from":date.today().isoformat(),"values":values})
        assert response.status_code==200
    assert http.post("/api/hr/payroll/events",headers=headers,json={"company_id":company_id,"environment":"homologation","code":"BONUS10","description":"Bônus 10%","kind":"earning","calculation":"percentage","value":10,"base":"salary","active":True}).status_code==201
    assert http.post("/api/hr/benefits/catalog",headers=headers,json={"company_id":company_id,"environment":"homologation","code":"MOBILIDADE","name":"Mobilidade","description":"Benefício demonstrativo","fixed_value":350,"percentage_value":0,"approval_required":True,"effective_from":date.today().isoformat(),"active":True}).status_code==201
    package=http.get(f"/api/configurations/export?company_id={company_id}&module=all&environment=homologation",headers=headers).json()
    assert {row["module"] for row in package["parameters"]}=={"vacations","admission","timekeeping","payroll","benefits"}
    preview=http.post("/api/configurations/import/preview",headers=headers,json={"company_id":company_id,"package":package})
    assert preview.status_code==200
    assert preview.json()["valid"] is True, preview.json()["errors"]

def test_guided_vacation_exercise_completes_after_real_publication(client):
    http,refs=client
    company_id=refs[0][0].id
    consultant=auth(http,"consultor@hcmlab.example.com")
    vacation={"company_id":company_id,"environment":"homologation","group_id":None,"effective_from":date.today().isoformat(),"process_enabled":True,"employee_request_enabled":True,"manager_request_enabled":True,"manager_approval_required":True,"simulation_enabled":True,"installments_allowed":True,"max_installments":3,"min_advance_days":30,"allow_13th_advance":False,"show_balance_to_employee":True}
    assert http.put("/api/vacations/configurations",headers=consultant,json=vacation).status_code==200
    publication=http.post("/api/environments/publications",headers=consultant,json={"company_id":company_id})
    assert publication.status_code==201
    admin=auth(http,"admin@hcmlab.example.com")
    decision=http.post(f"/api/environments/publications/{publication.json()['id']}/decision",headers=admin,json={"approved":True,"comment":"Exercício validado"})
    assert decision.status_code==200 and decision.json()["status"]=="published"
    result=http.post(f"/api/training/exercises/vacation-publication/check?company_id={company_id}",headers=consultant,json={})
    assert result.status_code==200 and result.json()["passed"] is True, result.json()

def test_guided_payroll_and_group_exercises_use_persisted_configuration(client):
    http,refs=client
    company_id,employee=refs[0][0].id,refs[0][1]
    consultant=auth(http,"consultor@hcmlab.example.com")
    event={"company_id":company_id,"environment":"homologation","code":"BONUS10","description":"Bônus demonstrativo 15%","kind":"earning","calculation":"percentage","value":15,"base":"salary","active":True}
    assert http.post("/api/hr/payroll/events",headers=consultant,json=event).status_code==201
    payroll=http.post(f"/api/training/exercises/payroll-bonus/check?company_id={company_id}",headers=consultant,json={})
    assert payroll.status_code==200 and payroll.json()["passed"] is True, payroll.json()
    group_rule={"company_id":company_id,"environment":"homologation","group_id":employee.group_id,"effective_from":date.today().isoformat(),"process_enabled":True,"employee_request_enabled":True,"manager_request_enabled":True,"manager_approval_required":True,"simulation_enabled":True,"installments_allowed":True,"max_installments":2,"min_advance_days":25,"allow_13th_advance":False,"show_balance_to_employee":True}
    assert http.put("/api/vacations/configurations",headers=consultant,json=group_rule).status_code==200
    group=http.post(f"/api/training/exercises/group-precedence/check?company_id={company_id}",headers=consultant,json={})
    assert group.status_code==200 and group.json()["passed"] is True, group.json()

def test_guided_import_exercise_requires_and_recognizes_publication(client):
    http,refs=client
    company_id=refs[0][0].id
    consultant=auth(http,"consultor@hcmlab.example.com")
    package=http.get(f"/api/configurations/export?company_id={company_id}&module=vacations&environment=homologation",headers=consultant).json()
    preview=http.post("/api/configurations/import/preview",headers=consultant,json={"company_id":company_id,"package":package}).json()
    assert preview["valid"] is True
    assert http.post(f"/api/configurations/import/{preview['preview_id']}/apply",headers=consultant).status_code==200
    publication=http.post("/api/environments/publications",headers=consultant,json={"company_id":company_id}).json()
    admin=auth(http,"admin@hcmlab.example.com")
    assert http.post(f"/api/environments/publications/{publication['id']}/decision",headers=admin,json={"approved":True,"comment":"Importação validada"}).status_code==200
    result=http.post(f"/api/training/exercises/configuration-migration/check?company_id={company_id}",headers=consultant,json={})
    assert result.status_code==200 and result.json()["passed"] is True, result.json()

def test_environment_comparison_uses_configuration_in_force_and_flags_only_changed_fields(client):
    from app.models import HRModuleConfiguration
    from app.policies import DEFAULTS
    http,refs=client
    company,employee=refs[0];consultant=auth(http,"consultor@hcmlab.example.com")
    db=next(app.dependency_overrides[get_db]())
    in_force=db.query(VacationConfiguration).filter_by(scope_key=f"company:{company.id}:default",environment="homologation").one()
    values={k:getattr(in_force,k) for k in ("process_enabled","employee_request_enabled","manager_request_enabled","manager_approval_required","simulation_enabled","installments_allowed","max_installments","allow_13th_advance","show_balance_to_employee")}
    # Versions with other dates that are not in force today (an older one and a scheduled one) must not count.
    db.add(VacationConfiguration(scope_key=f"company:{company.id}:default",environment="homologation",company_id=company.id,version=2,effective_from=date.today()-timedelta(days=30),min_advance_days=99,**values))
    db.add(VacationConfiguration(scope_key=f"company:{company.id}:default",environment="homologation",company_id=company.id,version=3,effective_from=date.today()+timedelta(days=30),min_advance_days=60,**values))
    # A group rule only in homologation, equal to the company rule production inherits.
    db.add(VacationConfiguration(scope_key=f"company:{company.id}:group:{employee.group_id}",environment="homologation",company_id=company.id,group_id=employee.group_id,version=1,effective_from=date.today(),min_advance_days=in_force.min_advance_days,**values))
    # A module saved only in homologation: production runs with the system defaults.
    db.add(HRModuleConfiguration(company_id=company.id,module="dependents",environment="homologation",version=1,effective_from=date.today(),values={**DEFAULTS["dependents"],"max_dependents":5}))
    db.commit();db.close()
    result=http.get(f"/api/environments/compare?company_id={company.id}",headers=consultant).json()
    changed=[row["parameter"] for row in result["changes"] if row["changed"]]
    assert changed==["dependents.max_dependents"] and result["changed_count"]==1 and result["has_changes"] is True
    rows={row["parameter"]:row for row in result["changes"]}
    assert rows["dependents.max_dependents"]["homologation"]==5 and rows["dependents.max_dependents"]["production"]==DEFAULTS["dependents"]["max_dependents"]
    assert rows["dependents.max_dependents"]["production_source"]=="Padrão do sistema"
    assert rows["férias.empresa.min_advance_days"]["changed"] is False and rows["férias.empresa.min_advance_days"]["homologation"]==in_force.min_advance_days
    assert rows["férias.grupo Gestores.min_advance_days"]["production_source"].startswith("Herdada da regra da empresa")
    assert rows["termination.base_notice_days"]["changed"] is False
