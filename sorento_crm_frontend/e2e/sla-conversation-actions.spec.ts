/**
 * Conversation SLA tracking actions end-to-end - post-schema-migration verification.
 *
 * Guards migrations 298/299/300 (market_segments uuid PK, notifications dedup split,
 * conversation_sla_tracking.source_entity_id -> uuid). Drives the three write actions
 * on the Conversation SLA Tracking detail page through the real browser -> FE hook ->
 * service -> api-client -> FastAPI -> Postgres round-trip:
 * - Mark as responded   -> POST /{id}/test-overrides {is_responded:true}
 * - Escalate            -> POST /{id}/escalate         (tier N -> N+1)
 * - Mark as resolved    -> POST /{id}/test-overrides {is_resolved:true}
 *
 * NAVIGATION: sidebar only (SLA Management -> Conversation SLA Tracking), never deep-link
 * (project memory feedback_playwright_via_sidebar).
 *
 * SEED: a dedicated tracker for contact "E2E SLA Verify" (+60110000557) must exist,
 * tier 1, unresolved, unresponded. Seeded out-of-band before the run so real customer
 * trackers are never mutated.
 *
 * Creds: reuses REQUEST_BATCH_E2E_* (the only committed-env login) - mapped in via the
 * npx invocation. Skips if unset.
 */
import { test, expect, type Page } from '@playwright/test';

const EMAIL = process.env.REQUEST_BATCH_E2E_EMAIL;
const PASSWORD = process.env.REQUEST_BATCH_E2E_PASSWORD;
const CONTACT = 'E2E SLA Verify';

test.skip(!EMAIL || !PASSWORD, 'Set REQUEST_BATCH_E2E_* env vars to run this spec.');

async function login(page: Page) {
  await page.goto('/');
  const email = page.locator('input[type="email"], input[name="email"]').first();
  await expect(email).toBeVisible({ timeout: 20_000 });
  await email.fill(EMAIL!);
  await page
    .locator('input[type="password"], input[name="password"]')
    .first()
    .fill(PASSWORD!);
  await page.getByRole('button', { name: /sign in|log in|continue/i }).click();
  await expect(page).toHaveURL(/\/$|\/dashboard|home/i, { timeout: 30_000 });
}

async function openActions(page: Page) {
  await page.locator('button[title="Actions"]').first().click();
}

test.describe('Conversation SLA tracking actions (post-migration)', () => {
  test('mark responded -> escalate -> mark resolved on a seeded tracker', async ({ page }) => {
    await login(page);

    // Sidebar: SLA Management -> Conversation SLA Tracking.
    await page.goto('/');
    await page.getByRole('button', { name: /SLA Management/i }).first().click();
    await page.getByRole('link', { name: /Conversation SLA Tracking/i }).first().click();
    await expect(page).toHaveURL(/\/sla-management\/conversation-sla-tracking$/, { timeout: 20_000 });

    // Find the seeded row by contact name, open it.
    await page.getByPlaceholder(/Search by contact phone or name/i).fill(CONTACT);
    const row = page.getByRole('row', { name: new RegExp(CONTACT, 'i') }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.click();
    await expect(page).toHaveURL(/\/sla-management\/conversation-sla-tracking\/[0-9a-f-]{36}/, {
      timeout: 15_000,
    });
    await expect(page.getByText(CONTACT).first()).toBeVisible({ timeout: 15_000 });

    // ---- 1. Mark as responded -> POST /test-overrides {is_responded:true} ----
    await openActions(page);
    await page.getByRole('menuitem', { name: /Mark as responded/i }).click();
    await expect(page.getByRole('alertdialog').getByText(/Mark as responded/i)).toBeVisible();
    {
      const wait = page.waitForResponse(
        (r) => /\/test-overrides$/.test(new URL(r.url()).pathname) && r.request().method() === 'POST',
        { timeout: 30_000 },
      );
      await page.getByRole('button', { name: /^Confirm$/ }).click();
      const resp = await wait;
      expect(resp.ok()).toBeTruthy();
      const body = await resp.json().catch(() => ({}));
      expect(body.message).toBe('Updated'); // BE persisted it on the uuid-typed row
    }

    // ---- 2. Escalate -> POST /escalate (tier N -> N+1) ----
    await openActions(page);
    await page.getByRole('menuitem', { name: /^Escalate/i }).click();
    await expect(page.getByText(/Escalate to tier/i)).toBeVisible({ timeout: 10_000 });
    await page.getByPlaceholder(/Why escalate now/i).fill('E2E post-migration escalate check');
    {
      const wait = page.waitForResponse(
        (r) => /\/escalate$/.test(new URL(r.url()).pathname) && r.request().method() === 'POST',
        { timeout: 30_000 },
      );
      await page.getByRole('button', { name: /^Escalate$/ }).click();
      const resp = await wait;
      expect(resp.ok()).toBeTruthy();
    }

    // ---- 3. Mark as resolved -> POST /test-overrides {is_resolved:true} ----
    await openActions(page);
    await page.getByRole('menuitem', { name: /Mark as resolved/i }).click();
    await expect(page.getByRole('alertdialog').getByText(/Mark as resolved/i)).toBeVisible();
    {
      const wait = page.waitForResponse(
        (r) => /\/test-overrides$/.test(new URL(r.url()).pathname) && r.request().method() === 'POST',
        { timeout: 30_000 },
      );
      await page.getByRole('button', { name: /^Confirm$/ }).click();
      const resp = await wait;
      expect(resp.ok()).toBeTruthy();
      const body = await resp.json().catch(() => ({}));
      expect(body.message).toBe('Updated');
    }
  });
});
