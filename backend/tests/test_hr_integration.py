import copy
from datetime import date
from uuid import uuid4
import pytest
from test_execution import executor
from test_integration import integration
from test_api import client
from app.integration_execution import snapshot_hash, plan_hash

def plan(http, headers, module, company="EMP002"):
    path=f"/api/integration/v1/modules/{module}"
    before=http.get(path+"/snapshot",headers=headers,params={"company_code":company}).json()
    fields=http.get(path+"/catalog",headers=headers).json()["fields"]
    current=before["effective"]["homologation"][0]["configuration"]
    config={"company_id":before["company"]["id"],"group_id":None,"environment":"homologation","effective_from":before["as_of"],**{f["id"]:current[f["id"]] for f in fields}}
    key={"timekeeping":"allow_overtime","admission":"documents_required","payroll":"show_demo_warning","benefits":"allow_employee_requests"}[module]
    config[key]=not config[key]
    if module=="timekeeping": config["max_overtime_hours"]=0
    expected=snapshot_hash(before)
    return path,before,{"operation_id":str(uuid4()),"company_code":company,"approved_by":"Consultor", "expected_fingerprint":expected,"approval_hash":plan_hash(expected,[config]),"effective_from":before["as_of"],"configurations":[config]}

@pytest.mark.parametrize("module",["timekeeping","admission","payroll","benefits"])
def test_available_hr_parameters_apply_verify_and_restore(module, executor):
    http,_,headers,read_headers=executor
    path,before,packet=plan(http,headers,module)
    other=http.get(path+"/snapshot?company_code=EMP001",headers=headers).json()
    assert http.post(path+"/validate",headers=headers,json=packet["configurations"][0]).json()["valid"]
    assert http.post(path+"/apply",headers=read_headers,json=packet).status_code==403
    response=http.post(path+"/apply",headers=headers,json=packet)
    assert response.status_code==200,response.text
    op=response.json()
    assert op["status"]=="applied" and op["report"]["passed"],op
    assert http.post(path+"/apply",headers=headers,json=packet).json()["created_config_ids"]==op["created_config_ids"]
    assert http.get(path+f"/operations/{op['operation_id']}/verify",headers=headers).json()["report"]["passed"]
    assert http.get(path+"/snapshot?company_code=EMP001",headers=headers).json()["schedules"]==other["schedules"]
    assert op["after_snapshot"]["schedules"]["production"]==before["schedules"]["production"]
    assert http.get(f"/api/integration/v1/vacations/operations/{op['operation_id']}",headers=headers).status_code==404
    restored=http.post(path+f"/operations/{op['operation_id']}/rollback",headers=headers,json={"expected_fingerprint":snapshot_hash(op["after_snapshot"]),"reason":"Restaurar o teste do módulo"})
    assert restored.status_code==200,restored.text
    assert snapshot_hash(restored.json()["report"]["rollback"]["restored_snapshot"])==snapshot_hash(before)
    # Real operational resolver must ignore the withdrawn version too.
    from app.hr_api import active_config
    from app.main import app
    from app.database import get_db
    gen=app.dependency_overrides[get_db]();db=next(gen)
    try:
        assert active_config(db,before["company"]["id"],module,"homologation")=={f:before["effective"]["homologation"][0]["configuration"][f] for f in packet["configurations"][0] if f not in {"company_id","group_id","environment","effective_from"}}
    finally:gen.close()

def test_hr_scope_and_staleness_are_enforced(executor):
    http,refs,headers,_=executor
    path,before,packet=plan(http,headers,"timekeeping")
    bad=copy.deepcopy(packet);bad["configurations"][0]["company_id"]=refs[0][0].id
    bad["approval_hash"]=plan_hash(bad["expected_fingerprint"],bad["configurations"])
    assert http.post(path+"/apply",headers=headers,json=bad).status_code==403
    bad=copy.deepcopy(packet);bad["configurations"][0]["group_id"]=refs[1][1].group_id
    bad["approval_hash"]=plan_hash(bad["expected_fingerprint"],bad["configurations"])
    assert http.post(path+"/apply",headers=headers,json=bad).status_code==403
    bad=copy.deepcopy(packet);bad["configurations"][0]["environment"]="production"
    bad["approval_hash"]=plan_hash(bad["expected_fingerprint"],bad["configurations"])
    assert http.post(path+"/apply",headers=headers,json=bad).status_code==403
    assert http.post(path+"/apply",headers=headers,json=packet).json()["status"]=="applied"
    packet["operation_id"]=str(uuid4())
    assert http.post(path+"/apply",headers=headers,json=packet).status_code==409
