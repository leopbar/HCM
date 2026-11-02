"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDownToLine, BookOpen, Check, CircleHelp, ClipboardCheck, GraduationCap, RefreshCw, Upload } from "lucide-react";
import "./training.css";

type Company = { id: number; code: string; trade_name: string };
type Exercise = { id: string; title: string; module: string; objective: string; steps: string[]; expected: string; status: string; last_result: { passed: boolean; checks: { label: string; passed: boolean; details: string }[] } | null; completed_at: string | null };
type Props = { token: string; companyId: string; companies: Company[]; environment: "homologation" | "production"; onToast: (message: string) => void; initialTab?: "exercises" | "transfer" | "help" };
type ImportPreview = { preview_id: number; valid: boolean; errors: string[]; changes: { index: number; parameter_id?: string; module?: string; action: string; scope?: { type: string; group_name?: string }; values?: Record<string, unknown>; current_snapshot?: { scope: { type: string; group_name?: string }; values: Record<string, unknown> } | null; errors?: string[] }[]; target_environment: string };

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const modules = ["all", "vacations", "admission", "timekeeping", "payroll", "benefits"];
const moduleLabels: Record<string, string> = { all: "Todos os módulos", vacations: "Férias", admission: "Admissão", timekeeping: "Controle de ponto", payroll: "Folha", benefits: "Benefícios" };

async function call<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { ...init, credentials: "include", headers: { "X-Requested-With": "HCMLab", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers } });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.message || JSON.stringify(detail) || `Erro HTTP ${response.status}`);
  }
  return data as T;
}

export default function TrainingCenter({ token, companyId, companies, environment, onToast, initialTab }: Props) {
  const [tab, setTab] = useState<"exercises" | "transfer" | "help">(initialTab ?? "exercises");
  const [exercises, setExercises] = useState<Exercise[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [module, setModule] = useState("all");
  const [sourceEnvironment, setSourceEnvironment] = useState<"homologation" | "production">(environment);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const company = useMemo(() => companies.find((item) => String(item.id) === companyId), [companies, companyId]);
  const current = exercises.find((item) => item.id === selected);

  const load = useCallback(async () => {
    if (!companyId) { setExercises([]); return; }
    try { setExercises(await call<Exercise[]>(`/training/exercises?company_id=${companyId}`, token)); }
    catch (ex) { setError(ex instanceof Error ? ex.message : "Não foi possível carregar os exercícios."); }
  }, [companyId, token]);
  useEffect(() => { let active = true; if (companyId) call<Exercise[]>(`/training/exercises?company_id=${companyId}`, token).then((data) => { if (active) setExercises(data); }).catch((ex: unknown) => { if (active) setError(ex instanceof Error ? ex.message : "Não foi possível carregar os exercícios."); }); return () => { active = false; }; }, [companyId, token]);

  async function checkExercise(id: string) {
    if (!companyId) return;
    setBusy(true); setError("");
    try {
      const result = await call<{ passed: boolean; checks: { label: string; passed: boolean; details: string }[] }>(`/training/exercises/${id}/check?company_id=${companyId}`, token, { method: "POST", body: JSON.stringify({}) });
      setExercises(await call<Exercise[]>(`/training/exercises?company_id=${companyId}`, token));
      if (result.passed) onToast("Exercício validado e concluído.");
      else onToast("Verificação executada. Veja os critérios que ainda faltam.");
    } catch (ex) { setError(ex instanceof Error ? ex.message : "Não foi possível verificar o exercício."); }
    finally { setBusy(false); }
  }

  async function exportPackage() {
    if (!companyId) return;
    setBusy(true); setError("");
    try {
      const data = await call<Record<string, unknown>>(`/configurations/export?company_id=${companyId}&module=${module}&environment=${sourceEnvironment}`, token);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob); const link = document.createElement("a");
      link.href = url; link.download = `hcm-${company?.code || "configuracoes"}-${module}-${sourceEnvironment}.json`; link.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      onToast("Arquivo JSON exportado.");
    } catch (ex) { setError(ex instanceof Error ? ex.message : "Falha ao exportar."); }
    finally { setBusy(false); }
  }

  async function readFile(file?: File) {
    if (!file || !companyId) return;
    setBusy(true); setError(""); setPreview(null);
    try {
      const pkg = JSON.parse(await file.text());
      const result = await call<ImportPreview>("/configurations/import/preview", token, { method: "POST", body: JSON.stringify({ company_id: Number(companyId), package: pkg }) });
      setPreview(result);
    } catch (ex) { setError(ex instanceof Error ? ex.message : "O arquivo não contém JSON válido."); }
    finally { setBusy(false); }
  }

  async function applyImport() {
    if (!preview) return;
    setBusy(true); setError("");
    try {
      await call(`/configurations/import/${preview.preview_id}/apply`, token, { method: "POST" });
      onToast("Configurações importadas para Homologação."); setPreview(null);
      if (selected === "configuration-migration") await load();
    } catch (ex) { setError(ex instanceof Error ? ex.message : "Falha ao importar as configurações."); }
    finally { setBusy(false); }
  }

  return <>
    {tab !== "transfer" && <><div className="page-head"><div><div className="eyebrow">Capacitação funcional</div><h1 className="page-title">Aprenda a Parametrizar</h1><p className="page-desc">Pratique configurações, testes e migrações com dados fictícios. As verificações usam as regras reais do backend.</p></div><div className="training-company">{company?.trade_name || "Selecione uma empresa"}</div></div>
    <div className="training-tabs" role="tablist"><button className={tab === "exercises" ? "active" : ""} onClick={() => setTab("exercises")}><GraduationCap size={15}/>Exercícios</button><button className={tab === "help" ? "active" : ""} onClick={() => setTab("help")}><CircleHelp size={15}/>Central de Ajuda</button></div></>}
    {error && <div className="alert alert-error">{error}</div>}
    {!companyId && <div className="section-note">Selecione uma empresa na barra superior para abrir os roteiros e arquivos de configuração.</div>}

    {tab === "exercises" && <div className="training-layout"><section className="section-panel exercise-list"><div className="panel-head"><div><h2 className="panel-title">Roteiros práticos</h2><div className="panel-sub">O progresso é salvo por empresa e usuário.</div></div><button className="btn" onClick={() => void load()} disabled={busy}><RefreshCw size={14}/>Atualizar</button></div>{exercises.map((exercise) => <button key={exercise.id} className={`exercise-item ${selected === exercise.id ? "selected" : ""}`} onClick={() => setSelected(exercise.id)}><span className={`exercise-status ${exercise.status}`}>{exercise.status === "completed" ? <Check size={13}/> : <span>{exercise.status === "in_progress" ? "…" : "○"}</span>}</span><span><strong>{exercise.title}</strong><small>{exercise.module}</small></span></button>)}</section>
      <section className="section-panel exercise-detail">{current ? <><div className="eyebrow">{current.module}</div><h2 className="panel-title">{current.title}</h2><p className="training-objective">{current.objective}</p><h3>Passos</h3><ol className="training-steps">{current.steps.map((step) => <li key={step}>{step}</li>)}</ol><div className="training-expected"><strong>Resultado esperado</strong><p>{current.expected}</p></div><div className="training-actions"><button className="btn btn-primary" onClick={() => void checkExercise(current.id)} disabled={busy || !companyId}><ClipboardCheck size={15}/>{busy ? "Verificando…" : "Verificar exercício"}</button>{current.last_result && <span className={`tag ${current.last_result.passed ? "tag-success" : "tag-warn"}`}>{current.last_result.passed ? "Concluído" : "Há etapas pendentes"}</span>}</div>{current.last_result && <div className="exercise-checks">{current.last_result.checks.map((check) => <div className="exercise-check" key={check.label}><span className={check.passed ? "check-pass" : "check-fail"}>{check.passed ? "✓" : "•"}</span><div><strong>{check.label}</strong><small>{check.details}</small></div></div>)}</div>}</> : <div className="empty"><BookOpen size={22}/><p>Selecione um exercício para ver objetivo, passos e resultado esperado.</p></div>}</section></div>}

    {tab === "transfer" && <div className="training-transfer-grid"><section className="section-panel"><div className="panel-head"><div><h2 className="panel-title">Exportar configurações</h2><div className="panel-sub">O arquivo inclui identificadores estáveis, escopos, versões e vigências.</div></div><ArrowDownToLine size={17}/></div><label className="field-wrap"><span className="field-label">Módulo</span><select className="field" value={module} onChange={(e) => setModule(e.target.value)}>{modules.map((item) => <option value={item} key={item}>{moduleLabels[item]}</option>)}</select></label><label className="field-wrap"><span className="field-label">Ambiente de origem</span><select className="field" value={sourceEnvironment} onChange={(e) => setSourceEnvironment(e.target.value as "homologation" | "production")}><option value="homologation">Homologação</option><option value="production">Produção</option></select></label><p className="hint">O pacote exportado pode ser revisado e importado para homologação. Ele nunca grava diretamente em Produção.</p><button className="btn btn-primary" disabled={!companyId || busy} onClick={() => void exportPackage()}><ArrowDownToLine size={15}/>Exportar JSON</button></section>
      <section className="section-panel"><div className="panel-head"><div><h2 className="panel-title">Importar para Homologação</h2><div className="panel-sub">A prévia valida esquema, empresa, escopo e regras antes da aplicação.</div></div><Upload size={17}/></div><div className="training-target">Destino fixo: <strong>{company?.trade_name || "empresa selecionada"} · Homologação</strong></div><label className="field-wrap"><span className="field-label">Arquivo JSON</span><input className="field" type="file" accept="application/json,.json" disabled={!companyId || busy} onChange={(e) => void readFile(e.target.files?.[0])}/></label><p className="hint">A prévia pode apontar parâmetros novos, modificados, inalterados ou inválidos. Aplicar cria versões em Homologação e registra auditoria.</p>{preview && <><div className={`alert ${preview.valid ? "alert-success" : "alert-error"}`}>{preview.valid ? `Prévia válida · ${preview.changes.length} parâmetro(s)` : `Prévia inválida · ${preview.errors.length} problema(s)`}</div>{preview.errors.map((item) => <div className="import-error" key={item}>{item}</div>)}<div className="table-wrap"><table><thead><tr><th>Parâmetro</th><th>Escopo</th><th>Configuração atual</th><th>Proposta</th><th>Status</th></tr></thead><tbody>{preview.changes.map((item) => <tr key={item.index}><td className="code">{item.parameter_id || `Item ${item.index + 1}`}</td><td>{item.scope?.group_name || item.scope?.type || "—"}</td><td><code>{item.current_snapshot ? JSON.stringify(item.current_snapshot.values) : "Novo parâmetro"}</code></td><td><code>{item.values ? JSON.stringify(item.values) : (item.errors || []).join(" ")}</code></td><td><span className={`tag ${item.action === "invalid" ? "tag-danger" : item.action === "modified" ? "tag-warn" : "tag-success"}`}>{item.action}</span></td></tr>)}</tbody></table></div><button className="btn btn-primary" disabled={!preview.valid || busy} onClick={() => void applyImport()}><Check size={15}/>Validar e aplicar em Homologação</button></>}</section></div>}

    {tab === "help" && <div className="help-grid"><section className="section-panel"><BookOpen size={18}/><h2 className="panel-title">Como navegar no laboratório</h2><p>O consultor prepara regras em Homologação, testa nos módulos e compara com Produção em <strong>Ambientes</strong>. Apenas a publicação aprovada transfere as regras para Produção.</p><ul><li><strong>Férias:</strong> parâmetros por empresa ou grupo, vigência e simulação.</li><li><strong>Admissão:</strong> antecedência, finais de semana, feriados e documentos.</li><li><strong>Ponto:</strong> jornada, intervalo, tolerância e horas extras fictícias.</li><li><strong>Folha:</strong> eventos percentuais ou fixos para simulações de salário.</li><li><strong>Benefícios:</strong> catálogos, elegibilidade por grupo e aprovações.</li></ul></section><section className="section-panel"><CircleHelp size={18}/><h2 className="panel-title">Conceitos e perguntas frequentes</h2><details><summary>O que é vigência?</summary><p>É a data a partir da qual uma nova versão deve ser usada nas simulações e operações.</p></details><details><summary>Por que importar primeiro para homologação?</summary><p>Para conferir o conteúdo, testar suas regras e revisar as diferenças antes de solicitar publicação.</p></details><details><summary>O que significa uma regra de grupo prevalecer?</summary><p>Quando existe uma regra vigente para o grupo do funcionário, ela é escolhida antes da regra geral da empresa.</p></details><details><summary>O JSON é compatível com um ERP comercial?</summary><p>Não. Este é um formato didático próprio do HCM Lab, com validação de esquema versão 1.</p></details></section><section className="section-panel demo-data-card"><div className="eyebrow">Dados de demonstração</div><h2 className="panel-title">Cenários para praticar</h2><p>As empresas fictícias carregam funcionários, grupos, eventos de folha e catálogos de benefício para que os exercícios possam ser executados sem cadastrar dados reais.</p><div className="demo-data-tags"><span>ABC Tecnologia · 30 funcionários</span><span>Indústria Brasil · 50 funcionários</span><span>Bônus demonstrativo BONUS10 · 10%</span><span>Comissão COMISSAO-OPS · grupo Operacional</span><span>EDUCACAO-GESTORES · grupo Gestores</span></div></section></div>}
  </>;
}
