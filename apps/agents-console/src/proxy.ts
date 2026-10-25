import { NextRequest, NextResponse } from "next/server";

/** A fresh nonce binds the initial document and framework scripts to this response. Chat pages must stay dynamic. */
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(24))).toString("base64");
  const development = process.env.NODE_ENV === "development";
  const policy = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${development ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src 'self'${development ? " ws: wss:" : ""}`,
    "worker-src 'self' blob:",
    "media-src 'self' blob:",
    "frame-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
  const headers = new Headers(request.headers);
  headers.set("x-nonce", nonce);
  headers.set("Content-Security-Policy", policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set("Content-Security-Policy", policy);
  // Enforcement waits for the model renderer and framework sink audit. Do not silently install a permissive default policy.
  response.headers.set("Content-Security-Policy-Report-Only", "require-trusted-types-for 'script'");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set("Permissions-Policy", "camera=(), geolocation=()");
  return response;
}

export const config = {
  matcher: ["/", "/((?!api|_next/static|_next/image|favicon.svg|manifest.webmanifest|sw.js|offline.html).*)"],
};
