"""Visibility and permission rules per profile (what each role may read, simulate and change)."""
from datetime import date, timedelta
from test_api import auth, client, first_login  # noqa: F401 - pytest fixture reuse


def test_employee_reads_only_self_service_modules(client):
    http, refs = client
    me = auth(http, "pessoa@emp001.example.com")
    company = refs[0][0]
    for module in ("timekeeping", "benefits"):
        assert http.get(f"/api/hr/configurations/{module}?company_id={company.id}").status_code == 401 or http.get(f"/api/hr/configurations/{module}?company_id={company.id}", headers=me).status_code == 200
    for module in ("contracting", "termination", "vacation_policy", "config_assignments", "payroll_setup", "recruitment", "workflow", "assignments", "admission"):
        assert http.get(f"/api/hr/configurations/{module}?company_id={company.id}", headers=me).status_code == 403, module


def test_explicit_other_company_is_refused_for_scoped_profiles(client):
    http, refs = client
    hr, me = auth(http, "rh@emp001.example.com"), auth(http, "pessoa@emp001.example.com")
    other = refs[1][0]
    assert http.get(f"/api/employees?company_id={other.id}", headers=hr).status_code == 403
    assert http.get(f"/api/employees?company_id={other.id}", headers=me).status_code == 403
    assert http.get(f"/api/environments/publications?company_id={other.id}", headers=hr).status_code == 403
    assert http.get(f"/api/employees?company_id={refs[0][0].id}", headers=hr).status_code == 200


def test_consultant_simulates_processes_but_never_opens_them(client):
    http, refs = client
    consultant = auth(http, "consultor@hcmlab.example.com")
    company, employee = refs[0]
    day = date.today() + timedelta(days=3)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    body = {"process": "leave", "company_id": company.id, "employee_id": employee.id, "data": {"kind": "licenca_luto", "start_date": day.isoformat(), "end_date": day.isoformat()}}
    simulated = http.post("/api/processes/simulate", json=body, headers=consultant)
    assert simulated.status_code == 200 and simulated.json()["allowed"] is True
    assert http.post("/api/processes/requests", json=body, headers=consultant).status_code == 403
    requisition = {"process": "requisition", "company_id": company.id, "data": {"department_id": employee.department_id, "position_id": employee.position_id, "quantity": 1, "reason": "aumento_quadro", "salary_proposal": 4000, "justification": "Simulação do consultor"}}
    assert http.post("/api/processes/simulate", json=requisition, headers=consultant).status_code in (200, 422)
    assert http.post("/api/processes/requests", json=requisition, headers=consultant).status_code == 403


def test_access_group_gates_every_process_route(client):
    http, refs = client
    admin = auth(http, "admin@hcmlab.example.com")
    company, employee = refs[1]
    group = http.post("/api/access/groups", headers=admin, json={"company_id": company.id, "name": "Só ponto", "modules": ["timekeeping"]}).json()
    payload = {"full_name": employee.full_name, "email": "soponto@example.com", "role": "employee", "company_id": company.id, "employee_id": employee.id, "access_group_id": group["id"]}
    created = http.post("/api/access/users", headers=admin, json=payload)
    assert created.status_code == 201
    _, headers = first_login(http, payload["email"], created.json()["temporary_password"], "Definitiva#Forte2026")
    for path in ("/api/processes/requests?scope=mine", f"/api/processes/targets?process=leave&company_id={company.id}", f"/api/processes/options?company_id={company.id}", "/api/processes/catalog"):
        assert http.get(path, headers=headers).status_code == 403, path
    assert http.post("/api/processes/requests", headers=headers, json={"process": "dependents", "company_id": company.id, "employee_id": employee.id, "data": {}}).status_code == 403
    assert http.get("/api/notifications", headers=headers).status_code == 200
    assert http.post("/api/hr/timekeeping/simulate", headers=headers, json={"employee_id": employee.id, "work_date": "2026-10-27", "start_time": "09:00", "end_time": "18:00", "break_minutes": 60}).status_code == 200


def test_hiring_manager_gets_criteria_without_parametrization_access(client):
    http, refs = client
    hr, me = auth(http, "rh@emp001.example.com"), auth(http, "pessoa@emp001.example.com")
    company, employee = refs[0]
    vacancy = http.post("/api/recruitment/vacancies", json={"company_id": company.id, "title": "Analista", "department_id": employee.department_id, "position_id": employee.position_id, "description": "x", "requirements": "y", "hiring_manager_id": employee.id}, headers=hr).json()
    http.post(f"/api/recruitment/vacancies/{vacancy['id']}/status", json={"status": "open"}, headers=hr)
    candidate = http.post("/api/recruitment/candidates", json={"company_id": company.id, "full_name": "Candidata Teste", "email": "cand@example.com", "consent": True}, headers=hr).json()
    application = http.post(f"/api/recruitment/vacancies/{vacancy['id']}/applications", json={"candidate_id": candidate["id"]}, headers=hr).json()
    detail = http.get(f"/api/recruitment/applications/{application['id']}", headers=me).json()
    assert detail["evaluation_criteria"] == ["Conhecimento técnico", "Comunicação", "Aderência à cultura"]
    assert http.get(f"/api/hr/configurations/recruitment?company_id={company.id}", headers=me).status_code == 403
    scores = {c: 4 for c in detail["evaluation_criteria"]}
    assert http.post(f"/api/recruitment/applications/{application['id']}/evaluate", json={"criteria": scores}, headers=me).status_code == 200
