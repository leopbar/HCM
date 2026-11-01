"""Modelo de parametrização: configurações nomeadas, atribuições ordenadas por grupo,
colaborador em vários grupos e Guia Férias (grupo de cálculo, tipo de cargo, folhas e valores mensais)."""
from datetime import date, timedelta
from test_api import auth, client  # noqa: F401 - pytest fixture reuse
from app.vacations import next_valid_start

BASE = {"process_enabled": True, "employee_request_enabled": True, "manager_request_enabled": True, "manager_approval_required": True, "simulation_enabled": True,
        "installments_allowed": True, "min_advance_days": 30, "allow_13th_advance": False, "show_balance_to_employee": True}


def named(http, consultant, company_id, code, max_installments, **extra):
    body = {**BASE, "company_id": company_id, "environment": "homologation", "group_id": None, "effective_from": date.today().isoformat(), "config_code": code,
            "name": f"Configuração {code}", "max_installments": max_installments, **extra}
    response = http.put("/api/vacations/configurations", json=body, headers=consultant)
    assert response.status_code == 200, response.json()
    return response.json()


def save_module(http, consultant, company_id, module, values):
    return http.put("/api/hr/configurations", json={"company_id": company_id, "module": module, "effective_from": date.today().isoformat(), "values": values}, headers=consultant)


def simulate(http, headers, employee_id, **extra):
    body = {"employee_id": employee_id, "environment": "homologation", "start_date": next_valid_start(date.today() + timedelta(days=45)).isoformat(), "requested_days": 20,
            "installment_count": 1, "requester_type": "manager", **extra}
    return http.post("/api/vacations/simulate", json=body, headers=headers).json()


def test_assignment_order_defines_priority_between_groups(client):
    http, refs = client
    admin, consultant = auth(http, "admin@hcmlab.example.com"), auth(http, "consultor@hcmlab.example.com")
    company, employee = refs[0]  # primary group "Gestores"
    other = http.post(f"/api/companies/{company.id}/structure/groups", json={"name": "Operacional"}, headers=admin).json()
    assert http.post(f"/api/companies/{company.id}/setup/collaborator-groups/{other['id']}/members", json={"employee_id": employee.id}, headers=admin).status_code == 201
    groups = {g["name"]: g for g in http.get(f"/api/companies/{company.id}/setup/collaborator-groups", headers=admin).json()}
    assert any(m["id"] == employee.id and not m["primary"] for m in groups["Operacional"]["members"])
    named(http, consultant, company.id, "CFG-A", 2)
    named(http, consultant, company.id, "CFG-B", 3, simulation_enabled=False)
    bad = http.put("/api/vacations/configurations", json={**BASE, "company_id": company.id, "effective_from": date.today().isoformat(), "config_code": "CFG-C", "name": "x", "group_id": groups["Gestores"]["id"], "max_installments": 2}, headers=consultant)
    assert bad.status_code == 422  # named configurations are assigned, not tied to a group
    values = {"vacations": [{"group": "Operacional", "config": "CFG-B"}, {"group": "Gestores", "config": "CFG-A"}], "vacations_simulation": []}
    assert save_module(http, consultant, company.id, "config_assignments", values).status_code == 200
    result = simulate(http, consultant, employee.id)
    assert result["origin"]["scope"] == "assignment" and result["effective_configuration"]["config_code"] == "CFG-B" and result["origin"]["priority"] == 1
    values["vacations"].reverse()
    assert save_module(http, consultant, company.id, "config_assignments", values).status_code == 200
    assert simulate(http, consultant, employee.id)["effective_configuration"]["config_code"] == "CFG-A"
    # Only configurations available for simulation can be used in "Férias - Simulação"
    assert save_module(http, consultant, company.id, "config_assignments", {**values, "vacations_simulation": [{"group": "Gestores", "config": "CFG-B"}]}).status_code == 422
    assert save_module(http, consultant, company.id, "config_assignments", {**values, "vacations_simulation": [{"group": "Gestores", "config": "NAO-EXISTE"}]}).status_code == 422
    other_company_employee = refs[1][1]
    assert simulate(http, consultant, other_company_employee.id)["origin"]["scope"] != "assignment"


def test_selection_rules_follow_the_configured_order(client):
    http, refs = client
    consultant, admin = auth(http, "consultor@hcmlab.example.com"), auth(http, "admin@hcmlab.example.com")
    company, leader = refs[0]
    options = http.get(f"/api/companies/{company.id}/options", headers=admin).json()
    sub = http.post("/api/employees", json={"company_id": company.id, "department_id": options["departments"][0]["id"], "position_id": options["positions"][0]["id"], "group_id": options["groups"][0]["id"],
                                            "manager_id": leader.id, "registration": "S-1", "full_name": "Subordinado", "email": "s1@example.com", "admission_date": "2022-01-10", "salary": 4000}, headers=admin).json()
    rules = [{"process": "*", "requester_group": "Gestores", "targets": ["self"], "config_source": "target"}, {"process": "*", "requester_group": None, "targets": ["self", "direct"], "config_source": "target"}]
    assert save_module(http, consultant, company.id, "assignments", {"rules": rules}).status_code == 200
    publication = http.post("/api/environments/publications", json={"company_id": company.id}, headers=consultant)
    assert publication.status_code == 201, publication.json()
    assert http.post(f"/api/environments/publications/{publication.json()['id']}/decision", json={"approved": True}, headers=admin).status_code == 200
    me = auth(http, "pessoa@emp001.example.com")
    targets = {t["id"] for t in http.get(f"/api/processes/targets?process=leave&company_id={company.id}", headers=me).json()}
    assert targets == {leader.id}  # first rule (Gestores → only self) wins over the generic one
    rules.reverse()
    save_module(http, consultant, company.id, "assignments", {"rules": rules})
    publication = http.post("/api/environments/publications", json={"company_id": company.id}, headers=consultant)
    http.post(f"/api/environments/publications/{publication.json()['id']}/decision", json={"approved": True}, headers=admin)
    targets = {t["id"] for t in http.get(f"/api/processes/targets?process=leave&company_id={company.id}", headers=me).json()}
    assert targets == {leader.id, sub["id"]}


def test_general_vacation_configuration_by_calculation_group_and_job_type(client):
    http, refs = client
    consultant, admin = auth(http, "consultor@hcmlab.example.com"), auth(http, "admin@hcmlab.example.com")
    company, employee = refs[0]
    policy = http.get(f"/api/hr/configurations/vacation_policy?company_id={company.id}&environment=homologation", headers=consultant).json()["effective"]["values"]
    assert policy["by_job_type"] is False and policy["general_configs"][0]["calculation_group"] == "Mensalistas"
    ok = simulate(http, consultant, employee.id)
    assert ok["recording"]["calculation_group"] == "Mensalistas" and ok["recording"]["occurrence"] == 1 and ok["recording"]["kind_sheet"] == "Folha de férias"
    # By job type: the same calculation group is allowed only with different job types
    rows = [dict(policy["general_configs"][0], job_types=["Horário"]), dict(policy["general_configs"][0], job_types=["Horário", "Diário"])]
    assert save_module(http, consultant, company.id, "vacation_policy", {**policy, "by_job_type": True, "general_configs": rows}).status_code == 422
    rows[1]["job_types"] = ["Mensal"]
    rows[1]["sheet_normal"] = "Folha complementar"
    assert save_module(http, consultant, company.id, "vacation_policy", {**policy, "by_job_type": True, "general_configs": rows}).status_code == 200
    assert simulate(http, consultant, employee.id)["recording"]["kind_sheet"] == "Folha complementar"  # Analista is "Mensal" by default
    position_id = employee.position_id
    assert http.put(f"/api/companies/{company.id}/setup/job-types", json={"position_id": position_id, "job_type": "Tarefa"}, headers=admin).status_code == 200
    missing = simulate(http, consultant, employee.id)
    assert not missing["allowed"] and any("Guia Férias" in e for e in missing["errors"])
    unknown = dict(rows[1], calculation_group="Inexistente")
    assert save_module(http, consultant, company.id, "vacation_policy", {**policy, "general_configs": [unknown]}).status_code == 422


def test_monthly_values_treatment_blocks_the_vacation(client):
    http, refs = client
    consultant, admin, hr = auth(http, "consultor@hcmlab.example.com"), auth(http, "admin@hcmlab.example.com"), auth(http, "rh@emp001.example.com")
    company, employee = refs[0]
    start = next_valid_start(date.today() + timedelta(days=45))
    assert http.post("/api/hr/payroll/events", json={"company_id": company.id, "code": "HE", "description": "Horas extras", "kind": "earning", "calculation": "fixed", "value": 0}, headers=consultant).status_code == 201
    policy = http.get(f"/api/hr/configurations/vacation_policy?company_id={company.id}&environment=homologation", headers=consultant).json()["effective"]["values"]
    rows = [dict(r, monthly_values_treatment="block") for r in policy["general_configs"]]
    assert save_module(http, consultant, company.id, "vacation_policy", {**policy, "general_configs": rows}).status_code == 200
    publication = http.post("/api/environments/publications", json={"company_id": company.id}, headers=consultant)
    assert http.post(f"/api/environments/publications/{publication.json()['id']}/decision", json={"approved": True}, headers=admin).status_code == 200
    assert simulate(http, consultant, employee.id, start_date=start.isoformat())["allowed"] is True
    entry = http.post("/api/processes/requests", json={"process": "value_entry", "company_id": company.id, "employee_id": employee.id, "data": {"competence": start.strftime("%Y-%m"), "event_code": "HE", "amount": 100}}, headers=hr)
    assert entry.status_code == 201
    blocked = simulate(http, consultant, employee.id, start_date=start.isoformat())
    assert not blocked["allowed"] and any("impede efetivar" in e for e in blocked["errors"])


def test_employee_calculation_group_must_exist(client):
    http, refs = client
    hr = auth(http, "rh@emp001.example.com")
    employee = refs[0][1]
    body = {"full_name": employee.full_name, "email": employee.email, "status": "Ativo", "calculation_group": "Inexistente"}
    assert http.patch(f"/api/employees/{employee.id}", json=body, headers=hr).status_code == 422
    body["calculation_group"] = "Horistas"
    assert http.patch(f"/api/employees/{employee.id}", json=body, headers=hr).json()["calculation_group"] == "Horistas"


def test_named_configuration_keeps_its_identity_when_published_and_migrated(client):
    http, refs = client
    admin, consultant = auth(http, "admin@hcmlab.example.com"), auth(http, "consultor@hcmlab.example.com")
    company, employee = refs[0]
    named(http, consultant, company.id, "CFG-GESTORES", 3, simulation_text="Simule antes de pedir")
    assert save_module(http, consultant, company.id, "config_assignments", {"vacations": [{"group": "Gestores", "config": "CFG-GESTORES"}], "vacations_simulation": []}).status_code == 200
    publication = http.post("/api/environments/publications", json={"company_id": company.id}, headers=consultant)
    assert http.post(f"/api/environments/publications/{publication.json()['id']}/decision", json={"approved": True}, headers=admin).status_code == 200
    prod = http.get(f"/api/vacations/configurations?company_id={company.id}&environment=production", headers=consultant).json()
    assert [c["config_code"] for c in prod["named_configurations"]] == ["CFG-GESTORES"] and prod["named_configurations"][0]["name"] == "Configuração CFG-GESTORES"
    result = simulate(http, consultant, employee.id, environment="production")
    assert result["origin"]["label"].startswith("Configuração CFG-GESTORES") and result["effective_configuration"]["config_code"] == "CFG-GESTORES"
    # Migrator: the named configuration is exported with its code and imported as a named configuration (not as the company rule)
    package = http.get(f"/api/configurations/export?company_id={company.id}&module=vacations&environment=homologation", headers=consultant).json()
    named_items = [p for p in package["parameters"] if p["scope"].get("type") == "config"]
    assert named_items and named_items[0]["scope"]["code"] == "CFG-GESTORES"
    target = refs[1][0]
    preview = http.post("/api/configurations/import/preview", json={"company_id": target.id, "package": package}, headers=consultant).json()
    assert preview["valid"] is True, preview
    assert http.post(f"/api/configurations/import/{preview['preview_id']}/apply", headers=consultant).status_code == 200
    imported = http.get(f"/api/vacations/configurations?company_id={target.id}&environment=homologation", headers=consultant).json()
    assert [c["config_code"] for c in imported["named_configurations"]] == ["CFG-GESTORES"]
    assert all(not c.get("config_code") for c in imported["configurations"])
