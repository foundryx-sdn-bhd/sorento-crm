import { NextRequest, NextResponse } from 'next/server';

import { backendBaseUrl } from '../../[...nextauth]/auth-options';
import { devLoginAllowed, devLoginBackendHeaders } from '@/lib/dev-login';

/**
 * DEV-LOGIN-BYPASS: the "Sign in as" picker's user list (PLAN-dev-login-bypass-03oct.md).
 * A plain 404 unless every frontend guard holds; the backend then applies its own guards and
 * the DEV_AUTO_LOGIN_USERS allowlist. Returns name, email and role name only.
 */
export async function GET(request: NextRequest) {
  if (!devLoginAllowed(request.headers.get('host'))) {
    return NextResponse.json({ detail: 'Not Found' }, { status: 404 });
  }
  try {
    const res = await fetch(`${backendBaseUrl()}/api/v1/auth/dev-login/users`, {
      cache: 'no-store',
      headers: devLoginBackendHeaders(),
    });
    if (!res.ok) {
      return NextResponse.json({ detail: 'Not Found' }, { status: 404 });
    }
    const data = await res.json();
    const users = Array.isArray(data?.users)
      ? (data.users as Array<Record<string, unknown>>).map((u) => ({
          email: String(u.email ?? ''),
          name: typeof u.name === 'string' ? u.name : null,
          role_name: typeof u.role_name === 'string' ? u.role_name : null,
        }))
      : [];
    return NextResponse.json({ users });
  } catch {
    return NextResponse.json({ detail: 'Not Found' }, { status: 404 });
  }
}
