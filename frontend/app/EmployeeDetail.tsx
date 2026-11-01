"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { X } from "lucide-react";
import { api, datePt, message, money, patch, type SessionUser } from "./http";

type Detail = { id: number; company_id: number; full_name: string; email: string; registration: string; department: string; position: string; group_name: string; admission_date: string; salary: number; status: string;
  manager_id: number | null; manager_name: string | null; contract_type: string; calculation_group?: string; termination_date: string | null; job_position: string | null; cpf?: string | null; birth_date?: string | null; phone?: string | null;
  personal_email?: string | null; marital_status?: string | null; address?: string | null; emergency_contact?: string | null; movements: { kind: string; effective_date: string; status: string; before: Record<string, unknown>; after: Record<string, unknown> }[] };
type Peer = { id: number; full_name: string; status: string };

export const CONTRACT_TYPES = ["CLT", "Estágio", "Aprendiz", "Temporário", "Diretor"];
export const MARITAL = ["Solteiro(a)", "Casado(a)", "União estável", "Divorciado(a)", "Viúvo(a)"];

export default function EmployeeDetail({ employeeId, user, close, onSaved }: { employeeId: number; user: SessionUser; close: () => void; onSaved: (m: string) => void }) {
  const [detail, setDetail] = useState<Detail | null>(null); const [peers, setPeers] = useState<Peer[]>([]);
  const [form, setForm] = useState<Record<string, string>>({}); const [calcGroups, setCalcGroups] = useState<string[]>([]); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const canEdit = user.role === "system_admin" || user.role === "hr_manager";
  useEffect(() => {
    api<Detail>(`/employees/${employeeId}`).then(d => {
      setDetail(d);
      setForm({ full_name: d.full_name, email: d.email, manager_id: d.manager_id ? String(d.manager_id) : "", status: d.status, contract_type: d.contract_type, calculation_group: d.calculation_group || "Mensalistas", cpf: d.cpf || "", birth_date: d.birth_date || "", phone: d.phone || "", personal_email: d.personal_email || "", marital_status: d.marital_status || "", address: d.address || "", emergency_contact: d.emergency_contact || "" });
      if (canEdit) api<Peer[]>(`/employees?company_id=${d.company_id}`).then(setPeers).catch(() => setPeers([]));
      api<{ effective: { values: { calculation_groups: string[] } } }>(`/hr/configurations/payroll_setup?company_id=${d.company_id}`).then(c => setCalcGroups(c.effective.values.calculation_groups)).catch(() => setCalcGroups([]));
    }).catch(e => setError(message(e)));
  }, [employeeId, canEdit]);
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    const body: Record<string, unknown> = { ...form, manager_id: form.manager_id ? Number(form.manager_id) : null };
    for (const key of ["cpf", "birth_date", "phone", "personal_email", "marital_status", "address", "emergency_contact"]) if (!body[key]) body[key] = null;
    try { const d = await patch<Detail>(`/employees/${employeeId}`, body); setDetail(d); onSaved("Cadastro corrigido."); } catch (ex) { setError(message(ex)); } finally { setBusy(false); }
  }
  const set = (key: string) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}><section className="modal" style={{ maxWidth: 860 }}>
    <div className="modal-head"><h2 className="modal-title">{detail ? `${detail.full_name} · ${detail.registration}` : "Funcionário"}</h2><button className="btn btn-quiet" onClick={close} aria-label="Fechar"><X size={17} /></button></div>
    <form onSubmit={save}><div className="modal-body">
      {error && <div className="alert alert-error">{error}</div>}
      {!detail ? <div className="loading">Carregando…</div> : <>
        <div className="cards" style={{ marginBottom: 14 }}>
          <Info label="Departamento / cargo">{detail.department} · {detail.position}</Info><Info label="Grupo">{detail.group_name}</Info><Info label="Admissão">{datePt(detail.admission_date)}</Info>
          <Info label="Salário">{money(detail.salary)}</Info><Info label="Posição">{detail.job_position || "—"}</Info><Info label="Desligamento">{datePt(detail.termination_date)}</Info>
        </div>
        <p className="hint">Departamento, cargo, grupo, salário, transferência e desligamento mudam pelos processos de Movimentação e Rescisão, com aprovação e histórico.</p>
        <fieldset disabled={!canEdit || busy} style={{ border: 0, padding: 0, margin: 0 }}><div className="form-grid">
          <F label="Nome completo"><input className="field" required value={form.full_name || ""} onChange={set("full_name")} /></F>
          <F label="E-mail corporativo"><input className="field" type="email" required value={form.email || ""} onChange={set("email")} /></F>
          <F label="Gestor imediato"><select className="field" value={form.manager_id || ""} onChange={set("manager_id")}><option value="">Sem gestor</option>{peers.filter(p => p.id !== employeeId && p.status !== "Desligado").map(p => <option key={p.id} value={p.id}>{p.full_name}</option>)}{!peers.length && detail.manager_id && <option value={detail.manager_id}>{detail.manager_name}</option>}</select></F>
          <F label="Situação"><select className="field" value={form.status || ""} onChange={set("status")}>{["Ativo", "Afastado", "Férias", "Inativo", ...(detail.status === "Desligado" ? ["Desligado"] : [])].map(s => <option key={s}>{s}</option>)}</select></F>
          <F label="Tipo de contrato"><select className="field" value={form.contract_type || "CLT"} onChange={set("contract_type")}>{CONTRACT_TYPES.map(c => <option key={c}>{c}</option>)}</select></F>
          <F label="Grupo de cálculo (folha)"><select className="field" value={form.calculation_group || ""} onChange={set("calculation_group")}>{[...new Set([form.calculation_group, ...calcGroups])].filter(Boolean).map(g => <option key={g}>{g}</option>)}</select></F>
          <F label="CPF"><input className="field" value={form.cpf || ""} onChange={set("cpf")} placeholder="000.000.000-00" /></F>
          <F label="Nascimento"><input className="field" type="date" value={form.birth_date || ""} onChange={set("birth_date")} /></F>
          <F label="Telefone"><input className="field" value={form.phone || ""} onChange={set("phone")} /></F>
          <F label="E-mail pessoal"><input className="field" type="email" value={form.personal_email || ""} onChange={set("personal_email")} /></F>
          <F label="Estado civil"><select className="field" value={form.marital_status || ""} onChange={set("marital_status")}><option value="">—</option>{MARITAL.map(m => <option key={m}>{m}</option>)}</select></F>
          <F label="Endereço" full><input className="field" value={form.address || ""} onChange={set("address")} /></F>
          <F label="Contato de emergência" full><input className="field" value={form.emergency_contact || ""} onChange={set("emergency_contact")} /></F>
        </div></fieldset>
        {detail.movements.length > 0 && <><h3 style={{ fontSize: 14, margin: "16px 0 8px" }}>Movimentações</h3><div className="table-wrap"><table><thead><tr><th>Tipo</th><th>Data</th><th>Situação</th><th>Alterações</th></tr></thead><tbody>{detail.movements.map((m, i) => <tr key={i}><td>{m.kind === "transfer" ? "Transferência" : "Movimentação"}</td><td>{datePt(m.effective_date)}</td><td>{m.status === "scheduled" ? "Agendada" : "Aplicada"}</td>
          <td>{Object.keys(m.after).filter(k => m.before[k] !== m.after[k]).map(k => `${k}: ${String(m.before[k])} → ${String(m.after[k])}`).join("; ")}</td></tr>)}</tbody></table></div></>}
      </>}
    </div><div className="modal-foot"><button type="button" className="btn" onClick={close}>Fechar</button>{canEdit && <button className="btn btn-primary" disabled={busy || !detail}>Salvar correções</button>}</div></form>
  </section></div>;
}

function Info({ label, children }: { label: string; children: ReactNode }) { return <div className="stat-card"><div className="stat-top"><span>{label}</span></div><div style={{ fontWeight: 600 }}>{children}</div></div>; }
function F({ label, children, full }: { label: string; children: ReactNode; full?: boolean }) { return <label className={`field-wrap ${full ? "full" : ""}`}><span className="field-label">{label}</span>{children}</label>; }
