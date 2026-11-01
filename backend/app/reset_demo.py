"""Explicitly rebuild the fictional demo dataset; requires RESET_DEMO=YES.

System administrator accounts are preserved so the environment stays reachable; every other
account is removed together with the records that reference it."""
import os
from sqlalchemy import delete, update
from app.database import SessionLocal
from app.models import (Activity, AdmissionRequest, BenefitCatalog, Company, ConfigurationImportPreview,
    Department, Dependent, Employee, EmployeeBenefit, EmployeeGroup, EmployeeLeave, EmployeeMovement, EnvironmentPublication,
    HeadcountPlan, HRModuleConfiguration, HRModuleConfigurationHistory, IntegrationOperation, JobPosition, Notification,
    PayrollEvent, PayrollSimulation, Position, ProcessAction, ProcessRequest, RecessRecord, SalaryRange, Termination,
    TimeRecord, TimeRecordAdjustment, TrainingExerciseProgress, TransportEnrollment, TransportLine, User, UserSession,
    VacationConfiguration, VacationConfigurationHistory, VacationRequest, ValueEntry, AccessGroup)
from app.seed import seed_demo_data

if os.getenv("RESET_DEMO") != "YES":
    raise SystemExit("Define RESET_DEMO=YES para apagar e recriar apenas os dados do banco demonstrativo.")

with SessionLocal() as db:
    for model in (Notification, ProcessAction, Dependent, EmployeeLeave, EmployeeMovement, Termination, RecessRecord, TransportEnrollment, ValueEntry,
                  IntegrationOperation, Activity, TrainingExerciseProgress, ConfigurationImportPreview, EnvironmentPublication,
                  EmployeeBenefit, VacationRequest, AdmissionRequest, TimeRecordAdjustment, TimeRecord, PayrollSimulation, ProcessRequest,
                  HRModuleConfigurationHistory, HRModuleConfiguration, VacationConfigurationHistory,
                  VacationConfiguration, BenefitCatalog, PayrollEvent, UserSession):
        db.execute(delete(model))
    db.execute(delete(User).where(User.role != "system_admin"))
    db.execute(update(User).values(company_id=None, employee_id=None, access_group_id=None))
    db.execute(update(JobPosition).values(employee_id=None))
    db.execute(Employee.__table__.update().values(manager_id=None))
    for model in (JobPosition, HeadcountPlan, SalaryRange, TransportLine, AccessGroup, Employee, Department, Position, EmployeeGroup, Company):
        db.execute(delete(model))
    db.commit()
    seed_demo_data(db)
print("Dados demonstrativos recriados. Contas de administrador preservadas; demais contas removidas.")
