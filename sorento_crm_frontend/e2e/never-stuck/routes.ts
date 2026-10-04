/**
 * Route enumeration and shared fixtures for the never-stuck smoke (guard G4,
 * `documentation/reports/AUDIT-never-stuck-2026-10-01.md` section 7).
 *
 * Routes are read off the file system, not off the menu: every `page.tsx` under
 * `app/(protected)` is a URL a user can deep-link to, menu entry or not, and a new page
 * joins the smoke the night it merges with nothing to register.
 */
import fs from 'node:fs';
import path from 'node:path';

export const PERSONAS = ['admin', 'restricted', 'expired'] as const;
export type Persona = (typeof PERSONAS)[number];

const FRONTEND_ROOT = path.resolve(__dirname, '..', '..');
const PROTECTED_ROOT = path.join(FRONTEND_ROOT, 'app', '(protected)');
export const STATE_DIR = path.join(FRONTEND_ROOT, 'e2e', '.never-stuck');

export const statePath = (persona: Persona) => path.join(STATE_DIR, `${persona}.json`);

/**
 * Metronic template directories: the demo shell the app was built on, never wired to a
 * feature, a permission or the backend. Same exclusion `loading-inventory.test.tsx` uses,
 * plus the two demo pages that sit beside them.
 */
export const EXCLUDED_PREFIXES = ['/public-profile', '/network', '/dark-sidebar', '/i18n-test'];

/**
 * Stands in for a dynamic segment this seed has no record for. A detail page opened on a
 * record that does not exist must still end in an honest state (not found, error), never
 * a skeleton: that is the audit's "skeleton forever on any error" class (rows 11, 15, 35).
 */
export const MISSING_RECORD_ID = '00000000-0000-4000-8000-000000000000';

export interface Seed {
  password: string;
  personas: Record<Persona, { email: string; id: string }>;
  restrictedSlugs: string[];
  /** Route template (`/user-management/users/[id]`) to a real record id. */
  records: Record<string, string>;
}

/** The manifest `scripts/seed_never_stuck_smoke.py` wrote, or null when not configured. */
export function loadSeed(): Seed | null {
  const file = process.env.NEVER_STUCK_SEED;
  if (!file || !fs.existsSync(file)) return null;
  return JSON.parse(fs.readFileSync(file, 'utf8')) as Seed;
}

/** Every `page.tsx` under `app/(protected)` as a route template, route groups removed. */
export function routeTemplates(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (entry.name === 'page.tsx') {
        const rel = path.relative(PROTECTED_ROOT, dir).split(path.sep);
        const segments = rel.filter((s) => s && !(s.startsWith('(') && s.endsWith(')')));
        out.push('/' + segments.join('/'));
      }
    }
  };
  walk(PROTECTED_ROOT);
  return [...new Set(out)]
    .filter((r) => !EXCLUDED_PREFIXES.some((p) => r === p || r.startsWith(p + '/')))
    .sort();
}

/** A template with every dynamic segment filled: the seeded record, else the missing id. */
export function concreteUrl(template: string, seed: Seed): string {
  const seeded = seed.records[template];
  return template.replace(/\[\[?\.{0,3}[^\]]+\]\]?/g, () => seeded ?? MISSING_RECORD_ID);
}
