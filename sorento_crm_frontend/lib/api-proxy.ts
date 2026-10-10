/**
 * Helper to proxy Next.js API routes to FastAPI backend.
 * This allows Next.js routes to forward requests to FastAPI without duplicating logic.
 */
import { NextRequest, NextResponse } from 'next/server';
import { getToken } from 'next-auth/jwt';

import { sessionTokenCookieName } from '@/lib/auth-cookie';
import { forwardedForHeader } from '@/lib/forwarded-for';

/**
 * Base URL for server-side calls from Next.js to FastAPI (not browser-facing).
 *
 * Resolution order:
 *   1. FASTAPI_INTERNAL_URL - runtime override for server-side calls
 *   2. NEXT_PUBLIC_API_URL - baked at build time
 *   3. production            → http://backend:8000  (Docker compose service name)
 *   4. development           → throw (avoid silent localhost:8000 fallback that breaks
 *                              multi-instance dev where alt ports are in use)
 */
export function getBackendBaseUrl(): string {
  const url =
    process.env.FASTAPI_INTERNAL_URL?.replace(/\/$/, '') ||
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '');
  if (url) return url;
  if (process.env.NODE_ENV === 'production') {
    return 'http://backend:8000';
  }
  throw new Error(
    'Backend URL not configured. Set NEXT_PUBLIC_API_URL (or FASTAPI_INTERNAL_URL for server-side overrides).',
  );
}

/**
 * Get the opaque FastAPI session token stored in the NextAuth cookie.
 * FastAPI owns + validates it; we just forward it as a Bearer token.
 */
export async function getFastAPITokenForRequest(
  request: NextRequest,
): Promise<string | null> {
  const secret = process.env.NEXTAUTH_SECRET;
  if (!secret) return null;

  try {
    const tokenPayload = await getToken({
      req: request,
      secret,
      raw: false,
      cookieName: sessionTokenCookieName(),
    });
    return tokenPayload?.apiToken ?? null;
  } catch {
    return null;
  }
}

/**
 * Proxy a request to FastAPI backend
 */
export async function proxyToFastAPI(
  request: NextRequest,
  fastApiPath: string,
  options: {
    method?: string;
    body?: any;
    queryParams?: Record<string, string>;
    requireAuth?: boolean; // If false, allows requests without NextAuth token
  } = {}
): Promise<NextResponse> {
  let apiUrl: string;
  try {
    apiUrl = getBackendBaseUrl();
  } catch (e) {
    console.error('FastAPI proxy: backend URL not configured', e);
    return NextResponse.json(
      { message: 'Backend URL not configured on this deployment' },
      { status: 500 },
    );
  }
  const method = options.method || request.method;

  // Build URL with query params
  const url = new URL(`${apiUrl}${fastApiPath}`);
  if (options.queryParams) {
    Object.entries(options.queryParams).forEach(([key, value]) => {
      if (value) url.searchParams.append(key, value);
    });
  }
  // Also include original query params
  request.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.append(key, value);
  });

  // Prepare headers - forward all original headers that might be needed
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  
  // SORENTO-CLIENT-IP: the browser's forwarded chain, so per-IP limits see the real client.
  Object.assign(headers, forwardedForHeader(request.headers));

  // Forward X-API-Key header if present (for API key authentication)
  const apiKey = request.headers.get('X-API-Key');
  if (apiKey) {
    headers['X-API-Key'] = apiKey;
  }

  // Forward X-Portal-Token header if present (for public user-submission portal)
  const portalToken = request.headers.get('X-Portal-Token');
  if (portalToken) {
    headers['X-Portal-Token'] = portalToken;
  }

  // Forward admin impersonation header so /api/v1/* dispatched via Next.js proxy
  // routes still see the active session.
  const impersonateUserId = request.headers.get('X-Impersonate-User-Id');
  if (impersonateUserId) {
    headers['X-Impersonate-User-Id'] = impersonateUserId;
  }

  // Forward Authorization header if present (for API keys or Bearer tokens)
  const authHeader = request.headers.get('Authorization');
  if (authHeader) {
    headers['Authorization'] = authHeader;
  } else {
    // Try to get NextAuth token if auth is required and no API key provided
    const requireAuth = options.requireAuth !== false; // Default to true
    if (requireAuth && !apiKey) {
      const token = await getFastAPITokenForRequest(request);
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
    }
  }

  // Get body - avoid sending empty string so backend doesn't try to parse invalid JSON
  let body: string | undefined;
  if (options.body !== undefined && options.body !== null) {
    body = JSON.stringify(options.body);
  } else if (method !== 'GET' && method !== 'HEAD') {
    try {
      const raw = await request.text();
      body = raw && raw.trim() ? raw : undefined;
    } catch {
      // No body
    }
  }

  try {
    const response = await fetch(url.toString(), {
      method,
      headers,
      ...(body !== undefined && body !== '' ? { body } : {}),
    });

    if (response.status === 204) {
      return new NextResponse(null, { status: 204 });
    }
    const data = await response.json().catch(() => ({}));

    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error('FastAPI proxy error:', error);
    return NextResponse.json(
      { message: 'Failed to connect to backend service' },
      { status: 500 }
    );
  }
}
