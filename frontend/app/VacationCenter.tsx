"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Activity, BadgeCheck, CalendarDays, CircleAlert, ClipboardCheck, FlaskConical, History, Plus, Save, ShieldCheck, Users } from "lucide-react";

type Role="system_admin"|"consultant"|"hr_manager"|"employee";
type User={id:number;email:string;full_name:string;role:Role;company_id:number|null;employee_id:number|null};
type Company={id:number;trade_name:string};
type Employee={id:number;company_id:number;company_name:string;full_name:string;registration:string;group_name:string};
type Config={id:number;scope_key:string;company_id:number|null;group_id:number|null;version:number;environment:string;effective_from:string;active:boolean;process_enabled:boolean;employee_request_enabled:boolean;manager_request_enabled:boolean;manager_approval_required:boolean;simulation_enabled:boolean;installments_allowed:boolean;max_installments:number;min_advance_days:number;allow_13th_advance:boolean;show_balance_to_employee:boolean;company_name:string;group_name:string|null};
type ConfigState=Omit<Config,"id"|"scope_key"|"company_id"|"group_id"|"version"|"environment"|"active"|"company_name"|"group_name">;
type RequestRow={id:number;company_id:number;company_name:string;employee_id:number;employee_name:string;requested_by:string;approved_by:string|null;start_date:string;requested_days:number;installment_count:number;advance_13th_requested:boolean;status:string;approval_comment:string|null;config_version:number;config_scope:string;created_at:string|null;cash_bonus_days?:number;acknowledged_at?:string|null;end_date?:string;return_date?:string;current_approver?:string|null;due_at?:string|null};
type Period={start:string;end:string;concession_end:string;remaining:number;parcels:number[];cash_bonus?:number};
type HistoryRow={id:number;group_id:number|null;version:number;actor:string;before:Record<string,unknown>|null;after:Record<string,unknown>;created_at:string|null};
type Effective={employee:{id:number;name:string;group_id:number;company_id:number};configuration:Config;origin:{scope:string;label:string;version:number};why:string;balance_days:number|null;acquisition_period?:Period|null;policy?:{cash_bonus_allowed:boolean;notice_min_days:number;require_acknowledgement:boolean}};
type Simulation={allowed:boolean;errors:string[];effective_configuration:Config;origin:{scope:string;label:string;version:number};why:string;balance_days:number|null;employee:{id:number;full_name:string}};

const today=()=>{const d=new Date();return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`};
const futureDate=(days:number)=>{const d=new Date();d.setDate(d.getDate()+days);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`};
const ptDate=(value:string|null)=>value?new Date(`${value.slice(0,10)}T12:00:00`).toLocaleDateString("pt-BR"):"—";
const defaultConfig:ConfigState={effective_from:today(),process_enabled:true,employee_request_enabled:true,manager_request_enabled:true,manager_approval_required:true,simulation_enabled:true,installments_allowed:true,max_installments:2,min_advance_days:30,allow_13th_advance:false,show_balance_to_employee:true};
const settingFields:[keyof ConfigState,string][]=[["process_enabled","Habilitar processo de férias"],["employee_request_enabled","Permitir solicitação pelo funcionário"],["manager_request_enabled","Permitir solicitação pelo gestor"],["manager_approval_required","Exigir aprovação do gestor"],["simulation_enabled","Permitir simulação"],["installments_allowed","Permitir parcelamento"],["allow_13th_advance","Permitir adiantamento do 13º"],["show_balance_to_employee","Exibir saldo ao funcionário"]];

async function api<T>(path:string,token:string,init:RequestInit={}):Promise<T>{
  const response=await fetch(`${process.env.NEXT_PUBLIC_API_URL||"http://localhost:8000/api"}${path}`,{...init,credentials:"include",headers:{"Content-Type":"application/json","X-Requested-With":"HCMLab",...init.headers}});
  const body=await response.json().catch(()=>({detail:"Não foi possível ler a resposta da API."}));
  if(!response.ok){const detail=Array.isArray(body.detail)?body.detail.join(" "):body.detail;throw new Error(detail||`Erro HTTP ${response.status}`)}
  return body as T;
}

export default function VacationCenter({token,user,companyId,companies,environment,onToast,initialTab}:{token:string;user:User;companyId:string;companies:Company[];environment:"homologation"|"production";initialTab?:"requests"|"parameters"|"lab";onToast:(message:string)=>void}){
  const isGlobal=user.role==="system_admin"||user.role==="consultant";
  const canConfigure=isGlobal;
  const canOperate=user.role==="employee"||user.role==="hr_manager";
  const canApprove=user.role==="system_admin"||user.role==="hr_manager";
  const canLab=isGlobal||user.role==="hr_manager";
  const activeCompany=companyId||String(user.company_id||companies[0]?.id||"");
  const [tab,setTab]=useState<"requests"|"parameters"|"lab">(initialTab??(canConfigure?"parameters":"requests"));
  const [employees,setEmployees]=useState<Employee[]>([]);const [requests,setRequests]=useState<RequestRow[]>([]);
  const [configs,setConfigs]=useState<Config[]>([]);const [systemConfig,setSystemConfig]=useState<Config|null>(null);const [groups,setGroups]=useState<{id:number;name:string}[]>([]);const [history,setHistory]=useState<HistoryRow[]>([]);
  const [employeeId,setEmployeeId]=useState("");const [groupId,setGroupId]=useState("");const [configForm,setConfigForm]=useState<ConfigState>(defaultConfig);
  const [requestForm,setRequestForm]=useState({start_date:futureDate(60),requested_days:"20",installment_count:"1",cash_bonus_days:"0",advance_13th_requested:false,requester_type:(user.role==="employee"?"employee":"manager") as "employee"|"manager"});
  const [effective,setEffective]=useState<Effective|null>(null);const [simulation,setSimulation]=useState<Simulation|null>(null);
  const [loading,setLoading]=useState(false);const [saving,setSaving]=useState(false);const [error,setError]=useState("");

  const loadRequests=useCallback(async()=>{
    if(!token)return;
    const query=activeCompany&&isGlobal?`?company_id=${activeCompany}`:"";
    const result=await api<RequestRow[]>(`/vacations/requests${query}`,token);setRequests(result);
  },[token,activeCompany,isGlobal]);
  const loadEffective=useCallback(async(id:string)=>{
    if(!id)return;
    const result=await api<Effective>(`/vacations/effective?employee_id=${id}&environment=${environment}`,token);setEffective(result);
  },[token,environment]);
  // Load the persisted vacation data whenever the selected company changes.
  useEffect(()=>{
    if(!token||!activeCompany)return;
    let cancelled=false;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);setError("");
    const jobs:Promise<unknown>[]=[api<Employee[]>(`/employees?company_id=${activeCompany}`,token),loadRequests()];
    if(user.role!=="employee")jobs.push(api<{configurations:Config[];system_default:Config|null;groups:{id:number;name:string}[]}>(`/vacations/configurations?company_id=${activeCompany}&environment=${environment}`,token).then(data=>{
      if(cancelled)return;setConfigs(data.configurations);setSystemConfig(data.system_default);setGroups(data.groups);setGroupId("");
      const base=data.configurations.find(c=>c.group_id===null)||data.system_default;
      if(base)setConfigForm({...defaultConfig,...Object.fromEntries(Object.keys(defaultConfig).map(key=>[key,(base as unknown as Record<string,unknown>)[key]]))} as ConfigState);
    }).catch(e=>{if(user.role!=="hr_manager")throw e}));
    Promise.all(jobs).then(results=>{
      if(cancelled)return;const rows=results[0] as Employee[];setEmployees(rows);
      const preferred=user.role==="employee"?String(user.employee_id||""):(rows.some(x=>String(x.id)===employeeId)?employeeId:String(rows[0]?.id||""));
      setEmployeeId(preferred);
    }).catch(e=>{if(!cancelled)setError(e instanceof Error?e.message:"Falha ao carregar o módulo de férias.")}).finally(()=>{if(!cancelled)setLoading(false)});
    return()=>{cancelled=true};
  // employeeId intentionally retained when it still belongs to the selected company.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[token,activeCompany,environment,user.id,user.role,user.employee_id,loadRequests]);

  // Update the effective rule card when the employee selection changes.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(()=>{if(employeeId&&token)void loadEffective(employeeId).catch(e=>setError(e instanceof Error?e.message:"Não foi possível resolver as regras vigentes."))},[employeeId,token,loadEffective]);

  const employee=employees.find(x=>String(x.id)===employeeId);
  const companyDefault=configs.find(c=>c.group_id===null)||null;
  const selectedGroupRule=configs.find(c=>c.group_id!==null&&String(c.group_id)===groupId)||null;
  const inheritedRule=selectedGroupRule||companyDefault||systemConfig;
  const hasGroupOverride=Boolean(selectedGroupRule);
  const canSubmit=Boolean(effective?.configuration.process_enabled&&(requestForm.requester_type==="employee"?effective.configuration.employee_request_enabled:effective.configuration.manager_request_enabled));
  const balanceLabel=effective?.balance_days===null?"Saldo oculto pela empresa":`${effective?.balance_days??"—"} dias disponíveis`;

  function selectGroup(value:string){setGroupId(value);const source=configs.find(c=>String(c.group_id||"")===value)||companyDefault||systemConfig;if(source)setConfigForm({...defaultConfig,effective_from:today(),...Object.fromEntries(Object.keys(defaultConfig).filter(key=>key!=="effective_from").map(key=>[key,(source as unknown as Record<string,unknown>)[key]]))} as ConfigState);}

  async function saveConfiguration(e:FormEvent){
    e.preventDefault();if(!activeCompany)return;setSaving(true);setError("");
    try{
      const saved=await api<Config>("/vacations/configurations",token,{method:"PUT",body:JSON.stringify({company_id:Number(activeCompany),environment,group_id:groupId?Number(groupId):null,...configForm})});
      setConfigs(rows=>[saved,...rows.filter(row=>row.scope_key!==saved.scope_key)]);setConfigForm({...configForm,effective_from:saved.effective_from});
      const historyRows=await api<HistoryRow[]>(`/vacations/history?company_id=${activeCompany}&environment=${environment}`,token);setHistory(historyRows);
      if(employeeId)await loadEffective(employeeId);onToast(`Regra salva na versão ${saved.version}.`);
    }catch(e){setError(e instanceof Error?e.message:"Não foi possível salvar a regra.")}finally{setSaving(false)}
  }

  async function runSimulation(){
    if(!employeeId)return;setSaving(true);setError("");setSimulation(null);
    try{const result=await api<Simulation>("/vacations/simulate",token,{method:"POST",body:JSON.stringify({...requestForm,employee_id:Number(employeeId),environment,requested_days:Number(requestForm.requested_days),installment_count:Number(requestForm.installment_count),cash_bonus_days:Number(requestForm.cash_bonus_days||0)})});setSimulation(result)}
    catch(e){setError(e instanceof Error?e.message:"Falha ao executar a simulação.")}finally{setSaving(false)}
  }

  async function createRequest(e:FormEvent){
    e.preventDefault();if(!employeeId)return;setSaving(true);setError("");
    try{
      await api<RequestRow>("/vacations/requests",token,{method:"POST",body:JSON.stringify({...requestForm,employee_id:Number(employeeId),environment:"production",requested_days:Number(requestForm.requested_days),installment_count:Number(requestForm.installment_count),cash_bonus_days:Number(requestForm.cash_bonus_days||0)})});
      await loadRequests();await loadEffective(employeeId);onToast("Solicitação de férias registrada.");setTab("requests");
    }catch(e){setError(e instanceof Error?e.message:"Não foi possível registrar a solicitação.")}finally{setSaving(false)}
  }

  async function acknowledge(row:RequestRow){setSaving(true);setError("");try{await api(`/vacations/requests/${row.id}/acknowledge`,token,{method:"POST"});await loadRequests();onToast("Ciência do aviso de férias registrada.")}catch(e){setError(e instanceof Error?e.message:"Não foi possível registrar a ciência.")}finally{setSaving(false)}}
  async function decideRequest(row:RequestRow,approved:boolean){
    let comment="";if(!approved){comment=window.prompt("Informe o motivo da rejeição")||"";if(!comment.trim())return}
    setSaving(true);setError("");
    try{await api(`/vacations/requests/${row.id}/decision`,token,{method:"POST",body:JSON.stringify({approved,comment})});await loadRequests();onToast(approved?"Solicitação aprovada.":"Solicitação rejeitada.")}
    catch(e){setError(e instanceof Error?e.message:"Não foi possível decidir a solicitação.")}finally{setSaving(false)}
  }

  async function openHistory(){
    if(!activeCompany)return;setTab("parameters");
    try{setHistory(await api<HistoryRow[]>(`/vacations/history?company_id=${activeCompany}&environment=${environment}`,token))}catch(e){setError(e instanceof Error?e.message:"Não foi possível carregar o histórico.")}
  }

  const pendingCount=requests.filter(r=>r.status==="pending_approval").length;
  const displayedRequest=requests;
  const minimumDate=effective?futureDate(effective.configuration.min_advance_days):futureDate(0);

  return <>
    <div className="page-head"><div><div className="eyebrow">Processos de RH</div><h1 className="page-title">Férias</h1><p className="page-desc">Configure regras, registre solicitações e acompanhe aprovações.</p></div><div className="vacation-head-info"><span className="env-pill">{environment==="homologation"?"Homologação":"Produção"}</span>{pendingCount>0&&<span className="pending-pill">{pendingCount} pendente{pendingCount===1?"":"s"}</span>}</div></div>
    <div className="vacation-tabs">{canOperate&&<button className={tab==="requests"?"vacation-tab active":"vacation-tab"} onClick={()=>setTab("requests")}><ClipboardCheck size={15}/>Solicitações</button>}{canConfigure&&<button className={tab==="parameters"?"vacation-tab active":"vacation-tab"} onClick={()=>setTab("parameters")}><ShieldCheck size={15}/>Parametrização</button>}{canLab&&<button className={tab==="lab"?"vacation-tab active":"vacation-tab"} onClick={()=>setTab("lab")}><FlaskConical size={15}/>Laboratório</button>}{isGlobal&&<button className="vacation-tab history-tab" onClick={()=>void openHistory()}><History size={15}/>Histórico</button>}</div>
    {error&&<div className="alert alert-error">{error}</div>}{loading&&<div className="loading">Carregando regras e solicitações…</div>}

    {tab==="parameters"&&canConfigure&&<div className="vacation-layout">
      <section className="section-panel"><div className="section-note">As regras valem em Homologação. A publicação em Produção depende de validação e aprovação na área Ambientes.</div><div className="vacation-body">
        <div className="vacation-toolbar"><label className="field-wrap"><span className="field-label">Empresa</span><div className="field company-readonly">{companies.find(c=>String(c.id)===activeCompany)?.trade_name||"Selecione uma empresa"}</div><span className="hint">Use o seletor da barra superior para trocar de empresa.</span></label><label className="field-wrap"><span className="field-label">Escopo da regra</span><select className="field" value={groupId} onChange={e=>selectGroup(e.target.value)}><option value="">Empresa inteira</option>{groups.map(g=><option key={g.id} value={g.id}>Grupo: {g.name}</option>)}</select></label></div>
        {groupId&&!hasGroupOverride&&<div className="alert alert-info">Este grupo ainda herda os parâmetros da empresa. Salvar criará uma regra específica para o grupo.</div>}
        {inheritedRule&&<div className="inheritance-card"><div className="inheritance-icon"><Activity size={17}/></div><div><strong>Como esta configuração foi determinada?</strong><p>{hasGroupOverride?`Esta regra específica do grupo ${selectedGroupRule?.group_name} prevalece para os funcionários desse grupo.`:companyDefault?`A regra atual da empresa prevalece sobre o padrão do sistema. O padrão permite ${systemConfig?.max_installments??3} parcelas e exige ${systemConfig?.min_advance_days??15} dias de antecedência.`:"Usando os valores padrão do sistema."}</p><span>Origem: {hasGroupOverride?selectedGroupRule?.group_name:companyDefault?.company_name||"Padrão do sistema"} · Versão {inheritedRule.version}</span></div></div>}
        <form onSubmit={saveConfiguration}>
          <div className="vacation-subhead"><div><h2>Configuração de férias</h2><p>As alterações criam uma nova versão e entram em vigor na data indicada.</p></div><span className="status-chip">Versão seguinte: {((groupId?selectedGroupRule:companyDefault)?.version||0)+1}</span></div>
          <div className="vacation-setting-grid">{settingFields.map(([key,label])=><label className="setting-row" key={key}><span>{label}</span><select className="field bool-select" value={String(Boolean(configForm[key]))} onChange={e=>setConfigForm({...configForm,[key]:e.target.value==="true"})}><option value="true">Sim</option><option value="false">Não</option></select></label>)}</div>
          <div className="form-grid vacation-numbers"><Field label="Máximo de parcelas"><input className="field" type="number" min={configForm.installments_allowed?2:1} max="3" disabled={!configForm.installments_allowed} value={configForm.max_installments} onChange={e=>setConfigForm({...configForm,max_installments:Number(e.target.value)})}/><span className="hint">De 1 a 3 parcelas; desabilitar parcelamento limita o valor a 1.</span></Field><Field label="Antecedência mínima (dias)"><input className="field" type="number" min="0" max="365" value={configForm.min_advance_days} onChange={e=>setConfigForm({...configForm,min_advance_days:Number(e.target.value)})}/></Field><Field label="Data de vigência"><input className="field" type="date" value={configForm.effective_from} onChange={e=>setConfigForm({...configForm,effective_from:e.target.value})}/></Field></div>
          {environment==="production"&&<div className="alert-info">Produção é somente leitura. Para alterar esta regra, selecione Homologação e solicite a publicação aprovada.</div>}<div className="vacation-actions"><button type="submit" className="btn btn-primary" disabled={saving||!activeCompany||environment!=="homologation"}><Save size={15}/>{saving?"Salvando…":"Salvar nova versão"}</button></div>
        </form>
      </div></section>
      <aside className="panel vacation-side"><div className="panel-head"><div><h2 className="panel-title">Regras cadastradas</h2><div className="panel-sub">Empresa e grupos de funcionários · {environment==="homologation"?"Homologação":"Produção"}</div></div><Users size={17} color="#8290a4"/></div>{configs.map(config=><div className="rule-list-row" key={config.id}><div className="rule-dot"/><div><strong>{config.group_name||"Empresa inteira"}</strong><div className="company-mini-meta">Máx. {config.max_installments} parcelas · {config.min_advance_days} dias · v{config.version}</div></div></div>)}{!configs.length&&<div className="empty">Nenhuma regra encontrada.</div>}<div className="panel-head history-head"><div><h2 className="panel-title">Histórico recente</h2><div className="panel-sub">Versões salvas</div></div><History size={16} color="#8290a4"/></div>{history.slice(0,5).map(item=><div className="history-row" key={item.id}><div><strong>Versão {item.version}</strong><div className="company-mini-meta">{item.actor} · {ptDate(item.created_at)}</div></div></div>)}{!history.length&&<button type="button" className="btn" onClick={()=>void (async()=>{try{setHistory(await api<HistoryRow[]>(`/vacations/history?company_id=${activeCompany}&environment=${environment}`,token))}catch(e){setError(e instanceof Error?e.message:"Falha ao buscar histórico.")}})()}>Carregar histórico</button>}</aside>
    </div>}

    {tab==="requests"&&<div className="vacation-layout"><section className="section-panel"><div className="toolbar"><div><h2 className="panel-title">Solicitações de férias</h2><div className="panel-sub">Status, período e versão da regra aplicada</div></div>{canOperate&&<span className="status-chip">{employee?.full_name||"Selecione o funcionário"}</span>}</div>
      {canOperate&&<form className="vacation-request-form" onSubmit={createRequest}><div className="vacation-request-head"><div><h3>{user.role==="employee"?"Nova solicitação":"Solicitar para funcionário"}</h3><p>A solicitação será validada pelo backend conforme a configuração vigente.</p><span className="hint vacation-clarification">Cada solicitação é uma parcela do período aquisitivo. Regras da CLT: até 3 parcelas, uma com 14 dias ou mais e nenhuma com menos de 5; início fora dos 2 dias que antecedem feriado ou domingo; abono de até 1/3; aviso com {effective?.policy?.notice_min_days??30} dias de antecedência.</span></div><CalendarDays size={18} color="#1e4fd1"/></div>
        {user.role==="hr_manager"&&<Field label="Funcionário"><select className="field" value={employeeId} onChange={e=>setEmployeeId(e.target.value)}>{employees.map(e=><option key={e.id} value={e.id}>{e.full_name} · {e.group_name}</option>)}</select></Field>}
        {user.role==="employee"&&<div className="employee-fixed"><div className="small-avatar">{employee?.full_name.slice(0,1)}</div><span>{employee?.full_name||user.full_name}</span><span className="tag">Solicitante</span></div>}
        <div className="request-balance"><div><span className="hint">SALDO DO PERÍODO</span><strong>{balanceLabel}</strong>{effective?.acquisition_period&&<span className="hint">Aquisitivo {ptDate(effective.acquisition_period.start)} a {ptDate(effective.acquisition_period.end)} · conceder até {ptDate(effective.acquisition_period.concession_end)} · parcelas usadas: {effective.acquisition_period.parcels.length}</span>}</div>{effective?.origin&&<div className="effective-origin"><span className="hint">REGRA APLICADA</span><strong>{effective.origin.label} · v{effective.origin.version}</strong></div>}</div>
        {effective&&!canSubmit&&<div className="alert alert-info">As regras atuais não permitem solicitação por este perfil. Consulte o RH da empresa.</div>}
        <div className="form-grid"><Field label="Data de início"><input className="field" type="date" min={minimumDate} required value={requestForm.start_date} onChange={e=>setRequestForm({...requestForm,start_date:e.target.value})}/></Field><Field label="Dias solicitados"><input className="field" type="number" min="1" max={effective?.balance_days||60} required value={requestForm.requested_days} onChange={e=>setRequestForm({...requestForm,requested_days:e.target.value})}/></Field><Field label="Número de parcelas"><select className="field" value={requestForm.installment_count} onChange={e=>setRequestForm({...requestForm,installment_count:e.target.value})}>{Array.from({length:Math.max(1,effective?.configuration.installments_allowed?effective.configuration.max_installments:1)},(_,i)=>i+1).map(i=><option key={i} value={i}>{i} {i===1?"parcela":"parcelas"}</option>)}</select></Field>{effective?.policy?.cash_bonus_allowed&&<Field label="Abono pecuniário (vender dias)"><select className="field" value={requestForm.cash_bonus_days} onChange={e=>setRequestForm({...requestForm,cash_bonus_days:e.target.value})}>{[0,5,10].map(d=><option key={d} value={d}>{d===0?"Não vender":`${d} dias`}</option>)}</select></Field>}{effective?.configuration.allow_13th_advance&&<Field label="Adiantamento do 13º"><select className="field" value={String(requestForm.advance_13th_requested)} onChange={e=>setRequestForm({...requestForm,advance_13th_requested:e.target.value==="true"})}><option value="false">Não</option><option value="true">Sim</option></select></Field>}</div>
        {effective?.why&&<div className="hint rule-explanation">{effective.why}</div>}
        <div className="vacation-actions"><button type="submit" className="btn btn-primary" disabled={saving||!employeeId||!canSubmit}><Plus size={15}/>{saving?"Enviando…":"Enviar solicitação"}</button></div>
      </form>}
      <div className="table-wrap"><table><thead><tr><th>Funcionário</th><th>Período</th><th>Dias</th><th>Abono</th><th>Regra</th><th>Status</th><th>Aviso</th>{canApprove&&<th>Ações</th>}</tr></thead><tbody>{displayedRequest.map(row=><tr key={row.id}><td><div className="person-name">{row.employee_name}</div><div className="person-email">Por {row.requested_by}</div></td><td>{ptDate(row.start_date)}{row.end_date?` a ${ptDate(row.end_date)}`:""}</td><td>{row.requested_days}</td><td>{row.cash_bonus_days?`${row.cash_bonus_days} dias`:"—"}</td><td><span className="code">v{row.config_version}</span></td><td><StatusBadge status={row.status}/>{row.status==="pending_approval"&&row.current_approver&&<div className="hint">Com: {row.current_approver}</div>}</td>
      <td>{row.status==="approved"||row.status==="approved_auto"?(row.acknowledged_at?<span className="hint">Ciente em {ptDate(row.acknowledged_at)}</span>:(user.employee_id===row.employee_id?<button className="btn btn-small" disabled={saving} onClick={()=>void acknowledge(row)}>Dar ciência</button>:<span className="hint">Aguardando ciência</span>)):"—"}</td>{canApprove&&<td>{row.status==="pending_approval"&&<div className="table-actions"><button className="btn btn-primary btn-small" disabled={saving} onClick={()=>void decideRequest(row,true)}>Aprovar</button><button className="btn btn-small" disabled={saving} onClick={()=>void decideRequest(row,false)}>Rejeitar</button></div>}</td>}</tr>)}</tbody></table>{!displayedRequest.length&&<div className="empty">Ainda não há solicitações de férias neste escopo.</div>}</div>
    </section><aside className="panel vacation-side"><div className="panel-head"><div><h2 className="panel-title">Fluxo do processo</h2><div className="panel-sub">Etapas da solicitação demonstrativa</div></div><BadgeCheck size={17} color="#8290a4"/></div><div className="workflow-step"><span>1</span><div><strong>Validação das regras</strong><p>Empresa, grupo, antecedência, saldo e parcelas.</p></div></div><div className="workflow-step"><span>2</span><div><strong>{effective?.configuration.manager_approval_required?"Aprovação pelo fluxo configurado":"Aprovação automática"}</strong><p>{effective?.configuration.manager_approval_required?"As etapas (gestor imediato, RH, administrador) e o SLA são definidos em Processos → Parametrização → Fluxos de aprovação.":"A regra vigente permite aprovação automática."}</p></div></div><div className="workflow-step"><span>3</span><div><strong>Aviso e ciência</strong><p>Após a aprovação, o colaborador dá ciência do aviso de férias (art. 135 da CLT). A solicitação mantém a cópia da regra usada.</p></div></div></aside></div>}

    {tab==="lab"&&canLab&&<div className="vacation-layout"><section className="section-panel"><div className="section-note">O laboratório e o formulário operacional usam o mesmo motor determinístico de regras no backend.</div><div className="vacation-body"><div className="vacation-subhead"><div><h2>Simular solicitação de férias</h2><p>Altere os parâmetros, execute e veja a origem da regra que decidiu o resultado.</p></div><FlaskConical size={20} color="#1e4fd1"/></div><div className="form-grid"><Field label="Funcionário"><select className="field" value={employeeId} onChange={e=>{setEmployeeId(e.target.value);setSimulation(null)}}>{employees.map(e=><option key={e.id} value={e.id}>{e.full_name} · {e.group_name}</option>)}</select></Field><Field label="Solicitação feita por"><select className="field" value={requestForm.requester_type} onChange={e=>setRequestForm({...requestForm,requester_type:e.target.value as "employee"|"manager"})} disabled={user.role!=="system_admin"&&user.role!=="consultant"}><option value="employee">Próprio funcionário</option><option value="manager">Gestor</option></select></Field><Field label="Data de início"><input className="field" type="date" value={requestForm.start_date} onChange={e=>setRequestForm({...requestForm,start_date:e.target.value})}/></Field><Field label="Dias solicitados"><input className="field" type="number" min="1" max="60" value={requestForm.requested_days} onChange={e=>setRequestForm({...requestForm,requested_days:e.target.value})}/></Field><Field label="Parcelas"><select className="field" value={requestForm.installment_count} onChange={e=>setRequestForm({...requestForm,installment_count:e.target.value})}><option value="1">1 parcela</option><option value="2">2 parcelas</option><option value="3">3 parcelas</option></select></Field><Field label="Abono pecuniário (dias)"><select className="field" value={requestForm.cash_bonus_days} onChange={e=>setRequestForm({...requestForm,cash_bonus_days:e.target.value})}>{[0,5,10].map(d=><option key={d} value={d}>{d}</option>)}</select></Field><Field label="Solicitar adiantamento do 13º"><select className="field" value={String(requestForm.advance_13th_requested)} onChange={e=>setRequestForm({...requestForm,advance_13th_requested:e.target.value==="true"})}><option value="false">Não</option><option value="true">Sim</option></select></Field></div><div className="vacation-actions"><button className="btn btn-primary" onClick={()=>void runSimulation()} disabled={saving||!employeeId}><FlaskConical size={15}/>{saving?"Testando…":"Executar simulação"}</button></div>
        {simulation&&<div className={simulation.allowed?"simulation-result accepted":"simulation-result rejected"}><div className="simulation-result-head">{simulation.allowed?<BadgeCheck size={19}/>:<CircleAlert size={19}/>}<strong>{simulation.allowed?"Solicitação aceita pelas regras atuais":"Solicitação rejeitada"}</strong></div>{simulation.errors.length>0&&<ul>{simulation.errors.map(msg=><li key={msg}>{msg}</li>)}</ul>}{simulation.allowed&&<p>As datas e os parâmetros informados passaram pela validação demonstrativa.</p>}<div className="result-trace"><span>Parâmetro: <b>HCM.VACATION.MAX_INSTALLMENTS</b></span><span>Valor configurado: <b>{simulation.effective_configuration.max_installments}</b></span><span>Origem: <b>{simulation.origin.label}</b></span><span>Versão: <b>{simulation.origin.version}</b></span><span>Saldo: <b>{simulation.balance_days===null?"Oculto":`${simulation.balance_days} dias`}</b></span></div><div className="hint">{simulation.why}</div></div>}
      </div></section><aside className="panel vacation-side"><div className="panel-head"><div><h2 className="panel-title">Cenário atual</h2><div className="panel-sub">Regra efetiva para a pessoa escolhida</div></div><Activity size={17} color="#8290a4"/></div>{effective&&<><div className="scenario-number"><span>Máximo de parcelas</span><strong>{effective.configuration.max_installments}</strong></div><div className="scenario-number"><span>Antecedência mínima</span><strong>{effective.configuration.min_advance_days} dias</strong></div><div className="scenario-number"><span>Origem</span><strong>{effective.origin.label}</strong></div><div className="scenario-number"><span>Versão</span><strong>v{effective.configuration.version}</strong></div><div className="inheritance-card compact"><div><strong>Como foi decidido</strong><p>{effective.why}</p></div></div></>}</aside></div>}
  </>;
}

function Field({label,children}:{label:string;children:React.ReactNode}){return <label className="field-wrap"><span className="field-label">{label}</span>{children}</label>}
function StatusBadge({status}:{status:string}){const labels:Record<string,string>={pending_approval:"Aguardando aprovação",approved:"Aprovada",approved_auto:"Aprovada automaticamente",rejected:"Rejeitada"};return <span className={`vac-status ${status}`}>{labels[status]||status}</span>}
