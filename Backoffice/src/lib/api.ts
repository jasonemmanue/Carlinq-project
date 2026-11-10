/**
 * Client de l'API FastAPI Carlinq, utilise UNIQUEMENT cote serveur
 * (Server Components, Route Handlers) : le jeton admin ne quitte jamais le serveur
 * et aucune mutation ne part directement du navigateur vers l'API.
 *
 * Developpement : le jeton est obtenu avec le compte admin de l'amorcage
 * (CARLINQ_ADMIN_PHONE / CARLINQ_ADMIN_PASSWORD). En production, il viendra
 * de la session de l'administrateur connecte.
 */
const API_URL = process.env.CARLINQ_API_URL ?? "http://localhost:8010";
const ADMIN_PHONE = process.env.CARLINQ_ADMIN_PHONE;
const ADMIN_PASSWORD = process.env.CARLINQ_ADMIN_PASSWORD;

let cachedToken: { value: string; expiresAt: number } | null = null;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

async function adminToken(): Promise<string> {
  if (cachedToken && cachedToken.expiresAt > Date.now()) return cachedToken.value;
  if (!ADMIN_PHONE || !ADMIN_PASSWORD) throw new ApiError(500, "ADMIN_NOT_CONFIGURED", "Compte admin non configure");
  const res = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ phone: ADMIN_PHONE, password: ADMIN_PASSWORD }),
    cache: "no-store",
  });
  if (!res.ok) throw new ApiError(res.status, "ADMIN_LOGIN_FAILED", "Connexion admin a l'API impossible");
  const body = await res.json();
  cachedToken = { value: body.access_token, expiresAt: Date.now() + 50 * 60 * 1000 };
  return cachedToken.value;
}

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    method,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${await adminToken()}` },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new ApiError(res.status, err?.error?.code ?? "API_ERROR", err?.error?.message ?? res.statusText);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => call<T>("GET", path),
  post: <T>(path: string, body?: unknown) => call<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => call<T>("PUT", path, body),
};

// ------------------------------------------------------------------ types (contrat API)

export type ShareStatus = "pending" | "accepted" | "declined" | "cancelled";
export type GoalStatus = "active" | "paused" | "achieved" | "failed" | "settled";

export interface GoalShare {
  id: number;
  helper_driver_id: number;
  helper_name: string | null;
  percent: number;
  status: ShareStatus;
  contributed_rides: number;
}

export interface SplitLine {
  driver_id: number;
  driver_name: string | null;
  role: "owner" | "helper";
  percent: number;
  amount_xaf: number;
  contributed_rides: number;
}

export interface Goal {
  id: number;
  driver_id: number;
  driver_name: string;
  week_start: string;
  target_rides: number;
  bonus_xaf: number;
  own_rides: number;
  shared_rides: number;
  progress_rides: number;
  status: GoalStatus;
  deadline: string | null;
  shares: GoalShare[];
  projected_split: SplitLine[];
}

export interface Settlement {
  id: number;
  goal_id: number;
  week_start: string;
  bonus_xaf: number;
  shared_total_xaf: number;
  owner_net_xaf: number;
  created_at: string;
  lines: { driver_id: number; driver_name: string; role: string; percent: number; amount_xaf: number }[];
}

export interface GoalsConfig {
  tiers: { target_rides: number; bonus_xaf: number }[];
  points_on_achieved: number;
  pause_max_hours: number;
  grace_days: number;
  sharing: {
    enabled: boolean;
    max_helpers: number;
    min_percent: number;
    max_percent_per_helper: number;
    max_total_percent: number;
  };
}

export const xaf = (n: number) => `${n.toLocaleString("fr-FR").replace(/ | /g, " ")} XAF`;

/** Lundi (heure de Douala) de la semaine contenant `d`, au format AAAA-MM-JJ. */
export function mondayOf(d = new Date()): string {
  const local = new Date(d.getTime() + 60 * 60 * 1000); // UTC+1
  const day = (local.getUTCDay() + 6) % 7;
  local.setUTCDate(local.getUTCDate() - day);
  return local.toISOString().slice(0, 10);
}
