export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

/** The session lives in an HttpOnly cookie; the custom header lets the API reject cross-site requests. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API}${path}`, { ...init, credentials: "include", headers: { "Content-Type": "application/json", "X-Requested-With": "HCMLab", ...(init.headers || {}) } });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({ detail: "Resposta inválida da API." }));
  if (!res.ok) throw new Error(errorText(data.detail) || `Erro HTTP ${res.status}`);
  return data as T;
}

export function errorText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(item => typeof item === "string" ? item : (item && typeof item === "object" && "msg" in item) ? String((item as { msg: unknown }).msg).replace(/^Value error,\s*/i, "") : JSON.stringify(item)).join(" ");
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; report?: unknown };
    if (typeof d.message === "string") return d.message;
    return JSON.stringify(detail);
  }
  return "";
}

export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const del = (path: string) => api<void>(path, { method: "DELETE" });

export function datePt(value?: string | null) { if (!value) return "—"; const [y, m, d] = value.slice(0, 10).split("-"); return `${d}/${m}/${y}`; }
export function dateTimePt(value?: string | null) { return value ? new Date(value).toLocaleString("pt-BR") : "—"; }
export function money(value: number) { return new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(value); }
export function message(error: unknown) { return error instanceof Error ? error.message : "Ocorreu um erro inesperado."; }

export type Role = "system_admin" | "consultant" | "hr_manager" | "employee";
export type SessionUser = { id: number; email: string; full_name: string; role: Role; company_id: number | null; employee_id: number | null; allowed_modules?: string[] | null; must_change_password?: boolean; has_team?: boolean };
export type Environment = "homologation" | "production";
