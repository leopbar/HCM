"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ArrowDown, ArrowUp, Plus, Save, Trash2, UserMinus, UserPlus } from "lucide-react";
import { api, datePt, del, message, post, put, type Environment } from "./http";

type ConfigResponse = { effective: { version: number; values: Record<string, unknown> } };
const FIELDS: [string, string][] = [["process_enabled", "Processo habilitado"], ["employee_request_enabled", "Solicitação pelo colaborador"], ["manager_request_enabled", "Solicitação pelo gestor"],
  ["manager_approval_required", "Exigir aprovação"], ["simulation_enabled", "Disponível para simulação"], ["installments_allowed", "Permitir parcelamento"], ["allow_13th_advance", "Permitir adiantamento do 13º"], ["show_balance_to_employee", "Exibir saldo ao colaborador"]];

function F({ label, children, full }: { label: string; children: ReactNode; full?: boolean }) { return <label className={`field-wrap ${full ? "full" : ""}`}><span className="field-label">{label}</span>{children}</label>; }
function Note({ env, canEdit }: { env: Environment; canEdit: boolean }) {
  return <div className="section-note" style={{ marginBottom: 12, borderRadius: 8 }}>{!canEdit ? "Somente consulta para o seu perfil." : env !== "homologation" ? "Produção é somente leitura: altere em Homologação e publique em Ambientes." : "Editando homologação. As alterações valem em produção somente após a publicação aprovada."}</div>;
}

/* ------------------------------------------------------------- Configuração → Autoatendimento → Processos → Férias */
type Named = { id: number; config_code: string; name: string; version: number; effective_from: string; simulation_text: string | null } & Record<string, unknown>;
export function VacationNamedConfigs({ companyId, environment, canEdit, onToast }: { companyId: string; environment: Environment; canEdit: boolean; onToast: (m: string) => void }) {
  const [rows, setRows] = useState<Named[]>([]); const [error, setError] = useState(""); const [editing, setEditing] = useState<Record<string, unknown> | null>(null);
  const load = useCallback(async () => { try { const data = await api<{ named_configurations: Named[] }>(`/vacations/configurations?company_id=${companyId}&environment=${environment}`); setRows(data.named_configurations); } catch (e) { setError(message(e)); } }, [companyId, environment]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  const blank = () => ({ config_code: "", name: "", simulation_text: "", effective_from: new Date().toISOString().slice(0, 10), process_enabled: true, employee_request_enabled: true, manager_request_enabled: true, manager_approval_required: true,
    simulation_enabled: true, installments_allowed: true, max_installments: 3, min_advance_days: 30, allow_13th_advance: false, show_balance_to_employee: true });
  async function save(e: FormEvent) {
    e.preventDefault(); setError("");
    try { await put("/vacations/configurations", { ...editing, company_id: Number(companyId), environment: "homologation", group_id: null, config_code: String(editing!.config_code).toUpperCase(), max_installments: Number(editing!.max_installments), min_advance_days: Number(editing!.min_advance_days) });
      onToast(`Configuração ${String(editing!.config_code).toUpperCase()} salva em homologação.`); setEditing(null); await load(); } catch (ex) { setError(message(ex)); }
  }
  const edit = canEdit && environment === "homologation";
  return <div>
    <Note env={environment} canEdit={canEdit} />
    {error && <div className="alert alert-error">{error}</div>}
    <div className="table-wrap"><table><thead><tr><th>Código</th><th>Descrição</th><th>Disponível para simulação</th><th>Parcelas</th><th>Antecedência</th><th>Versão</th><th>Vigência</th><th></th></tr></thead><tbody>
      {rows.map(r => <tr key={r.id}><td className="code">{r.config_code}</td><td>{r.name}</td><td>{r.simulation_enabled ? "Sim" : "Não"}</td><td>{r.installments_allowed ? String(r.max_installments) : "1"}</td><td>{String(r.min_advance_days)} dias</td><td>v{r.version}</td><td>{datePt(r.effective_from)}</td>
        <td>{edit && <button className="btn" onClick={() => setEditing({ ...r, simulation_text: r.simulation_text || "", effective_from: new Date().toISOString().slice(0, 10) })}>Nova versão</button>}</td></tr>)}
    </tbody></table>{rows.length === 0 && <div className="empty">Nenhuma configuração nomeada. Sem atribuições, valem as regras da empresa e dos grupos abaixo.</div>}</div>
    {edit && !editing && <button className="btn btn-primary" style={{ marginTop: 10 }} onClick={() => setEditing(blank())}><Plus size={14} />Nova configuração</button>}
    {editing && <form onSubmit={save} className="suite-block" style={{ marginTop: 12 }}><h2>{rows.some(r => r.config_code === editing.config_code) ? `Nova versão de ${String(editing.config_code)}` : "Nova configuração de férias"}</h2>
      <div className="form-grid">
        <F label="Código (ex.: CFG-GESTORES)"><input className="field" required pattern="[A-Za-z0-9_\-]{1,20}" value={String(editing.config_code)} disabled={rows.some(r => r.config_code === editing.config_code)} onChange={e => setEditing({ ...editing, config_code: e.target.value.toUpperCase() })} /></F>
        <F label="Descrição"><input className="field" required value={String(editing.name)} onChange={e => setEditing({ ...editing, name: e.target.value })} /></F>
        <F label="Vigência a partir de"><input className="field" type="date" required value={String(editing.effective_from)} onChange={e => setEditing({ ...editing, effective_from: e.target.value })} /></F>
        <F label="Máximo de parcelas"><select className="field" value={String(editing.max_installments)} onChange={e => setEditing({ ...editing, max_installments: Number(e.target.value) })}>{[1, 2, 3].map(n => <option key={n}>{n}</option>)}</select></F>
        <F label="Antecedência mínima (dias)"><input className="field" type="number" min={0} max={365} value={String(editing.min_advance_days)} onChange={e => setEditing({ ...editing, min_advance_days: Number(e.target.value) })} /></F>
        {FIELDS.map(([key, label]) => <label key={key} className="field-wrap" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}><input type="checkbox" checked={Boolean(editing[key])} onChange={e => setEditing({ ...editing, [key]: e.target.checked })} />{label}</label>)}
        {Boolean(editing.simulation_enabled) && <F label="Guia Simulação: texto explicativo exibido no autoatendimento" full><textarea className="field" rows={2} maxLength={2000} value={String(editing.simulation_text || "")} onChange={e => setEditing({ ...editing, simulation_text: e.target.value })} /></F>}
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12 }}><button className="btn btn-primary"><Save size={14} />Salvar em homologação</button><button type="button" className="btn" onClick={() => setEditing(null)}>Cancelar</button></div></form>}
  </div>;
}

/* ------------------------------------------------------------- Atribuições de Configurações (lista ordenada) */
type Assignment = { group: string; config: string };
export function ConfigAssignments({ companyId, environment, canEdit, onToast }: { companyId: string; environment: Environment; canEdit: boolean; onToast: (m: string) => void }) {
  const [values, setValues] = useState<{ vacations: Assignment[]; vacations_simulation: Assignment[] } | null>(null); const [process, setProcess] = useState<"vacations" | "vacations_simulation">("vacations");
  const [groups, setGroups] = useState<string[]>([]); const [configs, setConfigs] = useState<Named[]>([]); const [error, setError] = useState(""); const [effectiveFrom, setEffectiveFrom] = useState(new Date().toISOString().slice(0, 10));
  useEffect(() => {
    api<ConfigResponse>(`/hr/configurations/config_assignments?company_id=${companyId}&environment=${environment}`).then(d => setValues(structuredClone(d.effective.values) as typeof values)).catch(e => setError(message(e)));
    api<{ groups: { name: string }[] }>(`/companies/${companyId}/options`).then(o => setGroups(o.groups.map(g => g.name))).catch(() => setGroups([]));
    api<{ named_configurations: Named[] }>(`/vacations/configurations?company_id=${companyId}&environment=${environment}`).then(d => setConfigs(d.named_configurations)).catch(() => setConfigs([]));
  }, [companyId, environment]);
  if (!values) return error ? <div className="alert alert-error">{error}</div> : <div className="loading">Carregando…</div>;
  const list = values[process]; const edit = canEdit && environment === "homologation";
  const available = configs.filter(c => process === "vacations" || c.simulation_enabled);
  const setList = (next: Assignment[]) => setValues({ ...values, [process]: next });
  const move = (i: number, d: number) => { const next = [...list]; const [x] = next.splice(i, 1); next.splice(i + d, 0, x); setList(next); };
  async function save() { setError(""); try { const out = await put<{ version: number }>("/hr/configurations", { company_id: Number(companyId), module: "config_assignments", environment: "homologation", effective_from: effectiveFrom, values }); onToast(`Atribuições salvas na versão ${out.version} (homologação).`); } catch (e) { setError(message(e)); } }
  return <div>
    <Note env={environment} canEdit={canEdit} />
    <div className="form-grid"><F label="Processo"><select className="field" value={process} onChange={e => setProcess(e.target.value as typeof process)}><option value="vacations">Férias</option><option value="vacations_simulation">Férias - Simulação</option></select></F>
      <F label="Vigência a partir de"><input className="field" type="date" value={effectiveFrom} disabled={!edit} onChange={e => setEffectiveFrom(e.target.value)} /></F></div>
    <div className="suite-help" style={{ borderTop: "1px solid #d9ecc9", borderRadius: 8, margin: "12px 0" }}>A ordenação definida abaixo determina a prioridade a ser considerada quando um colaborador faz parte de mais de um grupo.{process === "vacations_simulation" ? " Só aparecem configurações com Disponível para Simulação = Sim; sem linhas, a simulação usa as atribuições de Férias." : ""}</div>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="table-wrap"><table><thead><tr><th style={{ width: 70 }}>Ordem</th><th>Grupo de colaboradores</th><th>Configuração</th><th></th></tr></thead><tbody>
      {list.map((row, i) => <tr key={i}><td>{edit && <><button className="btn btn-quiet" disabled={i === 0} aria-label="Subir prioridade" onClick={() => move(i, -1)}><ArrowUp size={13} /></button><button className="btn btn-quiet" disabled={i === list.length - 1} aria-label="Descer prioridade" onClick={() => move(i, 1)}><ArrowDown size={13} /></button></>}{!edit && i + 1}</td>
        <td><select className="field" disabled={!edit} value={row.group} onChange={e => setList(list.map((r, j) => j === i ? { ...r, group: e.target.value } : r))}>{groups.map(g => <option key={g}>{g}</option>)}</select></td>
        <td><select className="field" disabled={!edit} value={row.config} onChange={e => setList(list.map((r, j) => j === i ? { ...r, config: e.target.value } : r))}>{available.map(c => <option key={c.config_code} value={c.config_code}>{c.config_code} - {c.name}</option>)}</select></td>
        <td>{edit && <button className="btn btn-quiet" aria-label="Remover atribuição" onClick={() => setList(list.filter((_, j) => j !== i))}><Trash2 size={14} /></button>}</td></tr>)}
    </tbody></table>{list.length === 0 && <div className="empty">Nenhuma atribuição: valem as regras da empresa e dos grupos.</div>}</div>
    {edit && <div style={{ display: "flex", gap: 8, marginTop: 10 }}><button className="btn" disabled={!groups.length || !available.length} onClick={() => setList([...list, { group: groups[0], config: available[0].config_code }])}><Plus size={14} />Adicionar</button><button className="btn btn-primary" onClick={() => void save()}><Save size={14} />Salvar em homologação</button></div>}
    {edit && !available.length && <p className="hint">Crie antes uma configuração em Configuração › Autoatendimento › Processos › Férias.</p>}
  </div>;
}

/* ------------------------------------------------------------- Grupos de colaboradores (participação em vários grupos) */
type GroupRow = { id: number; name: string; primary_count: number; members: { id: number; name: string; primary: boolean }[] };
export function CollaboratorGroups({ companyId, onToast }: { companyId: string; onToast: (m: string) => void }) {
  const [rows, setRows] = useState<GroupRow[]>([]); const [employees, setEmployees] = useState<{ id: number; full_name: string; status: string }[]>([]); const [selected, setSelected] = useState<number | null>(null); const [pick, setPick] = useState(""); const [error, setError] = useState("");
  const base = `/companies/${companyId}/setup/collaborator-groups`;
  const load = useCallback(async () => { try { const data = await api<GroupRow[]>(base); setRows(data); setSelected(s => s ?? data[0]?.id ?? null); } catch (e) { setError(message(e)); } }, [base]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); api<typeof employees>(`/employees?company_id=${companyId}`).then(setEmployees).catch(() => setEmployees([])); }, [load, companyId]);
  const group = rows.find(r => r.id === selected);
  async function add() { if (!pick || !group) return; setError(""); try { await post(`${base}/${group.id}/members`, { employee_id: Number(pick) }); setPick(""); onToast("Participação incluída."); await load(); } catch (e) { setError(message(e)); } }
  async function remove(id: number) { if (!group) return; try { await del(`${base}/${group.id}/members/${id}`); await load(); } catch (e) { setError(message(e)); } }
  return <div className="form-grid" style={{ alignItems: "start" }}>
    <div className="table-wrap"><table><thead><tr><th>Grupo</th><th>Principal</th><th>Adicionais</th></tr></thead><tbody>{rows.map(r => <tr key={r.id} onClick={() => setSelected(r.id)} style={{ cursor: "pointer", background: r.id === selected ? "var(--brand-blue-50)" : undefined }}><td>{r.name}</td><td>{r.primary_count}</td><td>{r.members.filter(m => !m.primary).length}</td></tr>)}</tbody></table></div>
    <div>{error && <div className="alert alert-error">{error}</div>}{group && <>
      <p className="hint">O grupo principal vem do cadastro e muda pela Movimentação. As participações adicionais permitem que o colaborador pertença a vários grupos; a prioridade é dada pela ordem das atribuições.</p>
      <div style={{ display: "flex", gap: 8, margin: "8px 0" }}><select className="field" value={pick} onChange={e => setPick(e.target.value)} aria-label="Colaborador"><option value="">Incluir colaborador…</option>{employees.filter(e => e.status !== "Desligado" && !group.members.some(m => m.id === e.id)).map(e => <option key={e.id} value={e.id}>{e.full_name}</option>)}</select><button className="btn btn-primary" disabled={!pick} onClick={() => void add()}><UserPlus size={14} />Incluir</button></div>
      <div className="table-wrap" style={{ maxHeight: 380 }}><table><thead><tr><th>Colaborador</th><th>Participação</th><th></th></tr></thead><tbody>{group.members.map(m => <tr key={`${m.id}-${m.primary}`}><td>{m.name}</td><td>{m.primary ? "Grupo principal" : "Adicional"}</td><td>{!m.primary && <button className="btn btn-quiet" aria-label={`Remover ${m.name}`} onClick={() => void remove(m.id)}><UserMinus size={14} /></button>}</td></tr>)}</tbody></table></div></>}</div>
  </div>;
}

/* ------------------------------------------------------------- Tipo de cargo */
export function JobTypes({ companyId, onToast }: { companyId: string; onToast: (m: string) => void }) {
  const [data, setData] = useState<{ job_types: string[]; positions: { id: number; title: string; job_type: string }[] } | null>(null); const [error, setError] = useState("");
  const base = `/companies/${companyId}/setup/job-types`;
  useEffect(() => { api<typeof data>(base).then(setData).catch(e => setError(message(e))); }, [base]);
  async function change(id: number, job_type: string) { try { await put(base, { position_id: id, job_type }); setData(d => d && { ...d, positions: d.positions.map(p => p.id === id ? { ...p, job_type } : p) }); onToast("Tipo de cargo atualizado."); } catch (e) { setError(message(e)); } }
  if (!data) return error ? <div className="alert alert-error">{error}</div> : <div className="loading">Carregando…</div>;
  return <div>{error && <div className="alert alert-error">{error}</div>}<div className="table-wrap"><table><thead><tr><th>Cargo</th><th>Tipo de cargo</th></tr></thead><tbody>{data.positions.map(p => <tr key={p.id}><td>{p.title}</td><td><select className="filter-select" aria-label={`Tipo de cargo de ${p.title}`} value={p.job_type} onChange={e => void change(p.id, e.target.value)}>{data.job_types.map(t => <option key={t}>{t}</option>)}</select></td></tr>)}</tbody></table></div>
    <p className="hint" style={{ marginTop: 8 }}>Usado pelo Guia Férias quando a configuração por tipo de cargo está habilitada.</p></div>;
}

/* ------------------------------------------------------------- Guia Férias: configurações gerais por grupo de cálculo */
export type GeneralConfig = Record<string, unknown> & { calculation_group: string; job_types: string[]; monthly_values_treatment: string };
const SHEETS: [string, string][] = [["sheet_normal", "Férias normais"], ["sheet_cash_bonus", "Férias com abono"], ["sheet_13th", "Férias com 13º salário"], ["sheet_cash_bonus_13th", "Férias com abono e 13º"],
  ["sheet_occurrence_1", "Folha 1ª ocorrência no mês"], ["sheet_occurrence_2", "Folha 2ª ocorrência no mês"], ["sheet_occurrence_3", "Folha 3ª ocorrência no mês"]];
const SIM: [string, string][] = [["simulation_sheet_1", "Simulação: folha 1ª ocorrência"], ["simulation_sheet_2", "Simulação: folha 2ª ocorrência"], ["simulation_sheet_3", "Simulação: folha 3ª ocorrência"]];
const TREATMENTS: [string, string][] = [["none", "Não há manipulação referente aos valores mensais"], ["block", "Não efetivar as férias caso existam valores mensais calculados"], ["delete_monthly", "Excluir valores das folhas tipo Mensal"]];
const JOB_TYPES = ["Mensal", "Quinzenal", "Semanal", "Diário", "Horário", "Tarefa", "Outros"];
export function GeneralConfigsEditor({ value, byJobType, companyId, onChange }: { value: GeneralConfig[]; byJobType: boolean; companyId: string; onChange: (v: GeneralConfig[]) => void }) {
  const [catalog, setCatalog] = useState<{ calculation_groups: string[]; sheets: string[] }>({ calculation_groups: [], sheets: [] });
  useEffect(() => { api<ConfigResponse>(`/hr/configurations/payroll_setup?company_id=${companyId}&environment=homologation`).then(d => setCatalog(d.effective.values as typeof catalog)).catch(() => undefined); }, [companyId]);
  const upd = (i: number, patch: Record<string, unknown>) => onChange(value.map((r, j) => j === i ? { ...r, ...patch } as GeneralConfig : r));
  const sheetSelect = (i: number, key: string, optional = false) => <select className="field" value={String(value[i][key] ?? "")} onChange={e => upd(i, { [key]: e.target.value })}>{optional && <option value="">— não informar —</option>}{catalog.sheets.map(s => <option key={s}>{s}</option>)}</select>;
  return <div style={{ marginTop: 16 }}><h3 style={{ fontSize: 15 }}>Configurações gerais de férias (por grupo de cálculo{byJobType ? " e tipo de cargo" : ""})</h3>
    <p className="hint">Define como a ocorrência de férias é gravada. Grupos de cálculo e folhas vêm de Configuração › Folha de Pagamento › Grupos de cálculo e folhas.{byJobType ? " Grupos de cálculo iguais são permitidos desde que os tipos de cargo sejam diferentes." : ""}</p>
    {value.map((row, i) => <section key={i} className="panel" style={{ padding: 14, marginTop: 10 }}>
      <div className="form-grid">
        <F label="Grupo de cálculo"><select className="field" value={row.calculation_group} onChange={e => upd(i, { calculation_group: e.target.value })}>{[...new Set([row.calculation_group, ...catalog.calculation_groups])].map(g => <option key={g}>{g}</option>)}</select></F>
        {byJobType && <div className="field-wrap"><span className="field-label">Tipo de cargo</span><div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{JOB_TYPES.map(t => <label key={t} style={{ display: "flex", gap: 4 }}><input type="checkbox" checked={row.job_types.includes(t)} onChange={e => upd(i, { job_types: e.target.checked ? [...row.job_types, t] : row.job_types.filter(x => x !== t) })} />{t}</label>)}</div></div>}
        {SHEETS.map(([key, label]) => <F key={key} label={label}>{sheetSelect(i, key)}</F>)}
        <F label="Tratamento para valores mensais" full><select className="field" value={row.monthly_values_treatment} onChange={e => upd(i, { monthly_values_treatment: e.target.value })}>{TREATMENTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></F>
        {SIM.map(([key, label]) => <F key={key} label={label}>{sheetSelect(i, key, true)}</F>)}
      </div>
      <button type="button" className="btn btn-quiet" style={{ marginTop: 8 }} onClick={() => onChange(value.filter((_, j) => j !== i))}><Trash2 size={14} />Remover</button></section>)}
    <button type="button" className="btn" style={{ marginTop: 10 }} onClick={() => onChange([...value, { calculation_group: catalog.calculation_groups[0] || "Mensalistas", job_types: [], sheet_normal: catalog.sheets[0] || "", sheet_cash_bonus: catalog.sheets[0] || "", sheet_13th: catalog.sheets[0] || "", sheet_cash_bonus_13th: catalog.sheets[0] || "",
      sheet_occurrence_1: catalog.sheets[0] || "", sheet_occurrence_2: catalog.sheets[0] || "", sheet_occurrence_3: catalog.sheets[0] || "", monthly_values_treatment: "none", simulation_sheet_1: "", simulation_sheet_2: "", simulation_sheet_3: "" }])}><Plus size={14} />Nova configuração geral</button>
  </div>;
}

/* ------------------------------------------------------------- Informações adicionais por processo */
import type { AdditionalField } from "./ProcessCenter";
type CatalogLite = { processes: { process: string; label: string }[]; form_processes?: string[] };
const FIELD_TYPE_LABELS: [AdditionalField["type"], string][] = [["text", "Texto"], ["number", "Número"], ["date", "Data"], ["boolean", "Sim/Não"], ["select", "Lista de opções"]];
export function AdditionalFieldsEditor({ value, catalog, onChange }: { value: Record<string, AdditionalField[]>; catalog: CatalogLite; onChange: (v: Record<string, AdditionalField[]>) => void }) {
  const processes = (catalog.form_processes || Object.keys(value)).map(key => ({ key, label: catalog.processes.find(p => p.process === key)?.label || key }));
  const [process, setProcess] = useState(processes[0]?.key || "");
  const fields = value[process] || [];
  const setFields = (next: AdditionalField[]) => onChange({ ...value, [process]: next });
  const upd = (i: number, patch: Partial<AdditionalField>) => setFields(fields.map((f, j) => j === i ? { ...f, ...patch } : f));
  const slug = (label: string) => label.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").replace(/^[0-9]/, "c$&").slice(0, 31);
  return <div style={{ marginTop: 16 }}>
    <F label="Processo"><select className="field" value={process} onChange={e => setProcess(e.target.value)}>{processes.map(p => <option key={p.key} value={p.key}>{p.label} ({(value[p.key] || []).length})</option>)}</select></F>
    <p className="hint" style={{ margin: "8px 0 12px" }}>Campos extras exibidos no formulário deste processo, na ordem abaixo. O nome técnico identifica o campo na exportação e nas integrações; o rótulo é o que o colaborador vê.</p>
    {fields.map((f, i) => <section key={i} className="panel" style={{ padding: 12, marginBottom: 8 }}><div className="form-grid">
      <F label="Rótulo"><input className="field" value={f.label} maxLength={60} onChange={e => upd(i, { label: e.target.value, name: f.name || slug(e.target.value) })} /></F>
      <F label="Nome técnico (letras minúsculas, números e _)"><input className="field" value={f.name} maxLength={31} pattern="[a-z][a-z0-9_]{1,30}" onChange={e => upd(i, { name: e.target.value.toLowerCase() })} /></F>
      <F label="Tipo"><select className="field" value={f.type} onChange={e => upd(i, { type: e.target.value as AdditionalField["type"], options: e.target.value === "select" ? f.options : [] })}>{FIELD_TYPE_LABELS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></F>
      <label className="field-wrap" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}><input type="checkbox" checked={f.required} onChange={e => upd(i, { required: e.target.checked })} />Preenchimento obrigatório</label>
      {f.type === "select" && <F label="Opções — separe por vírgula" full><input className="field" value={f.options.join(", ")} onChange={e => upd(i, { options: e.target.value.split(",").map(x => x.trimStart()).filter((x, k, all) => x || k === all.length - 1) })} onBlur={e => upd(i, { options: e.target.value.split(",").map(x => x.trim()).filter(Boolean) })} /></F>}
      <F label="Texto de ajuda (opcional)" full><input className="field" value={f.help} maxLength={200} onChange={e => upd(i, { help: e.target.value })} /></F>
    </div><div style={{ display: "flex", gap: 6, marginTop: 8 }}>
      <button type="button" className="btn btn-quiet" disabled={i === 0} aria-label="Subir" onClick={() => { const n = [...fields]; [n[i - 1], n[i]] = [n[i], n[i - 1]]; setFields(n); }}><ArrowUp size={13} /></button>
      <button type="button" className="btn btn-quiet" disabled={i === fields.length - 1} aria-label="Descer" onClick={() => { const n = [...fields]; [n[i + 1], n[i]] = [n[i], n[i + 1]]; setFields(n); }}><ArrowDown size={13} /></button>
      <button type="button" className="btn btn-quiet" onClick={() => setFields(fields.filter((_, j) => j !== i))}><Trash2 size={14} />Remover</button></div></section>)}
    {fields.length < 10 && <button type="button" className="btn" onClick={() => setFields([...fields, { name: "", label: "", type: "text", required: false, options: [], help: "" }])}><Plus size={14} />Novo campo</button>}
    {fields.length === 0 && <p className="hint">Nenhum campo adicional neste processo.</p>}
  </div>;
}
