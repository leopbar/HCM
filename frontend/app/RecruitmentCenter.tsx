"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Briefcase, CalendarClock, Check, ChevronRight, Plus, Search, Star, UserPlus, Users, X } from "lucide-react";
import { api, datePt, dateTimePt, message, money, post, put, type SessionUser } from "./http";

type Vacancy = { id: number; code: string; title: string; department_id: number; department: string; position_id: number; position: string; requisition_id: number | null; hiring_manager_id?: number | null; hiring_manager: string | null;
  description: string; requirements: string; contract_type: string; salary_min: number | null; salary_max: number | null; quantity: number; hired?: number; recruitment_type: "internal" | "external" | "mixed";
  status: string; status_label: string; deadline: string | null; stages: string[]; candidates?: number; active_candidates?: number; by_stage?: Record<string, number>; applications?: AppRow[] };
type AppRow = { id: number; vacancy_id: number; vacancy_title: string; vacancy_code: string; candidate_id: number; candidate_name: string; internal: boolean; stage: string; stage_index: number | null; total_stages: number;
  status: string; status_label: string; created_at: string | null; candidate_email?: string; candidate_phone?: string | null; source?: string; score?: number | null; rejection_reason?: string | null; admission_id?: number | null;
  summary?: string; events?: { kind: string; stage: string; to_stage: string | null; score?: number | null; criteria?: Record<string, number> | null; comment?: string | null; actor?: string; created_at: string | null }[];
  interviews?: Interview[]; stages?: string[]; department_id?: number; position_id?: number; evaluation_criteria?: string[] };
type Interview = { id: number; application_id: number; stage: string; scheduled_at: string; duration_minutes: number; interviewer: string; mode: string; location: string | null; status: string; candidate_name?: string; vacancy_title?: string };
type Candidate = { id: number; full_name: string; email: string; phone: string | null; city: string | null; source: string; summary: string; internal: boolean; applications: number; retention_expired: boolean; consent_at: string };
type Options = { departments: { id: number; name: string }[]; positions: { id: number; title: string }[]; groups: { id: number; name: string }[] };
type Summary = { open_vacancies: number; draft_vacancies: number; active_applications: number; hired: number; internal_hired: number; average_days_to_hire: number | null; upcoming_interviews: number };
type Tab = "vacancies" | "pipeline" | "talent" | "internal" | "mine" | "interviews";

const TYPE_LABELS = { internal: "Interna", external: "Externa", mixed: "Interna e externa" };
const SOURCES: Record<string, string> = { site: "Site de carreiras", indicacao: "Indicação", linkedin: "LinkedIn", agencia: "Agência", interno: "Interno", outro: "Outro" };
const EVENT_LABELS: Record<string, string> = { applied: "Candidatura registrada", moved: "Mudou de etapa", evaluation: "Avaliação", interview: "Entrevista", rejected: "Reprovado", withdrawn: "Desistência", offer: "Contratação iniciada", hired: "Contratado", note: "Observação" };

export default function RecruitmentCenter({ user, companyId, onToast }: { user: SessionUser; companyId: string; onToast: (m: string) => void }) {
  const manager = user.role === "system_admin" || user.role === "hr_manager";
  const viewer = manager || user.role === "consultant";
  const [tab, setTab] = useState<Tab>(viewer ? "vacancies" : "internal");
  const [vacancies, setVacancies] = useState<Vacancy[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [editing, setEditing] = useState<Vacancy | "new" | null>(null);
  const [opened, setOpened] = useState<number | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    if (!companyId) return;
    try { setVacancies(await api<Vacancy[]>(`/recruitment/vacancies?company_id=${companyId}`)); if (viewer) setSummary(await api<Summary>(`/recruitment/summary?company_id=${companyId}`)); setError(""); } catch (e) { setError(message(e)); }
  }, [companyId, viewer]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  if (!companyId) return <div className="empty">Selecione uma empresa no topo da página.</div>;
  const managed = vacancies.filter(v => v.hiring_manager_id === user.employee_id && user.employee_id);
  const tabs: [Tab, string, boolean][] = [["vacancies", "Vagas", viewer || managed.length > 0], ["pipeline", "Processo seletivo", viewer || managed.length > 0], ["talent", "Banco de talentos", manager],
    ["internal", "Vagas internas", user.role === "employee"], ["mine", "Minhas candidaturas", Boolean(user.employee_id)], ["interviews", "Minhas entrevistas", true]];
  const openPipeline = (id: number) => { setSelected(id); setTab("pipeline"); };
  return <>
    <div className="page-head"><div><div className="eyebrow">Recrutar e selecionar</div><h1 className="page-title">Recrutamento e seleção</h1>
      <p className="page-desc">Vagas abertas pela requisição de pessoal, banco de talentos com consentimento (LGPD), etapas e avaliações, entrevistas, vagas internas e contratação integrada à admissão.</p></div>
      {manager && <button className="btn btn-primary" onClick={() => setEditing("new")}><Plus size={15} />Nova vaga</button>}</div>
    {summary && <div className="cards" style={{ marginBottom: 14 }}>
      <Stat label="Vagas abertas" value={summary.open_vacancies} note={`${summary.draft_vacancies} em rascunho`} /><Stat label="Candidatos em processo" value={summary.active_applications} note="Ativos ou em contratação" />
      <Stat label="Contratados" value={summary.hired} note={`${summary.internal_hired} internos`} /><Stat label="Tempo médio até contratar" value={summary.average_days_to_hire != null ? `${summary.average_days_to_hire} dias` : "—"} note={`${summary.upcoming_interviews} entrevista(s) agendada(s)`} /></div>}
    <div style={{ display: "flex", gap: 8, marginBottom: 14, flexWrap: "wrap" }}>{tabs.filter(t => t[2]).map(([key, label]) => <button key={key} className={`btn ${tab === key ? "btn-primary" : ""}`} onClick={() => setTab(key)}>{label}</button>)}</div>
    {error && <div className="alert alert-error">{error}</div>}
    {tab === "vacancies" && <VacancyList rows={viewer ? vacancies : managed} manager={manager} onEdit={setEditing} onPipeline={openPipeline} onChanged={async m => { onToast(m); await load(); }} onError={setError} />}
    {tab === "pipeline" && <Pipeline vacancies={viewer ? vacancies : managed} selected={selected} setSelected={setSelected} onOpen={setOpened} />}
    {tab === "talent" && manager && <TalentPool companyId={companyId} vacancies={vacancies.filter(v => v.status === "open")} onToast={onToast} onChanged={load} />}
    {tab === "internal" && <InternalVacancies rows={vacancies.filter(v => v.status === "open")} onApplied={async m => { onToast(m); await load(); setTab("mine"); }} />}
    {tab === "mine" && <MyApplications onOpen={setOpened} />}
    {tab === "interviews" && <MyInterviews onOpen={setOpened} />}
    {editing && <VacancyForm companyId={companyId} vacancy={editing === "new" ? null : editing} close={() => setEditing(null)} onSaved={async m => { setEditing(null); onToast(m); await load(); }} />}
    {opened !== null && <ApplicationModal id={opened} user={user} companyId={companyId} close={() => setOpened(null)} onChanged={async m => { onToast(m); await load(); }} />}
  </>;
}

function Stat({ label, value, note }: { label: string; value: ReactNode; note: string }) { return <div className="stat-card"><div className="stat-top"><span>{label}</span></div><div className="stat-value">{value}</div><div className="stat-note">{note}</div></div>; }
function F({ label, children, full }: { label: string; children: ReactNode; full?: boolean }) { return <label className={`field-wrap ${full ? "full" : ""}`}><span className="field-label">{label}</span>{children}</label>; }
function Modal({ title, children, close }: { title: string; children: ReactNode; close: () => void }) {
  return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}><section className="modal" style={{ maxWidth: 880 }}><div className="modal-head"><h2 className="modal-title">{title}</h2><button className="btn btn-quiet" type="button" onClick={close} aria-label="Fechar"><X size={17} /></button></div>{children}</section></div>;
}
const range = (v: Vacancy) => v.salary_min || v.salary_max ? `${v.salary_min ? money(v.salary_min) : "—"} a ${v.salary_max ? money(v.salary_max) : "—"}` : "A combinar";

function VacancyList({ rows, manager, onEdit, onPipeline, onChanged, onError }: { rows: Vacancy[]; manager: boolean; onEdit: (v: Vacancy) => void; onPipeline: (id: number) => void; onChanged: (m: string) => Promise<void>; onError: (m: string) => void }) {
  async function setStatus(v: Vacancy, status: string) {
    let reason = "";
    if (status === "closed" || status === "cancelled") { reason = window.prompt("Motivo (será registrado nos candidatos ainda ativos):", "Vaga encerrada") || ""; if (!reason.trim()) return; }
    try { await post(`/recruitment/vacancies/${v.id}/status`, { status, reason }); await onChanged(`Vaga ${v.code} atualizada.`); } catch (e) { onError(message(e)); }
  }
  return <section className="section-panel"><div className="table-wrap"><table><thead><tr><th>Código</th><th>Vaga</th><th>Tipo</th><th>Faixa</th><th>Candidatos</th><th>Preenchidas</th><th>Status</th><th></th></tr></thead><tbody>
    {rows.map(v => <tr key={v.id}><td className="code">{v.code}</td><td><div className="person-name">{v.title}</div><div className="person-email">{v.department} · {v.position}{v.requisition_id ? ` · requisição nº ${v.requisition_id}` : ""}{v.hiring_manager ? ` · gestor: ${v.hiring_manager}` : ""}</div></td>
      <td>{TYPE_LABELS[v.recruitment_type]}</td><td>{range(v)}</td><td>{v.active_candidates ?? "—"}</td><td>{v.hired ?? 0}/{v.quantity}</td><td><span className="tag">{v.status_label}</span>{v.deadline && <div className="hint">até {datePt(v.deadline)}</div>}</td>
      <td style={{ whiteSpace: "nowrap" }}><button className="btn" onClick={() => onPipeline(v.id)}>Processo<ChevronRight size={13} /></button>
        {manager && !["filled", "closed", "cancelled"].includes(v.status) && <> <button className="btn" onClick={() => onEdit(v)}>Editar</button>
          {(v.status === "draft" || v.status === "paused") && <button className="btn btn-primary" onClick={() => void setStatus(v, "open")}>Publicar</button>}
          {v.status === "open" && <button className="btn" onClick={() => void setStatus(v, "paused")}>Pausar</button>}
          <button className="btn btn-quiet" onClick={() => void setStatus(v, v.status === "draft" ? "cancelled" : "closed")}>{v.status === "draft" ? "Cancelar" : "Encerrar"}</button></>}</td></tr>)}
  </tbody></table>{rows.length === 0 && <div className="empty">Nenhuma vaga. Vagas são criadas aqui ou automaticamente quando uma requisição de pessoal é aprovada.</div>}</div></section>;
}

function VacancyForm({ companyId, vacancy, close, onSaved }: { companyId: string; vacancy: Vacancy | null; close: () => void; onSaved: (m: string) => Promise<void> }) {
  const [options, setOptions] = useState<Options | null>(null); const [employees, setEmployees] = useState<{ id: number; full_name: string; status: string }[]>([]);
  const [form, setForm] = useState({ title: vacancy?.title || "", department_id: String(vacancy?.department_id || ""), position_id: String(vacancy?.position_id || ""), hiring_manager_id: String(vacancy?.hiring_manager_id || ""),
    requisition_id: vacancy?.requisition_id ? String(vacancy.requisition_id) : "", description: vacancy?.description || "", requirements: vacancy?.requirements || "", contract_type: vacancy?.contract_type || "CLT",
    salary_min: vacancy?.salary_min != null ? String(vacancy.salary_min) : "", salary_max: vacancy?.salary_max != null ? String(vacancy.salary_max) : "", quantity: String(vacancy?.quantity || 1),
    recruitment_type: vacancy?.recruitment_type || "mixed", deadline: vacancy?.deadline || "" });
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { api<Options>(`/companies/${companyId}/options`).then(o => { setOptions(o); setForm(f => ({ ...f, department_id: f.department_id || String(o.departments[0]?.id || ""), position_id: f.position_id || String(o.positions[0]?.id || "") })); }).catch(e => setError(message(e)));
    api<{ id: number; full_name: string; status: string }[]>(`/employees?company_id=${companyId}`).then(setEmployees).catch(() => setEmployees([])); }, [companyId]);
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    const body = { ...form, company_id: Number(companyId), department_id: Number(form.department_id), position_id: Number(form.position_id), hiring_manager_id: form.hiring_manager_id ? Number(form.hiring_manager_id) : null,
      requisition_id: form.requisition_id ? Number(form.requisition_id) : null, salary_min: form.salary_min ? Number(form.salary_min) : null, salary_max: form.salary_max ? Number(form.salary_max) : null,
      quantity: Number(form.quantity), deadline: form.deadline || null };
    try { if (vacancy) await put(`/recruitment/vacancies/${vacancy.id}`, body); else await post("/recruitment/vacancies", body); await onSaved(vacancy ? "Vaga atualizada." : "Vaga criada em rascunho. Publique quando estiver pronta."); } catch (ex) { setError(message(ex)); } finally { setBusy(false); }
  }
  return <Modal title={vacancy ? `Editar vaga ${vacancy.code}` : "Nova vaga"} close={close}><form onSubmit={save}><div className="modal-body">{error && <div className="alert alert-error">{error}</div>}{!options ? <div className="loading">Carregando…</div> : <div className="form-grid">
    <F label="Título" full><input className="field" required minLength={3} value={form.title} onChange={set("title")} /></F>
    <F label="Departamento"><select className="field" value={form.department_id} onChange={set("department_id")}>{options.departments.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></F>
    <F label="Cargo"><select className="field" value={form.position_id} onChange={set("position_id")}>{options.positions.map(p => <option key={p.id} value={p.id}>{p.title}</option>)}</select></F>
    <F label="Gestor da vaga (avalia os candidatos)"><select className="field" value={form.hiring_manager_id} onChange={set("hiring_manager_id")}><option value="">Sem gestor definido</option>{employees.filter(x => x.status !== "Desligado").map(x => <option key={x.id} value={x.id}>{x.full_name}</option>)}</select></F>
    <F label="Requisição de pessoal (nº)"><input className="field" inputMode="numeric" value={form.requisition_id} onChange={set("requisition_id")} placeholder="Opcional" /></F>
    <F label="Tipo de recrutamento"><select className="field" value={form.recruitment_type} onChange={set("recruitment_type")}>{Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></F>
    <F label="Contrato"><select className="field" value={form.contract_type} onChange={set("contract_type")}>{["CLT", "Estágio", "Aprendiz", "Temporário", "Diretor"].map(c => <option key={c}>{c}</option>)}</select></F>
    <F label="Salário mínimo (R$)"><input className="field" type="number" min="0" step="0.01" value={form.salary_min} onChange={set("salary_min")} /></F>
    <F label="Salário máximo (R$)"><input className="field" type="number" min="0" step="0.01" value={form.salary_max} onChange={set("salary_max")} /></F>
    <F label="Quantidade de vagas"><input className="field" type="number" min="1" max="50" value={form.quantity} onChange={set("quantity")} /></F>
    <F label="Inscrições até"><input className="field" type="date" value={form.deadline} onChange={set("deadline")} /></F>
    <F label="Descrição das atividades" full><textarea className="field" rows={3} value={form.description} onChange={set("description")} /></F>
    <F label="Requisitos" full><textarea className="field" rows={3} value={form.requirements} onChange={set("requirements")} /></F>
  </div>}</div><div className="modal-foot"><button type="button" className="btn" onClick={close}>Cancelar</button><button className="btn btn-primary" disabled={busy || !options}>Salvar</button></div></form></Modal>;
}

function Pipeline({ vacancies, selected, setSelected, onOpen }: { vacancies: Vacancy[]; selected: number | null; setSelected: (id: number) => void; onOpen: (id: number) => void }) {
  const [detail, setDetail] = useState<Vacancy | null>(null); const [error, setError] = useState("");
  const id = selected ?? vacancies[0]?.id ?? null;
  useEffect(() => { if (id) api<Vacancy>(`/recruitment/vacancies/${id}`).then(setDetail).catch(e => setError(message(e))); }, [id, vacancies]);
  if (!vacancies.length) return <div className="empty">Nenhuma vaga disponível.</div>;
  const apps = detail?.applications || [];
  const closed = apps.filter(a => !["active", "offer"].includes(a.status));
  return <section>
    <div className="toolbar" style={{ marginBottom: 10 }}><select className="filter-select" value={id ?? ""} onChange={e => setSelected(Number(e.target.value))} aria-label="Vaga">{vacancies.map(v => <option key={v.id} value={v.id}>{v.code} · {v.title} ({v.status_label})</option>)}</select></div>
    {error && <div className="alert alert-error">{error}</div>}
    {detail && <div style={{ display: "grid", gridTemplateColumns: `repeat(${detail.stages.length}, minmax(170px, 1fr))`, gap: 10, overflowX: "auto" }}>{detail.stages.map(stage => <div key={stage} className="panel" style={{ padding: 10, minHeight: 160 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}><strong style={{ fontSize: 13 }}>{stage}</strong><span className="tag">{apps.filter(a => a.stage === stage && ["active", "offer"].includes(a.status)).length}</span></div>
      {apps.filter(a => a.stage === stage && ["active", "offer"].includes(a.status)).map(a => <button key={a.id} className="company-mini" style={{ width: "100%", textAlign: "left", marginTop: 8, cursor: "pointer", display: "block" }} onClick={() => onOpen(a.id)}>
        <div className="company-mini-name">{a.candidate_name}</div><div className="company-mini-meta">{a.internal ? "Interno · " : ""}{a.score != null ? <><Star size={11} /> {a.score.toFixed(1)}</> : "Sem avaliação"}{a.status === "offer" ? " · em contratação" : ""}</div></button>)}
    </div>)}</div>}
    {closed.length > 0 && <section className="section-panel" style={{ marginTop: 14 }}><div className="table-wrap"><table><thead><tr><th>Candidato</th><th>Situação</th><th>Etapa</th><th>Motivo</th><th></th></tr></thead><tbody>{closed.map(a => <tr key={a.id}><td>{a.candidate_name}</td><td><span className="tag">{a.status_label}</span></td><td>{a.stage}</td><td>{a.rejection_reason || "—"}</td><td><button className="btn" onClick={() => onOpen(a.id)}>Ver</button></td></tr>)}</tbody></table></div></section>}
  </section>;
}

function ApplicationModal({ id, user, companyId, close, onChanged }: { id: number; user: SessionUser; companyId: string; close: () => void; onChanged: (m: string) => Promise<void> }) {
  const manager = user.role === "system_admin" || user.role === "hr_manager";
  const [row, setRow] = useState<AppRow | null>(null); const [criteria, setCriteria] = useState<string[]>([]); const [scores, setScores] = useState<Record<string, number>>({}); const [comment, setComment] = useState("");
  const [stages, setStages] = useState<string[]>([]); const [interviewers, setInterviewers] = useState<{ id: number; name: string }[]>([]); const [options, setOptions] = useState<Options | null>(null);
  const [seats, setSeats] = useState<{ id: number; code: string; status: string; department_id: number; position_id: number }[]>([]);
  const [interview, setInterview] = useState({ scheduled_at: "", duration_minutes: "60", interviewer_id: "", mode: "online", location: "" });
  const [hire, setHire] = useState({ planned_date: "", salary: "", registration: "", group_id: "", job_position_id: "", documents_complete: false });
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const reload = useCallback(async () => { const r = await api<AppRow>(`/recruitment/applications/${id}`); setRow(r); return r; }, [id]);
  useEffect(() => {
    // Load the application and its lookups when the dialog opens.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    reload().then(async r => {
      if (r.candidate_email !== undefined) {
        setStages(r.stages || []);
        setCriteria(r.evaluation_criteria || []);
        if (manager) {
          api<{ id: number; name: string }[]>(`/recruitment/interviewers?company_id=${companyId}`).then(setInterviewers).catch(() => setInterviewers([]));
          api<Options>(`/companies/${companyId}/options`).then(setOptions).catch(() => setOptions(null));
          api<typeof seats>(`/companies/${companyId}/setup/positions`).then(all => setSeats(all.filter(s => s.status === "vacant" && s.department_id === r.department_id && s.position_id === r.position_id))).catch(() => setSeats([]));
        }
      }
    }).catch(e => setError(message(e)));
  }, [reload, companyId, manager]);
  async function act(fn: () => Promise<unknown>, done: string) { setBusy(true); setError(""); try { await fn(); await reload(); await onChanged(done); } catch (e) { setError(message(e)); } finally { setBusy(false); } }
  if (!row) return <Modal title="Candidatura" close={close}><div className="modal-body">{error ? <div className="alert alert-error">{error}</div> : <div className="loading">Carregando…</div>}</div></Modal>;
  const full = row.candidate_email !== undefined; const active = row.status === "active";
  const last = stages.length > 0 && row.stage === stages[stages.length - 1];
  const myCandidacy = !full;
  return <Modal title={`${row.candidate_name} · ${row.vacancy_code}`} close={close}><div className="modal-body">
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", marginBottom: 10 }}><span className="tag">{row.status_label}</span><span className="hint">Etapa {row.stage_index != null ? row.stage_index + 1 : "—"}/{row.total_stages}: <strong>{row.stage}</strong></span>
      {row.internal && <span className="tag">Candidato interno</span>}{full && <span className="hint">{row.candidate_email}{row.candidate_phone ? ` · ${row.candidate_phone}` : ""} · origem {SOURCES[row.source || "outro"]}</span>}{row.score != null && <span className="hint"><Star size={12} /> Média {row.score.toFixed(2)}</span>}</div>
    {row.summary && <p className="hint">{row.summary}</p>}
    {error && <div className="alert alert-error">{error}</div>}
    {(row.interviews?.length || 0) > 0 && <><h3 style={{ fontSize: 14 }}>Entrevistas</h3><ul style={{ paddingLeft: 18 }}>{row.interviews!.map(i => <li key={i.id}>{dateTimePt(i.scheduled_at)} · {i.stage} · {i.interviewer} · {i.mode}{i.location ? ` (${i.location})` : ""} · <strong>{{ scheduled: "Agendada", done: "Realizada", cancelled: "Cancelada", no_show: "Não compareceu" }[i.status]}</strong>
      {i.status === "scheduled" && full && <> <button className="btn btn-quiet" disabled={busy} onClick={() => void act(() => post(`/recruitment/interviews/${i.id}/status`, { status: "done" }), "Entrevista registrada como realizada.")}>Realizada</button><button className="btn btn-quiet" disabled={busy} onClick={() => void act(() => post(`/recruitment/interviews/${i.id}/status`, { status: "no_show" }), "Ausência registrada.")}>Não compareceu</button></>}</li>)}</ul></>}
    {full && active && criteria.length > 0 && user.role !== "consultant" && <section className="panel" style={{ padding: 12, marginTop: 10 }}><strong>Avaliar na etapa {row.stage}</strong><div className="form-grid" style={{ marginTop: 8 }}>{criteria.map(c => <F key={c} label={c}><select className="field" value={scores[c] ?? ""} onChange={e => setScores({ ...scores, [c]: Number(e.target.value) })}><option value="">Nota…</option>{[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n}</option>)}</select></F>)}
      <F label="Parecer" full><textarea className="field" rows={2} value={comment} onChange={e => setComment(e.target.value)} /></F></div>
      <button className="btn btn-primary" disabled={busy || criteria.some(c => !scores[c])} onClick={() => void act(() => post(`/recruitment/applications/${id}/evaluate`, { criteria: scores, comment }), "Avaliação registrada.").then(() => { setScores({}); setComment(""); })}><Star size={14} />Registrar avaliação</button></section>}
    {manager && active && full && <section className="panel" style={{ padding: 12, marginTop: 10 }}><strong>Conduzir o processo</strong>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>{stages.filter(s => s !== row.stage).map(s => <button key={s} className="btn" disabled={busy} onClick={() => void act(() => post(`/recruitment/applications/${id}/move`, { to_stage: s }), `Candidato movido para ${s}.`)}>Mover para {s}</button>)}
        <button className="btn btn-quiet" disabled={busy} onClick={() => { const reason = window.prompt("Motivo da reprovação (mínimo de 5 caracteres):") || ""; if (reason.trim().length >= 5) void act(() => post(`/recruitment/applications/${id}/reject`, { reason }), "Candidato reprovado."); }}><X size={14} />Reprovar</button></div>
      <div className="form-grid" style={{ marginTop: 12 }}><F label="Entrevista em"><input className="field" type="datetime-local" value={interview.scheduled_at} onChange={e => setInterview({ ...interview, scheduled_at: e.target.value })} /></F>
        <F label="Entrevistador"><select className="field" value={interview.interviewer_id} onChange={e => setInterview({ ...interview, interviewer_id: e.target.value })}><option value="">Selecione…</option>{interviewers.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}</select></F>
        <F label="Formato"><select className="field" value={interview.mode} onChange={e => setInterview({ ...interview, mode: e.target.value })}><option value="online">Online</option><option value="presencial">Presencial</option><option value="telefone">Telefone</option></select></F>
        <F label="Duração (min)"><input className="field" type="number" min="15" max="480" value={interview.duration_minutes} onChange={e => setInterview({ ...interview, duration_minutes: e.target.value })} /></F>
        <F label="Local ou link" full><input className="field" value={interview.location} onChange={e => setInterview({ ...interview, location: e.target.value })} /></F></div>
      <button className="btn" disabled={busy || !interview.scheduled_at || !interview.interviewer_id} onClick={() => void act(() => post(`/recruitment/applications/${id}/interviews`, { ...interview, interviewer_id: Number(interview.interviewer_id), duration_minutes: Number(interview.duration_minutes) }), "Entrevista agendada e entrevistador notificado.")}><CalendarClock size={14} />Agendar entrevista</button>
      {last && <div style={{ marginTop: 14 }}><strong>{row.internal ? "Aprovar e movimentar (candidato interno)" : "Aprovar e solicitar admissão"}</strong><div className="form-grid" style={{ marginTop: 8 }}>
        <F label={row.internal ? "Data de efetivação" : "Data de admissão"}><input className="field" type="date" value={hire.planned_date} onChange={e => setHire({ ...hire, planned_date: e.target.value })} /></F>
        <F label="Salário (R$)"><input className="field" type="number" min="0.01" step="0.01" value={hire.salary} onChange={e => setHire({ ...hire, salary: e.target.value })} /></F>
        {!row.internal && <F label="Matrícula"><input className="field" value={hire.registration} onChange={e => setHire({ ...hire, registration: e.target.value })} /></F>}
        <F label="Grupo de funcionários"><select className="field" value={hire.group_id} onChange={e => setHire({ ...hire, group_id: e.target.value })}><option value="">{row.internal ? "Manter o atual" : "Primeiro grupo da empresa"}</option>{options?.groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}</select></F>
        {!row.internal && <F label="Posição"><select className="field" value={hire.job_position_id} onChange={e => setHire({ ...hire, job_position_id: e.target.value })}><option value="">Sem posição</option>{seats.map(s => <option key={s.id} value={s.id}>{s.code}</option>)}</select></F>}
        {!row.internal && <label className="field-wrap" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}><input type="checkbox" checked={hire.documents_complete} onChange={e => setHire({ ...hire, documents_complete: e.target.checked })} />Documentos completos</label>}</div>
        <button className="btn btn-primary" disabled={busy || !hire.planned_date || !hire.salary} onClick={() => void act(async () => { const out = await post<{ kind: string; status: string }>(`/recruitment/applications/${id}/hire`, { ...hire, salary: Number(hire.salary), group_id: hire.group_id ? Number(hire.group_id) : null, job_position_id: hire.job_position_id ? Number(hire.job_position_id) : null, registration: hire.registration || null }); return out; }, row.internal ? "Movimentação interna solicitada." : "Admissão solicitada; aprove em Admissão para gerar o funcionário.")}><UserPlus size={14} />Contratar</button></div>}
    </section>}
    {myCandidacy && active && <button className="btn" style={{ marginTop: 10 }} disabled={busy} onClick={() => { if (window.confirm("Desistir desta candidatura?")) void act(() => post(`/recruitment/applications/${id}/withdraw`, {}), "Desistência registrada."); }}>Desistir da candidatura</button>}
    <h3 style={{ fontSize: 14, margin: "14px 0 6px" }}>Histórico</h3>
    <div style={{ display: "grid", gap: 6 }}>{(row.events || []).map((e, i) => <div key={i} className="activity-row"><span className="activity-dot" /><div><div className="activity-text">{e.actor ? <strong>{e.actor} · </strong> : null}{EVENT_LABELS[e.kind] || e.kind}{e.to_stage ? `: ${e.stage} → ${e.to_stage}` : ` (${e.stage})`}{e.score != null ? ` · nota ${e.score}` : ""}{e.criteria ? ` · ${Object.entries(e.criteria).map(([k, v]) => `${k} ${v}`).join(", ")}` : ""}{e.comment ? ` — ${e.comment}` : ""}</div><div className="activity-time">{dateTimePt(e.created_at)}</div></div></div>)}</div>
  </div><div className="modal-foot"><button className="btn" onClick={close}>Fechar</button></div></Modal>;
}

function TalentPool({ companyId, vacancies, onToast, onChanged }: { companyId: string; vacancies: Vacancy[]; onToast: (m: string) => void; onChanged: () => Promise<void> }) {
  const [rows, setRows] = useState<Candidate[]>([]); const [q, setQ] = useState(""); const [error, setError] = useState(""); const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({ full_name: "", email: "", phone: "", city: "", source: "site", summary: "", consent: false });
  const load = useCallback(async () => { try { setRows(await api<Candidate[]>(`/recruitment/candidates?company_id=${companyId}&q=${encodeURIComponent(q)}`)); } catch (e) { setError(message(e)); } }, [companyId, q]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  async function save(e: FormEvent) { e.preventDefault(); setError(""); try { await post("/recruitment/candidates", { ...form, company_id: Number(companyId), phone: form.phone || null, city: form.city || null }); onToast("Candidato incluído no banco de talentos."); setAdding(false); setForm({ full_name: "", email: "", phone: "", city: "", source: "site", summary: "", consent: false }); await load(); } catch (ex) { setError(message(ex)); } }
  async function addTo(c: Candidate, vacancyId: string) { if (!vacancyId) return; try { await post(`/recruitment/vacancies/${vacancyId}/applications`, { candidate_id: c.id }); onToast(`${c.full_name} incluído(a) no processo seletivo.`); await load(); await onChanged(); } catch (e) { setError(message(e)); } }
  async function anonymize(c: Candidate) { if (!window.confirm(`Anonimizar os dados de ${c.full_name}? Esta ação não pode ser desfeita.`)) return; try { await post(`/recruitment/candidates/${c.id}/anonymize`, {}); onToast("Dados pessoais removidos (LGPD)."); await load(); } catch (e) { setError(message(e)); } }
  return <section className="section-panel">
    <div className="toolbar"><div className="toolbar-left"><div className="search-wrap"><Search size={14} className="search-icon" /><input className="search" placeholder="Buscar nome, e-mail ou competência" value={q} onChange={e => setQ(e.target.value)} /></div></div>
      <div className="toolbar-right"><button className="btn btn-primary" onClick={() => setAdding(!adding)}><UserPlus size={14} />Novo candidato</button></div></div>
    {error && <div className="alert alert-error">{error}</div>}
    {adding && <form onSubmit={save} style={{ padding: 14 }}><div className="form-grid">
      <F label="Nome completo"><input className="field" required minLength={3} value={form.full_name} onChange={e => setForm({ ...form, full_name: e.target.value })} /></F>
      <F label="E-mail"><input className="field" type="email" required value={form.email} onChange={e => setForm({ ...form, email: e.target.value })} /></F>
      <F label="Telefone"><input className="field" value={form.phone} onChange={e => setForm({ ...form, phone: e.target.value })} /></F>
      <F label="Cidade"><input className="field" value={form.city} onChange={e => setForm({ ...form, city: e.target.value })} /></F>
      <F label="Origem"><select className="field" value={form.source} onChange={e => setForm({ ...form, source: e.target.value })}>{Object.entries(SOURCES).filter(([k]) => k !== "interno").map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></F>
      <F label="Resumo profissional" full><textarea className="field" rows={3} value={form.summary} onChange={e => setForm({ ...form, summary: e.target.value })} /></F>
      <label className="field-wrap full" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}><input type="checkbox" checked={form.consent} onChange={e => setForm({ ...form, consent: e.target.checked })} />O candidato consentiu com o tratamento dos dados para recrutamento (LGPD).</label></div>
      <button className="btn btn-primary" style={{ marginTop: 10 }} disabled={!form.consent}><Check size={14} />Salvar</button></form>}
    <div className="table-wrap"><table><thead><tr><th>Candidato</th><th>Origem</th><th>Processos</th><th>Retenção</th><th>Incluir em vaga</th><th></th></tr></thead><tbody>{rows.map(c => <tr key={c.id}>
      <td><div className="person-name">{c.full_name}{c.internal ? " (interno)" : ""}</div><div className="person-email">{c.email}{c.city ? ` · ${c.city}` : ""}</div>{c.summary && <div className="hint">{c.summary.slice(0, 120)}</div>}</td>
      <td>{SOURCES[c.source] || c.source}</td><td>{c.applications}</td><td>{c.retention_expired ? <span className="tag">Prazo vencido</span> : <span className="hint">Consentido em {datePt(c.consent_at)}</span>}</td>
      <td><select className="filter-select" value="" aria-label={`Incluir ${c.full_name} em uma vaga`} onChange={e => void addTo(c, e.target.value)}><option value="">Selecionar vaga…</option>{vacancies.map(v => <option key={v.id} value={v.id}>{v.code} · {v.title}</option>)}</select></td>
      <td><button className="btn btn-quiet" onClick={() => void anonymize(c)}>Anonimizar</button></td></tr>)}</tbody></table>{rows.length === 0 && <div className="empty">Nenhum candidato no banco de talentos.</div>}</div>
  </section>;
}

function InternalVacancies({ rows, onApplied }: { rows: Vacancy[]; onApplied: (m: string) => Promise<void> }) {
  const [applying, setApplying] = useState<Vacancy | null>(null); const [consent, setConsent] = useState(false); const [summary, setSummary] = useState(""); const [error, setError] = useState("");
  async function apply() { if (!applying) return; setError(""); try { await post(`/recruitment/vacancies/${applying.id}/apply`, { consent, summary }); setApplying(null); setConsent(false); setSummary(""); await onApplied(`Candidatura enviada para ${applying.code}.`); } catch (e) { setError(message(e)); } }
  return <section style={{ display: "grid", gap: 12 }}>{error && <div className="alert alert-error">{error}</div>}
    {rows.map(v => <article key={v.id} className="panel" style={{ padding: 16 }}><div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}><div><div className="eyebrow">{v.code} · {TYPE_LABELS[v.recruitment_type]}</div><h3 style={{ margin: "4px 0" }}><Briefcase size={15} /> {v.title}</h3><div className="hint">{v.department} · {v.position} · {v.contract_type} · {range(v)}{v.deadline ? ` · inscrições até ${datePt(v.deadline)}` : ""}</div></div>
      <button className="btn btn-primary" onClick={() => setApplying(v)}>Candidatar-me</button></div>
      <p style={{ whiteSpace: "pre-wrap" }}>{v.description}</p><p className="hint" style={{ whiteSpace: "pre-wrap" }}><strong>Requisitos:</strong> {v.requirements}</p></article>)}
    {rows.length === 0 && <div className="empty">Nenhuma vaga interna aberta no momento.</div>}
    {applying && <Modal title={`Candidatar-me: ${applying.title}`} close={() => setApplying(null)}><div className="modal-body">{error && <div className="alert alert-error">{error}</div>}
      <F label="Por que você se interessa pela vaga? (opcional)" full><textarea className="field" rows={3} value={summary} maxLength={2000} onChange={e => setSummary(e.target.value)} /></F>
      <label style={{ display: "flex", gap: 8, marginTop: 10 }}><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} />Autorizo o uso dos meus dados neste processo seletivo. O RH e o gestor da vaga verão minha candidatura, e meu gestor imediato poderá ser avisado.</label></div>
      <div className="modal-foot"><button className="btn" onClick={() => setApplying(null)}>Cancelar</button><button className="btn btn-primary" disabled={!consent} onClick={() => void apply()}>Enviar candidatura</button></div></Modal>}
  </section>;
}

function MyApplications({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<AppRow[]>([]);
  useEffect(() => { api<AppRow[]>("/recruitment/applications/mine").then(setRows).catch(() => setRows([])); }, []);
  return <section className="section-panel"><div className="table-wrap"><table><thead><tr><th>Vaga</th><th>Etapa</th><th>Situação</th><th>Enviada em</th><th></th></tr></thead><tbody>{rows.map(a => <tr key={a.id}><td>{a.vacancy_code} · {a.vacancy_title}</td><td>{a.stage} ({(a.stage_index ?? 0) + 1}/{a.total_stages})</td><td><span className="tag">{a.status_label}</span></td><td>{datePt(a.created_at)}</td><td><button className="btn" onClick={() => onOpen(a.id)}>Acompanhar</button></td></tr>)}</tbody></table>{rows.length === 0 && <div className="empty"><Users size={16} /> Você ainda não se candidatou a vagas internas.</div>}</div></section>;
}

function MyInterviews({ onOpen }: { onOpen: (id: number) => void }) {
  const [rows, setRows] = useState<Interview[]>([]);
  useEffect(() => { api<Interview[]>("/recruitment/interviews/mine").then(setRows).catch(() => setRows([])); }, []);
  return <section className="section-panel"><div className="table-wrap"><table><thead><tr><th>Quando</th><th>Candidato</th><th>Vaga</th><th>Etapa</th><th>Formato</th><th>Situação</th><th></th></tr></thead><tbody>{rows.map(i => <tr key={i.id}><td>{dateTimePt(i.scheduled_at)}</td><td>{i.candidate_name}</td><td>{i.vacancy_title}</td><td>{i.stage}</td><td>{i.mode}{i.location ? ` · ${i.location}` : ""}</td><td><span className="tag">{{ scheduled: "Agendada", done: "Realizada", cancelled: "Cancelada", no_show: "Não compareceu" }[i.status]}</span></td><td><button className="btn" onClick={() => onOpen(i.application_id)}>Avaliar</button></td></tr>)}</tbody></table>{rows.length === 0 && <div className="empty">Nenhuma entrevista atribuída a você.</div>}</div></section>;
}
