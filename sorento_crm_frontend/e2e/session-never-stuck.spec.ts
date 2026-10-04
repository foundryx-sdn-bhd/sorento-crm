/**
 * SESSION-NEVER-STUCK: a dead session or a stale view-as never leaves the app spinning.
 *
 * Run against a local or crew test copy:
 *   PORTAL_E2E_BASE_URL=http://localhost:3000 \
 *   IMPERSONATION_E2E_EMAIL=admin@example.com \
 *   IMPERSONATION_E2E_PASSWORD='...' \
 *   IMPERSONATION_E2E_TARGET_EMAIL=someone@example.com \
 *   npx playwright test e2e/session-never-stuck.spec.ts
 *
 * Skipped when the credentials are not set, like e2e/impersonation.spec.ts. Every
 * test starts view-as from the Users list (sidebar, never a deep link), then kills
 * the session or the view-as from OUTSIDE the page, the way it dies in real life:
 * revoked on the server, cookie unusable, or view-as stopped in another tab.
 */
import { test, expect, Page } from '@playwright/test';

const EMAIL = process.env.IMPERSONATION_E2E_EMAIL;
const PASSWORD = process.env.IMPERSONATION_E2E_PASSWORD;
const TARGET_EMAIL = process.env.IMPERSONATION_E2E_TARGET_EMAIL;

/** The owner's bar: sign-in must be on screen within this, never a spinner. */
const SIGNIN_WITHIN_MS = 10_000;
/** The user record's gear menu (DetailActionsMenu). */
const USER_ACTIONS = /actions|options/i;

test.skip(
  !EMAIL || !PASSWORD || !TARGET_EMAIL,
  'Set IMPERSONATION_E2E_EMAIL, IMPERSONATION_E2E_PASSWORD, and IMPERSONATION_E2E_TARGET_EMAIL',
);

async function login(page: Page) {
  await page.goto('/signin');
  await page.waitForLoadState('networkidle');
  await page.locator('input[name="email"]').fill(EMAIL!);
  await page.locator('input[type="password"]').first().fill(PASSWORD!);
  await page.getByRole('button', { name: /^continue$/i }).click();
  await page.waitForURL((url) => !/\/signin/.test(url.toString()), { timeout: 30_000 });
}

async function startViewAsFromSidebar(page: Page) {
  // A view-as left open on the server (an earlier failed run) comes back on sign-in.
  await asAnotherTab(page, '/api/v1/user-management/impersonation/stop');
  await page.evaluate(() => localStorage.removeItem('impersonation-session-v1'));
  await page.reload();
  // Exact names: each sidebar entry also has a "Pin ... to quick access" button, and the
  // header mega menu carries hidden copies of the same entries.
  const entry = (role: 'button' | 'link', name: string) =>
    page.getByRole(role, { name, exact: true }).filter({ visible: true }).first();
  await entry('button', 'Users & Access').click();
  const people = entry('button', 'People');
  if (await people.count()) await people.click();
  await entry('link', 'Administrative Users').click();
  await page.waitForURL(/\/user-management\/users/);
  const row = page.locator('tr', { hasText: TARGET_EMAIL! }).first();
  await expect(row).toBeVisible({ timeout: 15_000 });
  await row.getByText(TARGET_EMAIL!).click();
  await page.waitForURL(/\/user-management\/users\/[^/]+/);
  await page.getByRole('button', { name: USER_ACTIONS }).filter({ visible: true }).first().click();
  await page.getByRole('menuitem', { name: 'Impersonate user' }).click();
  // Starting view-as reloads the page; let that land before anything reads it.
  await Promise.all([page.waitForEvent('load'), page.getByTestId('impersonate-confirm').click()]);
  await expect(page.getByTestId('impersonation-banner')).toBeVisible({ timeout: 15_000 });
  await page.waitForLoadState('networkidle');
}

/** A request made the way another tab would: the same cookie, no view-as header. */
async function asAnotherTab(page: Page, path: string) {
  return page.evaluate(async (p) => {
    const { token } = await (await fetch('/api/auth/token')).json();
    const res = await fetch(p, { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
    return res.status;
  }, path);
}

function countRequests(page: Page) {
  const seen = { signinDocuments: 0, api: 0 };
  page.on('request', (r) => {
    if (r.isNavigationRequest() && r.url().includes('/signin')) seen.signinDocuments += 1;
    if (r.url().includes('/api/v1/') || r.url().includes('/api/auth/token')) seen.api += 1;
  });
  return seen;
}

test('session revoked mid view-as: the next click lands on sign-in once, view-as cleared', async ({ page }) => {
  await login(page);
  await startViewAsFromSidebar(page);
  expect(await asAnotherTab(page, '/api/v1/auth/logout')).toBe(200);

  const seen = countRequests(page);
  const started = Date.now();
  await page.getByRole('link', { name: 'Dashboards', exact: true }).filter({ visible: true }).first().click();
  await page.waitForURL(/\/signin\?callbackUrl=/, { timeout: SIGNIN_WITHIN_MS });

  expect(Date.now() - started).toBeLessThan(SIGNIN_WITHIN_MS);
  // A fixed wait on purpose: it asserts a SECOND navigation does not follow.
  await page.waitForTimeout(1500);
  expect(seen.signinDocuments).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem('impersonation-session-v1'))).toBeNull();
});

test('cookie no longer usable: a reload lands on sign-in, no request storm', async ({ page }) => {
  await login(page);
  await startViewAsFromSidebar(page);
  // What /api/auth/token answers when the NextAuth cookie is gone or was overwritten
  // by another localhost copy on a different port.
  await page.route('**/api/auth/token', (r) =>
    r.fulfill({ status: 401, contentType: 'application/json', body: '{"error":"No valid session"}' }),
  );

  const seen = countRequests(page);
  await page.reload();
  await page.waitForURL(/\/signin\?callbackUrl=/, { timeout: SIGNIN_WITHIN_MS });

  // A fixed wait on purpose: it asserts no second navigation and no storm follow.
  await page.waitForTimeout(1500);
  expect(seen.signinDocuments).toBe(1);
  // Main sent 9 token reads + 19 bearer-less calls in 1.5 s and never left the page.
  expect(seen.api).toBeLessThan(6);
});

test('view-as stopped in another tab: banner goes, one notice, still signed in', async ({ page }) => {
  await login(page);
  await startViewAsFromSidebar(page);
  expect(await asAnotherTab(page, '/api/v1/user-management/impersonation/stop')).toBe(200);

  await page.getByRole('link', { name: 'Dashboards', exact: true }).filter({ visible: true }).first().click();

  await expect(page.getByText('View-as ended - you are seeing your own data')).toBeVisible({
    timeout: SIGNIN_WITHIN_MS,
  });
  await expect(page.getByTestId('impersonation-banner')).toHaveCount(0);
  expect(page.url()).not.toMatch(/\/signin/);
});
