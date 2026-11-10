import { NextRequest, NextResponse } from "next/server";

/**
 * Protection HTTP Basic du backoffice deploye (Railway), en attendant la vraie
 * session administrateur. En production, l'absence d'identifiants ferme l'acces.
 */
function sameText(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  }
  return diff === 0;
}

function hasValidCredentials(header: string, user: string, password: string): boolean {
  if (!header.startsWith("Basic ")) return false;
  let decoded: string;
  try {
    decoded = atob(header.slice(6));
  } catch {
    return false;
  }
  const sep = decoded.indexOf(":");
  return sep >= 0 && sameText(decoded.slice(0, sep), user) && sameText(decoded.slice(sep + 1), password);
}

export function middleware(req: NextRequest) {
  const user = process.env.BACKOFFICE_BASIC_AUTH_USER;
  const password = process.env.BACKOFFICE_BASIC_AUTH_PASSWORD;
  if (!user || !password) {
    if (process.env.NODE_ENV === "production") return new NextResponse("Service non configure", { status: 503 });
    return NextResponse.next();
  }
  if (hasValidCredentials(req.headers.get("authorization") ?? "", user, password)) {
    return NextResponse.next();
  }
  return new NextResponse("Authentification requise", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Carlinq Backoffice", charset="UTF-8"' },
  });
}

export const config = {
  // Tout sauf la sonde de sante Railway et les fichiers statiques Next.
  matcher: ["/((?!api/health|_next/static|_next/image|favicon.ico).*)"],
};
