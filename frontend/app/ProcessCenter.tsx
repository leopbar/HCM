"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { AlarmClock, Check, ClipboardList, FilePlus2, Inbox, ListChecks, Plus, Save, Trash2, X } from "lucide-react";
import { SuiteLink } from "./Navigation";
import { api, datePt, dateTimePt, message, post, put, type Environment, type SessionUser } from "./http";
import { AdditionalFieldsEditor, GeneralConfigsEditor, type GeneralConfig } from "./SuiteEditors";

type Field = { name: string; label: string; type: string; required?: boolean; options?: { value: string; label: string }[]; min?: number; max?: number; show_if?: Record<string, string> };
export type AdditionalField = { name: string; label: string; type: "text" | "number" | "date" | "boolean" | "select"; required: boolean; options: string[]; help: string };
type ProcessDef = { process: string; label: string; target: "employee" | "none"; self_service: boolean; form: boolean; fields: Field[]; additional_fields?: AdditionalField[] };
type ModuleField = { key: string; label: string; type: "boolean" | "number" | "text" | "days" | "tags" | "json"; min?: number; max?: number };
export type ModuleDef = { module: string; label: string; fields: ModuleField[]; defaults: Record<string, unknown> };
type Catalog = { processes: ProcessDef[]; modules: ModuleDef[]; approvers: Record<string, string>; process_keys: string[]; form_processes?: string[] };
type Step = { approver: string; sla_hours: number; finish?: boolean; approver_label?: string; fallback?: string };
type RequestRow = { id: number; company_name: string; process: string; process_label: string; employee_name: string | null; requested_by: string; status: string; status_label: string; data: Record<string, unknown>; result: Record<string, unknown> | null;
  current_step: number; total_steps: number; current_approver: string | null; steps: Step[]; additional_fields?: AdditionalField[]; due_at: string | null; overdue: boolean; can_act: boolean; created_at: string | null; decided_at: string | null;
  history?: { step: number | null; actor: string; action: string; comment: string | null; late: boolean; created_at: string | null }[] };
type Option = { id: number | string; name: string };
type Options = { departments: Option[]; positions: Option[]; groups: Option[]; employees?: Option[]; companies?: Option[]; payroll_events?: Option[]; transport_lines: Option[]; dependents?: Option[]; recess_balance?: number | null;
  target?: { departments: Option[]; positions: Option[]; groups: Option[] } };
type Target = { id: number; full_name: string; registration: string; contract_type: string; is_self: boolean };
type Tab = "new" | "mine" | "manager";

const ACTION_LABELS: Record<string, string> = { submitted: "Enviado", approved: "Aprovado", rejected: "Reprovado", cancelled: "Cancelado", auto_approved: "Aprovado automaticamente", fallback: "Encaminhado", no_approver: "Sem aprovador" };
const DAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];
const TARGET_LABELS: Record<string, string> = { self: "Próprio solicitante", direct: "Subordinados diretos", indirect: "Subordinados indiretos", visibility: "Por visibilidade (mesmo departamento)" };

export default function ProcessCenter({ user, companyId, environment, onToast, initialTab, onOpenSuite }: { user: SessionUser; companyId: string; environment: Environment; onToast: (m: string) => void; initialTab?: Tab; onOpenSuite?: (nodeId: string) => void }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab || (user.role === "employee" ? "new" : "manager"));
  const [error, setError] = useState("");
  const [opened, setOpened] = useState<RequestRow | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { if (!companyId) return; api<Catalog>(`/processes/catalog?company_id=${companyId}&environment=${environment}`).then(setCatalog).catch(e => setError(message(e))); }, [companyId, environment]);
  const openRequest = useCallback(async (id: number) => { try { setOpened(await api<RequestRow>(`/processes/requests/${id}`)); } catch (e) { setError(message(e)); } }, []);
  if (!companyId) return <div className="empty">Selecione uma empresa no topo da página para usar os processos.</div>;
  return <>
    <div className="page-head"><div><div className="eyebrow">Autoatendimento e workflow</div><h1 className="page-title">Gerenciador de processos</h1>
      <p className="page-desc">Solicitações com etapas de aprovação, prazo de atendimento (SLA) e efeito automático no cadastro.</p><SuiteLink user={user} nodeId="guia-gerenciador" onOpen={onOpenSuite} /></div></div>
    <div className="tabs" role="tablist" style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
      <TabButton active={tab === "new"} onClick={() => setTab("new")} icon={<FilePlus2 size={15} />}>{user.role === "consultant" ? "Simular solicitação" : "Nova solicitação"}</TabButton>
      <TabButton active={tab === "mine"} onClick={() => setTab("mine")} icon={<ClipboardList size={15} />}>Minhas solicitações</TabButton>
      <TabButton active={tab === "manager"} onClick={() => setTab("manager")} icon={<Inbox size={15} />}>Gerenciador de processos</TabButton>
    </div>
    {error && <div className="alert alert-error">{error}</div>}
    {!catalog ? <div className="loading">Carregando…</div> : <>
      {tab === "new" && <NewRequest catalog={catalog} user={user} companyId={companyId} onCreated={row => { onToast(`Solicitação nº ${row.id} registrada: ${row.status_label}.`); setOpened(row); setRefresh(x => x + 1); }} />}
      {tab === "mine" && <RequestList key={`mine-${refresh}`} scope="mine" companyId={companyId} catalog={catalog} onOpen={openRequest} />}
      {tab === "manager" && <Manager key={`mgr-${refresh}`} user={user} companyId={companyId} catalog={catalog} onOpen={openRequest} />}
    </>}
    {opened && <RequestModal row={opened} user={user} close={() => setOpened(null)} onChanged={row => { setOpened(row); setRefresh(x => x + 1); onToast(`Solicitação nº ${row.id}: ${row.status_label}.`); }} />}
  </>;
}

function TabButton({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: ReactNode; children: ReactNode }) {
  return <button type="button" role="tab" aria-selected={active} className={`btn ${active ? "btn-primary" : ""}`} onClick={onClick}>{icon}{children}</button>;
}

function Modal({ title, children, close }: { title: string; children: ReactNode; close: () => void }) {
  return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}><section className="modal" style={{ maxWidth: 820 }}><div className="modal-head"><h2 className="modal-title">{title}</h2><button className="btn btn-quiet" type="button" onClick={close} aria-label="Fechar"><X size={17} /></button></div>{children}</section></div>;
}

function Labeled({ label, children, full }: { label: string; children: ReactNode; full?: boolean }) {
  return <label className={`field-wrap ${full ? "full" : ""}`}><span className="field-label">{label}</span>{children}</label>;
}

function NewRequest({ catalog, user, companyId, onCreated }: { catalog: Catalog; user: SessionUser; companyId: string; onCreated: (row: RequestRow) => void }) {
  const available = useMemo(() => catalog.processes.filter(p => p.form && (user.role !== "employee" || p.self_service || user.has_team)), [catalog, user]);
  const [process, setProcess] = useState(available[0]?.process || "");
  const definition = available.find(p => p.process === process);
  const [targets, setTargets] = useState<Target[]>([]);
  const [employeeId, setEmployeeId] = useState("");
  const [options, setOptions] = useState<Options | null>(null);
  const [data, setData] = useState<Record<string, unknown>>({});
  const [preview, setPreview] = useState<{ data: Record<string, unknown>; steps: Step[] } | null>(null);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!definition) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setData({}); setPreview(null); setError("");
    if (definition.target === "employee") api<Target[]>(`/processes/targets?process=${process}&company_id=${companyId}`).then(rows => { setTargets(rows); setEmployeeId(String(rows.find(r => r.is_self)?.id || rows[0]?.id || "")); }).catch(e => setError(message(e)));
    else { setTargets([]); setEmployeeId(""); }
  }, [process, companyId, definition]);
  const targetCompany = String(data.target_company_id || "");
  useEffect(() => {
    const query = new URLSearchParams({ company_id: companyId });
    if (employeeId) query.set("employee_id", employeeId);
    if (targetCompany) query.set("target_company_id", targetCompany);
    api<Options>(`/processes/options?${query}`).then(setOptions).catch(e => setError(message(e)));
  }, [companyId, employeeId, targetCompany]);
  if (!definition) return <div className="empty">Nenhum processo disponível para o seu perfil.</div>;
  const payload = () => ({ process, company_id: Number(companyId), employee_id: employeeId ? Number(employeeId) : null, data });
  async function simulate() { setBusy(true); setError(""); try { setPreview(await post<{ data: Record<string, unknown>; steps: Step[] }>("/processes/simulate", payload())); } catch (e) { setPreview(null); setError(message(e)); } finally { setBusy(false); } }
  async function submit(e: FormEvent) { e.preventDefault(); setBusy(true); setError(""); try { onCreated(await post<RequestRow>("/processes/requests", payload())); setData({}); setPreview(null); } catch (ex) { setError(message(ex)); } finally { setBusy(false); } }
  const visible = definition.fields.filter(f => !f.show_if || Object.entries(f.show_if).every(([k, v]) => data[k] === v));
  return <section className="section-panel" style={{ padding: 20 }}>
    <form onSubmit={submit}>
      <div className="form-grid">
        <Labeled label="Processo"><select className="field" value={process} onChange={e => setProcess(e.target.value)}>{available.map(p => <option key={p.process} value={p.process}>{p.label}</option>)}</select></Labeled>
        {definition.target === "employee" && <Labeled label="Colaborador (conforme atribuições de seleção)"><select className="field" value={employeeId} onChange={e => setEmployeeId(e.target.value)} required>{targets.length === 0 && <option value="">Nenhum colaborador disponível</option>}{targets.map(t => <option key={t.id} value={t.id}>{t.full_name} · {t.registration}{t.is_self ? " (você)" : ""}</option>)}</select></Labeled>}
        {visible.map(field => <FieldInput key={field.name} field={field} value={data[field.name]} options={options} onChange={value => { setData(current => ({ ...current, [field.name]: value })); setPreview(null); }} />)}
      </div>
      {(definition.additional_fields?.length || 0) > 0 && <div className="suite-block" style={{ marginTop: 14 }}><h2>Informações adicionais</h2><div className="form-grid">
        {definition.additional_fields!.map(f => <div key={f.name}><FieldInput field={{ name: f.name, label: f.label + (f.required ? " *" : ""), type: f.type, required: f.required, options: f.options.map(o => ({ value: o, label: o })) }} value={(data.additional as Record<string, unknown> | undefined)?.[f.name]} options={options} onChange={value => { setData(current => ({ ...current, additional: { ...((current.additional as Record<string, unknown>) || {}), [f.name]: value } })); setPreview(null); }} />{f.help && <span className="hint">{f.help}</span>}</div>)}
      </div></div>}
      {process === "recess" && options?.recess_balance != null && <div className="section-note" style={{ marginTop: 12 }}>Saldo de recesso disponível: {options.recess_balance} dias.</div>}
      {error && <div className="alert alert-error" style={{ marginTop: 14 }}>{error}</div>}
      {preview && <div className="alert" style={{ marginTop: 14, background: "#eefaf3", border: "1px solid #bfe6cf" }}><strong>Validação aprovada.</strong> Fluxo: {preview.steps.length ? preview.steps.map((s, i) => `${i + 1}. ${s.approver_label} (${s.sla_hours}h úteis)`).join(" → ") : "sem aprovação"}.<PreviewData data={preview.data} /></div>}
      <div style={{ display: "flex", gap: 10, marginTop: 16 }}><button type="button" className="btn" onClick={() => void simulate()} disabled={busy}><ListChecks size={15} />Validar</button>{user.role !== "consultant" && <button className="btn btn-primary" disabled={busy || (definition.target === "employee" && !employeeId)}><Check size={15} />Enviar solicitação</button>}</div>
      {user.role === "consultant" && <p className="hint" style={{ marginTop: 8 }}>O consultor valida a parametrização sem registrar solicitações; quem abre processos é o colaborador, o gestor ou o RH.</p>}
    </form>
  </section>;
}

function PreviewData({ data }: { data: Record<string, unknown> }) {
  const additional = data.additional && typeof data.additional === "object" ? Object.entries(data.additional as Record<string, unknown>) : [];
  const shown = [...Object.entries(data).filter(([k, v]) => v !== null && v !== "" && typeof v !== "object" && !k.endsWith("_id")), ...additional.filter(([, v]) => v !== null && v !== "")];
  if (!shown.length) return null;
  return <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>{shown.map(([k, v]) => <li key={k}><span className="hint">{k}:</span> {typeof v === "boolean" ? (v ? "sim" : "não") : String(v)}</li>)}</ul>;
}

function FieldInput({ field, value, options, onChange }: { field: Field; value: unknown; options: Options | null; onChange: (v: unknown) => void }) {
  const list: Record<string, Option[] | undefined> = { department: options?.departments, position: options?.positions, group: options?.groups, employee: options?.employees, company: options?.companies, payroll_event: options?.payroll_events,
    dependent: options?.dependents, target_department: options?.target?.departments, target_position: options?.target?.positions, target_group: options?.target?.groups };
  const text = value == null ? "" : String(value);
  if (field.type === "boolean") return <label className="field-wrap" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><input type="checkbox" checked={Boolean(value)} onChange={e => onChange(e.target.checked)} /><span>{field.label}</span></label>;
  if (field.type === "select") return <Labeled label={field.label}><select className="field" value={text} required={field.required} onChange={e => onChange(e.target.value)}><option value="">Selecione…</option>{field.options?.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Labeled>;
  if (field.type in list) return <Labeled label={field.label}><select className="field" value={text} required={field.required} onChange={e => onChange(field.type === "payroll_event" ? e.target.value : e.target.value ? Number(e.target.value) : null)}><option value="">{field.required ? "Selecione…" : "Sem alteração"}</option>{(list[field.type] || []).map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select></Labeled>;
  if (field.type === "transport_lines") return <TransportLines value={Array.isArray(value) ? value as { line_id: number; trips_per_day: number }[] : []} lines={options?.transport_lines || []} onChange={onChange} />;
  const type = field.type === "date" ? "date" : field.type === "month" ? "month" : field.type === "number" ? "number" : "text";
  return <Labeled label={field.label}><input className="field" type={type} required={field.required} min={field.min} max={field.max} step={field.type === "number" ? "0.01" : undefined} value={text} onChange={e => onChange(field.type === "number" ? (e.target.value === "" ? null : Number(e.target.value)) : e.target.value)} /></Labeled>;
}

function TransportLines({ value, lines, onChange }: { value: { line_id: number; trips_per_day: number }[]; lines: Option[]; onChange: (v: unknown) => void }) {
  return <div className="field-wrap full"><span className="field-label">Linhas de transporte</span>
    {value.map((item, index) => <div key={index} style={{ display: "flex", gap: 8, marginBottom: 6 }}>
      <select className="field" value={item.line_id} onChange={e => onChange(value.map((v, i) => i === index ? { ...v, line_id: Number(e.target.value) } : v))}>{lines.map(l => <option key={l.id} value={l.id}>{l.name}</option>)}</select>
      <input className="field" type="number" min={1} max={6} value={item.trips_per_day} style={{ maxWidth: 110 }} aria-label="Viagens por dia" onChange={e => onChange(value.map((v, i) => i === index ? { ...v, trips_per_day: Number(e.target.value) } : v))} />
      <button type="button" className="btn btn-quiet" aria-label="Remover linha" onClick={() => onChange(value.filter((_, i) => i !== index))}><Trash2 size={14} /></button></div>)}
    {lines.length ? <button type="button" className="btn" onClick={() => onChange([...value, { line_id: Number(lines[0].id), trips_per_day: 2 }])}><Plus size={14} />Adicionar linha</button> : <span className="hint">Nenhuma linha cadastrada. O RH cadastra as linhas na Suíte, em Cadastros da Empresa → Linhas de Vale-Transporte.</span>}
  </div>;
}

function StatusTag({ row }: { row: RequestRow }) {
  const color = row.status === "approved" ? "#1f7a4c" : row.status === "rejected" || row.status === "cancelled" ? "#a33" : row.overdue ? "#b45309" : undefined;
  return <span className="tag" style={color ? { color, borderColor: color } : undefined}>{row.overdue && row.status === "pending_approval" ? "Atrasado" : row.status_label}</span>;
}

function RequestTable({ rows, onOpen }: { rows: RequestRow[]; onOpen: (id: number) => void }) {
  return <div className="table-wrap"><table><thead><tr><th>Nº</th><th>Processo</th><th>Colaborador</th><th>Solicitante</th><th>Etapa atual</th><th>Prazo (SLA)</th><th>Status</th><th></th></tr></thead><tbody>
    {rows.map(r => <tr key={r.id}><td className="code">{r.id}</td><td>{r.process_label}</td><td>{r.employee_name || "—"}</td><td>{r.requested_by}</td>
      <td>{r.current_approver ? `${r.current_step + 1}/${r.total_steps} · ${r.current_approver}` : "—"}</td><td>{r.due_at ? <span style={r.overdue ? { color: "#b45309", fontWeight: 600 } : undefined}><AlarmClock size={12} /> {dateTimePt(r.due_at)}</span> : "—"}</td>
      <td><StatusTag row={r} /></td><td><button className="btn" onClick={() => onOpen(r.id)}>{r.can_act ? "Analisar" : "Detalhes"}</button></td></tr>)}
  </tbody></table>{rows.length === 0 && <div className="empty">Nenhuma solicitação encontrada.</div>}</div>;
}

function RequestList({ scope, companyId, catalog, onOpen, extraFilters = true }: { scope: "mine" | "tasks" | "all"; companyId: string; catalog: Catalog; onOpen: (id: number) => void; extraFilters?: boolean }) {
  const [rows, setRows] = useState<RequestRow[]>([]); const [process, setProcess] = useState(""); const [status, setStatus] = useState(""); const [overdue, setOverdue] = useState(false); const [error, setError] = useState("");
  useEffect(() => {
    const query = new URLSearchParams({ scope }); if (scope !== "mine") query.set("company_id", companyId); if (process) query.set("process", process); if (status) query.set("status", status); if (overdue) query.set("overdue", "true");
    api<RequestRow[]>(`/processes/requests?${query}`).then(setRows).catch(e => setError(message(e)));
  }, [scope, companyId, process, status, overdue]);
  return <section className="section-panel">{extraFilters && <div className="toolbar"><div className="toolbar-left">
    <select className="filter-select" value={process} onChange={e => setProcess(e.target.value)} aria-label="Filtrar processo"><option value="">Todos os processos</option>{catalog.processes.map(p => <option key={p.process} value={p.process}>{p.label}</option>)}</select>
    {scope !== "tasks" && <select className="filter-select" value={status} onChange={e => setStatus(e.target.value)} aria-label="Filtrar status"><option value="">Todos os status</option><option value="pending_approval">Em aprovação</option><option value="approved">Aprovado</option><option value="scheduled">Agendado</option><option value="rejected">Reprovado</option><option value="cancelled">Cancelado</option></select>}
    <label className="hint" style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={overdue} onChange={e => setOverdue(e.target.checked)} />Somente atrasados</label>
  </div><div className="toolbar-right"><span className="hint">{rows.length} registros</span></div></div>}
    {error && <div className="alert alert-error">{error}</div>}<RequestTable rows={rows} onOpen={onOpen} /></section>;
}

type SlaRow = { process: string; label: string; total: number; pending: number; overdue: number; approved: number; rejected: number; cancelled: number };
function Manager({ user, companyId, catalog, onOpen }: { user: SessionUser; companyId: string; catalog: Catalog; onOpen: (id: number) => void }) {
  const [scope, setScope] = useState<"tasks" | "all">("tasks"); const [sla, setSla] = useState<SlaRow[]>([]);
  const canReport = user.role !== "employee";
  useEffect(() => { if (canReport) api<SlaRow[]>(`/processes/sla?company_id=${companyId}`).then(setSla).catch(() => setSla([])); }, [companyId, canReport]);
  return <>
    {canReport && sla.length > 0 && <section className="panel" style={{ marginBottom: 16 }}><div className="panel-head"><div><h2 className="panel-title">Acompanhamento de SLA</h2><div className="panel-sub">Volume, pendências e atrasos por processo</div></div></div>
      <div className="table-wrap"><table><thead><tr><th>Processo</th><th>Total</th><th>Em aprovação</th><th>Atrasados</th><th>Aprovados</th><th>Reprovados</th><th>Cancelados</th></tr></thead><tbody>{sla.map(r => <tr key={r.process}><td>{r.label}</td><td>{r.total}</td><td>{r.pending}</td><td style={r.overdue ? { color: "#b45309", fontWeight: 600 } : undefined}>{r.overdue}</td><td>{r.approved}</td><td>{r.rejected}</td><td>{r.cancelled}</td></tr>)}</tbody></table></div></section>}
    <div style={{ display: "flex", gap: 8, marginBottom: 10 }}><button className={`btn ${scope === "tasks" ? "btn-primary" : ""}`} onClick={() => setScope("tasks")}>Aguardando minha aprovação</button><button className={`btn ${scope === "all" ? "btn-primary" : ""}`} onClick={() => setScope("all")}>Todas as visíveis</button></div>
    <RequestList key={scope} scope={scope} companyId={companyId} catalog={catalog} onOpen={onOpen} />
  </>;
}

function RequestModal({ row, user, close, onChanged }: { row: RequestRow; user: SessionUser; close: () => void; onChanged: (row: RequestRow) => void }) {
  const [comment, setComment] = useState(""); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function act(path: string, body?: unknown) { setBusy(true); setError(""); try { onChanged(await post<RequestRow>(path, body ?? {})); setComment(""); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  const data = Object.entries(row.data).filter(([k, v]) => k !== "additional" && v !== null && v !== "" && !(Array.isArray(v) && v.length === 0));
  const additional = (row.data.additional as Record<string, unknown> | undefined) || {};
  const additionalRows = (row.additional_fields || []).map(f => [f.label, additional[f.name]] as [string, unknown]).filter(([, v]) => v !== null && v !== undefined && v !== "");
  return <Modal title={`${row.process_label} nº ${row.id}`} close={close}><div className="modal-body">
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 12 }}><StatusTag row={row} /><span className="hint">Colaborador: {row.employee_name || "—"}</span><span className="hint">Solicitante: {row.requested_by}</span><span className="hint">Aberto em {dateTimePt(row.created_at)}</span>{row.due_at && <span className="hint">Prazo da etapa: {dateTimePt(row.due_at)}</span>}</div>
    <h3 style={{ fontSize: 14, margin: "8px 0" }}>Etapas</h3>
    <ol style={{ margin: 0, paddingLeft: 18 }}>{row.steps.map((s, i) => <li key={i} style={{ fontWeight: i === row.current_step && row.status === "pending_approval" ? 600 : 400 }}>{s.approver_label} · {s.sla_hours}h úteis{s.fallback ? ` (${s.fallback})` : ""}{i < row.current_step || row.status === "approved" ? " ✓" : ""}</li>)}{row.steps.length === 0 && <li>Sem etapas de aprovação</li>}</ol>
    <h3 style={{ fontSize: 14, margin: "14px 0 8px" }}>Dados da solicitação</h3>
    <div className="table-wrap"><table><tbody>{data.map(([k, v]) => <tr key={k}><td className="hint" style={{ width: 220 }}>{k}</td><td>{renderValue(v)}</td></tr>)}</tbody></table></div>
    {additionalRows.length > 0 && <><h3 style={{ fontSize: 14, margin: "14px 0 8px" }}>Informações adicionais</h3><div className="table-wrap"><table><tbody>{additionalRows.map(([k, v]) => <tr key={k}><td className="hint" style={{ width: 220 }}>{k}</td><td>{renderValue(v)}</td></tr>)}</tbody></table></div></>}
    {row.result && <div className="section-note" style={{ marginTop: 12 }}>{String(row.result.message || "")}{Array.isArray(row.result.positions) && row.result.positions.length ? ` Posições: ${row.result.positions.join(", ")}.` : ""}</div>}
    {row.history && <><h3 style={{ fontSize: 14, margin: "14px 0 8px" }}>Histórico</h3><div style={{ display: "grid", gap: 6 }}>{row.history.map((h, i) => <div key={i} className="activity-row"><span className="activity-dot" /><div><div className="activity-text"><strong>{h.actor}</strong> · {ACTION_LABELS[h.action] || h.action}{h.step != null && h.action !== "submitted" ? ` (etapa ${h.step + 1})` : ""}{h.late ? " · fora do prazo" : ""}{h.comment ? ` — ${h.comment}` : ""}</div><div className="activity-time">{dateTimePt(h.created_at)}</div></div></div>)}</div></>}
    {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}
    {row.can_act && <div style={{ marginTop: 16 }}><Labeled label="Comentário (obrigatório para reprovar)" full><textarea className="field" rows={2} value={comment} maxLength={1000} onChange={e => setComment(e.target.value)} /></Labeled></div>}
  </div><div className="modal-foot">
    {row.status === "pending_approval" && (row.requested_by === user.full_name || user.role === "hr_manager" || user.role === "system_admin") && <button className="btn" disabled={busy} onClick={() => void act(`/processes/requests/${row.id}/cancel`)}>Cancelar solicitação</button>}
    {row.can_act && <><button className="btn" disabled={busy} onClick={() => void act(`/processes/requests/${row.id}/decision`, { approved: false, comment })}><X size={14} />Reprovar</button><button className="btn btn-primary" disabled={busy} onClick={() => void act(`/processes/requests/${row.id}/decision`, { approved: true, comment })}><Check size={14} />Aprovar etapa</button></>}
    <button className="btn" onClick={close}>Fechar</button></div></Modal>;
}

function renderValue(value: unknown): ReactNode {
  if (typeof value === "boolean") return value ? "Sim" : "Não";
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return datePt(value);
  if (Array.isArray(value)) return value.map(v => typeof v === "object" && v ? Object.values(v as Record<string, unknown>).join(" · ") : String(v)).join("; ");
  if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([k, v]) => `${k}: ${String(v)}`).join(", ");
  if (typeof value === "number" && value > 100) return value.toLocaleString("pt-BR");
  return String(value);
}

type ConfigResponse = { effective: { version: number; effective_from: string | null; values: Record<string, unknown> }; versions: { version: number; effective_from: string }[] };
export type WorkflowMode = "all" | "steps" | "overrides" | "sla";
let catalogCache: Promise<Catalog> | null = null;
export function loadCatalog(): Promise<Catalog> { catalogCache = catalogCache || api<Catalog>("/processes/catalog").catch(e => { catalogCache = null; throw e; }); return catalogCache; }

/** Versioned editor for one parameter module (homologation only; production is read-only until published). */
export function ModuleEditor({ module, companyId, environment, onToast, workflowMode = "all", canEdit = true }: { module: string; companyId: string; environment: Environment; onToast: (m: string) => void; workflowMode?: WorkflowMode; canEdit?: boolean }) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [values, setValues] = useState<Record<string, unknown> | null>(null); const [version, setVersion] = useState(0);
  const [effectiveFrom, setEffectiveFrom] = useState(new Date().toISOString().slice(0, 10));
  const [groups, setGroups] = useState<string[]>([]); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const definition = catalog?.modules.find(m => m.module === module);
  const readOnly = environment !== "homologation" || !canEdit;
  const load = useCallback(async () => { if (!module) return; setError(""); setValues(null); try { const data = await api<ConfigResponse>(`/hr/configurations/${module}?company_id=${companyId}&environment=${environment}`); setValues(structuredClone(data.effective.values)); setVersion(data.effective.version); } catch (e) { setError(message(e)); } }, [module, companyId, environment]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { loadCatalog().then(setCatalog).catch(e => setError(message(e))); }, []);
  useEffect(() => { api<{ groups: { name: string }[] }>(`/companies/${companyId}/options`).then(o => setGroups(o.groups.map(g => g.name))).catch(() => setGroups([])); }, [companyId]);
  async function save(e: FormEvent) { e.preventDefault(); setBusy(true); setError(""); try { const saved = await put<{ version: number }>("/hr/configurations", { company_id: Number(companyId), module, environment: "homologation", effective_from: effectiveFrom, values }); setVersion(saved.version); onToast(`${definition?.label} salvo na versão ${saved.version} (homologação). Publique em Ambientes.`); } catch (ex) { setError(message(ex)); } finally { setBusy(false); } }
  const set = (key: string, value: unknown) => setValues(current => ({ ...(current || {}), [key]: value }));
  const showTopFields = module !== "workflow" || workflowMode === "all" || workflowMode === "sla";
  return <div>
    <div className="suite-meta"><Labeled label="Vigência a partir de"><input className="field" type="date" value={effectiveFrom} disabled={readOnly} onChange={e => setEffectiveFrom(e.target.value)} /></Labeled>
      <div className="section-note">{!canEdit ? "Somente consulta para o seu perfil: a parametrização é feita por consultores e administradores." : environment !== "homologation" ? "Produção é somente leitura: altere em Homologação e publique pelo fluxo de Ambientes." : `Editando homologação · versão atual ${version || "padrão do sistema"}. As alterações valem em produção somente após a publicação aprovada.`}</div></div>
    {error && <div className="alert alert-error">{error}</div>}
    {values && definition && catalog && Object.keys(definition.defaults).every(k => k in values) && <form id={`module-form-${module}`} onSubmit={save}><fieldset disabled={readOnly || busy} style={{ border: 0, padding: 0, margin: 0 }}>
      {showTopFields && <div className="form-grid">{definition.fields.filter(f => f.type !== "json").map(f => <ModuleInput key={f.key} field={f} value={values[f.key]} onChange={v => set(f.key, v)} />)}</div>}
      {module === "workflow" && workflowMode !== "sla" && values.processes != null && <WorkflowEditor mode={workflowMode} value={values.processes as Record<string, { steps: Step[]; group_overrides: Record<string, Step[]> }>} catalog={catalog} groups={groups} onChange={v => set("processes", v)} />}
      {module === "vacation_policy" && Array.isArray(values.general_configs) && <GeneralConfigsEditor value={values.general_configs as GeneralConfig[]} byJobType={Boolean(values.by_job_type)} companyId={companyId} onChange={v => set("general_configs", v)} />}
      {module === "process_fields" && catalog.form_processes && <AdditionalFieldsEditor value={values as Record<string, AdditionalField[]>} catalog={catalog} onChange={v => setValues(v)} />}
      {module === "assignments" && Array.isArray(values.rules) && <AssignmentsEditor value={values.rules as Rule[]} catalog={catalog} groups={groups} onChange={v => set("rules", v)} />}
      {!readOnly && <button className="btn btn-primary" style={{ marginTop: 16 }}><Save size={15} />Salvar em homologação</button>}
    </fieldset></form>}
  </div>;
}

function ModuleInput({ field, value, onChange }: { field: ModuleField; value: unknown; onChange: (v: unknown) => void }) {
  if (field.type === "days" && Array.isArray(value)) return <div className="field-wrap full"><span className="field-label">{field.label}</span><div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>{DAYS.map((d, i) => <label key={d} style={{ display: "flex", gap: 4 }}><input type="checkbox" checked={(value as number[]).includes(i)} onChange={e => onChange(e.target.checked ? [...(value as number[]), i].sort() : (value as number[]).filter(x => x !== i))} />{d}</label>)}</div></div>;
  if (field.type === "boolean") return <label className="field-wrap" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}><input type="checkbox" checked={Boolean(value)} onChange={e => onChange(e.target.checked)} /><span>{field.label}</span></label>;
  if (field.type === "tags") return <Labeled label={`${field.label} — separe por vírgula`}><input className="field" value={(value as string[]).join(", ")} onChange={e => onChange(e.target.value.split(",").map(x => x.trimStart()).filter((x, i, all) => x || i === all.length - 1))} onBlur={e => onChange(e.target.value.split(",").map(x => x.trim()).filter(Boolean))} /></Labeled>;
  if (field.type === "number") return <Labeled label={`${field.label}${field.min != null ? ` (${field.min}–${field.max})` : ""}`}><input className="field" type="number" min={field.min} max={field.max} value={Number(value)} onChange={e => onChange(Math.trunc(Number(e.target.value)))} /></Labeled>;
  return <Labeled label={field.label}><input className="field" value={String(value ?? "")} onChange={e => onChange(e.target.value)} placeholder={/time|start|end/.test(field.key) ? "HH:MM" : undefined} /></Labeled>;
}

function StepsEditor({ steps, approvers, onChange }: { steps: Step[]; approvers: Record<string, string>; onChange: (s: Step[]) => void }) {
  return <div style={{ display: "grid", gap: 6 }}>{steps.map((s, i) => <div key={i} style={{ display: "flex", gap: 6, alignItems: "center" }}><span className="hint" style={{ width: 18 }}>{i + 1}.</span>
    <select className="field" value={s.approver} onChange={e => onChange(steps.map((x, j) => j === i ? { ...x, approver: e.target.value } : x))}>{Object.entries(approvers).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
    <input className="field" type="number" min={1} max={720} value={s.sla_hours} style={{ maxWidth: 100 }} aria-label="SLA em horas úteis" onChange={e => onChange(steps.map((x, j) => j === i ? { ...x, sla_hours: Math.trunc(Number(e.target.value)) } : x))} /><span className="hint">h</span>
    {i < steps.length - 1 && <select className="field" style={{ maxWidth: 260 }} aria-label="Redirecionamento ao aprovar" value={s.finish ? "finish" : "next"} onChange={e => onChange(steps.map((x, j) => j === i ? { ...x, finish: e.target.value === "finish" } : x))}><option value="next">Ao aprovar, segue para a próxima etapa</option><option value="finish">Ao aprovar, finaliza o processo (redirecionamento)</option></select>}
    <button type="button" className="btn btn-quiet" aria-label="Remover etapa" onClick={() => onChange(steps.filter((_, j) => j !== i))}><Trash2 size={14} /></button></div>)}
    {steps.length < 5 && <button type="button" className="btn" style={{ justifySelf: "start" }} onClick={() => onChange([...steps, { approver: "hr", sla_hours: 16 }])}><Plus size={14} />Etapa</button>}</div>;
}

function WorkflowEditor({ value, catalog, groups, onChange, mode = "all" }: { value: Record<string, { steps: Step[]; group_overrides: Record<string, Step[]> }>; catalog: Catalog; groups: string[]; onChange: (v: unknown) => void; mode?: WorkflowMode }) {
  const labels = Object.fromEntries(catalog.processes.map(p => [p.process, p.label]));
  const showSteps = mode !== "overrides"; const showOverrides = mode !== "steps";
  return <div style={{ marginTop: 18 }}><h3 style={{ fontSize: 15 }}>{mode === "overrides" ? "Configuração de aprovação específica por grupo de colaboradores" : mode === "steps" ? "Etapas de aprovação e prazo (SLA) por processo" : "Etapas de aprovação por processo (Atribuições de Configurações por grupo)"}</h3>
    <div style={{ display: "grid", gap: 12 }}>{catalog.process_keys.map(key => { const cfg = value[key]; if (!cfg) return null; return <section key={key} className="panel" style={{ padding: 14 }}>
      <strong>{labels[key] || key}</strong>
      {showSteps && <div style={{ marginTop: 8 }}><StepsEditor steps={cfg.steps} approvers={catalog.approvers} onChange={steps => onChange({ ...value, [key]: { ...cfg, steps } })} /></div>}
      {showOverrides && Object.entries(cfg.group_overrides).map(([group, steps]) => <div key={group} style={{ marginTop: 10, paddingLeft: 12, borderLeft: "3px solid #dbe3ef" }}><div style={{ display: "flex", justifyContent: "space-between" }}><span className="hint">Grupo {group}</span><button type="button" className="btn btn-quiet" onClick={() => { const rest = { ...cfg.group_overrides }; delete rest[group]; onChange({ ...value, [key]: { ...cfg, group_overrides: rest } }); }}>Remover regra do grupo</button></div>
        <StepsEditor steps={steps} approvers={catalog.approvers} onChange={s => onChange({ ...value, [key]: { ...cfg, group_overrides: { ...cfg.group_overrides, [group]: s } } })} /></div>)}
      {showOverrides && groups.filter(g => !(g in cfg.group_overrides)).length > 0 && <select className="filter-select" style={{ marginTop: 8 }} value="" aria-label="Adicionar regra por grupo" onChange={e => { if (e.target.value) onChange({ ...value, [key]: { ...cfg, group_overrides: { ...cfg.group_overrides, [e.target.value]: cfg.steps.map(s => ({ ...s })) } } }); }}><option value="">+ Regra específica para grupo…</option>{groups.filter(g => !(g in cfg.group_overrides)).map(g => <option key={g}>{g}</option>)}</select>}
    </section>; })}</div></div>;
}

type Rule = { process: string; requester_group: string | null; targets: string[]; config_source: "requester" | "target" };
function AssignmentsEditor({ value, catalog, groups, onChange }: { value: Rule[]; catalog: Catalog; groups: string[]; onChange: (v: Rule[]) => void }) {
  const update = (i: number, patchValue: Partial<Rule>) => onChange(value.map((r, j) => j === i ? { ...r, ...patchValue } : r));
  return <div style={{ marginTop: 18 }}><h3 style={{ fontSize: 15 }}>Atribuições de seleção de colaboradores</h3><p className="hint">Define para quem um colaborador com acesso de Funcionário pode abrir cada processo. RH e administradores atuam sobre toda a empresa. A regra mais específica (processo + grupo) prevalece.</p>
    <div className="table-wrap"><table><thead><tr><th>Processo</th><th>Grupo do solicitante</th><th>Pode solicitar para</th><th>Configuração obtida do</th><th></th></tr></thead><tbody>{value.map((r, i) => <tr key={i}>
      <td><select className="field" value={r.process} onChange={e => update(i, { process: e.target.value })}><option value="*">Todos os processos</option>{catalog.process_keys.map(k => <option key={k} value={k}>{catalog.processes.find(p => p.process === k)?.label || k}</option>)}</select></td>
      <td><select className="field" value={r.requester_group ?? ""} onChange={e => update(i, { requester_group: e.target.value || null })}><option value="">Qualquer grupo</option>{groups.map(g => <option key={g}>{g}</option>)}</select></td>
      <td>{Object.entries(TARGET_LABELS).map(([k, label]) => <label key={k} style={{ display: "flex", gap: 6 }}><input type="checkbox" checked={r.targets.includes(k)} onChange={e => update(i, { targets: e.target.checked ? [...r.targets, k] : r.targets.filter(t => t !== k) })} />{label}</label>)}</td>
      <td><select className="field" value={r.config_source} onChange={e => update(i, { config_source: e.target.value as Rule["config_source"] })}><option value="target">Alvo (colaborador)</option><option value="requester">Solicitante</option></select></td>
      <td><button type="button" className="btn btn-quiet" aria-label="Remover atribuição" onClick={() => onChange(value.filter((_, j) => j !== i))}><Trash2 size={14} /></button></td></tr>)}</tbody></table></div>
    <button type="button" className="btn" style={{ marginTop: 8 }} onClick={() => onChange([...value, { process: "*", requester_group: null, targets: ["self"], config_source: "target" }])}><Plus size={14} />Atribuição</button></div>;
}

