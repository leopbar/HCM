"use client";

import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Building2, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { API, api, del, message, patch, post, type SessionUser } from "./http";

/** Cadastros da empresa (dados mestres): gravam direto, sem homologação e sem publicação. */

type Company = { id: number; code: string; legal_name: string; trade_name: string; cnpj: string; city: string; state: string; sector: string; status: string; employee_count: number };
type Options = { departments: { id: number; name: string }[]; positions: { id: number; title: string }[]; groups: { id: number; name: string }[] };
type CatalogKind = "departments" | "positions" | "groups";
const UFS = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"];
const emptyCompany = { code: "", legal_name: "", trade_name: "", cnpj: "", city: "", state: "SP", sector: "", status: "Ativa" };
const FIELD_LABELS: Record<string, string> = { code: "Código interno", legal_name: "Razão social", trade_name: "Nome fantasia", cnpj: "CNPJ fictício", city: "Cidade", state: "Estado (UF)", sector: "Setor" };

/** Same as the shared helper, but prefixes validation messages with the company form field that failed. */
async function saveCompanyRequest(path: string, method: "POST" | "PATCH", body: unknown): Promise<Company> {
  const response = await fetch(`${API}${path}`, { method, credentials: "include", headers: { "Content-Type": "application/json", "X-Requested-With": "HCMLab" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({ detail: "Não foi possível interpretar a resposta da API." }));
  if (response.ok) return data as Company;
  const detail = data.detail;
  if (Array.isArray(detail)) throw new Error(detail.map(item => {
    const issue = (item || {}) as { loc?: unknown; msg?: unknown };
    const path = Array.isArray(issue.loc) ? issue.loc.filter(part => part !== "body").join(".") : "";
    const text = String(issue.msg ?? "").replace(/^Value error,\s*/i, "");
    return FIELD_LABELS[path] ? `${FIELD_LABELS[path]}: ${text}` : text;
  }).filter(Boolean).join(" "));
  throw new Error(typeof detail === "string" ? detail : `Erro HTTP ${response.status}`);
}

export function CompanyRegistry({ user, onChanged, onToast }: { user: SessionUser; onChanged: (companyId?: number) => void; onToast: (m: string) => void }) {
  const [companies, setCompanies] = useState<Company[]>([]); const [search, setSearch] = useState(""); const [error, setError] = useState("");
  const [editing, setEditing] = useState<Company | "new" | null>(null); const [form, setForm] = useState(emptyCompany); const [busy, setBusy] = useState(false);
  const canManage = user.role === "system_admin";
  const load = useCallback(async () => { try { setCompanies(await api<Company[]>("/companies")); } catch (e) { setError(message(e)); } }, []);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  function open(company?: Company) { setError(""); setEditing(company || "new"); setForm(company ? { code: company.code, legal_name: company.legal_name, trade_name: company.trade_name, cnpj: company.cnpj, city: company.city, state: company.state, sector: company.sector, status: company.status } : emptyCompany); }
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    try { const current = editing !== "new" ? editing : null; const row = await saveCompanyRequest(current ? `/companies/${current.id}` : "/companies", current ? "PATCH" : "POST", form); setEditing(null); onToast(current ? "Cadastro da empresa atualizado." : "Empresa cadastrada."); await load(); onChanged(row.id); }
    catch (ex) { setError(message(ex)); } finally { setBusy(false); }
  }
  const rows = companies.filter(c => !search || `${c.code} ${c.trade_name} ${c.city} ${c.sector}`.toLowerCase().includes(search.toLowerCase()));
  return <>
    <div className="toolbar"><div className="toolbar-left"><div className="search-wrap"><Search size={14} className="search-icon" /><input className="search" placeholder="Buscar empresa por nome, código ou cidade" value={search} onChange={e => setSearch(e.target.value)} /></div></div>
      <div className="toolbar-right"><span className="hint">{companies.length} empresas</span>{canManage && <button className="btn btn-primary" onClick={() => open()}><Plus size={15} />Nova empresa</button>}</div></div>
    {error && !editing && <div className="alert alert-error">{error}</div>}
    <div className="table-wrap"><table><thead><tr><th>Código</th><th>Empresa</th><th>CNPJ fictício</th><th>Cidade</th><th>Setor</th><th>Funcionários</th><th>Status</th>{canManage && <th>Ações</th>}</tr></thead><tbody>
      {rows.map(c => <tr key={c.id}><td className="code">{c.code}</td><td><div className="person-cell"><div className="small-avatar"><Building2 size={14} /></div><div><div className="person-name">{c.trade_name}</div><div className="person-email">{c.legal_name}</div></div></div></td><td>{c.cnpj}</td><td>{c.city} / {c.state}</td><td>{c.sector}</td><td>{c.employee_count}</td><td><span className="tag">{c.status}</span></td>{canManage && <td><button className="btn" onClick={() => open(c)}>Editar</button></td>}</tr>)}
    </tbody></table>{companies.length === 0 && <div className="empty">Nenhuma empresa disponível para este perfil.</div>}</div>
    <p className="hint" style={{ marginTop: 10 }}>Os dados deste laboratório são fictícios; o CNPJ é validado pelos dígitos verificadores. Departamentos, cargos e grupos de cada empresa ficam em Estrutura Organizacional.</p>
    {editing && <Modal title={editing === "new" ? "Cadastrar empresa" : "Editar empresa"} close={() => setEditing(null)}><form onSubmit={save}><div className="modal-body">{error && <div className="alert alert-error">{error}</div>}<div className="form-grid">
      <Field label="Código interno"><input className="field" required value={form.code} onChange={e => setForm({ ...form, code: e.target.value.toUpperCase() })} /></Field>
      <Field label="CNPJ fictício"><input className="field" required inputMode="numeric" maxLength={18} placeholder="00.000.000/0000-00" title="Informe 14 dígitos, com ou sem pontuação." value={form.cnpj} onChange={e => setForm({ ...form, cnpj: e.target.value })} /><span className="hint">Informe 14 dígitos; os dígitos verificadores são conferidos.</span></Field>
      <Field label="Razão social" full><input className="field" required value={form.legal_name} onChange={e => setForm({ ...form, legal_name: e.target.value })} /></Field>
      <Field label="Nome fantasia"><input className="field" required value={form.trade_name} onChange={e => setForm({ ...form, trade_name: e.target.value })} /></Field>
      <Field label="Setor"><input className="field" required value={form.sector} onChange={e => setForm({ ...form, sector: e.target.value })} /></Field>
      <Field label="Cidade"><input className="field" required value={form.city} onChange={e => setForm({ ...form, city: e.target.value })} /></Field>
      <Field label="Estado (UF)"><select className="field" required value={form.state} onChange={e => setForm({ ...form, state: e.target.value })}>{UFS.map(uf => <option key={uf}>{uf}</option>)}</select></Field>
      <Field label="Status"><select className="field" value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}><option>Ativa</option><option>Inativa</option></select></Field>
    </div></div><div className="modal-foot"><button type="button" className="btn" onClick={() => setEditing(null)}>Cancelar</button><button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Salvando…" : "Salvar empresa"}</button></div></form></Modal>}
  </>;
}

/** Departamentos, cargos e grupos de funcionários da empresa selecionada no topo da página. */
export function CompanyStructure({ user, companyId, onToast }: { user: SessionUser; companyId: string; onToast: (m: string) => void }) {
  const [options, setOptions] = useState<Options>({ departments: [], positions: [], groups: [] });
  const [drafts, setDrafts] = useState<Record<CatalogKind, string>>({ departments: "", positions: "", groups: "" });
  const [loading, setLoading] = useState(true); const [saving, setSaving] = useState(false); const [error, setError] = useState("");
  const canEdit = user.role === "system_admin";
  const base = `/companies/${companyId}/structure`;
  const loadOptions = useCallback(async () => { setLoading(true); try { setOptions(await api<Options>(`/companies/${companyId}/options`)); } catch (e) { setError(message(e)); } finally { setLoading(false); } }, [companyId]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void loadOptions(); }, [loadOptions]);
  async function run(action: () => Promise<unknown>, done: string) { setSaving(true); setError(""); try { await action(); await loadOptions(); onToast(done); } catch (ex) { setError(message(ex)); } finally { setSaving(false); } }
  function renameItem(kind: CatalogKind, id: number, current: string) { const name = window.prompt("Novo nome", current)?.trim(); if (name && name !== current) void run(() => patch(`${base}/${kind}/${id}`, { name }), "Item renomeado."); }
  function deleteItem(kind: CatalogKind, id: number, name: string) { if (window.confirm(`Excluir ${name}? Itens em uso não podem ser excluídos.`)) void run(() => del(`${base}/${kind}/${id}`), "Item excluído."); }
  function addItem(e: FormEvent, kind: CatalogKind, label: string) { e.preventDefault(); const name = drafts[kind].trim(); if (name) void run(async () => { await post(`${base}/${kind}`, { name }); setDrafts(current => ({ ...current, [kind]: "" })); }, `${label}: item cadastrado.`); }
  const sections: { kind: CatalogKind; label: string; items: { id: number; name: string }[] }[] = [
    { kind: "departments", label: "Departamentos", items: options.departments.map(item => ({ id: item.id, name: item.name })) },
    { kind: "positions", label: "Cargos", items: options.positions.map(item => ({ id: item.id, name: item.title })) },
    { kind: "groups", label: "Grupos de funcionários", items: options.groups.map(item => ({ id: item.id, name: item.name })) },
  ];
  return <>
    <p className="hint">Opções organizacionais usadas ao registrar funcionários. Para habilitar o cadastro de funcionários, a empresa precisa de pelo menos um departamento, um cargo e um grupo.{!canEdit && " Somente o administrador altera estes itens."}</p>
    {error && <div className="alert alert-error">{error}</div>}
    {loading ? <div className="loading">Carregando estrutura…</div> : <div className="form-grid">{sections.map(section => <section className="panel" key={section.kind} style={{ padding: 16 }}><h3 style={{ margin: "0 0 12px", fontSize: 15 }}>{section.label}</h3>
      {canEdit && <form onSubmit={e => addItem(e, section.kind, section.label)}><Field label="Nome do item"><input className="field" required minLength={2} maxLength={120} value={drafts[section.kind]} onChange={e => setDrafts(current => ({ ...current, [section.kind]: e.target.value }))} /></Field><button className="btn btn-primary" type="submit" disabled={saving} style={{ marginTop: 10 }}><Plus size={14} />Adicionar</button></form>}
      <div style={{ display: "grid", gap: 7, marginTop: 16 }}>{section.items.map(item => <div className="company-mini" key={item.id} style={{ justifyContent: "space-between" }}><span className="company-mini-name">{item.name}</span>{canEdit && <span><button type="button" className="btn btn-quiet" aria-label={`Renomear ${item.name}`} disabled={saving} onClick={() => renameItem(section.kind, item.id, item.name)}><Pencil size={13} /></button><button type="button" className="btn btn-quiet" aria-label={`Excluir ${item.name}`} disabled={saving} onClick={() => deleteItem(section.kind, item.id, item.name)}><Trash2 size={13} /></button></span>}</div>)}{section.items.length === 0 && <div className="empty">Nenhum item cadastrado.</div>}</div>
    </section>)}</div>}
  </>;
}

function Field({ label, children, full = false }: { label: string; children: ReactNode; full?: boolean }) { return <label className={`field-wrap ${full ? "full" : ""}`}><span className="field-label">{label}</span>{children}</label>; }
function Modal({ title, children, close }: { title: string; children: ReactNode; close: () => void }) { return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}><section className="modal"><div className="modal-head"><h2 className="modal-title">{title}</h2><button className="btn btn-quiet" type="button" onClick={close} aria-label="Fechar"><X size={17} /></button></div>{children}</section></div>; }
