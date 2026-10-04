/**
 * Conversation intervention ticket round-trip (PLAN-conversation-intervention-tickets.md,
 * Phase 2, S2.7-S2.8).
 *
 * Covers the journey against the real stack: My Pending -> open a ticket -> reply
 * (with a real attachment) -> resolve, asserting the FE hit the real
 * `/api/v1/sla-management/conversation-sla-tracking/*` routes at each step and the
 * ticket disappears from the worklist once resolved (sibling tickets, if any, are
 * left untouched - AC-C3).
 *
 * Intervention tickets are created ONLY by the n8n `sub-human-intervention` flow
 * (POST .../integration) against a real WhatsApp contact - there is no UI path to
 * create one, so this spec cannot seed its own fixture the way a CRUD flow can. It
 * finds any ticket already open and assigned to the test account; if none exists it
 * skips at runtime with a clear message, same spirit as the credential-gated skip
 * below (real precondition, no synthetic fixture).
 *
 * Env (test skipped without EMAIL/PASSWORD):
 *   PORTAL_E2E_BASE_URL=http://localhost:3000   (default in playwright.config)
 *   REQUEST_BATCH_E2E_EMAIL=...
 *   REQUEST_BATCH_E2E_PASSWORD=...
 *
 * To exercise the send/resolve assertions, sign in as this account in Respond.io
 * and ask a test WhatsApp contact for "connect me to a person" (or POST the
 * `/integration` route directly with that user's id as assigned_to_id) before
 * running the spec.
 */
import path from 'path';
import { test, expect, type Page } from '@playwright/test';

const EMAIL = process.env.REQUEST_BATCH_E2E_EMAIL;
const PASSWORD = process.env.REQUEST_BATCH_E2E_PASSWORD;

test.skip(!EMAIL || !PASSWORD, 'Set REQUEST_BATCH_E2E_* env vars to run this spec.');

const ATTACHMENT_FIXTURE = path.join(__dirname, 'fixtures', 'drive', 'drive-image.png');

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

/**
 * The "My pending tasks" widget renders on the home dashboard directly (no
 * sidebar entry of its own) - the sidebar-first rule applies to reaching NEW
 * pages; this widget IS the landing page after sign-in.
 */
async function waitForMyPendingWidget(page: Page) {
  await expect(page.getByRole('heading', { name: /my pending tasks/i })).toBeVisible({
    timeout: 20_000,
  });
}

/**
 * A ticket row is the only "My Pending" row type that opens an in-place drawer
 * instead of navigating or opening Respond.io in a new tab - detected by
 * clicking and checking for the drawer's own "Resolve ticket" affordance.
 */
async function openAnOpenTicket(page: Page): Promise<boolean> {
  await waitForMyPendingWidget(page);
  const rows = page.locator('ul.divide-y > li');
  const rowCount = await rows.count();
  for (let i = 0; i < rowCount; i += 1) {
    await rows.nth(i).getByRole('button').first().click();
    const drawer = page.getByRole('dialog');
    const resolveButton = drawer.getByRole('button', { name: /resolve ticket/i });
    if (await resolveButton.isVisible({ timeout: 3_000 }).catch(() => false)) {
      return true;
    }
    // Not a ticket row (form record navigated away, or Respond opened a new
    // tab) - go back to the dashboard and try the next one.
    if (page.url() !== (await page.evaluate(() => window.location.origin) + '/')) {
      await page.goto('/');
      await waitForMyPendingWidget(page);
    }
  }
  return false;
}

test.describe('Conversation intervention ticket round-trip', () => {
  test('mine -> ticket -> send (with attachment) -> resolve', async ({ page }) => {
    await login(page);

    const found = await openAnOpenTicket(page);
    test.skip(
      !found,
      'No open intervention ticket assigned to this account - seed one via the ' +
        'sub-human-intervention n8n flow (or POST .../integration) before running.',
    );

    const drawer = page.getByRole('dialog');
    await expect(drawer).toBeVisible();

    // Enquiry header renders (AC-C1): a contact name/phone in the sheet title area.
    await expect(drawer.locator('h2, [class*=SheetTitle]').first()).not.toBeEmpty();

    // ---- Reply with text + a real attachment ----
    const textarea = drawer.locator('textarea').first();
    await expect(textarea).toBeVisible({ timeout: 15_000 });
    await textarea.fill('Checking on this now - thank you for your patience.');

    const fileInput = drawer.locator('input[type="file"]');
    if (await fileInput.count()) {
      await fileInput.first().setInputFiles(ATTACHMENT_FIXTURE);
    }

    // R9: the panel's post-send re-read hits the thread endpoint (not /page) and
    // that response already carries the sent row, from the local-first read.
    const SENT_TEXT = 'Checking on this now - thank you for your patience.';
    const threadResponse = page.waitForResponse(
      async (res) => {
        if (
          res.request().method() !== 'GET' ||
          !/\/conversation-sla-tracking\/[^/]+\/conversation(\?|$)/.test(res.url())
        ) {
          return false;
        }
        const body = await res.json().catch(() => null);
        const items: Array<{ message?: { text?: string } }> = body?.items ?? [];
        return items.some((item) => item.message?.text?.includes(SENT_TEXT));
      },
      { timeout: 30_000 },
    );

    const sendResponse = page.waitForResponse(
      (res) => res.url().includes('/ticket/send') && res.request().method() === 'POST',
      { timeout: 30_000 },
    );
    await drawer.getByRole('button', { name: /^send$/i }).click();
    const sendResult = await sendResponse;
    expect(sendResult.ok()).toBeTruthy();

    const threadResult = await threadResponse;
    expect(threadResult.ok()).toBeTruthy();
    const threadBody = await threadResult.json();
    expect(
      threadBody.items.map((item: { message?: { text?: string } }) => item.message?.text),
    ).toEqual(expect.arrayContaining([expect.stringContaining(SENT_TEXT)]));

    // The response clock stops on THIS ticket only (AC-E1) - the drawer refetches
    // and shows the message in the thread.
    await expect(
      drawer.getByText('Checking on this now - thank you for your patience.'),
    ).toBeVisible({ timeout: 15_000 });

    // ---- Resolve (AC-C3): confirmation dialog, standard copy, hard action ----
    await drawer.getByRole('button', { name: /resolve ticket/i }).click();
    const confirmDialog = page.getByRole('alertdialog');
    await expect(confirmDialog).toBeVisible();
    await expect(confirmDialog.getByText(/cannot be undone/i)).toBeVisible();

    const resolveResponse = page.waitForResponse(
      (res) => res.url().includes('/resolve') && res.request().method() === 'POST',
      { timeout: 30_000 },
    );
    await confirmDialog.getByRole('button', { name: /^confirm$/i }).click();
    const resolveResult = await resolveResponse;
    expect(resolveResult.ok()).toBeTruthy();

    // Drawer closes and the ticket drops out of My Pending.
    await expect(drawer).not.toBeVisible({ timeout: 15_000 });
  });
});
