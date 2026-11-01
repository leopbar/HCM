import copy
import hashlib
from datetime import date, timedelta
from uuid import uuid4
import pytest
from test_api import client, auth
from test_integration import integration
from app.integration_execution import snapshot_hash, plan_hash

@pytest.fixture
def executor(integration, monkeypatch):
    http, refs, headers = integration
    monkeypatch.setenv("HCM_EXECUTION_TOKEN_SHA256", hashlib.sha256(b"execution-test-secret").hexdigest())
    return http, refs, {**headers, "X-HCM-Execution-Key": "execution-test-secret"}, headers

def make_plan(http, headers, group=False, future=False, company_code="EMP001"):
    effective = date.today() + timedelta(days=30 if future else 0)
    before = http.get(f"/api/integration/v1/vacations/snapshot?company_code={company_code}&as_of={effective}", headers=headers).json()
    catalog = http.get("/api/integration/v1/catalog", headers=headers).json()
    config = before["effective"]["homologation"][0]["configuration"]
    group_id = before["groups"][0]["id"] if group else None
    target = {"company_id": before["company"]["id"], "group_id": group_id, "environment": "homologation", "effective_from": str(effective), **{field["id"]: config[field["id"]] for field in catalog["fields"]}}
    target["max_installments"] = 3
    if company_code != "EMP001": target["min_advance_days"] += 1
    expected = snapshot_hash(before)
    return before, {"operation_id": str(uuid4()), "company_code": company_code, "approved_by": "Consultor de teste", "expected_fingerprint": expected,
        "approval_hash": plan_hash(expected, [target]), "effective_from": str(effective), "configurations": [target]}

def test_atomic_apply_tests_idempotency_and_exact_rollback(executor):
    http, refs, headers, read_headers = executor
    before, packet = make_plan(http, headers)
    r = http.post("/api/integration/v1/vacations/apply", headers=headers, json=packet)
    assert r.status_code == 200, r.text
    operation = r.json()
    assert operation["status"] == "applied" and operation["report"]["passed"], operation
    assert operation["after_snapshot"]["effective"]["homologation"][0]["configuration"]["max_installments"] == 3
    assert operation["after_snapshot"]["schedules"]["production"] == before["schedules"]["production"]
    repeat = http.post("/api/integration/v1/vacations/apply", headers=headers, json=packet).json()
    assert repeat["created_config_ids"] == operation["created_config_ids"]
    changed = copy.deepcopy(packet); changed["approved_by"] = "Different"
    assert http.post("/api/integration/v1/vacations/apply", headers=headers, json=changed).status_code == 409
    verified = http.get(f"/api/integration/v1/vacations/operations/{packet['operation_id']}/verify", headers=read_headers).json()
    assert verified["report"]["passed"]
    rollback = {"expected_fingerprint": snapshot_hash(operation["after_snapshot"]), "reason": "Reversão do teste de integração"}
    r = http.post(f"/api/integration/v1/vacations/operations/{packet['operation_id']}/rollback", headers=headers, json=rollback)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reverted"
    current = http.get("/api/integration/v1/vacations/snapshot?company_code=EMP001", headers=headers).json()
    assert snapshot_hash(current) == snapshot_hash(before)
    assert http.post(f"/api/integration/v1/vacations/operations/{packet['operation_id']}/rollback", headers=headers, json=rollback).json()["status"] == "reverted"

def test_group_and_future_date_rollback_restores_inheritance(executor):
    http, _, headers, _ = executor
    before, packet = make_plan(http, headers, group=True, future=True)
    operation = http.post("/api/integration/v1/vacations/apply", headers=headers, json=packet).json()
    assert operation["status"] == "applied" and operation["report"]["passed"]
    effective = operation["after_snapshot"]["effective"]["homologation"]
    assert effective[1]["configuration"]["max_installments"] == 3
    assert effective[0]["configuration"]["max_installments"] == 2
    r = http.post(f"/api/integration/v1/vacations/operations/{packet['operation_id']}/rollback", headers=headers, json={"expected_fingerprint": snapshot_hash(operation["after_snapshot"]), "reason": "Restaurar herança original"})
    assert r.status_code == 200, r.text
    assert snapshot_hash(r.json()["report"]["rollback"]["restored_snapshot"]) == snapshot_hash(before)

def test_read_token_scope_and_hash_cannot_authorize_changes(executor):
    http, refs, headers, read_headers = executor
    _, packet = make_plan(http, headers)
    assert http.post("/api/integration/v1/vacations/apply", headers=read_headers, json=packet).status_code == 403
    invalid = copy.deepcopy(packet); invalid["configurations"][0]["max_installments"] = 1
    assert http.post("/api/integration/v1/vacations/apply", headers=headers, json=invalid).status_code == 409
    for field, value in (("environment", "production"), ("company_id", refs[1][0].id), ("group_id", refs[1][1].group_id)):
        invalid = copy.deepcopy(packet); invalid["configurations"][0][field] = value
        invalid["approval_hash"] = plan_hash(invalid["expected_fingerprint"], invalid["configurations"])
        assert http.post("/api/integration/v1/vacations/apply", headers=headers, json=invalid).status_code == 403

def test_stale_plan_and_later_changes_block_apply_and_rollback(executor):
    http, _, headers, _ = executor
    _, first = make_plan(http, headers)
    operation = http.post("/api/integration/v1/vacations/apply", headers=headers, json=first).json()
    stale = copy.deepcopy(first); stale["operation_id"] = str(uuid4())
    assert http.post("/api/integration/v1/vacations/apply", headers=headers, json=stale).status_code == 409
    manual = copy.deepcopy(first["configurations"][0]); manual["min_advance_days"] += 1
    assert http.put("/api/vacations/configurations", headers=auth(http, "consultor@hcmlab.example.com"), json=manual).status_code == 200
    r = http.post(f"/api/integration/v1/vacations/operations/{first['operation_id']}/rollback", headers=headers, json={"expected_fingerprint": snapshot_hash(operation["after_snapshot"]), "reason": "Não deve desfazer mudança posterior"})
    assert r.status_code == 409

def test_failed_tests_roll_back_entire_batch(executor, monkeypatch):
    import app.integration_execution as execution
    http, _, headers, _ = executor
    before, packet = make_plan(http, headers)
    monkeypatch.setattr(execution, "test_result", lambda *args: {"passed": False, "checks": []})
    response = http.post("/api/integration/v1/vacations/apply", headers=headers, json=packet)
    assert response.status_code == 200
    assert response.json()["status"] == "failed" and response.json()["report"]["rolled_back"]
    current = http.get("/api/integration/v1/vacations/snapshot?company_code=EMP001", headers=headers).json()
    assert snapshot_hash(current) == snapshot_hash(before)

def test_companies_apply_verify_and_rollback_independently(executor):
    http, _, headers, _ = executor
    baseline_a, a = make_plan(http, headers)
    baseline_b, b = make_plan(http, headers, company_code="EMP002")
    op_b = http.post("/api/integration/v1/vacations/apply", headers=headers, json=b)
    assert op_b.status_code == 200, op_b.text
    assert op_b.json()["status"] == "applied"
    assert op_b.json()["after_snapshot"]["company"]["code"] == "EMP002"
    assert http.get("/api/integration/v1/vacations/snapshot?company_code=EMP001", headers=headers).json()["schedules"] == baseline_a["schedules"]
    op_a = http.post("/api/integration/v1/vacations/apply", headers=headers, json=a)
    assert op_a.status_code == 200 and op_a.json()["status"] == "applied"
    for before, packet, operation in ((baseline_b, b, op_b.json()), (baseline_a, a, op_a.json())):
        path = f"/api/integration/v1/vacations/operations/{packet['operation_id']}"
        assert http.get(path, headers=headers).json()["operation_id"] == packet["operation_id"]
        assert http.get(path + "/verify", headers=headers).json()["report"]["passed"]
        repeat = http.post("/api/integration/v1/vacations/apply", headers=headers, json=packet)
        assert repeat.json()["created_config_ids"] == operation["created_config_ids"]
        restored = http.post(path + "/rollback", headers=headers, json={"expected_fingerprint": snapshot_hash(operation["after_snapshot"]), "reason": "Restaurar somente esta empresa"})
        assert restored.status_code == 200, restored.text
        assert snapshot_hash(restored.json()["report"]["rollback"]["restored_snapshot"]) == snapshot_hash(before)

def test_explicit_company_required_for_empty_plan_and_legacy_retry_supported(executor):
    http, _, headers, _ = executor
    _, packet = make_plan(http, headers)
    packet.pop("company_code")
    first = http.post("/api/integration/v1/vacations/apply", headers=headers, json=packet)
    assert first.status_code == 200, first.text
    assert http.post("/api/integration/v1/vacations/apply", headers=headers, json=packet).json()["created_config_ids"] == first.json()["created_config_ids"]
    before, empty = make_plan(http, headers, company_code="EMP002")
    empty["configurations"] = []
    empty["approval_hash"] = plan_hash(empty["expected_fingerprint"], [])
    assert http.post("/api/integration/v1/vacations/apply", headers=headers, json=empty).status_code == 200
    empty["operation_id"] = str(uuid4())
    empty.pop("company_code")
    assert http.post("/api/integration/v1/vacations/apply", headers=headers, json=empty).status_code == 422
