/**
 * Form submission + SLA lifecycle end-to-end via CONTACT IMPERSONATION - 
 * post-schema-migration verification (migrations 298/299/300).
 *
 * An admin impersonates a portal contact, submits a form through the real portal UI
 * (starting its form-SLA tracker on the uuid-typed conversation_sla_tracking rows),
 * then, back as staff, drives the lifecycle actions that transition the SLA:
 *   stock_inquiry: submit -> project_sales approve (resolves project_sales stage,
 *                  starts purchasing stage).
 *
 * Vertical slice first (stock_inquiry has no required portal fields). The other three
 * kinds follow the same skeleton once this pipeline is green.
 *
 * Creds: REQUEST_BATCH_E2E_* (mapped in via env). Seeded contact "E2E Form Submitter"
 * (slug from E2E_CONTACT_SLUG) must exist with a workspace so it is impersonatable.
 */
import { test, expect, type Page } from '@playwright/test';

const EMAIL = process.env.REQUEST_BATCH_E2E_EMAIL;
const PASSWORD = process.env.REQUEST_BATCH_E2E_PASSWORD;
const SLUG = process.env.E2E_CONTACT_SLUG || '';
const CONTACT = 'E2E Form Submitter';
const PHONE = '+60110000526';

test.skip(!EMAIL || !PASSWORD || !SLUG, 'Set REQUEST_BATCH_E2E_* and E2E_CONTACT_SLUG.');

async function login(page: Page) {
  await page.goto('/');
  const email = page.locator('input[type="email"], input[name="email"]').first();
  await expect(email).toBeVisible({ timeout: 20_000 });
  await email.fill(EMAIL!);
  await page.locator('input[type="password"], input[name="password"]').first().fill(PASSWORD!);
  await page.getByRole('button', { name: /sign in|log in|continue/i }).click();
  await expect(page).toHaveURL(/\/$|\/dashboard|home/i, { timeout: 30_000 });
}

/**
 * Sidebar nav: expand `group` (accordion trigger contains a link child, so a plain
 * click can race navigation) then click `link`. Retries the expand until the link
 * shows - the accordion state is nondeterministic across runs.
 */
async function navSidebar(page: Page, group: RegExp, link: RegExp, urlRe: RegExp) {
  await page.goto('/');
  const linkEl = page.getByRole('link', { name: link }).first();
  for (let i = 0; i < 4; i++) {
    if (await linkEl.isVisible().catch(() => false)) break;
    await page.getByRole('button', { name: group }).first().click({ timeout: 5_000 }).catch(() => {});
    await page.waitForTimeout(400);
  }
  await expect(linkEl).toBeVisible({ timeout: 10_000 });
  await linkEl.click();
  await expect(page).toHaveURL(urlRe, { timeout: 20_000 });
}

test.describe('Form submission + SLA lifecycle via impersonation (post-migration)', () => {
  test('impersonate -> submit stock inquiry -> staff approve -> SLA transitions', async ({
    page,
    context,
  }) => {
    await login(page);

    // --- Contacts list (direct nav: this spec verifies data flow, not menu gating;
    // sidebar navigability is already covered by sla-conversation-actions.spec) ---
    await page.goto('/user-management/contacts');
    await expect(page).toHaveURL(/\/user-management\/contacts/, { timeout: 20_000 });

    // Find the seeded contact, start impersonation, capture the minted portal URL.
    const searchBox = page.getByPlaceholder(/Search contacts/i);
    await expect(searchBox).toBeEditable({ timeout: 20_000 });
    await searchBox.click();
    await searchBox.fill(PHONE); // grid shows the phone, name column can render blank
    const row = page.getByRole('row', { name: new RegExp(PHONE.replace('+', '\\+')) }).first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.getByRole('button', { name: /Impersonate in portal/i }).click();
    await expect(page.getByText(/Confirm Impersonation/i)).toBeVisible();

    const startResp = page.waitForResponse(
      (r) => /\/contact-impersonation\/start$/.test(new URL(r.url()).pathname) && r.ok(),
      { timeout: 30_000 },
    );
    // The confirm button opens the portal in a new tab; we grab portal_url from the API
    // response instead and drive it in this same page (no popup juggling).
    const popupP = context.waitForEvent('page').catch(() => null);
    await page.getByRole('button', { name: /^Continue$|^Impersonate$/i }).click();
    const portalUrl = (await (await startResp).json()).portal_url as string;
    expect(portalUrl).toContain('/portal?token=');
    const popup = await popupP;
    if (popup) await popup.close().catch(() => {});

    // portal_url is built from FRONTEND_BASE_URL (may be a different host than the
    // test baseURL). Re-home the token onto THIS origin, else it lands in the wrong
    // origin's sessionStorage and every portal fetch 401s.
    const token = new URL(portalUrl).searchParams.get('token')!;
    expect(token).toBeTruthy();

    // --- Enter portal as the contact. Impersonation stays on the legacy /portal
    // tree; wait for the portal home to render (session established) before opening
    // the form, or the submit 401s. ---
    await page.goto(`/portal?token=${encodeURIComponent(token)}&impersonation=1`);
    await expect(page.getByRole('heading', { name: new RegExp(`Welcome, ${CONTACT}`, 'i') })).toBeVisible({
      timeout: 30_000,
    });
    await page.goto('/portal/stock_inquiry/new');
    await expect(page.getByRole('button', { name: /^Submit$/ })).toBeVisible({ timeout: 20_000 });

    // stock_inquiry has no required portal fields -> submit straight away.
    const submitResp = page.waitForResponse(
      (r) =>
        /\/public\/portal\/submissions\/stock_inquiry\/[^/]+\/submit$/.test(new URL(r.url()).pathname) &&
        r.request().method() === 'POST',
      { timeout: 30_000 },
    );
    await page.getByRole('button', { name: /^Submit$/ }).click();
    // A confirmation dialog gates the submit.
    const confirm = page.getByRole('button', { name: /^(Submit|Confirm|Yes)$/ }).last();
    if (await confirm.isVisible().catch(() => false)) await confirm.click();
    const sResp = await submitResp;
    // eslint-disable-next-line no-console
    console.log('SUBMIT_STATUS=' + sResp.status() + ' BODY=' + (await sResp.text()).slice(0, 300));
    expect(sResp.ok()).toBeTruthy();
    // id is the {id} path segment of .../submissions/stock_inquiry/{id}/submit
    const m = new URL(sResp.url()).pathname.match(/submissions\/stock_inquiry\/([^/]+)\/submit/);
    const inquiryId = m ? decodeURIComponent(m[1]) : '';
    expect(inquiryId).toBeTruthy();
    // eslint-disable-next-line no-console
    console.log('SUBMITTED_STOCK_INQUIRY_ID=' + inquiryId);

    // --- Back as staff: open the SI detail and approve (project_sales -> purchasing).
    // The NextAuth (admin) cookie session is untouched by the portal token, so the
    // staff route authenticates normally. This is the SLA-transition action:
    // project_sales_approve resolves the project_sales stage AND starts purchasing. ---
    await page.goto(`/procurement-management/stock-inquiries/${inquiryId}`);
    const approveBtn = page.getByRole('button', { name: /Approve \(send to purchasing\)/i });
    await expect(approveBtn).toBeVisible({ timeout: 20_000 });
    {
      const wait = page.waitForResponse(
        (r) =>
          /\/stock-inquiries\/[^/]+\/project-sales-approve$/.test(new URL(r.url()).pathname) &&
          r.request().method() === 'POST',
        { timeout: 30_000 },
      );
      await approveBtn.click();
      const resp = await wait;
      expect(resp.ok()).toBeTruthy();
    }
    // eslint-disable-next-line no-console
    console.log('APPROVED_STOCK_INQUIRY_ID=' + inquiryId);
  });
});
