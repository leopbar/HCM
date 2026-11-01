from datetime import date, timedelta
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from app.validators import UFS, format_cpf, valid_cpf

UF = Literal[UFS]
EmployeeStatus = Literal["Ativo", "Afastado", "Férias", "Desligado", "Inativo"]
ContractType = Literal["CLT", "Estágio", "Aprendiz", "Temporário", "Diretor"]

class LoginInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)

class CompanyInput(BaseModel):
    code: str = Field(min_length=2, max_length=20)
    legal_name: str = Field(min_length=2, max_length=180)
    trade_name: str = Field(min_length=2, max_length=180)
    cnpj: str = Field(min_length=1, max_length=18)
    city: str = Field(min_length=2, max_length=100)
    state: UF
    sector: str = Field(min_length=2, max_length=100)
    status: Literal["Ativa", "Inativa"] = "Ativa"

    @field_validator("cnpj")
    @classmethod
    def validate_fictional_cnpj(cls, value: str) -> str:
        if not re.fullmatch(r"(?:\d{14}|\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", value):
            raise ValueError("Informe 14 dígitos, sem pontuação ou no formato 00.000.000/0000-00.")
        return value

class CompanyCatalogItemInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_catalog_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Informe um nome para o cadastro.")
        return value

class CompanyOut(CompanyInput):
    model_config = ConfigDict(from_attributes=True)
    state: str
    status: str
    id: int
    employee_count: int = 0

class EmployeePersonalData(BaseModel):
    cpf: str | None = Field(default=None, max_length=14)
    birth_date: date | None = None
    phone: str | None = Field(default=None, max_length=30)
    personal_email: EmailStr | None = None
    marital_status: Literal["Solteiro(a)", "Casado(a)", "União estável", "Divorciado(a)", "Viúvo(a)"] | None = None
    address: str | None = Field(default=None, max_length=300)
    emergency_contact: str | None = Field(default=None, max_length=180)

    @field_validator("cpf")
    @classmethod
    def validate_cpf(cls, value):
        if value in (None, ""):
            return None
        if not valid_cpf(value):
            raise ValueError("CPF inválido.")
        return format_cpf(value)

    @field_validator("birth_date")
    @classmethod
    def validate_birth(cls, value):
        if value and not (date.today() - timedelta(days=365 * 100) <= value <= date.today() - timedelta(days=365 * 14)):
            raise ValueError("Data de nascimento fora do intervalo permitido (14 a 100 anos).")
        return value

class EmployeeInput(EmployeePersonalData):
    company_id: int
    department_id: int
    position_id: int
    group_id: int
    manager_id: int | None = None
    registration: str = Field(min_length=1, max_length=30)
    full_name: str = Field(min_length=2, max_length=180)
    email: EmailStr
    admission_date: date
    salary: float = Field(gt=0, le=10000000)
    status: Literal["Ativo", "Afastado"] = "Ativo"
    contract_type: ContractType = "CLT"
    job_position_id: int | None = None
    calculation_group: str = Field(default="Mensalistas", min_length=2, max_length=60)

    @field_validator("admission_date")
    @classmethod
    def validate_admission(cls, value):
        if value > date.today() + timedelta(days=90):
            raise ValueError("A data de admissão não pode estar a mais de 90 dias no futuro.")
        if value < date(1950, 1, 1):
            raise ValueError("Data de admissão inválida.")
        return value

class EmployeeUpdate(EmployeePersonalData):
    full_name: str = Field(min_length=2, max_length=180)
    email: EmailStr
    manager_id: int | None = None
    status: EmployeeStatus
    contract_type: ContractType = "CLT"
    calculation_group: str = Field(default="Mensalistas", min_length=2, max_length=60)

class EmployeeOut(BaseModel):
    id: int
    company_id: int
    company_name: str
    department: str
    position: str
    group_name: str
    registration: str
    full_name: str
    email: str
    admission_date: date
    salary: float
    status: str
    manager_id: int | None = None
    manager_name: str | None = None
    contract_type: str = "CLT"
    department_id: int | None = None
    position_id: int | None = None
    group_id: int | None = None
    termination_date: date | None = None
    calculation_group: str = "Mensalistas"

class VacationConfigurationInput(BaseModel):
    company_id: int
    environment: Literal["homologation", "production"] = "homologation"
    group_id: int | None = None
    effective_from: date
    process_enabled: bool
    employee_request_enabled: bool
    manager_request_enabled: bool
    manager_approval_required: bool
    simulation_enabled: bool
    installments_allowed: bool
    max_installments: int = Field(ge=1, le=3)
    min_advance_days: int = Field(ge=0, le=365)
    allow_13th_advance: bool
    show_balance_to_employee: bool
    # Configuração nomeada (Configuração → Autoatendimento → Processos → Férias)
    config_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_-]{1,20}$")
    name: str | None = Field(default=None, max_length=120)
    simulation_text: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def check_named(self):
        if self.config_code and self.group_id is not None:
            raise ValueError("Uma configuração nomeada não é ligada diretamente a um grupo: use Atribuições de Configurações.")
        if self.config_code and not (self.name or "").strip():
            raise ValueError("Informe a descrição da configuração.")
        return self

class VacationSimulationInput(BaseModel):
    employee_id: int
    environment: Literal["homologation", "production"] = "production"
    start_date: date
    requested_days: int = Field(ge=1, le=60)
    installment_count: int = Field(ge=1, le=3)
    advance_13th_requested: bool = False
    requester_type: Literal["employee", "manager"]
    cash_bonus_days: int = Field(default=0, ge=0, le=10)

class VacationRequestInput(VacationSimulationInput):
    pass

class VacationDecisionInput(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)

ModuleName = Literal["admission", "timekeeping", "payroll", "benefits", "contracting", "vacation_policy", "termination", "transport", "work_schedule", "dependents", "personal_data", "leave", "movement", "transfer", "requisition", "recess", "value_entry", "workflow", "assignments", "recruitment", "payroll_setup", "config_assignments", "process_fields"]
EnvironmentName = Literal["homologation", "production"]

class HRModuleConfigurationInput(BaseModel):
    company_id: int
    module: ModuleName
    environment: EnvironmentName = "homologation"
    effective_from: date
    values: dict

class AdmissionRequestInput(BaseModel):
    company_id: int
    candidate_name: str = Field(min_length=2, max_length=180)
    candidate_email: EmailStr
    department_id: int
    position_id: int
    planned_date: date
    documents_complete: bool = False
    group_id: int | None = None
    salary: float | None = Field(default=None, gt=0, le=10000000)
    registration: str | None = Field(default=None, min_length=1, max_length=30)
    contract_type: ContractType = "CLT"
    job_position_id: int | None = None

class AdmissionSimulationInput(AdmissionRequestInput):
    environment: EnvironmentName = "homologation"

class AdmissionDecisionInput(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)

class TimeSimulationInput(BaseModel):
    employee_id: int
    environment: EnvironmentName = "production"
    work_date: date
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    break_minutes: int = Field(ge=0, le=300)

class TimeAdjustmentInput(TimeSimulationInput):
    justification: str = Field(min_length=10, max_length=1000)

class PayrollEventInput(BaseModel):
    company_id: int
    environment: EnvironmentName = "homologation"
    code: str = Field(min_length=1, max_length=30)
    description: str = Field(min_length=2, max_length=180)
    kind: Literal["earning", "deduction"]
    calculation: Literal["fixed", "percentage"]
    value: float = Field(ge=0, le=10000000)
    base: Literal["salary"] = "salary"
    group_id: int | None = None
    active: bool = True

    @model_validator(mode="after")
    def check_value(self):
        if self.calculation == "percentage" and self.value > 100:
            raise ValueError("Eventos percentuais aceitam no máximo 100% do salário-base.")
        if self.calculation == "fixed" and self.value > 1000000:
            raise ValueError("Valor fixo acima do limite demonstrativo de R$ 1.000.000,00.")
        return self

class PayrollSimulationInput(BaseModel):
    company_id: int
    environment: EnvironmentName = "homologation"
    competence: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    employee_id: int | None = None

class BenefitInput(BaseModel):
    company_id: int
    environment: EnvironmentName = "homologation"
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=2, max_length=180)
    description: str = Field(default="", max_length=2000)
    fixed_value: float = Field(default=0, ge=0, le=10000000)
    percentage_value: float = Field(default=0, ge=0, le=100)
    group_id: int | None = None
    approval_required: bool = True
    effective_from: date
    active: bool = True

class BenefitRequestInput(BaseModel):
    employee_id: int
    benefit_id: int

class BenefitDecisionInput(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)

class PublicationRequestInput(BaseModel):
    company_id: int

class PublicationDecisionInput(BaseModel):
    approved: bool
    comment: str = Field(default="", max_length=1000)

class ConfigurationImportPreviewInput(BaseModel):
    company_id: int
    package: dict

class TrainingExerciseCheckInput(BaseModel):
    probe: dict = Field(default_factory=dict)
