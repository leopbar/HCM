from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass

class Company(Base):
    __tablename__ = "companies"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    legal_name: Mapped[str] = mapped_column(String(180))
    trade_name: Mapped[str] = mapped_column(String(180))
    cnpj: Mapped[str] = mapped_column(String(18), unique=True)
    city: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(2))
    sector: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="Ativa")
    employees: Mapped[list["Employee"]] = relationship(back_populates="company")

class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    __table_args__ = (UniqueConstraint("company_id", "name"),)

class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    job_type: Mapped[str] = mapped_column(String(20), default="Mensal", server_default="Mensal")
    __table_args__ = (UniqueConstraint("company_id", "title"),)

class EmployeeGroup(Base):
    __tablename__ = "employee_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    __table_args__ = (UniqueConstraint("company_id", "name"),)

class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("employee_groups.id"))
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    registration: Mapped[str] = mapped_column(String(30))
    full_name: Mapped[str] = mapped_column(String(180))
    email: Mapped[str] = mapped_column(String(180))
    admission_date: Mapped[date] = mapped_column(Date)
    salary: Mapped[float] = mapped_column(Numeric(12, 2))
    vacation_balance_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    status: Mapped[str] = mapped_column(String(20), default="Ativo")
    contract_type: Mapped[str] = mapped_column(String(20), default="CLT", server_default="CLT")
    cpf: Mapped[str | None] = mapped_column(String(14), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    personal_email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    emergency_contact: Mapped[str | None] = mapped_column(String(180), nullable=True)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    calculation_group: Mapped[str] = mapped_column(String(60), default="Mensalistas", server_default="Mensalistas")
    company: Mapped[Company] = relationship(back_populates="employees")
    __table_args__ = (UniqueConstraint("company_id", "registration"), UniqueConstraint("company_id", "email"))

class EmployeeGroupMembership(Base):
    """Participação adicional em grupos de colaboradores (além do grupo principal do cadastro)."""
    __tablename__ = "employee_group_memberships"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("employee_groups.id", ondelete="CASCADE"), index=True)
    __table_args__ = (UniqueConstraint("employee_id", "group_id", name="uq_employee_group_membership"),)

class AccessGroup(Base):
    __tablename__ = "access_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    modules: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("company_id", "name"),)

class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("uq_users_employee_access", "employee_id", unique=True),)
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(180))
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(30), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    access_group_id: Mapped[int | None] = mapped_column(ForeignKey("access_groups.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class UserSession(Base):
    """Server-side session: logout, password change and deactivation revoke it immediately."""
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

class VacationConfiguration(Base):
    __tablename__ = "vacation_configurations"
    id: Mapped[int] = mapped_column(primary_key=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    scope_key: Mapped[str] = mapped_column(String(100), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production", server_default="production", index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("employee_groups.id", ondelete="CASCADE"), nullable=True, index=True)
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    process_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    employee_request_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    manager_request_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    manager_approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    simulation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    installments_allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    max_installments: Mapped[int] = mapped_column(Integer, default=3)
    min_advance_days: Mapped[int] = mapped_column(Integer, default=30)
    allow_13th_advance: Mapped[bool] = mapped_column(Boolean, default=False)
    show_balance_to_employee: Mapped[bool] = mapped_column(Boolean, default=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Configuração nomeada: código e descrição; atribuída a grupos em Atribuições de Configurações.
    config_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    simulation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("scope_key", "version", "environment", name="uq_vacation_config_scope_version_env"),
        Index("ix_vacation_config_company_effective", "company_id", "environment", "effective_from"),
    )

class VacationConfigurationHistory(Base):
    __tablename__ = "vacation_configuration_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("employee_groups.id", ondelete="SET NULL"), nullable=True)
    config_id: Mapped[int | None] = mapped_column(ForeignKey("vacation_configurations.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    environment: Mapped[str] = mapped_column(String(20), default="production", server_default="production", index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(180))
    before_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

class VacationRequest(Base):
    __tablename__ = "vacation_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    approved_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    start_date: Mapped[date] = mapped_column(Date)
    requested_days: Mapped[int] = mapped_column(Integer)
    installment_count: Mapped[int] = mapped_column(Integer)
    advance_13th_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), index=True)
    approval_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_id: Mapped[int] = mapped_column(ForeignKey("vacation_configurations.id"))
    config_version: Mapped[int] = mapped_column(Integer)
    config_scope: Mapped[str] = mapped_column(String(100))
    rules_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cash_bonus_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    acquisition_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class HRModuleConfiguration(Base):
    __tablename__ = "hr_module_configurations"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    module: Mapped[str] = mapped_column(String(30), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production", server_default="production", index=True)
    version: Mapped[int] = mapped_column(Integer)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    effective_from: Mapped[date] = mapped_column(Date)
    values: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("company_id", "module", "version", "environment", name="uq_hr_module_company_version_env"),)

class HRModuleConfigurationHistory(Base):
    __tablename__ = "hr_module_configuration_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    module: Mapped[str] = mapped_column(String(30), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production", server_default="production", index=True)
    version: Mapped[int] = mapped_column(Integer)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(180))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

class AdmissionRequest(Base):
    __tablename__ = "admission_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    candidate_name: Mapped[str] = mapped_column(String(180))
    candidate_email: Mapped[str] = mapped_column(String(180))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    planned_date: Mapped[date] = mapped_column(Date)
    documents_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(30), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    group_id: Mapped[int | None] = mapped_column(ForeignKey("employee_groups.id", ondelete="SET NULL"), nullable=True)
    salary: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    registration: Mapped[str | None] = mapped_column(String(30), nullable=True)
    contract_type: Mapped[str] = mapped_column(String(20), default="CLT", server_default="CLT")
    job_position_id: Mapped[int | None] = mapped_column(ForeignKey("job_positions.id", ondelete="SET NULL"), nullable=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("applications.id", ondelete="SET NULL", use_alter=True, name="fk_admission_application"), nullable=True)

class TimeRecord(Base):
    __tablename__ = "time_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    work_date: Mapped[date] = mapped_column(Date, index=True)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    break_minutes: Mapped[int] = mapped_column(Integer, default=60)
    worked_minutes: Mapped[int] = mapped_column(Integer)
    overtime_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("employee_id", "work_date", name="uq_time_employee_date"),)

class TimeRecordAdjustment(Base):
    __tablename__ = "time_record_adjustments"
    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("time_records.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(180))
    justification: Mapped[str] = mapped_column(Text)
    before_snapshot: Mapped[dict] = mapped_column(JSON)
    after_snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class PayrollEvent(Base):
    __tablename__ = "payroll_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production", server_default="production", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    code: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(20))
    calculation: Mapped[str] = mapped_column(String(20))
    value: Mapped[float] = mapped_column(Numeric(12, 2))
    base: Mapped[str] = mapped_column(String(30), default="salary")
    group_id: Mapped[int | None] = mapped_column(ForeignKey("employee_groups.id", ondelete="CASCADE"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("company_id", "environment", "code", name="uq_payroll_event_company_env_code"),)

class PayrollSimulation(Base):
    __tablename__ = "payroll_simulations"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production", server_default="production", index=True)
    competence: Mapped[str] = mapped_column(String(7), index=True)
    results: Mapped[list] = mapped_column(JSON)
    simulated_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class BenefitCatalog(Base):
    __tablename__ = "benefit_catalog"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production", server_default="production", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    code: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    fixed_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    percentage_value: Mapped[float] = mapped_column(Numeric(7, 2), default=0)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("employee_groups.id", ondelete="CASCADE"), nullable=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    effective_from: Mapped[date] = mapped_column(Date)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("company_id", "environment", "code", name="uq_benefit_company_env_code"),)

class EmployeeBenefit(Base):
    __tablename__ = "employee_benefits"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    benefit_id: Mapped[int] = mapped_column(ForeignKey("benefit_catalog.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("employee_id", "benefit_id", name="uq_employee_benefit"),)

class EnvironmentPublication(Base):
    __tablename__ = "environment_publications"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending_approval", index=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    validation_report: Mapped[list] = mapped_column(JSON)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ConfigurationImportPreview(Base):
    __tablename__ = "configuration_import_previews"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    package: Mapped[dict] = mapped_column(JSON)
    report: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), index=True, default="invalid")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

class TrainingExerciseProgress(Base):
    __tablename__ = "training_exercise_progress"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    exercise_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="not_started")
    last_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("company_id", "user_id", "exercise_id", name="uq_training_progress_company_user_exercise"),)

class IntegrationOperation(Base):
    __tablename__ = "integration_operations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    approved_by: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    before_snapshot: Mapped[dict] = mapped_column(JSON)
    after_snapshot: Mapped[dict] = mapped_column(JSON)
    report: Mapped[dict] = mapped_column(JSON)
    created_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessRequest(Base):
    """One instance of a parameterizable self-service process (Processos Parametrizáveis)."""
    __tablename__ = "process_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    process: Mapped[str] = mapped_column(String(40), index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True)
    requested_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    data: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), index=True, default="pending_approval")
    current_step: Mapped[int] = mapped_column(Integer, default=0)
    steps: Mapped[list] = mapped_column(JSON)
    config_snapshot: Mapped[dict] = mapped_column(JSON)
    reference_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    step_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class ProcessAction(Base):
    __tablename__ = "process_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("process_requests.id", ondelete="CASCADE"), index=True)
    step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(180))
    action: Mapped[str] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    late: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Dependent(Base):
    __tablename__ = "dependents"
    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(180))
    relationship_type: Mapped[str] = mapped_column(String(30))
    birth_date: Mapped[date] = mapped_column(Date)
    cpf: Mapped[str | None] = mapped_column(String(14), nullable=True)
    income_tax_dependent: Mapped[bool] = mapped_column(Boolean, default=False)
    health_plan_dependent: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class EmployeeLeave(Base):
    __tablename__ = "employee_leaves"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    paid: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class EmployeeMovement(Base):
    __tablename__ = "employee_movements"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    effective_date: Mapped[date] = mapped_column(Date)
    before_snapshot: Mapped[dict] = mapped_column(JSON)
    after_snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="applied")
    request_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Termination(Base):
    __tablename__ = "terminations"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    reason: Mapped[str] = mapped_column(String(40))
    notice_type: Mapped[str] = mapped_column(String(20))
    notice_days: Mapped[int] = mapped_column(Integer)
    termination_date: Mapped[date] = mapped_column(Date)
    projected_end_date: Mapped[date] = mapped_column(Date)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class RecessRecord(Base):
    __tablename__ = "recess_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    days: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class SalaryRange(Base):
    """Tabela Salarial: faixa por cargo e nível."""
    __tablename__ = "salary_ranges"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"), index=True)
    level: Mapped[str] = mapped_column(String(20))
    min_salary: Mapped[float] = mapped_column(Numeric(12, 2))
    max_salary: Mapped[float] = mapped_column(Numeric(12, 2))
    __table_args__ = (UniqueConstraint("position_id", "level", name="uq_salary_range_position_level"),)

class HeadcountPlan(Base):
    """Quadro de Vagas: quantidade planejada por departamento e cargo."""
    __tablename__ = "headcount_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"))
    planned: Mapped[int] = mapped_column(Integer)
    __table_args__ = (UniqueConstraint("department_id", "position_id", name="uq_headcount_department_position"),)

class JobPosition(Base):
    """Posição: assento numerado de departamento e cargo, ocupado por no máximo um funcionário."""
    __tablename__ = "job_positions"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), default="vacant")
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, unique=True)
    requisition_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_job_position_company_code"),)

class TransportLine(Base):
    __tablename__ = "transport_lines"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(String(180))
    fare: Mapped[float] = mapped_column(Numeric(8, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_transport_line_company_code"),)

class TransportEnrollment(Base):
    __tablename__ = "transport_enrollments"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    lines: Mapped[list] = mapped_column(JSON)
    workdays: Mapped[int] = mapped_column(Integer, default=22)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class ValueEntry(Base):
    """Lançamento de Valores: valores variáveis informados para uma competência da folha."""
    __tablename__ = "value_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), index=True)
    competence: Mapped[str] = mapped_column(String(7), index=True)
    event_code: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(String(180))
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    request_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    message: Mapped[str] = mapped_column(Text)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="CASCADE"), nullable=True)
    email_status: Mapped[str] = mapped_column(String(20), default="not_sent")
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Vacancy(Base):
    """Vaga de recrutamento: aberta a partir de uma requisição aprovada ou diretamente pelo RH."""
    __tablename__ = "vacancies"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(180))
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"))
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    requisition_id: Mapped[int | None] = mapped_column(ForeignKey("process_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    hiring_manager_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    requirements: Mapped[str] = mapped_column(Text, default="")
    contract_type: Mapped[str] = mapped_column(String(20), default="CLT")
    salary_min: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    salary_max: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    recruitment_type: Mapped[str] = mapped_column(String(20), default="mixed")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    stages: Mapped[list] = mapped_column(JSON)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_vacancy_company_code"),)

class Candidate(Base):
    """Banco de talentos. Dados pessoais tratados com consentimento (LGPD) e passíveis de anonimização."""
    __tablename__ = "candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(180))
    email: Mapped[str] = mapped_column(String(180))
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="site")
    summary: Mapped[str] = mapped_column(Text, default="")
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True)
    consent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("company_id", "email", name="uq_candidate_company_email"),)

class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    score: Mapped[float | None] = mapped_column(Numeric(4, 2), nullable=True)
    admission_id: Mapped[int | None] = mapped_column(ForeignKey("admission_requests.id", ondelete="SET NULL"), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("vacancy_id", "candidate_id", name="uq_application_vacancy_candidate"),)

class ApplicationEvent(Base):
    """Histórico da candidatura: mudanças de etapa, avaliações com nota e comentários."""
    __tablename__ = "application_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    stage: Mapped[str] = mapped_column(String(40))
    to_stage: Mapped[str | None] = mapped_column(String(40), nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criteria: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(180))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60)
    interviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    mode: Mapped[str] = mapped_column(String(20), default="online")
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
