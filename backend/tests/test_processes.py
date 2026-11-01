"""Security hardening and the parameterizable processes."""
from datetime import date, timedelta
from test_api import auth, client, first_login  # noqa: F401 - pytest fixture reuse
from app.vacations import next_valid_start

VALID_CNPJ = "11.222.333/0001-81"


def weekday_from(days: int) -> date:
    day = date.today() + timedelta(days=days)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def subordinate(http, admin, refs, **extra):
    """A second employee of company 1 reporting to Pessoa EMP001 (who has the employee login)."""
    company, leader = refs[0]
    options = http.get(f"/api/companies/{company.id}/options", headers=admin).json()
    payload = {"company_id": company.id, "department_id": options["departments"][0]["id"], "position_id": options["positions"][0]["id"], "group_id": options["groups"][0]["id"],
               "manager_id": leader.id, "registration": extra.pop("registration", "SUB-1"), "full_name": extra.pop("full_name", "Subordinada Teste"), "email": extra.pop("email", "sub@example.com"),
               "admission_date": "2021-03-01", "salary": 5000, **extra}
    response = http.post("/api/employees", json=payload, headers=admin)
    assert response.status_code == 201, response.json()
    return response.json()


def test_demo_accounts_endpoint_and_docs_are_gone(client):
    http, _ = client
    assert http.get("/api/demo-accounts").status_code == 404
    assert http.get("/docs").status_code == 404


def test_lockout_after_repeated_failures_and_admin_unlock(client):
    http, _ = client
    for _ in range(5):
        assert http.post("/api/auth/login", json={"email": "consultor@hcmlab.example.com", "password": "errada"}).status_code == 401
    assert http.post("/api/auth/login", json={"email": "consultor@hcmlab.example.com", "password": "hcm123"}).status_code == 423
    admin = auth(http, "admin@hcmlab.example.com")
    users = http.get("/api/access/users", headers=admin).json()
    consultant = next(u for u in users if u["email"] == "consultor@hcmlab.example.com")
    assert consultant["locked"] is True
    assert http.patch(f"/api/access/users/{consultant['id']}", headers=admin, json={"active": True, "unlock": True}).status_code == 200
    assert http.post("/api/auth/login", json={"email": "consultor@hcmlab.example.com", "password": "hcm123"}).status_code == 200


def test_cookie_session_logout_and_csrf_header(client):
    http, _ = client
    http.cookies.clear()
    login = http.post("/api/auth/login", json={"email": "admin@hcmlab.example.com", "password": "hcm123"})
    assert login.status_code == 200 and "hcm_session" in login.headers.get("set-cookie", "") and "HttpOnly" in login.headers["set-cookie"]
    assert http.get("/api/auth/me").status_code == 200  # cookie only
    assert http.post("/api/auth/logout").status_code == 403  # no application header -> rejected (CSRF)
    assert http.post("/api/auth/logout", headers={"X-Requested-With": "HCMLab"}).status_code == 200
    assert http.get("/api/auth/me", headers={"Authorization": "Bearer " + login.json()["access_token"]}).status_code == 401


def test_invalid_cnpj_duplicate_registration_and_structure_maintenance(client):
    http, refs = client
    admin = auth(http, "admin@hcmlab.example.com")
    base = {"code": "EMP9", "legal_name": "Nova Ltda", "trade_name": "Nova", "city": "Curitiba", "state": "PR", "sector": "Serviços", "status": "Ativa"}
    assert http.post("/api/companies", json={**base, "cnpj": "11111111111111"}, headers=admin).status_code == 422
    assert http.post("/api/companies", json={**base, "cnpj": "11222333000182"}, headers=admin).status_code == 422
    assert http.post("/api/companies", json={**base, "state": "ZZ", "cnpj": VALID_CNPJ}, headers=admin).status_code == 422
    company = http.post("/api/companies", json={**base, "cnpj": VALID_CNPJ}, headers=admin).json()
    dept = http.post(f"/api/companies/{company['id']}/structure/departments", json={"name": "Loja"}, headers=admin).json()
    assert http.patch(f"/api/companies/{company['id']}/structure/departments/{dept['id']}", json={"name": "Loja Centro"}, headers=admin).json()["name"] == "Loja Centro"
    pos = http.post(f"/api/companies/{company['id']}/structure/positions", json={"name": "Vendedor"}, headers=admin).json()
    grp = http.post(f"/api/companies/{company['id']}/structure/groups", json={"name": "Operacional"}, headers=admin).json()
    employee = {"company_id": company["id"], "department_id": dept["id"], "position_id": pos["id"], "group_id": grp["id"], "registration": "N-1", "full_name": "Pessoa Nova", "email": "nova@example.com", "admission_date": "2025-01-10", "salary": 3000}
    assert http.post("/api/employees", json=employee, headers=admin).status_code == 201
    assert http.post("/api/employees", json=employee, headers=admin).status_code == 409  # was HTTP 500
    assert http.post("/api/employees", json={**employee, "registration": "N-2", "email": "zero@example.com", "salary": 0}, headers=admin).status_code == 422
    assert http.delete(f"/api/companies/{company['id']}/structure/departments/{dept['id']}", headers=admin).status_code == 409  # in use
    spare = http.post(f"/api/companies/{company['id']}/structure/departments", json={"name": "Sem uso"}, headers=admin).json()
    assert http.delete(f"/api/companies/{company['id']}/structure/departments/{spare['id']}", headers=admin).status_code == 204


def test_employee_update_manager_cycle_and_termination_rule(client):
    http, refs = client
    admin = auth(http, "admin@hcmlab.example.com")
    sub = subordinate(http, admin, refs)
    leader = refs[0][1]
    hr = auth(http, "rh@emp001.example.com")
    body = {"full_name": leader.full_name, "email": leader.email, "manager_id": sub["id"], "status": "Ativo"}
    assert http.patch(f"/api/employees/{leader.id}", json=body, headers=hr).status_code == 422  # cycle
    assert http.patch(f"/api/employees/{leader.id}", json={**body, "manager_id": None, "status": "Desligado"}, headers=hr).status_code == 422
    updated = http.patch(f"/api/employees/{leader.id}", json={**body, "manager_id": None, "phone": "(11) 99999-0000", "cpf": "529.982.247-25"}, headers=hr)
    assert updated.status_code == 200 and updated.json()["phone"] == "(11) 99999-0000"
    assert http.get(f"/api/employees/{sub['id']}", headers=hr).json()["manager_name"] == leader.full_name


def test_leave_flows_through_direct_manager_then_hr(client):
    http, refs = client
    admin = auth(http, "admin@hcmlab.example.com")
    sub = subordinate(http, admin, refs)
    company = refs[0][0]
    # The subordinate needs a login to request; the leader (pessoa@emp001) approves the first step.
    account = {"full_name": "Subordinada Teste", "email": "sub.login@example.com", "role": "employee", "company_id": company.id, "employee_id": sub["id"]}
    created = http.post("/api/access/users", json=account, headers=admin)
    assert created.status_code == 201
    _, sub_headers = first_login(http, account["email"], created.json()["temporary_password"], "Definitiva#Forte2026")
    leader = auth(http, "pessoa@emp001.example.com")
    hr = auth(http, "rh@emp001.example.com")
    start = weekday_from(3)
    leave = {"process": "leave", "company_id": company.id, "employee_id": sub["id"], "data": {"kind": "licenca_casamento", "start_date": start.isoformat(), "end_date": (start + timedelta(days=4)).isoformat()}}
    too_long = http.post("/api/processes/requests", json=leave, headers=sub_headers)
    assert too_long.status_code == 422 and "3 dias" in str(too_long.json()["detail"])
    leave["data"]["end_date"] = (start + timedelta(days=2)).isoformat()
    created = http.post("/api/processes/requests", json=leave, headers=sub_headers)
    assert created.status_code == 201 and created.json()["current_approver"] == "Gestor imediato" and created.json()["due_at"]
    assert http.post(f"/api/processes/requests/{created.json()['id']}/decision", json={"approved": True}, headers=hr).status_code == 403  # not HR's turn
    tasks = http.get("/api/processes/requests", params={"scope": "tasks"}, headers=leader).json()
    assert [t["id"] for t in tasks] == [created.json()["id"]]
    step1 = http.post(f"/api/processes/requests/{created.json()['id']}/decision", json={"approved": True}, headers=leader).json()
    assert step1["status"] == "pending_approval" and step1["current_approver"] == "RH da empresa"
    done = http.post(f"/api/processes/requests/{created.json()['id']}/decision", json={"approved": True, "comment": "ok"}, headers=hr).json()
    assert done["status"] == "approved" and [h["action"] for h in done["history"]] == ["submitted", "approved", "approved"]
    overlap = http.post("/api/processes/requests", json=leave, headers=sub_headers)
    assert overlap.status_code == 422 and "Conflita" in str(overlap.json()["detail"])
    assert any("aguarda sua aprovação" in n["message"] for n in http.get("/api/notifications", headers=leader).json())


def test_missing_direct_manager_falls_back_to_hr_and_is_persisted(client):
    http, refs = client
    company, me = refs[0]  # Pessoa EMP001 has no manager
    requester = auth(http, "pessoa@emp001.example.com")
    hr = auth(http, "rh@emp001.example.com")
    start = weekday_from(3)
    leave = {"process": "leave", "company_id": company.id, "employee_id": me.id, "data": {"kind": "licenca_casamento", "start_date": start.isoformat(), "end_date": (start + timedelta(days=2)).isoformat()}}
    created = http.post("/api/processes/requests", json=leave, headers=requester)
    assert created.status_code == 201, created.json()
    # Read back in a new request (new DB session): the fallback must have been written, not only logged.
    stored = http.get(f"/api/processes/requests/{created.json()['id']}", headers=hr).json()
    assert stored["current_approver"] == "RH da empresa"
    assert stored["steps"][0]["approver"] == "hr" and "encaminhada ao RH" in stored["steps"][0]["fallback"]
    assert "fallback" in [h["action"] for h in stored["history"]]
    decided = http.post(f"/api/processes/requests/{created.json()['id']}/decision", json={"approved": True}, headers=hr)
    assert decided.status_code == 200 and decided.json()["current_step"] == 1


def test_step_without_any_approver_warns_the_administrator(client):
    http, refs = client
    company, me = refs[0]
    admin = auth(http, "admin@hcmlab.example.com")
    requester = auth(http, "pessoa@emp001.example.com")
    hr_account = next(u for u in http.get("/api/access/users", headers=admin).json() if u["email"] == "rh@emp001.example.com")
    assert http.patch(f"/api/access/users/{hr_account['id']}", headers=admin, json={"active": False}).status_code == 200
    start = weekday_from(3)
    leave = {"process": "leave", "company_id": company.id, "employee_id": me.id, "data": {"kind": "licenca_casamento", "start_date": start.isoformat(), "end_date": (start + timedelta(days=2)).isoformat()}}
    created = http.post("/api/processes/requests", json=leave, headers=requester)
    assert created.status_code == 201, created.json()
    stored = http.get(f"/api/processes/requests/{created.json()['id']}", headers=admin).json()
    assert [h["action"] for h in stored["history"]] == ["submitted", "fallback", "no_approver"]
    assert any(f"nº {created.json()['id']} está parado" in n["message"] for n in http.get("/api/notifications", headers=admin).json())
    assert http.post(f"/api/processes/requests/{created.json()['id']}/decision", json={"approved": True}, headers=admin).status_code == 200


def test_selection_assignments_limit_who_can_be_targeted(client):
    http, refs = client
    admin = auth(http, "admin@hcmlab.example.com")
    sub = subordinate(http, admin, refs)
    leader = auth(http, "pessoa@emp001.example.com")
    company, me = refs[0]
    other = refs[1][1]
    ids = {t["id"] for t in http.get("/api/processes/targets", params={"process": "leave", "company_id": company.id}, headers=leader).json()}
    assert ids == {me.id, sub["id"]}
    movement_ids = {t["id"] for t in http.get("/api/processes/targets", params={"process": "movement", "company_id": company.id}, headers=leader).json()}
    assert movement_ids == {sub["id"]}  # not self-service
    denied = http.post("/api/processes/requests", json={"process": "dependents", "company_id": other.company_id, "employee_id": other.id, "data": {}}, headers=leader)
    assert denied.status_code == 403


def test_movement_rules_salary_table_and_irreducibility(client):
    http, refs = client
    admin = auth(http, "admin@hcmlab.example.com")
    hr = auth(http, "rh@emp001.example.com")
    company, employee = refs[0]
    base = {"process": "movement", "company_id": company.id, "employee_id": employee.id, "data": {"effective_date": date.today().isoformat(), "salary": 4000, "reason": "Ajuste de faixa"}}
    lower = http.post("/api/processes/requests", json=base, headers=hr)
    assert lower.status_code == 422 and "Redução salarial" in str(lower.json()["detail"])
    base["data"]["salary"] = 9000
    assert "Aumento acima" in str(http.post("/api/processes/requests", json=base, headers=hr).json()["detail"])
    base["data"]["salary"] = 5500
    applied = http.post("/api/processes/requests", json=base, headers=hr).json()
    assert applied["status"] == "approved"  # HR is the only step and is the requester
    assert http.get(f"/api/employees/{employee.id}", headers=hr).json()["salary"] == 5500
    future = {**base, "data": {**base["data"], "salary": 6000, "effective_date": (date.today() + timedelta(days=10)).isoformat()}}
    assert http.post("/api/processes/requests", json=future, headers=hr).json()["status"] == "scheduled"


def test_requisition_respects_headcount_and_creates_positions(client):
    http, refs = client
    hr = auth(http, "rh@emp001.example.com")
    admin = auth(http, "admin@hcmlab.example.com")
    company, employee = refs[0]
    data = {"department_id": employee.department_id, "position_id": employee.position_id, "quantity": 2, "reason": "aumento_quadro", "salary_proposal": 4000, "justification": "Expansão da equipe de atendimento"}
    no_plan = http.post("/api/processes/requests", json={"process": "requisition", "company_id": company.id, "data": data}, headers=hr)
    assert no_plan.status_code == 422 and "quadro de vagas" in str(no_plan.json()["detail"])
    assert http.put(f"/api/companies/{company.id}/setup/headcount", json={"department_id": employee.department_id, "position_id": employee.position_id, "planned": 2}, headers=hr).status_code == 200
    assert http.post("/api/processes/requests", json={"process": "requisition", "company_id": company.id, "data": data}, headers=hr).status_code == 422  # 1 occupied + 2 > 2
    data["quantity"] = 1
    created = http.post("/api/processes/requests", json={"process": "requisition", "company_id": company.id, "data": data}, headers=hr).json()
    assert created["current_approver"] == "Administrador do sistema"
    approved = http.post(f"/api/processes/requests/{created['id']}/decision", json={"approved": True}, headers=admin).json()
    assert approved["status"] == "approved" and len(approved["result"]["positions"]) == 1
    seats = http.get(f"/api/companies/{company.id}/setup/positions", headers=hr).json()
    assert seats[0]["status"] == "vacant" and seats[0]["requisition_id"] == created["id"]


def test_termination_computes_proportional_notice_and_blocks_access(client):
    http, refs = client
    hr = auth(http, "rh@emp001.example.com")
    company, employee = refs[0]  # admitted 2020-01-01
    years = date.today().year - 2020 - (1 if (date.today().month, date.today().day) < (1, 1) else 0)
    data = {"reason": "sem_justa_causa", "notice_type": "indenizado", "termination_date": date.today().isoformat()}
    sim = http.post("/api/processes/simulate", json={"process": "termination", "company_id": company.id, "employee_id": employee.id, "data": data}, headers=hr).json()
    assert sim["data"]["notice_days"] == min(90, 30 + 3 * years)
    assert http.post("/api/processes/simulate", json={"process": "termination", "company_id": company.id, "employee_id": employee.id, "data": {**data, "reason": "justa_causa"}}, headers=hr).status_code == 422
    done = http.post("/api/processes/requests", json={"process": "termination", "company_id": company.id, "employee_id": employee.id, "data": data}, headers=hr).json()
    assert done["status"] == "approved"
    assert http.get(f"/api/employees/{employee.id}", headers=hr).json()["status"] == "Desligado"
    assert http.post("/api/auth/login", json={"email": "pessoa@emp001.example.com", "password": "hcm123"}).status_code == 401


def test_transport_and_value_entries_reach_the_payroll(client):
    http, refs = client
    hr = auth(http, "rh@emp001.example.com")
    consultant = auth(http, "consultor@hcmlab.example.com")
    admin = auth(http, "admin@hcmlab.example.com")
    company, employee = refs[0]
    line = http.put(f"/api/companies/{company.id}/setup/transport-lines", json={"code": "BUS1", "description": "Linha 1", "fare": 5}, headers=hr).json()
    vt = http.post("/api/processes/requests", json={"process": "transport", "company_id": company.id, "employee_id": employee.id, "data": {"lines": [{"line_id": line["id"], "trips_per_day": 2}]}}, headers=hr).json()
    assert vt["status"] == "approved" and vt["data"]["monthly_cost"] == 220 and vt["data"]["employee_discount"] == 220  # min(cost, 6% of 5000 = 300)
    event = {"company_id": company.id, "code": "HE50", "description": "Horas extras 50%", "kind": "earning", "calculation": "fixed", "value": 0}
    assert http.post("/api/hr/payroll/events", json=event, headers=consultant).status_code == 201
    assert http.post("/api/hr/payroll/events", json={**event, "code": "X", "calculation": "percentage", "value": 500}, headers=consultant).status_code == 422
    publication = http.post("/api/environments/publications", json={"company_id": company.id}, headers=consultant)
    assert publication.status_code == 201, publication.json()
    assert http.post(f"/api/environments/publications/{publication.json()['id']}/decision", json={"approved": True}, headers=admin).status_code == 200
    competence = date.today().strftime("%Y-%m")
    entry = http.post("/api/processes/requests", json={"process": "value_entry", "company_id": company.id, "employee_id": employee.id, "data": {"competence": competence, "event_code": "HE50", "amount": 350}}, headers=hr)
    assert entry.status_code == 201 and entry.json()["status"] == "approved"
    payroll = http.post("/api/hr/payroll/simulate", json={"company_id": company.id, "environment": "homologation", "competence": competence}, headers=hr).json()
    assert payroll["environment"] == "production"  # HR always uses the published configuration
    row = next(r for r in payroll["results"] if r["employee_id"] == employee.id)
    assert any(e["code"] == "HE50" and e["amount"] == 350 for e in row["earnings"]) and any(d["code"] == "VT" and d["amount"] == 220 for d in row["deductions"])


def test_dependents_require_valid_cpf_and_are_created(client):
    http, refs = client
    me = auth(http, "pessoa@emp001.example.com")
    company, employee = refs[0]
    data = {"action": "add", "full_name": "Filha Teste", "relationship_type": "filho", "birth_date": "2015-05-10", "cpf": "111.111.111-11"}
    assert http.post("/api/processes/requests", json={"process": "dependents", "company_id": company.id, "employee_id": employee.id, "data": data}, headers=me).status_code == 422
    data["cpf"] = "529.982.247-25"
    created = http.post("/api/processes/requests", json={"process": "dependents", "company_id": company.id, "employee_id": employee.id, "data": data}, headers=me).json()
    assert created["status"] == "pending_approval" and created["current_approver"] == "RH da empresa"
    hr = auth(http, "rh@emp001.example.com")
    assert http.post(f"/api/processes/requests/{created['id']}/decision", json={"approved": False}, headers=hr).status_code == 422
    assert http.post(f"/api/processes/requests/{created['id']}/decision", json={"approved": True}, headers=hr).json()["status"] == "approved"
    options = http.get("/api/processes/options", params={"company_id": company.id, "employee_id": employee.id}, headers=me).json()
    assert options["dependents"][0]["name"] == "Filha Teste"


def test_vacation_clt_rules_cash_bonus_and_acknowledgement(client):
    http, refs = client
    me = auth(http, "pessoa@emp001.example.com")
    hr = auth(http, "rh@emp001.example.com")
    company, employee = refs[0]
    start = next_valid_start(date.today() + timedelta(days=45))
    base = {"employee_id": employee.id, "start_date": start.isoformat(), "requested_days": 4, "installment_count": 1, "requester_type": "employee"}
    assert "inferior a 5 dias" in str(http.post("/api/vacations/simulate", json=base, headers=me).json()["errors"])
    friday = date.today() + timedelta(days=45)
    while friday.weekday() != 4: friday += timedelta(days=1)
    assert "descanso semanal" in str(http.post("/api/vacations/simulate", json={**base, "requested_days": 20, "start_date": friday.isoformat()}, headers=me).json()["errors"])
    assert http.post("/api/vacations/simulate", json={**base, "requested_days": 15, "cash_bonus_days": 11}, headers=me).status_code == 422  # more than 1/3
    assert "14 dias" in str(http.post("/api/vacations/simulate", json={**base, "requested_days": 10, "cash_bonus_days": 10}, headers=me).json()["errors"])
    ok = http.post("/api/vacations/simulate", json={**base, "requested_days": 20, "cash_bonus_days": 10}, headers=me).json()
    assert ok["allowed"] is True and ok["acquisition_period"]["remaining"] == 30
    created = http.post("/api/vacations/requests", json={**base, "requested_days": 20, "cash_bonus_days": 10}, headers=me).json()
    assert created["status"] == "pending_approval" and created["current_approver"] == "RH da empresa"
    assert http.post(f"/api/vacations/requests/{created['id']}/acknowledge", headers=me).status_code == 409
    http.post(f"/api/vacations/requests/{created['id']}/decision", json={"approved": True}, headers=hr)
    acknowledged = http.post(f"/api/vacations/requests/{created['id']}/acknowledge", headers=me)
    assert acknowledged.status_code == 200 and acknowledged.json()["acknowledged_at"]
    period = http.get("/api/vacations/effective", headers=me).json()["acquisition_period"]
    assert period["start"] != ok["acquisition_period"]["start"]  # the first period is fully used (20 + 10 sold)


def test_admission_approval_creates_the_employee(client):
    http, refs = client
    hr = auth(http, "rh@emp001.example.com")
    company = refs[0][0]
    options = http.get(f"/api/companies/{company.id}/options", headers=hr).json()
    planned = weekday_from(10)
    while (planned.month, planned.day) in {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)}:
        planned = weekday_from((planned - date.today()).days + 1)
    payload = {"company_id": company.id, "candidate_name": "Nova Contratada", "candidate_email": "nova.contratada@example.com", "department_id": options["departments"][0]["id"], "position_id": options["positions"][0]["id"],
               "planned_date": planned.isoformat(), "documents_complete": True, "group_id": options["groups"][0]["id"], "salary": 3500, "registration": "ADM-9"}
    created = http.post("/api/hr/admissions", json=payload, headers=hr).json()
    assert http.post("/api/hr/admissions", json={**payload, "registration": "ADM-10"}, headers=hr).status_code == 422  # duplicate candidate
    approved = http.post(f"/api/hr/admissions/{created['id']}/decision", json={"approved": True}, headers=hr).json()
    assert approved["employee_id"]
    assert http.get(f"/api/employees/{approved['employee_id']}", headers=hr).json()["registration"] == "ADM-9"


def test_workflow_configuration_is_versioned_and_validated(client):
    http, refs = client
    consultant = auth(http, "consultor@hcmlab.example.com")
    company = refs[0][0]
    current = http.get("/api/hr/configurations/workflow", params={"company_id": company.id, "environment": "homologation"}, headers=consultant).json()["effective"]["values"]
    broken = {**current, "processes": {**current["processes"], "leave": {"steps": [{"approver": "ceo", "sla_hours": 8}], "group_overrides": {}}}}
    assert http.put("/api/hr/configurations", json={"company_id": company.id, "module": "workflow", "effective_from": date.today().isoformat(), "values": broken}, headers=consultant).status_code == 422
    good = {**current, "processes": {**current["processes"], "leave": {"steps": [{"approver": "hr", "sla_hours": 8}], "group_overrides": {"Gestores": [{"approver": "system_admin", "sla_hours": 4}]}}}}
    saved = http.put("/api/hr/configurations", json={"company_id": company.id, "module": "workflow", "effective_from": date.today().isoformat(), "values": good}, headers=consultant)
    assert saved.status_code == 200
    employee = auth(http, "pessoa@emp001.example.com")
    assert http.get("/api/hr/configurations/workflow", params={"company_id": company.id}, headers=employee).status_code == 403
