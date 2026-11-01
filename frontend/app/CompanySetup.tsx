"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Bus, Grid3x3, Network, Plus, Save, Table2, Trash2, Users } from "lucide-react";
import { api, del, message, money, patch, post, put, type SessionUser } from "./http";

type Option = { id: number; name?: string; title?: string };
type Options = { departments: Option[]; positions: Option[]; groups: Option[] };
type Range = { id: number; position_id: number; position: string; level: string; min_salary: number; max_salary: number };
type Headcount = { id: number; department: string; position: string; planned: number; occupied: number; pending_requisitions: number; open_positions: number; available: number };
type Seat = { id: number; code: string; department: string; position: string; department_id: number; position_id: number; status: string; employee_id: number | null; employee_name: string | null; requisition_id: number | null };
type Line = { id: number; code: string; description: string; fare: number; active: boolean };
type OrgNode = { id: number; name: string; position: string; department: string; status: string; children: OrgNode[] };
type EmployeeRow = { id: number; full_name: string; department_id: number; position_id: number; status: string };
export type Tab = "salary" | "headcount" | "positions" | "transport" | "org";

export default function CompanySetup({ user, companyId, onToast, initialTab, embedded }: { user: SessionUser; companyId: string; onToast: (m: string) => void; initialTab?: Tab; embedded?: boolean }) {
  const [tab, setTab] = useState<Tab>(initialTab ?? "salary");
  const [options, setOptions] = useState<Options>({ departments: [], positions: [], groups: [] });
  const [error, setError] = useState("");
  useEffect(() => { if (companyId) api<Options>(`/companies/${companyId}/options`).then(setOptions).catch(e => setError(message(e))); }, [companyId]);
  if (!companyId) return <div className="empty">Selecione uma empresa no topo da página.</div>;
  const base = `/companies/${companyId}/setup`;
  const tabs: [Tab, string, ReactNode][] = [["salary", "Tabela salarial", <Table2 key="s" size={15} />], ["headcount", "Quadro de vagas", <Users key="h" size={15} />], ["positions", "Posições", <Grid3x3 key="p" size={15} />], ["transport", "Vale-transporte", <Bus key="t" size={15} />], ["org", "Organograma", <Network key="o" size={15} />]];
  return <>
    {!embedded && <div className="page-head"><div><div className="eyebrow">Configurações por empresa</div><h1 className="page-title">Estrutura de cargos e vagas</h1><p className="page-desc">Cadastros usados pelas regras de Contratação, Movimentação, Requisição e Vale-transporte.</p></div></div>}
    {!embedded && <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>{tabs.map(([key, label, icon]) => <button key={key} className={`btn ${tab === key ? "btn-primary" : ""}`} onClick={() => setTab(key)}>{icon}{label}</button>)}</div>}
    {error && <div className="alert alert-error">{error}</div>}
    {tab === "salary" && <SalaryTable base={base} options={options} onToast={onToast} />}
    {tab === "headcount" && <HeadcountTable base={base} options={options} onToast={onToast} />}
    {tab === "positions" && <Positions base={base} companyId={companyId} options={options} onToast={onToast} />}
    {tab === "transport" && <TransportLines base={base} onToast={onToast} />}
    {tab === "org" && <OrgChart base={base} />}
    {user.role === "consultant" && <p className="hint" style={{ marginTop: 12 }}>Estes cadastros são dados mestres da empresa (não passam por homologação). As regras que os utilizam são parametrizadas em Processos → Parametrização.</p>}
  </>;
}

function useRows<T>(path: string) {
  const [rows, setRows] = useState<T[]>([]); const [error, setError] = useState("");
  const load = useCallback(async () => { try { setRows(await api<T[]>(path)); setError(""); } catch (e) { setError(message(e)); } }, [path]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  return { rows, error, setError, load };
}

function SalaryTable({ base, options, onToast }: { base: string; options: Options; onToast: (m: string) => void }) {
  const { rows, error, setError, load } = useRows<Range>(`${base}/salary-ranges`);
  const [form, setForm] = useState({ position_id: "", level: "Júnior", min_salary: "", max_salary: "" });
  async function save(e: FormEvent) { e.preventDefault(); try { await put(`${base}/salary-ranges`, { position_id: Number(form.position_id || options.positions[0]?.id), level: form.level, min_salary: Number(form.min_salary), max_salary: Number(form.max_salary) }); onToast("Faixa salarial salva."); await load(); } catch (ex) { setError(message(ex)); } }
  return <section className="section-panel" style={{ padding: 18 }}>
    <form onSubmit={save} className="form-grid"><Select label="Cargo" value={form.position_id} items={options.positions} onChange={v => setForm({ ...form, position_id: v })} /><Text label="Nível" value={form.level} onChange={v => setForm({ ...form, level: v })} />
      <Text label="Salário mínimo (R$)" type="number" value={form.min_salary} onChange={v => setForm({ ...form, min_salary: v })} /><Text label="Salário máximo (R$)" type="number" value={form.max_salary} onChange={v => setForm({ ...form, max_salary: v })} />
      <div><button className="btn btn-primary"><Save size={14} />Salvar faixa</button></div></form>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="table-wrap" style={{ marginTop: 14 }}><table><thead><tr><th>Cargo</th><th>Nível</th><th>Mínimo</th><th>Máximo</th><th></th></tr></thead><tbody>{rows.map(r => <tr key={r.id}><td>{r.position}</td><td>{r.level}</td><td>{money(r.min_salary)}</td><td>{money(r.max_salary)}</td>
      <td><button className="btn btn-quiet" aria-label="Excluir faixa" onClick={async () => { try { await del(`${base}/salary-ranges/${r.id}`); await load(); } catch (e) { setError(message(e)); } }}><Trash2 size={14} /></button></td></tr>)}</tbody></table>{rows.length === 0 && <div className="empty">Nenhuma faixa cadastrada.</div>}</div>
  </section>;
}

function HeadcountTable({ base, options, onToast }: { base: string; options: Options; onToast: (m: string) => void }) {
  const { rows, error, setError, load } = useRows<Headcount>(`${base}/headcount`);
  const [form, setForm] = useState({ department_id: "", position_id: "", planned: "1" });
  async function save(e: FormEvent) { e.preventDefault(); try { await put(`${base}/headcount`, { department_id: Number(form.department_id || options.departments[0]?.id), position_id: Number(form.position_id || options.positions[0]?.id), planned: Number(form.planned) }); onToast("Quadro de vagas atualizado."); await load(); } catch (ex) { setError(message(ex)); } }
  return <section className="section-panel" style={{ padding: 18 }}>
    <form onSubmit={save} className="form-grid"><Select label="Departamento" value={form.department_id} items={options.departments} onChange={v => setForm({ ...form, department_id: v })} /><Select label="Cargo" value={form.position_id} items={options.positions} onChange={v => setForm({ ...form, position_id: v })} />
      <Text label="Quantidade planejada" type="number" value={form.planned} onChange={v => setForm({ ...form, planned: v })} /><div><button className="btn btn-primary"><Save size={14} />Salvar</button></div></form>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="table-wrap" style={{ marginTop: 14 }}><table><thead><tr><th>Departamento</th><th>Cargo</th><th>Planejado</th><th>Ocupado</th><th>Posições vagas</th><th>Requisições pendentes</th><th>Disponível</th></tr></thead><tbody>{rows.map(r => <tr key={r.id}><td>{r.department}</td><td>{r.position}</td><td>{r.planned}</td><td>{r.occupied}</td><td>{r.open_positions}</td><td>{r.pending_requisitions}</td><td><strong>{r.available}</strong></td></tr>)}</tbody></table>{rows.length === 0 && <div className="empty">Nenhum quadro planejado.</div>}</div>
  </section>;
}

function Positions({ base, companyId, options, onToast }: { base: string; companyId: string; options: Options; onToast: (m: string) => void }) {
  const { rows, error, setError, load } = useRows<Seat>(`${base}/positions`);
  const [employees, setEmployees] = useState<EmployeeRow[]>([]);
  const [form, setForm] = useState({ department_id: "", position_id: "", quantity: "1" });
  useEffect(() => { api<EmployeeRow[]>(`/employees?company_id=${companyId}`).then(setEmployees).catch(() => setEmployees([])); }, [companyId]);
  async function create(e: FormEvent) { e.preventDefault(); try { const out = await post<{ codes: string[] }>(`${base}/positions`, { department_id: Number(form.department_id || options.departments[0]?.id), position_id: Number(form.position_id || options.positions[0]?.id), quantity: Number(form.quantity) }); onToast(`Posições criadas: ${out.codes.join(", ")}.`); await load(); } catch (ex) { setError(message(ex)); } }
  async function assign(seat: Seat, employeeId: string) { try { await patch(`${base}/positions/${seat.id}`, { employee_id: employeeId ? Number(employeeId) : null }); await load(); } catch (e) { setError(message(e)); } }
  return <section className="section-panel" style={{ padding: 18 }}>
    <form onSubmit={create} className="form-grid"><Select label="Departamento" value={form.department_id} items={options.departments} onChange={v => setForm({ ...form, department_id: v })} /><Select label="Cargo" value={form.position_id} items={options.positions} onChange={v => setForm({ ...form, position_id: v })} />
      <Text label="Quantidade" type="number" value={form.quantity} onChange={v => setForm({ ...form, quantity: v })} /><div><button className="btn btn-primary"><Plus size={14} />Criar posições</button></div></form>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="table-wrap" style={{ marginTop: 14 }}><table><thead><tr><th>Código</th><th>Departamento</th><th>Cargo</th><th>Situação</th><th>Ocupante</th><th></th></tr></thead><tbody>{rows.map(r => <tr key={r.id}><td className="code">{r.code}</td><td>{r.department}</td><td>{r.position}</td><td><span className="tag">{r.status === "vacant" ? "Vaga" : "Ocupada"}</span>{r.requisition_id ? <span className="hint"> · requisição {r.requisition_id}</span> : null}</td>
      <td><select className="filter-select" value={r.employee_id ?? ""} aria-label={`Ocupante da posição ${r.code}`} onChange={e => void assign(r, e.target.value)}><option value="">— vaga —</option>{employees.filter(x => x.status !== "Desligado" && x.department_id === r.department_id && x.position_id === r.position_id).map(x => <option key={x.id} value={x.id}>{x.full_name}</option>)}</select></td>
      <td>{!r.employee_id && <button className="btn btn-quiet" aria-label="Excluir posição" onClick={async () => { try { await del(`${base}/positions/${r.id}`); await load(); } catch (e) { setError(message(e)); } }}><Trash2 size={14} /></button>}</td></tr>)}</tbody></table>{rows.length === 0 && <div className="empty">Nenhuma posição cadastrada.</div>}</div>
  </section>;
}

function TransportLines({ base, onToast }: { base: string; onToast: (m: string) => void }) {
  const { rows, error, setError, load } = useRows<Line>(`${base}/transport-lines`);
  const [form, setForm] = useState({ code: "", description: "", fare: "", active: true });
  async function save(e: FormEvent) { e.preventDefault(); try { await put(`${base}/transport-lines`, { ...form, fare: Number(form.fare) }); onToast("Linha de transporte salva."); setForm({ code: "", description: "", fare: "", active: true }); await load(); } catch (ex) { setError(message(ex)); } }
  return <section className="section-panel" style={{ padding: 18 }}>
    <form onSubmit={save} className="form-grid"><Text label="Código" value={form.code} onChange={v => setForm({ ...form, code: v })} /><Text label="Descrição" value={form.description} onChange={v => setForm({ ...form, description: v })} /><Text label="Tarifa (R$)" type="number" value={form.fare} onChange={v => setForm({ ...form, fare: v })} />
      <label className="field-wrap" style={{ flexDirection: "row", gap: 8, alignItems: "center" }}><input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} />Ativa</label><div><button className="btn btn-primary"><Save size={14} />Salvar linha</button></div></form>
    {error && <div className="alert alert-error">{error}</div>}
    <div className="table-wrap" style={{ marginTop: 14 }}><table><thead><tr><th>Código</th><th>Descrição</th><th>Tarifa</th><th>Situação</th></tr></thead><tbody>{rows.map(r => <tr key={r.id} onClick={() => setForm({ code: r.code, description: r.description, fare: String(r.fare), active: r.active })} style={{ cursor: "pointer" }}><td className="code">{r.code}</td><td>{r.description}</td><td>{money(r.fare)}</td><td><span className="tag">{r.active ? "Ativa" : "Inativa"}</span></td></tr>)}</tbody></table>{rows.length === 0 && <div className="empty">Nenhuma linha cadastrada.</div>}</div>
    <p className="hint">O desconto do colaborador (até 6% do salário-base, Lei 7.418/1985) é parametrizado em Processos → Parametrização → Vale-transporte.</p>
  </section>;
}

function OrgChart({ base }: { base: string }) {
  const [data, setData] = useState<{ roots: OrgNode[]; total: number } | null>(null); const [error, setError] = useState("");
  useEffect(() => { api<{ roots: OrgNode[]; total: number }>(`${base}/org-chart`).then(setData).catch(e => setError(message(e))); }, [base]);
  if (error) return <div className="alert alert-error">{error}</div>;
  if (!data) return <div className="loading">Carregando…</div>;
  return <section className="section-panel" style={{ padding: 18 }}><p className="hint">{data.total} colaboradores ativos, organizados pelo gestor imediato.</p><ul style={{ listStyle: "none", paddingLeft: 0 }}>{data.roots.map(n => <Node key={n.id} node={n} />)}</ul></section>;
}

function Node({ node }: { node: OrgNode }) {
  const [open, setOpen] = useState(true);
  return <li style={{ margin: "4px 0" }}><div className="company-mini" style={{ display: "inline-flex", gap: 10 }}>
    {node.children.length > 0 && <button className="btn btn-quiet" aria-expanded={open} aria-label={open ? "Recolher equipe" : "Expandir equipe"} onClick={() => setOpen(!open)}>{open ? "−" : "+"}</button>}
    <div><div className="company-mini-name">{node.name}</div><div className="company-mini-meta">{node.position} · {node.department}{node.children.length ? ` · ${node.children.length} direto(s)` : ""}{node.status !== "Ativo" ? ` · ${node.status}` : ""}</div></div></div>
    {open && node.children.length > 0 && <ul style={{ listStyle: "none", paddingLeft: 26, borderLeft: "2px solid #e3e8f0", marginLeft: 12 }}>{node.children.map(c => <Node key={c.id} node={c} />)}</ul>}</li>;
}

function Select({ label, value, items, onChange }: { label: string; value: string; items: Option[]; onChange: (v: string) => void }) {
  return <label className="field-wrap"><span className="field-label">{label}</span><select className="field" value={value || String(items[0]?.id || "")} onChange={e => onChange(e.target.value)}>{items.map(i => <option key={i.id} value={i.id}>{i.name || i.title}</option>)}</select></label>;
}
function Text({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (v: string) => void; type?: string }) {
  return <label className="field-wrap"><span className="field-label">{label}</span><input className="field" required type={type} step={type === "number" ? "0.01" : undefined} min={type === "number" ? 0 : undefined} value={value} onChange={e => onChange(e.target.value)} /></label>;
}
