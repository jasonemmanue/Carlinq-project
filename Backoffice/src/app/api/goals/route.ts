import { NextResponse } from "next/server";

import { api, ApiError } from "@/lib/api";

/**
 * Route Handler des actions admin sur les objectifs : le navigateur appelle
 * cette route, le serveur Next.js appelle l'API avec le jeton admin.
 *   { action: "close-week", week_start: "2026-09-28" }
 *   { action: "settle", goal_id: 12 }
 */
export async function POST(req: Request) {
  // Un autre site ne peut pas declencher un versement avec la session du navigateur.
  const origin = req.headers.get("origin");
  if (origin && new URL(origin).host !== req.headers.get("host")) {
    return NextResponse.json({ error: { code: "FORBIDDEN_ORIGIN", message: "Origine refusee" } }, { status: 403 });
  }
  const body = await req.json();
  try {
    if (body.action === "close-week") {
      return NextResponse.json(await api.post("/admin/goals/close-week", { week_start: body.week_start }));
    }
    if (body.action === "settle") {
      return NextResponse.json(await api.post(`/admin/goals/${Number(body.goal_id)}/settle`));
    }
    return NextResponse.json({ error: { code: "UNKNOWN_ACTION", message: "Action inconnue" } }, { status: 400 });
  } catch (e) {
    const err = e instanceof ApiError ? e : new ApiError(502, "API_UNREACHABLE", "API injoignable");
    return NextResponse.json({ error: { code: err.code, message: err.message } }, { status: err.status });
  }
}
