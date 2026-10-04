/**
 * Signs in the three never-stuck smoke personas once and saves each browser state, so the
 * ~1100 route visits reuse a cookie instead of signing in per test (the login throttle and
 * the run time both care).
 *
 * The expired persona signs in, then its FastAPI session is revoked through
 * `POST /api/v1/auth/logout`. Its NextAuth cookie stays valid (NextAuth never re-validates
 * the FastAPI token), so every route visit starts in exactly the state the owner reported:
 * a signed-in shell whose every API call answers 401.
 */
import fs from 'node:fs';
import { request, type FullConfig } from '@playwright/test';
import { PERSONAS, STATE_DIR, loadSeed, statePath } from './routes';

async function signIn(baseURL: string, email: string, password: string, out: string) {
  const ctx = await request.newContext({ baseURL });
  const csrf = await ctx.get('/api/auth/csrf');
  if (!csrf.ok()) throw new Error(`csrf: HTTP ${csrf.status()}`);
  const { csrfToken } = (await csrf.json()) as { csrfToken: string };
  const res = await ctx.post('/api/auth/callback/credentials', {
    form: { csrfToken, email, password, json: 'true', callbackUrl: `${baseURL}/` },
    maxRedirects: 0,
  });
  if (res.status() >= 400) throw new Error(`sign-in ${email}: HTTP ${res.status()}`);
  const token = await ctx.get('/api/auth/token');
  if (!token.ok()) throw new Error(`sign-in ${email}: no session (token HTTP ${token.status()})`);
  const { token: apiToken } = (await token.json()) as { token: string };
  await ctx.storageState({ path: out });
  return { ctx, apiToken };
}

export default async function globalSetup(config: FullConfig) {
  const seed = loadSeed();
  if (!seed) return; // the spec skips itself; nothing to prepare
  const baseURL = config.projects[0].use.baseURL ?? 'http://localhost:3000';
  fs.mkdirSync(STATE_DIR, { recursive: true });

  for (const persona of PERSONAS) {
    const { email } = seed.personas[persona];
    const { ctx, apiToken } = await signIn(baseURL, email, seed.password, statePath(persona));
    if (persona === 'expired') {
      const out = await ctx.post('/api/v1/auth/logout', {
        headers: { Authorization: `Bearer ${apiToken}` },
      });
      if (!out.ok()) throw new Error(`revoking the expired persona's session: HTTP ${out.status()}`);
    }
    await ctx.dispose();
  }
}
