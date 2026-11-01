"""Informações adicionais por processo, redirecionamento (etapa finalizadora) e prazo mínimo para aprovação de férias."""
from datetime import date, timedelta
from test_api import auth, client  # noqa: F401 - pytest fixture reuse
from app.vacations import next_valid_start


def weekday(days):
    day = date.today() + timedelta(days=days)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def save(http, consultant, company_id, module, values):
    return http.put("/api/hr/configurations", json={"company_id": company_id, "module": module, "effective_from": date.today().isoformat(), "values": values}, headers=consultant)


def publish(http, consultant, admin, company_id):
    publication = http.post("/api/environments/publications", json={"company_id": company_id}, headers=consultant)
    assert publication.status_code == 201, publication.json()
    assert http.post(f"/api/environments/publications/{publication.json()['id']}/decision", json={"approved": True}, headers=admin).status_code == 200


def test_additional_fields_are_validated_recorded_and_exported(client):
    http, refs = client
    consultant, admin, hr, me = auth(http, "consultor@hcmlab.example.com"), auth(http, "admin@hcmlab.example.com"), auth(http, "rh@emp001.example.com"), auth(http, "pessoa@emp001.example.com")
    company, employee = refs[0]
    current = http.get(f"/api/hr/configurations/process_fields?company_id={company.id}&environment=homologation", headers=consultant).json()["effective"]["values"]
    bad = {**current, "leave": [{"name": "Número", "label": "x", "type": "text", "required": True, "options": [], "help": ""}]}
    assert save(http, consultant, company.id, "process_fields", bad).status_code == 422  # invalid technical name and short label
    fields = [{"name": "numero_atestado", "label": "Número do atestado", "type": "text", "required": True, "options": [], "help": "Conforme documento"},
              {"name": "hospital", "label": "Hospital", "type": "select", "required": False, "options": ["Santa Casa", "Unimed"], "help": ""}]
    assert save(http, consultant, company.id, "process_fields", {**current, "leave": fields}).status_code == 200
    catalog = http.get(f"/api/processes/catalog?company_id={company.id}&environment=homologation", headers=consultant).json()
    assert [f["name"] for f in next(p for p in catalog["processes"] if p["process"] == "leave")["additional_fields"]] == ["numero_atestado", "hospital"]
    production_catalog = http.get(f"/api/processes/catalog?company_id={company.id}", headers=me).json()
    assert production_catalog["modules"] == [] and next(p for p in production_catalog["processes"] if p["process"] == "leave")["additional_fields"] == []
    publish(http, consultant, admin, company.id)
    start = weekday(3)
    body = {"process": "leave", "company_id": company.id, "employee_id": employee.id, "data": {"kind": "licenca_luto", "start_date": start.isoformat(), "end_date": start.isoformat()}}
    missing = http.post("/api/processes/requests", json=body, headers=me)
    assert missing.status_code == 422 and "Número do atestado" in str(missing.json()["detail"])
    body["data"]["additional"] = {"numero_atestado": "AT-123", "hospital": "Clínica X"}
    assert http.post("/api/processes/requests", json=body, headers=me).status_code == 422  # option outside the list
    body["data"]["additional"]["hospital"] = "Unimed"
    created = http.post("/api/processes/requests", json=body, headers=me)
    assert created.status_code == 201, created.json()
    assert created.json()["data"]["additional"] == {"numero_atestado": "AT-123", "hospital": "Unimed"}
    assert [f["label"] for f in created.json()["additional_fields"]] == ["Número do atestado", "Hospital"]
    detail = http.get(f"/api/processes/requests/{created.json()['id']}", headers=hr).json()
    assert detail["data"]["additional"]["numero_atestado"] == "AT-123"
    package = http.get(f"/api/configurations/export?company_id={company.id}&module=all&environment=homologation", headers=consultant).json()
    assert any(p["module"] == "process_fields" and p["values"]["leave"][0]["name"] == "numero_atestado" for p in package["parameters"])


def test_finishing_step_redirects_and_skips_remaining_steps(client):
    http, refs = client
    consultant, admin, hr, me = auth(http, "consultor@hcmlab.example.com"), auth(http, "admin@hcmlab.example.com"), auth(http, "rh@emp001.example.com"), auth(http, "pessoa@emp001.example.com")
    company, employee = refs[0]
    workflow = http.get(f"/api/hr/configurations/workflow?company_id={company.id}&environment=homologation", headers=consultant).json()["effective"]["values"]
    workflow["processes"]["dependents"] = {"steps": [{"approver": "hr", "sla_hours": 8, "finish": True}, {"approver": "system_admin", "sla_hours": 8}], "group_overrides": {}}
    assert save(http, consultant, company.id, "workflow", {**workflow, "processes": {**workflow["processes"], "leave": {"steps": [{"approver": "hr", "sla_hours": 8, "finish": "sim"}], "group_overrides": {}}}}).status_code == 422
    assert save(http, consultant, company.id, "workflow", workflow).status_code == 200
    publish(http, consultant, admin, company.id)
    body = {"process": "dependents", "company_id": company.id, "employee_id": employee.id, "data": {"action": "add", "full_name": "Dependente Teste", "relationship_type": "filho", "birth_date": "2016-01-01", "cpf": "529.982.247-25"}}
    created = http.post("/api/processes/requests", json=body, headers=me).json()
    assert created["total_steps"] == 2 and created["current_approver"] == "RH da empresa"
    decided = http.post(f"/api/processes/requests/{created['id']}/decision", json={"approved": True}, headers=hr).json()
    assert decided["status"] == "approved"  # finished at step 1; the administrator step was skipped
    assert any(h["action"] == "redirected" for h in decided["history"])
    assert http.get(f"/api/processes/requests?scope=tasks", headers=admin).json() == []


def test_vacation_approval_respects_minimum_days_before_start(client):
    http, refs = client
    consultant, admin, hr, me = auth(http, "consultor@hcmlab.example.com"), auth(http, "admin@hcmlab.example.com"), auth(http, "rh@emp001.example.com"), auth(http, "pessoa@emp001.example.com")
    company, employee = refs[0]
    policy = http.get(f"/api/hr/configurations/vacation_policy?company_id={company.id}&environment=homologation", headers=consultant).json()["effective"]["values"]
    assert save(http, consultant, company.id, "vacation_policy", {**policy, "approval_min_days_before_start": 200}).status_code == 422  # above the bound
    assert save(http, consultant, company.id, "vacation_policy", {**policy, "approval_min_days_before_start": 60}).status_code == 200
    publish(http, consultant, admin, company.id)
    start = next_valid_start(date.today() + timedelta(days=45))
    created = http.post("/api/vacations/requests", json={"employee_id": employee.id, "start_date": start.isoformat(), "requested_days": 20, "installment_count": 1, "requester_type": "employee"}, headers=me)
    assert created.status_code == 201, created.json()
    blocked = http.post(f"/api/vacations/requests/{created.json()['id']}/decision", json={"approved": True}, headers=hr)
    assert blocked.status_code == 409 and "Prazo mínimo para aprovação" in blocked.json()["detail"]
    assert http.post(f"/api/vacations/requests/{created.json()['id']}/decision", json={"approved": False, "comment": "Fora do prazo"}, headers=hr).json()["status"] == "rejected"
    far = next_valid_start(date.today() + timedelta(days=90))
    later = http.post("/api/vacations/requests", json={"employee_id": employee.id, "start_date": far.isoformat(), "requested_days": 20, "installment_count": 1, "requester_type": "employee"}, headers=me).json()
    assert http.post(f"/api/vacations/requests/{later['id']}/decision", json={"approved": True}, headers=hr).json()["status"] == "approved"
