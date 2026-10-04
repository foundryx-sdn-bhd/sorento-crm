import { NextAuthOptions, Session, User } from 'next-auth';
import { JWT } from 'next-auth/jwt';
import CredentialsProvider from 'next-auth/providers/credentials';

import { sessionTokenCookieName } from '@/lib/auth-cookie';
import {
  devLoginAllowed,
  devLoginBackendHeaders,
  devLoginConfigured,
  hostFromHeaders,
} from '@/lib/dev-login';

/**
 * NextAuth is a thin shell over FastAPI-owned auth.
 *
 * There is NO database adapter and NO Prisma here. `authorize()` posts to the
 * FastAPI `/api/v1/auth/login`, which verifies the password, mints a rolling
 * `user_sessions` row, and returns an opaque session token. We stash that token
 * (`apiToken`) inside the NextAuth httpOnly cookie; every server-side proxy to
 * FastAPI forwards it as `Authorization: Bearer <apiToken>`. FastAPI is the sole
 * source of truth for session validity  - revocation and the sliding 30-day
 * window live there (see PLAN-staff-rolling-sessions-fastapi-auth.md).
 *
 * Every sign-in is the 30-day sliding session (no remember-me choice, same as
 * phone sign-in and the portal). The cookie maxAge (30d) only has to outlive the
 * FastAPI session; FastAPI decides the real expiry and revocation.
 */

export function backendBaseUrl(): string {
  const url =
    process.env.FASTAPI_INTERNAL_URL?.replace(/\/$/, '') ||
    process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, '');
  if (url) return url;
  if (process.env.NODE_ENV === 'production') return 'http://backend:8000';
  throw new Error('Backend URL not configured. Set NEXT_PUBLIC_API_URL.');
}

const THIRTY_DAYS_SECONDS = 30 * 24 * 60 * 60;

/**
 * Multi-company isolation (PLAN §3.11  - Q1 = backend-call-at-login): fetch the
 * user's switchable companies + active company from FastAPI, using the opaque
 * session token minted by /api/v1/auth/login as the Bearer. Resilient by design:
 * a failure never blocks login  - we return an empty context and the app falls
 * back to the backend resolver's last_active default on the first /my-context.
 */
async function fetchCompanyContext(
  apiToken: string,
): Promise<{ active_company_id?: string; company_grants: string[] }> {
  try {
    const res = await fetch(`${backendBaseUrl()}/api/v1/system/companies/my-context`, {
      headers: { Authorization: `Bearer ${apiToken}` },
    });
    if (!res.ok) return { company_grants: [] };
    const data = await res.json().catch(() => ({}));
    const grants = Array.isArray(data?.companies)
      ? (data.companies as Array<{ id?: string }>).map((c) => c.id).filter(Boolean)
      : [];
    return {
      active_company_id: (data?.active_company_id as string | null) ?? undefined,
      company_grants: grants as string[],
    };
  } catch (e) {
    console.warn('[next-auth][warn] company my-context fetch failed at login', e);
    return { company_grants: [] };
  }
}

/**
 * DEV-LOGIN-BYPASS (PLAN-dev-login-bypass-03oct.md): passwordless sign-in for LOCAL test copies.
 * Registered only when `devLoginConfigured()` (server-only `DEV_AUTO_LOGIN=true` and not a
 * production build), and `authorize()` re-checks the browser Host. Takes only an email; the
 * backend's own guards and allowlist decide, and it mints the SAME session `/auth/login` does.
 */
export function devLoginProvider() {
  return CredentialsProvider({
    id: 'dev-login',
    name: 'Dev sign-in',
    credentials: {
      email: { label: 'Email', type: 'text' },
    },
    async authorize(credentials, req) {
      if (!devLoginAllowed(hostFromHeaders(req?.headers))) {
        throw new Error(JSON.stringify({ code: 404, message: 'Dev sign-in is not available.' }));
      }
      if (!credentials?.email) {
        throw new Error(JSON.stringify({ code: 400, message: 'Choose a user.' }));
      }

      let res: Response;
      try {
        res = await fetch(`${backendBaseUrl()}/api/v1/auth/dev-login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...devLoginBackendHeaders() },
          body: JSON.stringify({ email: credentials.email }),
        });
      } catch {
        throw new Error(
          JSON.stringify({ code: 503, message: 'Unable to reach the server. Please try again.' }),
        );
      }

      if (!res.ok) {
        throw new Error(
          JSON.stringify({ code: res.status, message: 'Dev sign-in is not available.' }),
        );
      }
      const data = await res.json();

      return {
        id: data.id,
        email: data.email,
        name: data.name || 'Anonymous',
        avatar: data.avatar ?? null,
        status: data.status,
        roleId: data.role_id || (data.role_ids?.[0] ?? null),
        roleIds: data.role_ids ?? (data.role_id ? [data.role_id] : []),
        roleName: data.role_name ?? null,
        apiToken: data.token,
        homePath: data.home_path ?? null,
      } as User;
    },
  });
}

const devLoginOn = devLoginConfigured();
if (devLoginOn) {
  console.warn(
    '!!! DEV_AUTO_LOGIN is ACTIVE: passwordless dev sign-in is registered. It only answers on a ' +
      'dev server bound to 127.0.0.1 for localhost hosts. Never run this in production. !!!',
  );
}

const authOptions: NextAuthOptions = {
  providers: [
    CredentialsProvider({
      name: 'Credentials',
      credentials: {
        email: { label: 'Email', type: 'text' },
        password: { label: 'Password', type: 'password' },
      },
      async authorize(credentials) {
        if (!credentials || !credentials.email || !credentials.password) {
          throw new Error(
            JSON.stringify({ code: 400, message: 'Please enter both email and password.' }),
          );
        }

        let res: Response;
        try {
          res = await fetch(`${backendBaseUrl()}/api/v1/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              email: credentials.email,
              password: credentials.password,
            }),
          });
        } catch {
          throw new Error(
            JSON.stringify({ code: 503, message: 'Unable to reach the server. Please try again.' }),
          );
        }

        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const message =
            (typeof data?.detail === 'string' && data.detail) ||
            data?.detail?.message ||
            'Invalid credentials.';
          throw new Error(JSON.stringify({ code: res.status, message }));
        }

        return {
          id: data.id,
          email: data.email,
          name: data.name || 'Anonymous',
          avatar: data.avatar ?? null,
          status: data.status,
          roleId: data.role_id || (data.role_ids?.[0] ?? null),
          roleIds: data.role_ids ?? (data.role_id ? [data.role_id] : []),
          roleName: data.role_name ?? null,
          // Opaque FastAPI session token  - carried in the NextAuth cookie.
          apiToken: data.token,
          // Where this user lands after signing in (AC-28) - a salesperson's
          // portal home, the CRM home for anyone with a permission, else the
          // portal home. Null on a backend that predates this field.
          homePath: data.home_path ?? null,
        } as User;
      },
    }),
    CredentialsProvider({
      id: 'phone-otp',
      name: 'Phone',
      credentials: {
        phone: { label: 'Phone', type: 'text' },
        code: { label: 'Code', type: 'text' },
      },
      async authorize(credentials) {
        if (!credentials?.phone || !credentials?.code) {
          throw new Error(
            JSON.stringify({ code: 400, message: 'Enter the verification code.' }),
          );
        }

        let res: Response;
        try {
          res = await fetch(`${backendBaseUrl()}/api/v1/auth/phone/verify`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              phone: credentials.phone,
              code: credentials.code,
            }),
          });
        } catch {
          throw new Error(
            JSON.stringify({ code: 503, message: 'Unable to reach the server. Please try again.' }),
          );
        }

        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          // /api/v1/auth/phone/verify answers CODE_EXPIRED / CODE_WRONG / RATE_LIMITED
          // as a flat { code, message, ... } body (not nested under `detail`) -
          // see documentation/plans/identity/s1-contract.md.
          const message =
            (typeof data?.message === 'string' && data.message) ||
            (typeof data?.detail === 'string' && data.detail) ||
            data?.detail?.message ||
            'That code is not right.';
          throw new Error(JSON.stringify({ code: res.status, message }));
        }

        return {
          id: data.id,
          email: data.email,
          name: data.name || 'Anonymous',
          avatar: data.avatar ?? null,
          status: data.status,
          roleId: data.role_id || (data.role_ids?.[0] ?? null),
          roleIds: data.role_ids ?? (data.role_id ? [data.role_id] : []),
          roleName: data.role_name ?? null,
          apiToken: data.token,
          homePath: data.home_path ?? null,
        } as User;
      },
    }),
    ...(devLoginOn ? [devLoginProvider()] : []),
  ],
  session: {
    strategy: 'jwt',
    maxAge: THIRTY_DAYS_SECONDS,
  },
  callbacks: {
    async jwt({
      token,
      user,
      session,
      trigger,
    }: {
      token: JWT;
      user: User;
      session?: Session;
      trigger?: 'signIn' | 'signUp' | 'update';
    }) {
      if (trigger === 'update' && session) {
        // A company switch re-mints the token: useSession().update({ active_company_id })
        // sends a bare object (no `user`), so read it off the session root.
        const s = session as { active_company_id?: unknown };
        if (typeof s.active_company_id === 'string') {
          token.active_company_id = s.active_company_id;
        }
        if (session.user) {
          // Merge profile fields from update(); keep apiToken/exp/sub intact.
          const u = session.user as Record<string, unknown>;
          if (typeof u.avatar === 'string' || u.avatar === null) {
            token.avatar = u.avatar as string | null | undefined;
          }
          if (typeof u.name === 'string') token.name = u.name;
          if (typeof u.email === 'string') token.email = u.email;
          if (typeof u.id === 'string') token.id = u.id;
          if (Array.isArray(u.roles)) {
            const ids = (u.roles as { id: string }[]).map((r) => r.id);
            token.roleIds = ids;
            token.roleId = ids[0] ?? null;
          }
        }
      } else if (user) {
        token.id = (user.id || token.sub) as string;
        token.email = user.email;
        token.name = user.name;
        token.avatar = user.avatar;
        token.status = user.status;
        token.roleId = user.roleId;
        token.roleIds = user.roleIds ?? (user.roleId ? [user.roleId] : []);
        token.roleName = user.roleName ?? null;
        token.apiToken = (user as { apiToken?: string }).apiToken;
        token.homePath = (user as { homePath?: string | null }).homePath ?? null;

        // Multi-company: seed the active-company claim + grant ids at login.
        const apiToken = (user as { apiToken?: string }).apiToken;
        if (apiToken) {
          const ctx = await fetchCompanyContext(apiToken);
          token.active_company_id = ctx.active_company_id;
          token.company_grants = ctx.company_grants;
        } else {
          token.active_company_id = undefined;
          token.company_grants = [];
        }
      }

      return token;
    },
    async session({ session, token }: { session: Session; token: JWT }) {
      if (session.user) {
        session.user.id = token.id;
        session.user.email = token.email;
        session.user.name = token.name;
        session.user.avatar = token.avatar;
        session.user.status = token.status;
        session.user.roleId = token.roleId;
        session.user.roleIds = token.roleIds ?? (token.roleId ? [token.roleId] : []);
        session.user.roleName = token.roleName;
        session.user.active_company_id = token.active_company_id;
        session.user.company_grants = token.company_grants ?? [];
        session.user.homePath = token.homePath ?? null;
      }
      return session;
    },
  },
  pages: {
    signIn: '/signin',
  },
  // Per-instance cookie names so two local instances on different ports
  // (localhost:3000 vs localhost:3001) don't overwrite each other's session.
  // Browsers scope cookies by host only  - port is ignored. Single source of
  // truth: sessionTokenCookieName() in lib/auth-cookie.ts.
  cookies: (() => {
    const suffix = process.env.NEXTAUTH_COOKIE_SUFFIX
      ? `.${process.env.NEXTAUTH_COOKIE_SUFFIX}`
      : '';
    if (!suffix) return undefined;
    const secure = (process.env.NEXTAUTH_URL ?? '').startsWith('https://');
    const prefix = secure ? '__Secure-' : '';
    return {
      sessionToken: {
        name: sessionTokenCookieName(),
        options: { httpOnly: true, sameSite: 'lax', path: '/', secure },
      },
      callbackUrl: {
        name: `${prefix}next-auth.callback-url${suffix}`,
        options: { sameSite: 'lax', path: '/', secure },
      },
      csrfToken: {
        name: `${secure ? '__Host-' : ''}next-auth.csrf-token${suffix}`,
        options: { httpOnly: true, sameSite: 'lax', path: '/', secure },
      },
    };
  })(),
  logger: {
    error(code, metadata) {
      if (code === 'CLIENT_FETCH_ERROR') {
        const msg =
          typeof metadata === 'object' &&
          metadata !== null &&
          'error' in metadata &&
          metadata.error instanceof Error
            ? metadata.error.message
            : metadata instanceof Error
              ? metadata.message
              : metadata;
        console.warn(
          '[next-auth][warn] Session fetch failed. If this persists, check that the app and NEXTAUTH_URL use the same origin.',
          msg,
        );
        return;
      }
      console.error(`[next-auth][error][${code}]`, metadata);
    },
    warn(code) {
      console.warn(`[next-auth][warn][${code}]`);
    },
    debug(code, metadata) {
      if (process.env.NODE_ENV === 'development') {
        console.debug(`[next-auth][debug][${code}]`, metadata);
      }
    },
  },
};

export default authOptions;
