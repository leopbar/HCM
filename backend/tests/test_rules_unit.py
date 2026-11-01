"""Unit tests for deterministic HR rule functions (no HTTP or external services)."""
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from types import SimpleNamespace

from app.hr_api import DEFAULTS, admission_errors, validate_config
from app.models import Base, Company, Department, Employee, EmployeeGroup, Position, VacationConfiguration
from app.schemas import VacationSimulationInput
from app.vacations import config_errors, evaluate_vacation, next_valid_start, resolve_effective_config


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ({"process_enabled": False, "employee_request_enabled": True, "manager_request_enabled": False, "manager_approval_required": True, "simulation_enabled": True, "installments_allowed": True, "max_installments": 2, "min_advance_days": 10, "allow_13th_advance": False, "show_balance_to_employee": True}, "Desative as solicitações"),
        ({"process_enabled": True, "employee_request_enabled": True, "manager_request_enabled": True, "manager_approval_required": True, "simulation_enabled": True, "installments_allowed": False, "max_installments": 3, "min_advance_days": 10, "allow_13th_advance": False, "show_balance_to_employee": True}, "máximo de parcelas deve ser 1"),
        ({"process_enabled": True, "employee_request_enabled": True, "manager_request_enabled": True, "manager_approval_required": True, "simulation_enabled": True, "installments_allowed": True, "max_installments": 1, "min_advance_days": 10, "allow_13th_advance": False, "show_balance_to_employee": True}, "máximo entre 2 e 3"),
        ({"process_enabled": True, "employee_request_enabled": True, "manager_request_enabled": True, "manager_approval_required": True, "simulation_enabled": True, "installments_allowed": True, "max_installments": 2, "min_advance_days": 366, "allow_13th_advance": False, "show_balance_to_employee": True}, "entre 0 e 365"),
    ],
)
def test_vacation_configuration_dependencies_return_specific_errors(values, expected):
    errors = config_errors(values)
    assert any(expected in error for error in errors)


def test_valid_vacation_configuration_has_no_errors():
    assert config_errors({
        "process_enabled": True, "employee_request_enabled": True, "manager_request_enabled": True,
        "manager_approval_required": True, "simulation_enabled": True, "installments_allowed": True,
        "max_installments": 3, "min_advance_days": 30, "allow_13th_advance": False,
        "show_balance_to_employee": True,
    }) == []


@pytest.mark.parametrize(
    ("module", "change", "expected"),
    [
        ("admission", {"min_advance_days": -1}, "antecedência mínima"),
        ("admission", {"min_advance_days": 91}, "não pode superar a máxima"),
        ("timekeeping", {"daily_hours": 0}, "entre 1 e 16 horas"),
        ("timekeeping", {"min_break_minutes": 301}, "entre 0 e 300 minutos"),
        ("timekeeping", {"late_tolerance_minutes": 121}, "entre 0 e 120 minutos"),
        ("timekeeping", {"max_overtime_hours": 9}, "entre 0 e 8 horas"),
    ],
)
def test_hr_configuration_bounds_are_enforced(module, change, expected):
    values = dict(DEFAULTS[module])
    values.update(change)
    assert any(expected in error for error in validate_config(module, values))


def test_hr_configuration_requires_exact_keys_and_types():
    assert validate_config("admission", {"enabled": True})
    values = dict(DEFAULTS["timekeeping"])
    values["daily_hours"] = True
    assert any("daily_hours deve ser int" in error for error in validate_config("timekeeping", values))


def _date_on_weekday(weekday: int, offset: int = 1) -> date:
    current = date.today() + timedelta(days=offset)
    return current + timedelta(days=(weekday - current.weekday()) % 7)


def test_admission_rule_composes_advance_weekend_holiday_and_documents():
    config = dict(DEFAULTS["admission"])
    payload = SimpleNamespace(planned_date=date.today() + timedelta(days=2), documents_complete=False)
    errors = admission_errors(payload, config)
    assert any("antecedência mínima de 5 dias" in error for error in errors)
    assert any("documentos obrigatórios" in error for error in errors)

    payload.planned_date = _date_on_weekday(5, 8)
    errors = admission_errors(payload, config)
    assert any("final de semana não permitido" in error for error in errors)

    config.update({"min_advance_days": 0, "allow_weekends": True, "documents_required": False})
    assert admission_errors(payload, config) == []


@pytest.fixture()
def vacation_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        company = Company(code="UNIT", legal_name="Unidade Teste Ltda", trade_name="Unidade Teste", cnpj="00.000.000/0099-01", city="São Paulo", state="SP", sector="Teste", status="Ativa")
        db.add(company); db.flush()
        department = Department(company_id=company.id, name="RH")
        position = Position(company_id=company.id, title="Analista")
        group = EmployeeGroup(company_id=company.id, name="Gestores")
        db.add_all([department, position, group]); db.flush()
        employee = Employee(company_id=company.id, department_id=department.id, position_id=position.id, group_id=group.id, registration="U-001", full_name="Pessoa de Teste", email="pessoa.unit@example.com", admission_date=date(2020, 1, 1), salary=5000, vacation_balance_days=30, status="Ativo")
        db.add(employee); db.flush()
        common = {"process_enabled": True, "employee_request_enabled": True, "manager_request_enabled": True, "manager_approval_required": True, "simulation_enabled": True, "installments_allowed": True, "min_advance_days": 0, "allow_13th_advance": False, "show_balance_to_employee": True}
        for environment in ("homologation", "production"):
            db.add(VacationConfiguration(scope_key=f"company:{company.id}:default", company_id=company.id, group_id=None, environment=environment, version=1, effective_from=date.today(), max_installments=2, **common))
        db.add(VacationConfiguration(scope_key=f"company:{company.id}:group:{group.id}", company_id=company.id, group_id=group.id, environment="homologation", version=1, effective_from=date.today(), max_installments=3, **common))
        db.flush()
        yield db, company, employee, group
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_effective_vacation_rule_prefers_group_and_is_environment_scoped(vacation_session):
    db, company, employee, group = vacation_session
    config, origin, explanation = resolve_effective_config(db, employee, environment="homologation")
    assert config.max_installments == 3
    assert origin["scope"] == "group"
    assert group.name in explanation and "prevalece" in explanation

    production, production_origin, _ = resolve_effective_config(db, employee, environment="production")
    assert production.max_installments == 2
    assert production_origin["scope"] == "company"


def test_vacation_simulation_applies_same_rule_engine_per_environment(vacation_session):
    db, _, employee, _ = vacation_session
    payload = VacationSimulationInput(employee_id=employee.id, environment="homologation", start_date=next_valid_start(date.today() + timedelta(days=40)), requested_days=10, installment_count=3, requester_type="manager")
    hom = evaluate_vacation(db, payload)
    payload.environment = "production"
    prod = evaluate_vacation(db, payload)
    assert hom["allowed"] is True
    assert hom["origin"]["scope"] == "group"
    assert prod["allowed"] is False
    assert any("no máximo 2 parcelas" in error for error in prod["errors"])
