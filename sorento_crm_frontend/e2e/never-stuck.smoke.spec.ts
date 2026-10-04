/**
 * Never-stuck smoke (guard G4): open every app route as an admin, as a restricted user
 * and with an expired session, and fail on any state that is not final and honest.
 * Standard: `documentation/reference/NEVER-STUCK-UI.md`. Owner-approved exception to the
 * no-new-Playwright-specs order (1 Oct 2026): this is the one spec it covers.
 *
 * A route fails when:
 * - admin / restricted: anything is still loading 15 s after navigation (a visible
 *   `[data-loading]` placeholder, or a spinner outside a button);
 * - admin / restricted: the page bounced to sign-in although the session is alive;
 * - restricted: an API call answered 403 and the page shows an empty / not-found state
 *   instead of `AccessDenied` (a refusal rendered as "nothing here");
 * - any persona: raw backend permission text (`Permission required: x.y.z`) is on screen;
 * - expired: the page is not on `/signin` within 5 s.
 *
 * Rows the audit already ranked and whose fix lane has not merged are listed in
 * `never-stuck/known-failures.json` with their audit row id and problem kinds (`route: "*"`
 * covers every route for that persona). Those kinds are reported without failing the
 * night; any other kind on the same route still fails. The summary names the entries that
 * passed so they get deleted.
 * - admin / restricted: landing on `/` without asking for it (the module-guard bounce).
 *
 * Run: `scripts/never-stuck-smoke.sh` boots, seeds and runs it end to end. Against a stack
 * already up and seeded:
 *   NEVER_STUCK_SEED=e2e/.never-stuck/seed.json \
 *     npx playwright test -c playwright.never-stuck.config.ts
 */
import fs from 'node:fs';
import path from 'node:path';
import { test, expect, type Page } from '@playwright/test';
import {
  PERSONAS,
  type Persona,
  concreteUrl,
  loadSeed,
  routeTemplates,
  statePath,
} from './never-stuck/routes';

const SETTLE_MS = 15_000;
const SIGNIN_MS = 5_000;

const RAW_PERMISSION_TEXT =
  /Permission required:|One of these permissions required|Module not enabled:/;
/**
 * Copy a page shows when it decided there is nothing. The app's empty states open a line
 * with "No" / "Nothing" ("No proforma invoice behind this container.", "No projects match",
 * "Nothing is quoted on this project yet"), case-sensitive and followed by a space so
 * "no" inside a word and labels like "No-reply alert" never match; plus not-found states.
 * Only read after a 403, so its breadth is bounded.
 */
const EMPTY_LINE = /(?:^|\n)[ \t]*((?:No|Nothing) [^\n]{0,80})/;
const NOT_FOUND = /\bnot found\b|doesn't exist|does not exist/i;
const emptyStateText = (text: string) => text.match(EMPTY_LINE)?.[1] ?? text.match(NOT_FOUND)?.[0];

/** The kinds of problem a route can have; a known entry tolerates only its own kinds. */
const KINDS = [
  'still loading',
  'document did not finish loading',
  'expired session not on /signin',
  'live session bounced to /signin',
  'redirected to /',
  'raw permission text',
  'refusal rendered as empty',
] as const;
const kindOf = (problem: string) => KINDS.find((k) => problem.startsWith(k)) ?? problem;

interface KnownFailure {
  persona: Persona;
  route: string;
  /** Problem kinds (from KINDS) this entry tolerates; any other problem still fails. */
  kinds: string[];
  audit: string;
  reason: string;
}
const KNOWN: KnownFailure[] = JSON.parse(
  fs.readFileSync(path.join(__dirname, 'never-stuck', 'known-failures.json'), 'utf8'),
).failures;

const seed = loadSeed();
const routes = routeTemplates();

/**
 * Visible loading placeholders, described (`skeleton in "Packing list"`) so a failure says
 * where to look. A spinner inside a button is a pending action, not a stuck page.
 */
async function visibleLoaders(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const found: string[] = [];
    document.querySelectorAll('[data-loading], .animate-spin').forEach((el) => {
      if (el.closest('button')) return;
      const box = el.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) return;
      if ((el as Element & { checkVisibility?: () => boolean }).checkVisibility?.() === false) return;
      const kind = el.getAttribute('data-slot') ?? (el.hasAttribute('data-loading') ? 'loader' : 'spinner');
      const region = el.closest('section, [role=tabpanel], [role=dialog], main, header, aside');
      const label = (region?.querySelector('h1, h2, h3, [role=tab][aria-selected=true]')?.textContent ?? '')
        .trim()
        .slice(0, 40);
      found.push(label ? `${kind} in "${label}"` : `${kind} in <${region?.tagName.toLowerCase() ?? 'body'}>`);
    });
    return found;
  });
}

/**
 * Settled = no loading placeholder on two reads 500 ms apart, inside the budget. Returns
 * what is still loading. Always reads at least once, and a budget that ran out before two
 * clear reads is not settled: it returns the last loaders seen (or says it never got to look).
 */
async function waitSettled(page: Page, budgetMs: number): Promise<string[]> {
  const deadline = Date.now() + budgetMs;
  let clearReads = 0;
  let lastSeen: string[] = ['no time left to check after the document loaded'];
  do {
    const now = await visibleLoaders(page).catch(() => ['page not readable']);
    if (now.length === 0) clearReads += 1;
    else {
      clearReads = 0;
      lastSeen = now;
    }
    if (clearReads >= 2) return [];
    await page.waitForTimeout(500);
  } while (Date.now() < deadline);
  return lastSeen;
}

const onSignIn = (url: string) => /\/signin(?:[/?#]|$)/.test(new URL(url).pathname + new URL(url).search);

for (const persona of PERSONAS) {
  test.describe(`never-stuck ${persona}`, () => {
    test.describe.configure({ mode: 'parallel' });
    test.skip(!seed, 'Set NEVER_STUCK_SEED to the manifest scripts/seed_never_stuck_smoke.py wrote');
    test.use({ storageState: seed ? statePath(persona) : undefined });

    for (const route of routes) {
      test(`${persona} ${route}`, async ({ page }) => {
        const known = KNOWN.find(
          (k) => k.persona === persona && (k.route === route || k.route === '*'),
        );

        const url = concreteUrl(route, seed!);
        const refused: string[] = [];
        page.on('response', (res) => {
          if (res.status() === 403 && res.url().includes('/api/v1/')) {
            refused.push(new URL(res.url()).pathname);
          }
        });

        const problems: string[] = [];
        const started = Date.now();
        // `load`, not `commit`: before the document has parsed there is nothing on screen,
        // and "no loader visible" on a blank page would read as settled.
        const loaded = await page
          .goto(url, { waitUntil: 'load', timeout: SETTLE_MS })
          .then(() => true)
          .catch(() => false);

        if (!loaded && !onSignIn(page.url())) {
          problems.push(`document did not finish loading in ${SETTLE_MS / 1000}s`);
        } else if (persona === 'expired') {
          const landed = await page
            .waitForURL((u) => onSignIn(u.toString()), {
              // Never 0: Playwright reads a 0 timeout as "no timeout".
              timeout: Math.max(1, SIGNIN_MS - (Date.now() - started)),
            })
            .then(() => true)
            .catch(() => false);
          const took = Date.now() - started;
          if (!landed || took > SIGNIN_MS) {
            problems.push(
              `expired session not on /signin within ${SIGNIN_MS / 1000}s (${(took / 1000).toFixed(1)}s, at ${page.url()})`,
            );
          }
        } else {
          const stuck = await waitSettled(page, SETTLE_MS - (Date.now() - started));
          if (stuck.length) {
            const what = [...new Set(stuck)].slice(0, 3).join(', ');
            problems.push(`still loading after ${SETTLE_MS / 1000}s at ${new URL(page.url()).pathname}: ${what}`);
          }
          if (onSignIn(page.url())) problems.push('live session bounced to /signin');
        }

        // A live persona that ends somewhere else (a missing record sent back to its list, a
        // module guard) reached a final state, but not on the page under test: say so in the
        // report, so a run where everything quietly lands on `/` cannot read as all green.
        const landedOn = new URL(page.url()).pathname;
        const redirected = persona !== 'expired' && !onSignIn(page.url()) && landedOn !== url;
        if (redirected) test.info().annotations.push({ type: 'redirected', description: landedOn });
        // Landing on `/` without asking for it is the ModuleRouteGuard signature: a run where
        // every module route bounces to the dashboard must not read as green (it did once,
        // on a database with no module enabled).
        if (redirected && landedOn === '/') problems.push(`redirected to / instead of rendering ${url}`);

        const text = await page.locator('body').innerText({ timeout: 2_000 }).catch(() => '');
        const raw = text.match(RAW_PERMISSION_TEXT);
        if (raw) problems.push(`raw permission text on screen: "${raw[0]}..."`);

        // Judged only on the page that was asked for: after a redirect the empty copy on
        // screen belongs to some other page.
        if (persona === 'restricted' && refused.length > 0 && !redirected) {
          const denied = await page.locator('[data-access-denied]').count();
          const empty = emptyStateText(text);
          if (denied === 0 && empty) {
            problems.push(
              `refusal rendered as empty ("${empty}") after 403 on ${[...new Set(refused)].join(', ')}`,
            );
          }
        }

        if (known) {
          // A ranked audit row whose fix lane is open: its own kinds of problem are reported,
          // not failed. Not `test.fail()`: some are timing-bound and flap, and a ratchet that
          // fails the night a flaky row happens to pass is noise. Any OTHER kind of problem
          // on the same route is a regression and still fails below.
          const tolerated = problems.filter((p) => known.kinds.includes(kindOf(p)));
          test.info().annotations.push({
            type: tolerated.length ? 'known-failure' : 'known-passed',
            description: `${known.audit}: ${tolerated.join('; ') || known.reason}`,
          });
          expect(problems.filter((p) => !tolerated.includes(p)), `${persona} ${url}`).toEqual([]);
          return;
        }
        expect(problems, `${persona} ${url}`).toEqual([]);
      });
    }
  });
}
