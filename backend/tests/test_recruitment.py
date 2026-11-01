"""Recrutamento e seleção: requisição → vaga → candidatos → etapas → contratação (admissão ou movimentação)."""
from datetime import date, datetime, timedelta
from test_api import auth, client  # noqa: F401 - pytest fixture reuse


def weekday(days: int) -> date:
    holidays = {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (11, 20), (12, 25)}
    day = date.today() + timedelta(days=days)
    while day.weekday() >= 5 or (day.month, day.day) in holidays:
        day += timedelta(days=1)
    return day


def open_vacancy(http, hr, company_id, department_id, position_id, **extra):
    body = {"company_id": company_id, "title": "Analista de Atendimento", "department_id": department_id, "position_id": position_id, "description": "Atendimento a clientes",
            "requirements": "Ensino médio", "salary_min": 3000, "salary_max": 5000, "quantity": 1, **extra}
    created = http.post("/api/recruitment/vacancies", json=body, headers=hr)
    assert created.status_code == 201, created.json()
    opened = http.post(f"/api/recruitment/vacancies/{created.json()['id']}/status", json={"status": "open"}, headers=hr)
    assert opened.status_code == 200 and opened.json()["status"] == "open"
    return opened.json()


def candidate(http, hr, company_id, email, consent=True):
    return http.post("/api/recruitment/candidates", json={"company_id": company_id, "full_name": "Candidata " + email.split("@")[0], "email": email, "source": "linkedin", "consent": consent}, headers=hr)


def test_requisition_approval_opens_a_draft_vacancy(client):
    http, refs = client
    hr, admin = auth(http, "rh@emp001.example.com"), auth(http, "admin@hcmlab.example.com")
    company, employee = refs[0]
    http.put(f"/api/companies/{company.id}/setup/headcount", json={"department_id": employee.department_id, "position_id": employee.position_id, "planned": 5}, headers=hr)
    data = {"department_id": employee.department_id, "position_id": employee.position_id, "quantity": 2, "reason": "aumento_quadro", "salary_proposal": 4000, "justification": "Expansão do atendimento"}
    req = http.post("/api/processes/requests", json={"process": "requisition", "company_id": company.id, "data": data}, headers=hr).json()
    approved = http.post(f"/api/processes/requests/{req['id']}/decision", json={"approved": True}, headers=admin).json()
    vacancy_id = approved["result"]["vacancy_id"]
    vacancy = http.get(f"/api/recruitment/vacancies/{vacancy_id}", headers=hr).json()
    assert vacancy["status"] == "draft" and vacancy["requisition_id"] == req["id"] and vacancy["quantity"] == 2
    too_many = {"company_id": company.id, "title": "Extra", "department_id": employee.department_id, "position_id": employee.position_id, "requisition_id": req["id"], "quantity": 1}
    assert http.post("/api/recruitment/vacancies", json=too_many, headers=hr).status_code == 422  # requisition already fully used


def test_external_candidate_pipeline_until_hired(client):
    http, refs = client
    hr = auth(http, "rh@emp001.example.com")
    company, employee = refs[0]
    hr_user = next(u for u in http.get("/api/access/users", headers=auth(http, "admin@hcmlab.example.com")).json() if u["email"] == "rh@emp001.example.com")
    draft = http.post("/api/recruitment/vacancies", json={"company_id": company.id, "title": "Sem descrição", "department_id": employee.department_id, "position_id": employee.position_id}, headers=hr).json()
    assert http.post(f"/api/recruitment/vacancies/{draft['id']}/status", json={"status": "open"}, headers=hr).status_code == 422
    vacancy = open_vacancy(http, hr, company.id, employee.department_id, employee.position_id)
    assert candidate(http, hr, company.id, "sem.consentimento@example.com", consent=False).status_code == 422
    ana = candidate(http, hr, company.id, "ana@example.com").json()
    bia = candidate(http, hr, company.id, "bia@example.com").json()
    app_ana = http.post(f"/api/recruitment/vacancies/{vacancy['id']}/applications", json={"candidate_id": ana["id"]}, headers=hr).json()
    app_bia = http.post(f"/api/recruitment/vacancies/{vacancy['id']}/applications", json={"candidate_id": bia["id"]}, headers=hr).json()
    assert http.post(f"/api/recruitment/vacancies/{vacancy['id']}/applications", json={"candidate_id": ana["id"]}, headers=hr).status_code == 409
    assert app_ana["stage"] == "Triagem"
    bad = http.post(f"/api/recruitment/applications/{app_ana['id']}/evaluate", json={"criteria": {"Comunicação": 5}}, headers=hr)
    assert bad.status_code == 422
    evaluated = http.post(f"/api/recruitment/applications/{app_ana['id']}/evaluate", json={"criteria": {"Conhecimento técnico": 4, "Comunicação": 5, "Aderência à cultura": 4}, "comment": "Boa"}, headers=hr).json()
    assert evaluated["score"] == 4
    when = (datetime.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
    interview = http.post(f"/api/recruitment/applications/{app_ana['id']}/interviews", json={"scheduled_at": when, "interviewer_id": hr_user["id"], "mode": "online"}, headers=hr)
    assert interview.status_code == 201
    assert http.post(f"/api/recruitment/applications/{app_bia['id']}/interviews", json={"scheduled_at": when, "interviewer_id": hr_user["id"]}, headers=hr).status_code == 409  # same slot
    assert http.post(f"/api/recruitment/applications/{app_ana['id']}/hire", json={"planned_date": weekday(10).isoformat(), "salary": 4000, "registration": "REC-1"}, headers=hr).status_code == 422  # not last stage
    for stage in ("Entrevista RH", "Avaliação técnica", "Entrevista com gestor", "Proposta"):
        assert http.post(f"/api/recruitment/applications/{app_ana['id']}/move", json={"to_stage": stage}, headers=hr).status_code == 200
    assert http.post(f"/api/recruitment/applications/{app_ana['id']}/hire", json={"planned_date": weekday(10).isoformat(), "salary": 9000, "registration": "REC-1"}, headers=hr).status_code == 422  # above range
    hired = http.post(f"/api/recruitment/applications/{app_ana['id']}/hire", json={"planned_date": weekday(10).isoformat(), "salary": 4000, "registration": "REC-1", "documents_complete": True}, headers=hr)
    assert hired.status_code == 200 and hired.json()["kind"] == "admission" and hired.json()["application"]["status"] == "offer"
    decision = http.post(f"/api/hr/admissions/{hired.json()['admission_id']}/decision", json={"approved": True}, headers=hr).json()
    assert decision["employee_id"]
    detail = http.get(f"/api/recruitment/vacancies/{vacancy['id']}", headers=hr).json()
    assert detail["status"] == "filled" and detail["hired"] == 1
    assert http.post(f"/api/recruitment/applications/{app_bia['id']}/reject", json={"reason": "Vaga preenchida"}, headers=hr).status_code == 200
    summary = http.get(f"/api/recruitment/summary?company_id={company.id}", headers=hr).json()
    assert summary["hired"] == 1


def test_internal_application_becomes_a_movement(client):
    http, refs = client
    hr, admin = auth(http, "rh@emp001.example.com"), auth(http, "admin@hcmlab.example.com")
    me = auth(http, "pessoa@emp001.example.com")
    company, employee = refs[0]
    position = http.post(f"/api/companies/{company.id}/structure/positions", json={"name": "Coordenador"}, headers=admin).json()
    vacancy = open_vacancy(http, hr, company.id, employee.department_id, position["id"], recruitment_type="internal", salary_min=5000, salary_max=7000)
    listed = http.get(f"/api/recruitment/vacancies?company_id={company.id}", headers=me).json()
    assert [v["id"] for v in listed] == [vacancy["id"]] and "candidates" not in listed[0]
    assert http.get(f"/api/recruitment/candidates?company_id={company.id}", headers=me).status_code == 403
    assert http.post(f"/api/recruitment/vacancies/{vacancy['id']}/apply", json={"consent": False}, headers=me).status_code == 422
    applied = http.post(f"/api/recruitment/vacancies/{vacancy['id']}/apply", json={"consent": True, "summary": "Quero crescer"}, headers=me)
    assert applied.status_code == 201 and applied.json()["internal"] is True and "score" not in applied.json()
    app_id = applied.json()["id"]
    assert http.post(f"/api/recruitment/applications/{app_id}/move", json={"to_stage": "Proposta"}, headers=me).status_code == 403
    assert http.post(f"/api/recruitment/applications/{app_id}/move", json={"to_stage": "Proposta"}, headers=hr).status_code == 200
    hired = http.post(f"/api/recruitment/applications/{app_id}/hire", json={"planned_date": date.today().isoformat(), "salary": 6000}, headers=hr)
    assert hired.status_code == 200 and hired.json()["kind"] == "movement", hired.json()
    mine = http.get("/api/recruitment/applications/mine", headers=me).json()
    assert mine[0]["status"] == "hired"
    assert http.get(f"/api/employees/{employee.id}", headers=hr).json()["position"] == "Coordenador"


def test_candidate_data_can_be_anonymized_after_the_process(client):
    http, refs = client
    hr = auth(http, "rh@emp001.example.com")
    company, employee = refs[0]
    vacancy = open_vacancy(http, hr, company.id, employee.department_id, employee.position_id)
    cand = candidate(http, hr, company.id, "lgpd@example.com").json()
    application = http.post(f"/api/recruitment/vacancies/{vacancy['id']}/applications", json={"candidate_id": cand["id"]}, headers=hr).json()
    assert http.post(f"/api/recruitment/candidates/{cand['id']}/anonymize", headers=hr).status_code == 409
    http.post(f"/api/recruitment/applications/{application['id']}/reject", json={"reason": "Perfil diferente"}, headers=hr)
    assert http.post(f"/api/recruitment/candidates/{cand['id']}/anonymize", headers=hr).status_code == 200
    assert all(c["email"] != "lgpd@example.com" for c in http.get(f"/api/recruitment/candidates?company_id={company.id}", headers=hr).json())
    other_hr = auth(http, "rh@emp002.example.com")
    assert http.get(f"/api/recruitment/vacancies/{vacancy['id']}", headers=other_hr).status_code == 403


def test_recruitment_parameters_are_validated(client):
    http, refs = client
    consultant = auth(http, "consultor@hcmlab.example.com")
    company = refs[0][0]
    current = http.get(f"/api/hr/configurations/recruitment?company_id={company.id}&environment=homologation", headers=consultant).json()["effective"]["values"]
    body = {"company_id": company.id, "module": "recruitment", "effective_from": date.today().isoformat(), "values": {**current, "stages": ["Única"]}}
    assert http.put("/api/hr/configurations", json=body, headers=consultant).status_code == 422
    body["values"]["stages"] = ["Triagem", "Entrevista", "Proposta"]
    assert http.put("/api/hr/configurations", json=body, headers=consultant).status_code == 200
