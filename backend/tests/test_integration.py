import hashlib
from datetime import date, timedelta
import pytest
from sqlalchemy import select
from app.database import get_db
from app.models import Company, VacationConfiguration
from app.main import app
from test_api import client

@pytest.fixture
def integration(client, monkeypatch):
    monkeypatch.setenv("HCM_INTEGRATION_TOKEN_SHA256", hashlib.sha256(b"test-only-integration-secret").hexdigest())
    http, refs = client
    return http, refs, {"Authorization": "Bearer test-only-integration-secret"}

def test_integration_is_disabled_without_secret(client, monkeypatch):
    monkeypatch.delenv("HCM_INTEGRATION_TOKEN_SHA256", raising=False)
    assert client[0].get("/api/integration/v1/catalog").status_code == 401
    assert client[0].get("/api/integration/v1/companies?query=ABC").status_code == 401

def test_company_lookup_by_trade_name_legal_name_and_accents(integration):
    http, _, headers = integration
    for query in ("abc", "ABC Ltda", "Empresa ABC", "EMP001"):
        data = http.get("/api/integration/v1/companies", params={"query": query}, headers=headers).json()
        assert data["items"][0]["name"] == "ABC" and data["items"][0]["configuration_allowed"]
        assert data["items"][0]["legal_name"] == "ABC Ltda"
    result = http.get("/api/integration/v1/companies?query=industria", headers=headers).json()
    assert result["items"][0]["name"] == "Indústria"
    assert result["items"][0]["configuration_allowed"]
    assert http.get("/api/integration/v1/companies?query=inexistente", headers=headers).json()["items"] == []
    assert http.get("/api/integration/v1/vacations/snapshot?company_code=EMP002", headers=headers).status_code == 200

def test_company_lookup_returns_homonyms_for_user_to_disambiguate(integration):
    http, _, headers = integration
    generator = app.dependency_overrides[get_db]()
    db = next(generator)
    try:
        db.add(Company(code="EMP003", legal_name="ABC Serviços Ltda", trade_name="ABC", cnpj="00.000.000/0003-03", city="Recife", state="PE", sector="Serviços", status="Ativa"))
        db.commit()
    finally:
        generator.close()
    result = http.get("/api/integration/v1/companies?query=abc", headers=headers).json()
    assert len(result["items"]) == 2
    assert {c["legal_name"] for c in result["items"]} == {"ABC Ltda", "ABC Serviços Ltda"}

def test_company_scope_and_no_personal_data(integration):
    http, refs, headers = integration
    response = http.get("/api/integration/v1/vacations/snapshot?company_code=EMP001", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["company"]["code"] == "EMP001"
    assert "salary" not in response.text and "full_name" not in response.text and "email" not in response.text
    assert http.get("/api/integration/v1/vacations/snapshot?company_code=EMP002", headers=headers).status_code == 200
    assert http.get("/api/companies", headers=headers).status_code == 401
    assert http.put("/api/vacations/configurations", headers=headers, json={}).status_code == 401
    assert http.post("/api/environments/publications", headers=headers, json={}).status_code == 401

def test_future_effective_rule_and_validation_without_writes(integration):
    http, refs, headers = integration
    future = date.today() + timedelta(days=30)
    generator = app.dependency_overrides[get_db]()
    db = next(generator)
    try:
        base = db.scalar(select(VacationConfiguration).where(VacationConfiguration.company_id == refs[0][0].id, VacationConfiguration.environment == "homologation"))
        values = {name: getattr(base, name) for name in ("process_enabled", "employee_request_enabled", "manager_request_enabled", "manager_approval_required", "simulation_enabled", "installments_allowed", "min_advance_days", "allow_13th_advance", "show_balance_to_employee")}
        db.add(VacationConfiguration(scope_key=base.scope_key, company_id=base.company_id, environment="homologation", version=2, effective_from=future, max_installments=3, **values)); db.commit()
    finally:
        generator.close()
    current = http.get("/api/integration/v1/vacations/snapshot?company_code=EMP001", headers=headers).json()
    later = http.get(f"/api/integration/v1/vacations/snapshot?company_code=EMP001&as_of={future}", headers=headers).json()
    assert current["effective"]["homologation"][0]["configuration"]["max_installments"] == 2
    assert later["effective"]["homologation"][0]["configuration"]["max_installments"] == 3
    payload = {"company_id": refs[0][0].id, "environment": "homologation", "group_id": None, "effective_from": str(future), "max_installments": 3, **values}
    response = http.post("/api/integration/v1/vacations/validate", headers=headers, json=payload)
    assert response.status_code == 200 and response.json() == {"valid": True, "errors": [], "persisted": False}
    payload["installments_allowed"] = False
    assert http.post("/api/integration/v1/vacations/validate", headers=headers, json=payload).json()["valid"] is False
    payload["company_id"] = refs[1][0].id
    assert http.post("/api/integration/v1/vacations/validate", headers=headers, json=payload).status_code == 200
    unchanged = http.get(f"/api/integration/v1/vacations/snapshot?company_code=EMP001&as_of={future}", headers=headers).json()
    assert later["schedules"] == unchanged["schedules"]
