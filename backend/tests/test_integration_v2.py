"""Contract v2: catalog-driven modules, named vacation configurations, ordered assignments,
multi-group membership and precedence resolved by the same function as the operational simulator."""
import copy
from datetime import date, timedelta
from uuid import uuid4
import pytest
from test_api import client, auth  # noqa: F401 - pytest fixture reuse; integration/executor come from conftest
import app.integration_v2 as v2
from app.integration_v2 import PlanInput, canonical, plan_hash
from app.policies import DEFAULTS
from app.vacations import CONFIG_FIELDS, next_valid_start

V2 = "/api/integration/v2"
V1 = "/api/integration/v1"


def general_rule(snapshot):
    config = snapshot["effective"]["homologation"][0]["configuration"]
    return {field: config[field] for field in CONFIG_FIELDS}


def configuration(snapshot, group_id=None, **overrides):
    return {**general_rule(snapshot), "company_id": snapshot["company"]["id"], "environment": "homologation", "group_id": group_id, "effective_from": snapshot["as_of"], **overrides}


def packet(before, configurations, assignments=None):
    model = PlanInput(company_code=before["company"]["code"], effective_from=before["as_of"], configurations=configurations, assignments=assignments)
    confs, assigns = canonical(model)
    return {"operation_id": str(uuid4()), "approved_by": "Consultor de teste", "company_code": before["company"]["code"], "expected_fingerprint": before["fingerprint"],
            "approval_hash": plan_hash(before["fingerprint"], confs, assigns), "effective_from": before["as_of"], "configurations": confs, "assignments": assigns}


def named_via_ui(http, consultant, company_id, code, **extra):
    body = {"process_enabled": True, "employee_request_enabled": True, "manager_request_enabled": True, "manager_approval_required": True, "simulation_enabled": True,
            "installments_allowed": True, "max_installments": 3, "min_advance_days": 30, "allow_13th_advance": False, "show_balance_to_employee": True,
            "company_id": company_id, "environment": "homologation", "group_id": None, "effective_from": date.today().isoformat(), "config_code": code, "name": f"Configuração {code}", **extra}
    response = http.put("/api/vacations/configurations", json=body, headers=consultant)
    assert response.status_code == 200, response.json()


def assignments_via_ui(http, consultant, company_id, vacations):
    response = http.put("/api/hr/configurations", json={"company_id": company_id, "module": "config_assignments", "effective_from": date.today().isoformat(),
                                                        "values": {"vacations": vacations, "vacations_simulation": []}}, headers=consultant)
    assert response.status_code == 200, response.json()


def simulate(http, headers, employee_id):
    body = {"employee_id": employee_id, "environment": "homologation", "start_date": next_valid_start(date.today() + timedelta(days=60)).isoformat(),
            "requested_days": 20, "installment_count": 1, "requester_type": "manager"}
    return http.post("/api/vacations/simulate", json=body, headers=headers).json()


def test_catalog_lists_every_module_with_schemas_and_stable_hash(integration):
    http, _, headers = integration
    modules = http.get(V2 + "/modules", headers=headers).json()
    ids = {item["id"] for item in modules["items"]}
    assert ids == {"vacations", *DEFAULTS}
    assert {"workflow", "vacation_policy", "config_assignments", "termination"} <= ids
    catalog = http.get(V2 + "/catalog", headers=headers).json()
    assert catalog["version"] == "vacations-2" and catalog["module"] == "vacations"
    assert [scope["type"] for scope in catalog["scopes"]] == ["company", "group", "config"]
    assert [level["scope"] for level in catalog["precedence"]] == ["assignment", "group", "company", "system"]
    assert len(catalog["contract_hash"]) == 64 and catalog["contract_hash"] == http.get(V2 + "/catalog", headers=headers).json()["contract_hash"]
    assert all("schema" in field for field in catalog["fields"])
    assert {item["id"] for item in catalog["related_modules"]} == {"config_assignments", "vacation_policy", "workflow"}
    workflow = http.get(V2 + "/modules/workflow/catalog", headers=headers).json()
    processes = next(field for field in workflow["fields"] if field["id"] == "processes")
    assert processes["schema"]["type"] == "object" and "vacations" in processes["schema"]["properties"]
    assert processes["schema"]["properties"]["vacations"]["properties"]["steps"]["items"]["properties"]["approver"]["enum"] == ["direct_manager", "hr", "system_admin", "target_hr"]
    policy = http.get(V2 + "/catalog?module=vacation_policy", headers=headers).json()
    general = next(field for field in policy["fields"] if field["id"] == "general_configs")
    assert general["schema"]["items"]["required"][0] == "calculation_group"
    assert workflow["contract_hash"] != catalog["contract_hash"] != policy["contract_hash"]
    assert http.get(V2 + "/modules/inexistente/catalog", headers=headers).status_code == 404
    assert all(item["description"] for item in modules["items"] if item["id"] != "other")
    # The v1 contract is retired: its routes no longer exist.
    assert http.get(V1 + "/catalog", headers=headers).status_code == 404
    assert http.get(V1 + "/vacations/snapshot?company_code=EMP001", headers=headers).status_code == 404
    assert http.get(V2 + "/companies?query=abc", headers=headers).json()["items"][0]["code"] == "EMP001"


def test_snapshot_exposes_named_configurations_assignments_and_memberships(integration):
    http, refs, headers = integration
    consultant, admin = auth(http, "consultor@hcmlab.example.com"), auth(http, "admin@hcmlab.example.com")
    company, employee = refs[0]
    other = http.post(f"/api/companies/{company.id}/structure/groups", json={"name": "Operacional"}, headers=admin).json()
    assert http.post(f"/api/companies/{company.id}/setup/collaborator-groups/{other['id']}/members", json={"employee_id": employee.id}, headers=admin).status_code == 201
    named_via_ui(http, consultant, company.id, "CFG-B")
    assignments_via_ui(http, consultant, company.id, [{"group": "Operacional", "config": "CFG-B"}])
    snapshot = http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()
    assert snapshot["contract"] == 2 and snapshot["catalog_version"] == "vacations-2" and len(snapshot["fingerprint"]) == 64
    assert snapshot["contract_hash"] == http.get(V2 + "/catalog", headers=headers).json()["contract_hash"]
    assert [item["config_code"] for item in snapshot["named_configurations"]["homologation"]] == ["CFG-B"]
    assert snapshot["named_configurations"]["production"] == []
    assert snapshot["assignments"]["homologation"]["vacations"] == [{"group": "Operacional", "config": "CFG-B"}]
    groups = {group["name"]: group for group in snapshot["groups"]}
    assert groups["Gestores"]["primary_count"] == 1 and groups["Gestores"]["member_count"] == 1 and groups["Gestores"]["overlap_count"] == 1
    assert groups["Operacional"]["primary_count"] == 0 and groups["Operacional"]["member_count"] == 1 and groups["Operacional"]["overlap_count"] == 1
    effective = {row["group_name"]: row for row in snapshot["effective"]["homologation"]}
    assert effective["Regra geral"]["origin"]["scope"] == "company"
    assert effective["Gestores"]["origin"]["scope"] == "company" and effective["Gestores"]["overlap_count"] == 1
    assert effective["Operacional"]["origin"]["scope"] == "assignment" and effective["Operacional"]["configuration"]["config_code"] == "CFG-B"
    assert effective["Operacional"]["origin"]["priority"] == 1 and "Atribuições" in effective["Operacional"]["why"]
    assert any(row.get("config_code") == "CFG-B" for row in snapshot["schedules"]["homologation"])
    # The operational simulator agrees with the snapshot: the employee is in both groups and the assignment wins.
    result = simulate(http, consultant, employee.id)
    assert result["origin"]["scope"] == "assignment" and result["effective_configuration"]["config_code"] == "CFG-B"
    assert {group["name"]: group["employee_count"] for group in snapshot["groups"]} == {"Gestores": 1, "Operacional": 0}  # primary counts kept for continuity


def test_validate_reports_errors_without_writing(integration):
    http, refs, headers = integration
    before = http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()
    plan = packet(before, [configuration(before, max_installments=3)], {"vacations": [{"group": "Gestores", "config": "NAO-EXISTE"}], "vacations_simulation": []})
    body = {key: plan[key] for key in ("company_code", "effective_from", "configurations", "assignments")}
    report = http.post(V2 + "/vacations/validate", headers=headers, json=body).json()
    assert report["valid"] is False and any("NAO-EXISTE" in error for error in report["errors"]) and report["persisted"] is False
    assert report["fingerprint"] == before["fingerprint"]
    body["assignments"] = None
    assert http.post(V2 + "/vacations/validate", headers=headers, json=body).json()["valid"] is True
    body["configurations"][0]["company_id"] = refs[1][0].id
    assert http.post(V2 + "/vacations/validate", headers=headers, json=body).status_code == 403
    assert http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()["fingerprint"] == before["fingerprint"]


def test_apply_named_configuration_with_assignment_verify_and_rollback(executor):
    http, refs, headers, read_headers = executor
    consultant = auth(http, "consultor@hcmlab.example.com")
    company, employee = refs[0]
    before = http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()
    named = configuration(before, max_installments=3, config_code="CFG-ERP", name="Regra ERP para gestores", simulation_text="Até três parcelas")
    plan = packet(before, [named], {"vacations": [{"group": "Gestores", "config": "CFG-ERP"}], "vacations_simulation": [{"group": "Gestores", "config": "CFG-ERP"}]})
    validation = http.post(V2 + "/vacations/validate", headers=headers, json={key: plan[key] for key in ("company_code", "effective_from", "configurations", "assignments")}).json()
    assert validation["valid"] is True, validation
    assert http.post(V2 + "/vacations/apply", headers=read_headers, json=plan).status_code == 403
    assert http.post(V1 + "/vacations/apply", headers=headers, json=plan).status_code == 404
    response = http.post(V2 + "/vacations/apply", headers=headers, json=plan)
    assert response.status_code == 200, response.text
    operation = response.json()
    assert operation["status"] == "applied" and operation["report"]["passed"], operation["report"]
    assert {item["kind"] for item in operation["created_config_ids"]} == {"vacation", "module"}
    after = operation["after_snapshot"]
    effective = {row["group_name"]: row for row in after["effective"]["homologation"]}
    assert effective["Gestores"]["origin"]["scope"] == "assignment" and effective["Gestores"]["configuration"]["config_code"] == "CFG-ERP"
    assert effective["Gestores"]["configuration"]["max_installments"] == 3 and effective["Regra geral"]["configuration"]["max_installments"] == 2
    assert [item["config_code"] for item in after["named_configurations"]["homologation"]] == ["CFG-ERP"]
    assert after["assignments"]["homologation"]["vacations"] == [{"group": "Gestores", "config": "CFG-ERP"}]
    assert after["schedules"]["production"] == before["schedules"]["production"] and after["assignments"]["production"] == before["assignments"]["production"]
    named_check = next(check for check in operation["report"]["checks"] if check["name"].startswith("Configuração nomeada CFG-ERP"))
    assert "1 grupo" in named_check["detail"]
    result = simulate(http, consultant, employee.id)
    assert result["origin"]["scope"] == "assignment" and result["effective_configuration"]["config_code"] == "CFG-ERP"
    assert result["effective_configuration"]["max_installments"] == 3
    repeat = http.post(V2 + "/vacations/apply", headers=headers, json=plan).json()
    assert repeat["created_config_ids"] == operation["created_config_ids"]
    changed = copy.deepcopy(plan); changed["approved_by"] = "Outra pessoa"
    assert http.post(V2 + "/vacations/apply", headers=headers, json=changed).status_code == 409
    verified = http.get(f"{V2}/vacations/operations/{plan['operation_id']}/verify", headers=read_headers).json()
    assert verified["report"]["passed"]
    rollback = {"expected_fingerprint": after["fingerprint"], "reason": "Reversão do teste do contrato v2"}
    reverted = http.post(f"{V2}/vacations/operations/{plan['operation_id']}/rollback", headers=headers, json=rollback)
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["status"] == "reverted"
    current = http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()
    assert current["fingerprint"] == before["fingerprint"]
    assert current["assignments"]["homologation"]["vacations"] == [] and current["named_configurations"]["homologation"] == []
    assert simulate(http, consultant, employee.id)["origin"]["scope"] == "company"
    assert http.post(f"{V2}/vacations/operations/{plan['operation_id']}/rollback", headers=headers, json=rollback).json()["status"] == "reverted"


def test_group_rule_shadowed_by_assignment_fails_automatic_tests(executor):
    http, refs, headers, _ = executor
    consultant = auth(http, "consultor@hcmlab.example.com")
    company, _ = refs[0]
    named_via_ui(http, consultant, company.id, "CFG-X")
    assignments_via_ui(http, consultant, company.id, [{"group": "Gestores", "config": "CFG-X"}])
    before = http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()
    group_id = next(group["id"] for group in before["groups"] if group["name"] == "Gestores")
    plan = packet(before, [configuration(before, group_id=group_id, min_advance_days=45)])
    response = http.post(V2 + "/vacations/apply", headers=headers, json=plan)
    assert response.status_code == 200, response.text
    operation = response.json()
    assert operation["status"] == "failed" and operation["report"]["rolled_back"]
    shadow = next(check for check in operation["report"]["checks"] if check["name"].startswith("Regra planejada é a efetiva"))
    assert shadow["passed"] is False and "CFG-X" in shadow["detail"]
    assert http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()["fingerprint"] == before["fingerprint"]


def test_generic_module_workflow_validate_apply_verify_rollback(executor):
    from app.hr_api import active_config
    from app.main import app
    from app.database import get_db
    http, refs, headers, read_headers = executor
    path = V2 + "/modules/workflow"
    before = http.get(path + "/snapshot?company_code=EMP002", headers=headers).json()
    assert before["catalog_version"] == "workflow-2" and before["effective"]["homologation"][0]["origin"]["scope"] == "default"
    current = before["effective"]["homologation"][0]["configuration"]
    values = copy.deepcopy({key: current[key] for key in DEFAULTS["workflow"]})
    values["processes"]["vacations"]["steps"] = [{"approver": "direct_manager", "sla_hours": 8}, {"approver": "hr", "sla_hours": 16}]
    item = {"company_id": before["company"]["id"], "group_id": None, "environment": "homologation", "effective_from": before["as_of"], **values}
    assert http.post(path + "/validate", headers=headers, json=item).json()["valid"] is True
    invalid = copy.deepcopy(item); invalid["processes"]["vacations"]["steps"] = [{"approver": "ceo", "sla_hours": 8}]
    report = http.post(path + "/validate", headers=headers, json=invalid).json()
    assert report["valid"] is False and any("vacations" in error for error in report["errors"])
    plan = {"operation_id": str(uuid4()), "approved_by": "Consultor", "company_code": "EMP002", "expected_fingerprint": before["fingerprint"],
            "approval_hash": plan_hash(before["fingerprint"], [item], None), "effective_from": before["as_of"], "configurations": [item]}
    assert http.post(path + "/apply", headers=read_headers, json=plan).status_code == 403
    response = http.post(path + "/apply", headers=headers, json=plan)
    assert response.status_code == 200, response.text
    operation = response.json()
    assert operation["status"] == "applied" and operation["report"]["passed"], operation["report"]
    assert operation["after_snapshot"]["effective"]["homologation"][0]["configuration"]["processes"]["vacations"]["steps"] == values["processes"]["vacations"]["steps"]
    assert operation["after_snapshot"]["schedules"]["production"] == before["schedules"]["production"]
    generator = app.dependency_overrides[get_db](); db = next(generator)
    try:
        assert active_config(db, before["company"]["id"], "workflow", "homologation")["processes"]["vacations"]["steps"] == values["processes"]["vacations"]["steps"]
        assert active_config(db, before["company"]["id"], "workflow", "production")["processes"]["vacations"]["steps"] == DEFAULTS["workflow"]["processes"]["vacations"]["steps"]
    finally:
        generator.close()
    assert http.get(f"{path}/operations/{plan['operation_id']}/verify", headers=headers).json()["report"]["passed"]
    reverted = http.post(f"{path}/operations/{plan['operation_id']}/rollback", headers=headers, json={"expected_fingerprint": operation["after_snapshot"]["fingerprint"], "reason": "Restaurar o fluxo padrão"})
    assert reverted.status_code == 200, reverted.text
    assert http.get(path + "/snapshot?company_code=EMP002", headers=headers).json()["fingerprint"] == before["fingerprint"]
    generator = app.dependency_overrides[get_db](); db = next(generator)
    try:
        assert active_config(db, before["company"]["id"], "workflow", "homologation")["processes"]["vacations"]["steps"] == DEFAULTS["workflow"]["processes"]["vacations"]["steps"]
    finally:
        generator.close()


@pytest.mark.parametrize("module", ["timekeeping", "vacation_policy", "assignments"])
def test_generic_module_round_trip_keeps_defaults_and_cross_checks(module, executor):
    http, _, headers, _ = executor
    path = f"{V2}/modules/{module}"
    before = http.get(path + "/snapshot?company_code=EMP001", headers=headers).json()
    catalog = http.get(path + "/catalog", headers=headers).json()
    assert {field["id"] for field in catalog["fields"]} == set(DEFAULTS[module]) and all("schema" in field for field in catalog["fields"])
    current = before["effective"]["homologation"][0]["configuration"]
    item = {"company_id": before["company"]["id"], "group_id": None, "environment": "homologation", "effective_from": before["as_of"], **{key: current[key] for key in DEFAULTS[module]}}
    if module == "timekeeping":
        item["allow_overtime"] = False; item["max_overtime_hours"] = 0
    elif module == "vacation_policy":
        item["general_configs"] = [dict(item["general_configs"][0], calculation_group="Inexistente")]
        assert http.post(path + "/validate", headers=headers, json=item).json()["valid"] is False  # cross-check against payroll_setup
        item["general_configs"] = [dict(item["general_configs"][0], calculation_group="Mensalistas", monthly_values_treatment="block")]
    else:
        item["rules"] = [{"process": "leave", "requester_group": "Gestores", "targets": ["self", "direct", "indirect"], "config_source": "requester"}, *item["rules"]]
    assert http.post(path + "/validate", headers=headers, json=item).json()["valid"] is True
    plan = {"operation_id": str(uuid4()), "approved_by": "Consultor", "company_code": "EMP001", "expected_fingerprint": before["fingerprint"],
            "approval_hash": plan_hash(before["fingerprint"], [item], None), "effective_from": before["as_of"], "configurations": [item]}
    operation = http.post(path + "/apply", headers=headers, json=plan).json()
    assert operation["status"] == "applied" and operation["report"]["passed"], operation
    reverted = http.post(f"{path}/operations/{plan['operation_id']}/rollback", headers=headers, json={"expected_fingerprint": operation["after_snapshot"]["fingerprint"], "reason": "Restaurar o módulo"})
    assert reverted.status_code == 200, reverted.text
    assert http.get(path + "/snapshot?company_code=EMP001", headers=headers).json()["fingerprint"] == before["fingerprint"]


# ---- behaviours ported from the retired v1 suite

def test_integration_is_disabled_without_secret(client, monkeypatch):
    monkeypatch.delenv("HCM_INTEGRATION_TOKEN_SHA256", raising=False)
    assert client[0].get(V2 + "/catalog").status_code == 401
    assert client[0].get(V2 + "/companies?query=ABC").status_code == 401


def test_company_lookup_accents_homonyms_and_no_personal_data(integration):
    http, refs, headers = integration
    for query in ("abc", "ABC Ltda", "Empresa ABC", "EMP001"):
        data = http.get(V2 + "/companies", params={"query": query}, headers=headers).json()
        assert data["items"][0]["name"] == "ABC" and data["items"][0]["legal_name"] == "ABC Ltda" and data["items"][0]["configuration_allowed"]
    assert http.get(V2 + "/companies?query=industria", headers=headers).json()["items"][0]["name"] == "Indústria"
    assert http.get(V2 + "/companies?query=inexistente", headers=headers).json()["items"] == []
    from app.database import get_db
    from app.main import app
    from app.models import Company
    generator = app.dependency_overrides[get_db](); db = next(generator)
    try:
        db.add(Company(code="EMP003", legal_name="ABC Serviços Ltda", trade_name="ABC", cnpj="00.000.000/0003-03", city="Recife", state="PE", sector="Serviços", status="Ativa")); db.commit()
    finally:
        generator.close()
    assert {c["legal_name"] for c in http.get(V2 + "/companies?query=abc", headers=headers).json()["items"]} == {"ABC Ltda", "ABC Serviços Ltda"}
    response = http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers)
    assert response.status_code == 200 and "salary" not in response.text and "full_name" not in response.text and "email" not in response.text
    assert http.get("/api/companies", headers=headers).status_code == 401
    assert http.put("/api/vacations/configurations", headers=headers, json={}).status_code == 401


def test_stale_plan_tampered_hash_and_later_changes_are_blocked(executor):
    http, refs, headers, _ = executor
    consultant = auth(http, "consultor@hcmlab.example.com")
    before = http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()
    first = packet(before, [configuration(before, max_installments=3)])
    tampered = copy.deepcopy(first); tampered["configurations"][0]["max_installments"] = 1
    assert http.post(V2 + "/vacations/apply", headers=headers, json=tampered).status_code == 409
    operation = http.post(V2 + "/vacations/apply", headers=headers, json=first).json()
    assert operation["status"] == "applied"
    stale = copy.deepcopy(first); stale["operation_id"] = str(uuid4())
    assert http.post(V2 + "/vacations/apply", headers=headers, json=stale).status_code == 409
    manual = {key: value for key, value in first["configurations"][0].items() if key not in ("config_code", "name", "simulation_text")}
    manual["min_advance_days"] += 1
    assert http.put("/api/vacations/configurations", headers=consultant, json=manual).status_code == 200
    rollback = {"expected_fingerprint": operation["after_snapshot"]["fingerprint"], "reason": "Não deve desfazer mudança posterior"}
    assert http.post(f"{V2}/vacations/operations/{first['operation_id']}/rollback", headers=headers, json=rollback).status_code == 409


def test_failed_automatic_tests_roll_back_the_entire_batch(executor, monkeypatch):
    http, _, headers, _ = executor
    before = http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()
    plan = packet(before, [configuration(before, max_installments=3)])
    monkeypatch.setattr(v2, "test_result", lambda *args: {"passed": False, "checks": []})
    response = http.post(V2 + "/vacations/apply", headers=headers, json=plan)
    assert response.status_code == 200 and response.json()["status"] == "failed" and response.json()["report"]["rolled_back"]
    assert http.get(V2 + "/vacations/snapshot?company_code=EMP001", headers=headers).json()["fingerprint"] == before["fingerprint"]


def test_group_and_future_date_rollback_restores_inheritance(executor):
    from datetime import timedelta
    http, _, headers, _ = executor
    future = (date.today() + timedelta(days=30)).isoformat()
    before = http.get(f"{V2}/vacations/snapshot?company_code=EMP001&as_of={future}", headers=headers).json()
    group_id = before["groups"][0]["id"]
    plan = packet(before, [{**configuration(before, group_id=group_id, max_installments=3), "effective_from": future}])
    plan["effective_from"] = future
    operation = http.post(V2 + "/vacations/apply", headers=headers, json=plan).json()
    assert operation["status"] == "applied" and operation["report"]["passed"], operation["report"]
    effective = {row["group_name"]: row for row in operation["after_snapshot"]["effective"]["homologation"]}
    assert effective["Gestores"]["configuration"]["max_installments"] == 3 and effective["Gestores"]["origin"]["scope"] == "group"
    assert effective["Regra geral"]["configuration"]["max_installments"] == 2
    reverted = http.post(f"{V2}/vacations/operations/{plan['operation_id']}/rollback", headers=headers, json={"expected_fingerprint": operation["after_snapshot"]["fingerprint"], "reason": "Restaurar herança original"})
    assert reverted.status_code == 200 and reverted.json()["report"]["rollback"]["restored_snapshot"]["fingerprint"] == before["fingerprint"]
