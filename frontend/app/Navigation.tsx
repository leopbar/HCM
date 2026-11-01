"use client";

import { useEffect, useRef, useState } from "react";
import { BookOpen, Briefcase, Building2, CalendarDays, ClipboardList, Clock3, FileCheck2, FolderTree, GitCompareArrows, Home, Inbox, LayoutDashboard, ListChecks, Menu, Network, ShieldCheck, UserRound, Users, WalletCards, Workflow, Gift, type LucideIcon } from "lucide-react";
import type { SessionUser } from "./http";

export type NavTarget = { view: string; suite?: string; processTab?: "new" | "mine" | "manager" | "config"; action?: "my-data" };
type Item = { key: string; label: string; icon: LucideIcon; target: NavTarget; show?: boolean };

const can = (user: SessionUser, module: string) => !user.allowed_modules || user.allowed_modules.includes(module);

/** Menu da Suíte (RH, consultor e administrador): operação separada da configuração. */
export function StaffSidebar({ user, activeKey, onNavigate }: { user: SessionUser; activeKey: string; onNavigate: (t: NavTarget) => void }) {
  const admin = user.role === "system_admin"; const configurer = admin || user.role === "consultant";
  const groups: { title: string; items: Item[] }[] = [
    { title: "Operação", items: [
      { key: "dashboard", label: "Painel", icon: LayoutDashboard, target: { view: "dashboard" } },
      { key: "employees", label: "Funcionários", icon: Users, target: { view: "employees" } },
      { key: "processes", label: "Gerenciador de processos", icon: Workflow, target: { view: "processes", processTab: "manager" }, show: can(user, "processes") },
      { key: "admission", label: "Admissão", icon: FileCheck2, target: { view: "admission" }, show: can(user, "admission") },
      { key: "vacations", label: "Férias", icon: CalendarDays, target: { view: "vacations" }, show: can(user, "vacations") },
      { key: "timekeeping", label: "Controle de ponto", icon: Clock3, target: { view: "timekeeping" }, show: can(user, "timekeeping") },
      { key: "payroll", label: "Folha de pagamento", icon: WalletCards, target: { view: "payroll" }, show: can(user, "payroll") },
      { key: "benefits", label: "Benefícios", icon: Gift, target: { view: "benefits" }, show: can(user, "benefits") },
      { key: "recruitment", label: "Recrutamento e seleção", icon: Briefcase, target: { view: "recruitment" }, show: can(user, "recruitment") },
    ] },
    { title: "Configuração", items: [
      { key: "suite", label: "Suíte de parametrização", icon: FolderTree, target: { view: "suite", suite: "contratacao" } },
      { key: "companies", label: "Empresas", icon: Building2, target: { view: "companies" } },
      { key: "setup", label: "Cargos e vagas", icon: Network, target: { view: "setup" } },
      { key: "suite:processos", label: "Processos parametrizáveis", icon: ListChecks, target: { view: "suite", suite: "atribuicoes-selecao" } },
      { key: "suite:ambientes", label: "Ambientes / Migrador", icon: GitCompareArrows, target: { view: "suite", suite: "ambientes" }, show: configurer || user.role === "hr_manager" },
      { key: "access", label: "Usuários e acessos", icon: ShieldCheck, target: { view: "access" }, show: admin },
    ] },
    { title: "Treinamento", items: [
      { key: "training", label: "Aprenda a Parametrizar", icon: BookOpen, target: { view: "training" }, show: configurer },
    ] },
  ];
  return <aside className="sidebar">
    <div className="brand"><div className="brand-mark">H</div><div><div className="brand-name">HCM Lab</div><div className="brand-sub">Suíte de gestão de pessoas</div></div></div>
    {groups.map(group => { const items = group.items.filter(i => i.show !== false); if (!items.length) return null; return <div key={group.title}>
      <div className="nav-title">{group.title}</div>
      {items.map(item => <button key={item.key} className={`nav-item ${activeKey === item.key ? "active" : ""}`} aria-current={activeKey === item.key ? "page" : undefined} onClick={() => onNavigate(item.target)}><item.icon size={16} /><span>{item.label}</span></button>)}
    </div>; })}
    <div className="sidebar-bottom">HCM Lab é um simulador didático.<br />Não processa folha real nem envia dados a órgãos externos.<br />Dúvidas de parametrização? Use a Ajuda (❔) na Suíte.</div>
  </aside>;
}

/** Menu do Portal (colaborador e gestor): botão ≡ com menu suspenso, como no portal de autoatendimento. */
export function PortalMenu({ user, activeKey, onNavigate }: { user: SessionUser; activeKey: string; onNavigate: (t: NavTarget) => void }) {
  const [open, setOpen] = useState(false); const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => { if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", close);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", close); };
  }, [open]);
  const items: Item[] = [
    { key: "dashboard", label: "Início", icon: Home, target: { view: "dashboard" } },
    { key: "my-data", label: "Meus Dados", icon: UserRound, target: { view: "dashboard", action: "my-data" } },
    { key: "processes:new", label: "Solicitações", icon: ClipboardList, target: { view: "processes", processTab: "new" }, show: can(user, "processes") },
    { key: "recruitment", label: "Recrutamento (vagas internas)", icon: Briefcase, target: { view: "recruitment" }, show: can(user, "recruitment") },
    { key: "processes:manager", label: "Minhas Tarefas", icon: Inbox, target: { view: "processes", processTab: "manager" }, show: Boolean(user.has_team) && can(user, "processes") },
    { key: "processes:mine", label: "Gerenciar Processos", icon: ListChecks, target: { view: "processes", processTab: "mine" }, show: can(user, "processes") },
    { key: "vacations", label: "Férias", icon: CalendarDays, target: { view: "vacations" }, show: can(user, "vacations") },
    { key: "timekeeping", label: "Controle de ponto", icon: Clock3, target: { view: "timekeeping" }, show: can(user, "timekeeping") },
    { key: "benefits", label: "Benefícios", icon: Gift, target: { view: "benefits" }, show: can(user, "benefits") },
  ];
  return <div className="portal-menu" ref={ref}>
    <button className="portal-burger" aria-label="Abrir menu do portal" aria-expanded={open} onClick={() => setOpen(!open)}><Menu size={20} /></button>
    <div className="portal-brand"><div className="brand-mark">H</div><strong>HCM Lab</strong><span className="portal-sep">|</span><span className="portal-name">Portal do Colaborador</span></div>
    {open && <nav className="portal-dropdown" aria-label="Portal do Colaborador"><div className="portal-dropdown-title">Portal do Colaborador</div>
      {items.filter(i => i.show !== false).map(item => <button key={item.key} className={activeKey === item.key ? "active" : ""} onClick={() => { setOpen(false); onNavigate(item.target); }}><item.icon size={16} /><span>{item.label}</span></button>)}</nav>}
  </div>;
}
