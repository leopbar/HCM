"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, CircleHelp, FolderTree, RotateCcw, Save, Search, Settings2 } from "lucide-react";
import { ModuleEditor, type WorkflowMode } from "./ProcessCenter";
import CompanySetup, { type Tab as SetupTab } from "./CompanySetup";
import VacationCenter from "./VacationCenter";
import HRModules from "./HRModules";
import EnvironmentCenter from "./EnvironmentCenter";
import TrainingCenter from "./TrainingCenter";
import AccessCenter from "./AccessCenter";
import { CollaboratorGroups, ConfigAssignments, JobTypes, VacationNamedConfigs } from "./SuiteEditors";
import { api, type Environment, type SessionUser } from "./http";

type Company = { id: number; code: string; trade_name: string };
type Content =
  | { kind: "modules"; modules: { module: string; title?: string; mode?: WorkflowMode }[]; extra?: { setup?: SetupTab; hr?: "payroll" | "benefits" } }
  | { kind: "setup"; tab: SetupTab }
  | { kind: "vacations" } | { kind: "config-assignments" } | { kind: "groups" } | { kind: "job-types" } | { kind: "environments" } | { kind: "migrator" } | { kind: "training" } | { kind: "access" } | { kind: "email" };
export type SuiteNode = { id: string; label: string; help?: string; content?: Content; children?: SuiteNode[]; adminOnly?: boolean; configurersOnly?: boolean };

const M = (module: string, title?: string, mode?: WorkflowMode) => ({ module, title, mode });

/** Árvore por caminhos (Configuração → Autoatendimento → Processos → …). */
export const SUITE_TREE: SuiteNode[] = [
  { id: "configuracao", label: "Configuração", children: [
    { id: "por-empresa", label: "Configurações por Empresa", help: "Parametrizações válidas individualmente por empresa. Selecione a empresa no topo da página e ajuste cada guia.", children: [
      { id: "contratacao", label: "Contratação", help: "Regras de contratação e da solicitação de admissão: contrato de experiência, tabela salarial, quadro de vagas, controle por posição e geração do funcionário na aprovação.",
        content: { kind: "modules", modules: [M("contracting", "Contratação"), M("admission", "Solicitação de admissão")] } },
      { id: "ferias-recesso", label: "Férias / Recesso", help: "Recesso remunerado de estagiários. As regras de férias por empresa e grupo ficam em Autoatendimento → Processos → Férias.",
        content: { kind: "modules", modules: [M("recess", "Recesso de estágio")] } },
      { id: "rescisao", label: "Rescisão / Aviso Prévio", help: "Aviso prévio base, proporcionalidade (Lei 12.506/2011), datas retroativas e bloqueio do acesso do desligado.",
        content: { kind: "modules", modules: [M("termination")] } },
      { id: "outros", label: "Outros", children: [
        { id: "vale-transporte", label: "Vale Transporte", help: "Percentual de desconto, dias úteis e linhas com tarifa.", content: { kind: "modules", modules: [M("transport")], extra: { setup: "transport" } } },
        { id: "posicao", label: "Posição", help: "Assentos numerados por departamento e cargo, ocupados por no máximo um colaborador.", content: { kind: "setup", tab: "positions" } },
        { id: "quadro-vagas", label: "Quadro de Vagas", help: "Quantidade planejada por departamento e cargo, usada pela requisição, admissão e movimentação.", content: { kind: "setup", tab: "headcount" } },
        { id: "tabela-salarial", label: "Tabela Salarial", help: "Faixas salariais por cargo e nível.", content: { kind: "setup", tab: "salary" } },
        { id: "tipo-cargo", label: "Tipo de Cargo", help: "Classificação do cargo (Mensal, Quinzenal, Semanal, Diário, Horário, Tarefa, Outros), usada pelo Guia Férias por tipo de cargo.", content: { kind: "job-types" } },
      ] },
    ] },
    { id: "autoatendimento", label: "Autoatendimento", children: [
      { id: "grupos-colaboradores", label: "Grupos de Colaboradores", help: "Participação dos colaboradores em grupos. Além do grupo principal do cadastro, cada pessoa pode participar de outros grupos; a ordem das atribuições define a prioridade.", content: { kind: "groups" } },
      { id: "processos", label: "Processos", help: "Configuração dos processos do autoatendimento: quem pode solicitar para quem, qual configuração vale para cada grupo e as regras gerais de cada processo.", children: [
        { id: "atribuicoes-selecao", label: "Atribuições de Seleção de Colaboradores", help: "Define, por processo e grupo do solicitante, para quem a solicitação pode ser aberta: próprio solicitante, por visibilidade, subordinados diretos ou indiretos, e de onde a configuração é obtida (solicitante ou alvo).",
          content: { kind: "modules", modules: [M("assignments")] } },
        { id: "atribuicoes-configuracoes", label: "Atribuições de Configurações", help: "Associa grupos de colaboradores às configurações de cada processo. A ordenação define a prioridade quando um colaborador faz parte de mais de um grupo.",
          content: { kind: "config-assignments" } },
        { id: "processo-ferias", label: "Férias", help: "Configurações de férias com código e descrição (quem solicita, parcelamento, antecedência, aprovação, disponível para simulação, 13º). Elas passam a valer quando atribuídas a grupos em Atribuições de Configurações.", content: { kind: "vacations" } },
        { id: "configuracoes-gerais", label: "Configurações Gerais", help: "Guias com as regras gerais de cada processo parametrizável.", children: [
          { id: "guia-sla", label: "Guia SLA", help: "Prazo de atendimento: expediente, dias úteis e aprovação automática quando o solicitante é o próprio aprovador.", content: { kind: "modules", modules: [M("workflow", "Prazo de atendimento (SLA)", "sla")] } },
          { id: "guia-gerenciador", label: "Guia Gerenciador de Processos", help: "Etapas de aprovação de cada processo (gestor imediato, RH, administrador, RH de destino), prazo de cada etapa em horas úteis e redirecionamento: uma etapa pode finalizar o processo sem executar as seguintes.", content: { kind: "modules", modules: [M("workflow", "Etapas de aprovação", "steps")] } },
          { id: "guia-informacoes", label: "Guia Informações Adicionais", help: "Campos extras no formulário de cada processo (texto, número, data, sim/não ou lista), obrigatórios ou não. São exportados pelo Migrador e ficam gravados na solicitação.", content: { kind: "modules", modules: [M("process_fields")] } },
          { id: "guia-ferias", label: "Guia Férias", help: "Abono pecuniário, regras de parcelamento da CLT, bloqueio de início antes de DSR/feriado, aviso de férias com ciência e prazo mínimo para aprovação.", content: { kind: "modules", modules: [M("vacation_policy")] } },
          { id: "guia-beneficios", label: "Guia Benefícios", help: "Habilitação do processo, solicitação pelo colaborador e catálogo de benefícios.", content: { kind: "modules", modules: [M("benefits")], extra: { hr: "benefits" } } },
          { id: "guia-dependentes", label: "Guia Dependentes", content: { kind: "modules", modules: [M("dependents")] } },
          { id: "guia-dados", label: "Guia Alteração de Dados Pessoais", content: { kind: "modules", modules: [M("personal_data")] } },
          { id: "guia-afastamento", label: "Guia Afastamento/Ausência", content: { kind: "modules", modules: [M("leave")] } },
          { id: "guia-movimentacao", label: "Guia Movimentação", help: "Movimentar colaborador (área, cargo, grupo, gestor, salário) e Ceder/Buscar colaborador entre empresas.", content: { kind: "modules", modules: [M("movement", "Movimentar colaborador"), M("transfer", "Ceder / Buscar colaborador")] } },
          { id: "guia-requisicao", label: "Guia Requisição", content: { kind: "modules", modules: [M("requisition")] } },
          { id: "guia-rescisao", label: "Guia Rescisão", help: "Mesmo parâmetro de Configurações por Empresa → Rescisão / Aviso Prévio.", content: { kind: "modules", modules: [M("termination")] } },
          { id: "guia-recesso", label: "Guia Recesso", content: { kind: "modules", modules: [M("recess")] } },
          { id: "guia-vt", label: "Guia Vale Transporte", content: { kind: "modules", modules: [M("transport")] } },
          { id: "guia-lancamentos", label: "Guia Lançamento de Valores", content: { kind: "modules", modules: [M("value_entry")] } },
          { id: "guia-email", label: "Guia Servidor de E-mail", configurersOnly: true, help: "Servidor usado nas notificações dos processos.", content: { kind: "email" } },
          { id: "guia-migrador", label: "Guia Migrador", configurersOnly: true, help: "Exporta as configurações de um ambiente e importa em outro (homologação), com prévia e validação. Itens que não correspondem interrompem a importação inteira.", content: { kind: "migrator" } },
        ] },
      ] },
    ] },
    { id: "ponto", label: "Ponto", help: "Jornada, intervalo, tolerância, horas extras, ajuste manual e escala de trabalho.", content: { kind: "modules", modules: [M("timekeeping", "Controle de ponto"), M("work_schedule", "Jornada e escala")] } },
    { id: "folha", label: "Folha de Pagamento", help: "Grupos de cálculo e folhas (usados pelo Guia Férias), parâmetros da folha demonstrativa e eventos de proventos e descontos.", content: { kind: "modules", modules: [M("payroll_setup", "Grupos de cálculo e folhas"), M("payroll", "Folha demonstrativa")], extra: { hr: "payroll" } } },
    { id: "recrutamento", label: "Recrutamento e Seleção", help: "Etapas do processo seletivo, critérios de avaliação, vagas internas e retenção de dados (LGPD).", content: { kind: "modules", modules: [M("recruitment")] } },
    { id: "ambientes", label: "Ambientes e Publicação", help: "Compare homologação e produção, valide e solicite a publicação aprovada.", content: { kind: "environments" } },
    { id: "seguranca", label: "Segurança", children: [
      { id: "usuarios", label: "Usuários e Acessos", adminOnly: true, help: "Contas individuais, grupos de acesso por empresa e módulos.", content: { kind: "access" } },
    ] },
  ] },
  { id: "treinamento", label: "Treinamento", configurersOnly: true, children: [
    { id: "aprenda", label: "Aprenda a Parametrizar", help: "Roteiros práticos verificados com as regras reais do sistema.", content: { kind: "training" } },
  ] },
];

function flatten(nodes: SuiteNode[], trail: SuiteNode[] = []): { node: SuiteNode; trail: SuiteNode[] }[] {
  return nodes.flatMap(n => [{ node: n, trail }, ...flatten(n.children || [], [...trail, n])]);
}
const ALL = flatten(SUITE_TREE);
export const findNode = (id: string) => ALL.find(x => x.node.id === id);
const firstLeaf = (n: SuiteNode): SuiteNode => (n.content || !n.children?.length ? n : firstLeaf(n.children[0]));

export default function ParametrizationSuite({ user, token, companyId, companies, environment, nodeId, onNavigate, onToast }:
  { user: SessionUser; token: string; companyId: string; companies: Company[]; environment: Environment; nodeId: string; onNavigate: (id: string) => void; onToast: (m: string) => void }) {
  const configurer = user.role === "system_admin" || user.role === "consultant";
  const visible = (n: SuiteNode) => (!n.adminOnly || user.role === "system_admin") && (!n.configurersOnly || configurer);
  const current = findNode(nodeId) || ALL[0];
  const [open, setOpen] = useState<Set<string>>(() => new Set(["configuracao", ...current.trail.map(t => t.id)]));
  const [query, setQuery] = useState(""); const [help, setHelp] = useState(true); const [nonce, setNonce] = useState(0);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { setOpen(o => new Set([...o, ...current.trail.map(t => t.id)])); }, [current.trail]);
  const matches = useMemo(() => query.trim() ? ALL.filter(x => x.node.content && visible(x.node) && `${x.trail.map(t => t.label).join(" ")} ${x.node.label}`.toLowerCase().includes(query.toLowerCase())) : [], [query]); // eslint-disable-line react-hooks/exhaustive-deps
  const node = current.node.content ? current.node : firstLeaf(current.node);
  const trail = findNode(node.id)?.trail || [];
  const canEdit = user.role === "system_admin" || user.role === "consultant";
  const single = node.content?.kind === "modules" && node.content.modules.length === 1 ? node.content.modules[0].module : null;
  return <div className="suite">
    <aside className="suite-tree" aria-label="Árvore de parametrização">
      <div className="suite-tree-head"><FolderTree size={16} />Suíte de parametrização</div>
      <div className="search-wrap" style={{ margin: "10px 10px 6px" }}><Search size={14} className="search-icon" /><input className="search" style={{ width: "100%" }} placeholder="Buscar configuração" value={query} onChange={e => setQuery(e.target.value)} /></div>
      {query.trim() ? <ul className="suite-list">{matches.map(m => <li key={m.node.id}><button className={`suite-item ${m.node.id === node.id ? "active" : ""}`} onClick={() => { onNavigate(m.node.id); setQuery(""); }}><span>{m.node.label}<small>{m.trail.map(t => t.label).join(" › ")}</small></span></button></li>)}{matches.length === 0 && <li className="hint" style={{ padding: 12 }}>Nada encontrado.</li>}</ul>
        : <Tree nodes={SUITE_TREE.filter(visible)} depth={0} open={open} toggle={id => setOpen(o => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; })} active={node.id} onNavigate={onNavigate} visible={visible} />}
    </aside>
    <section className="suite-main">
      <nav className="suite-path" aria-label="Caminho">{[...trail, node].map((n, i, all) => <span key={n.id}>{i > 0 && <ChevronRight size={12} />}{i < all.length - 1 ? <button onClick={() => onNavigate(n.id)}>{n.label}</button> : <strong>{n.label}</strong>}</span>)}</nav>
      <h1 className="suite-title">{node.label}</h1>
      <div className="suite-toolbar">
        {single && canEdit && environment === "homologation" && <button type="submit" form={`module-form-${single}`} title="Salvar" aria-label="Salvar"><Save size={16} /></button>}
        <button type="button" title="Recarregar" aria-label="Recarregar" onClick={() => setNonce(n => n + 1)}><RotateCcw size={16} /></button>
        <span className="suite-env">{environment === "homologation" ? "Homologação" : "Produção"} · {companies.find(c => String(c.id) === companyId)?.trade_name || "selecione a empresa"}</span>
        <button type="button" title="Ajuda" aria-label="Ajuda" aria-pressed={help} onClick={() => setHelp(!help)} style={{ marginLeft: "auto" }}><CircleHelp size={16} /></button>
      </div>
      {help && node.help && <div className="suite-help">{node.help}</div>}
      <div className="suite-content" key={`${node.id}-${companyId}-${environment}-${nonce}`}>
        {!companyId ? <div className="empty">Selecione uma empresa no topo da página para parametrizar.</div> : <NodeContent content={node.content!} user={user} token={token} companyId={companyId} companies={companies} environment={environment} canEdit={canEdit} onToast={onToast} />}
      </div>
    </section>
  </div>;
}

function Tree({ nodes, depth, open, toggle, active, onNavigate, visible }: { nodes: SuiteNode[]; depth: number; open: Set<string>; toggle: (id: string) => void; active: string; onNavigate: (id: string) => void; visible: (n: SuiteNode) => boolean }) {
  return <ul className="suite-list" role={depth === 0 ? "tree" : "group"}>{nodes.filter(visible).map(n => {
    const branch = Boolean(n.children?.length); const isOpen = open.has(n.id);
    return <li key={n.id} role="treeitem" aria-selected={active === n.id} aria-expanded={branch ? isOpen : undefined}>
      <button className={`suite-item ${active === n.id ? "active" : ""} ${branch ? "branch" : ""}`} style={{ paddingLeft: 10 + depth * 14 }} onClick={() => { if (branch) toggle(n.id); if (n.content) onNavigate(n.id); }}>
        {branch ? (isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : <Settings2 size={12} className="leaf-icon" />}<span>{n.label}</span></button>
      {branch && isOpen && <Tree nodes={n.children!} depth={depth + 1} open={open} toggle={toggle} active={active} onNavigate={onNavigate} visible={visible} />}
    </li>; })}</ul>;
}

function Block({ title, children }: { title?: string; children: ReactNode }) {
  return <section className="suite-block">{title && <h2>{title}</h2>}{children}</section>;
}

function NodeContent({ content, user, token, companyId, companies, environment, canEdit, onToast }: { content: Content; user: SessionUser; token: string; companyId: string; companies: Company[]; environment: Environment; canEdit: boolean; onToast: (m: string) => void }) {
  const hrUser = user as unknown as Parameters<typeof HRModules>[0]["user"];
  switch (content.kind) {
    case "modules": return <>
      {content.modules.map(m => <Block key={m.module + (m.mode || "")} title={m.title}><ModuleEditor module={m.module} companyId={companyId} environment={environment} onToast={onToast} workflowMode={m.mode} canEdit={canEdit} /></Block>)}
      {content.extra?.setup && <Block title="Cadastro"><CompanySetup user={user} companyId={companyId} onToast={onToast} initialTab={content.extra.setup} embedded /></Block>}
      {content.extra?.hr && <Block title={content.extra.hr === "payroll" ? "Eventos da folha" : "Catálogo de benefícios"}><HRModules module={content.extra.hr} token={token} user={hrUser} companyId={companyId} companies={companies} environment={environment} onToast={onToast} /></Block>}
    </>;
    case "setup": return <Block><CompanySetup user={user} companyId={companyId} onToast={onToast} initialTab={content.tab} embedded /></Block>;
    case "vacations": return <>
      <Block title="Configurações de férias"><VacationNamedConfigs companyId={companyId} environment={environment} canEdit={canEdit} onToast={onToast} /></Block>
      <Block title="Regras padrão por empresa e grupo (quando não há atribuição)"><VacationCenter token={token} user={hrUser} companyId={companyId} companies={companies} environment={environment} onToast={onToast} initialTab="parameters" /></Block></>;
    case "config-assignments": return <>
      <Block title="Férias e Férias - Simulação"><ConfigAssignments companyId={companyId} environment={environment} canEdit={canEdit} onToast={onToast} /></Block>
      <Block title="Demais processos: etapas de aprovação por grupo"><ModuleEditor module="workflow" companyId={companyId} environment={environment} onToast={onToast} workflowMode="overrides" canEdit={canEdit} /></Block></>;
    case "groups": return <Block><CollaboratorGroups companyId={companyId} onToast={onToast} /></Block>;
    case "job-types": return <Block><JobTypes companyId={companyId} onToast={onToast} /></Block>;
    case "environments": return <EnvironmentCenter token={token} user={hrUser} companyId={companyId} companies={companies} onToast={onToast} />;
    case "migrator": return <TrainingCenter token={token} companyId={companyId} companies={companies} environment={environment} onToast={onToast} initialTab="transfer" />;
    case "training": return <TrainingCenter token={token} companyId={companyId} companies={companies} environment={environment} onToast={onToast} initialTab="exercises" />;
    case "access": return <AccessCenter token={token} companies={companies} onToast={onToast} />;
    case "email": return <EmailStatus />;
  }
}

function EmailStatus() {
  const [data, setData] = useState<{ configured: boolean; host: string | null; port: number; sender: string | null; starttls: boolean; authenticated: boolean } | null>(null); const [error, setError] = useState("");
  useEffect(() => { api<NonNullable<typeof data>>("/system/email").then(setData).catch(e => setError(e instanceof Error ? e.message : "Falha")); }, []);
  if (error) return <div className="alert alert-error">{error}</div>;
  if (!data) return <div className="loading">Carregando…</div>;
  return <Block><div className="table-wrap"><table><tbody>
    <tr><td style={{ width: 240 }}>Situação</td><td><span className="tag">{data.configured ? "Configurado" : "Não configurado"}</span></td></tr>
    <tr><td>Servidor</td><td>{data.host || "—"}:{data.port}</td></tr><tr><td>Remetente</td><td>{data.sender || "—"}</td></tr>
    <tr><td>Criptografia (STARTTLS)</td><td>{data.starttls ? "Sim" : "Não"}</td></tr><tr><td>Autenticação</td><td>{data.authenticated ? "Usuário configurado" : "Sem autenticação"}</td></tr>
  </tbody></table></div><p className="hint" style={{ marginTop: 10 }}>Por segurança, o servidor e a senha ficam no arquivo .env do servidor (SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM) e nunca são exibidos nem gravados no banco. Sem servidor configurado, as notificações continuam aparecendo dentro do sistema.</p></Block>;
}
