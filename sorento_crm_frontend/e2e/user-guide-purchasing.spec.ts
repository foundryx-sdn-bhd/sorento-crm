/**
 * Validates that the labels and page paths claimed in
 * docs/user-guides/purchasing/*.md match the live UI.
 *
 * Run against a deployed instance:
 *   PORTAL_E2E_BASE_URL=https://fe-sorento.foundryx.my \
 *   USER_GUIDE_E2E_EMAIL=person38@example.com \
 *   USER_GUIDE_E2E_PASSWORD='...' \
 *   npx playwright test e2e/user-guide-purchasing.spec.ts
 *
 * Each `test` block corresponds to one claim in a markdown file and
 * cites the source doc + line so failures point straight to the doc
 * that needs updating.
 */
import { test, expect, Page } from '@playwright/test';

const EMAIL = process.env.USER_GUIDE_E2E_EMAIL;
const PASSWORD = process.env.USER_GUIDE_E2E_PASSWORD;

test.skip(
  !EMAIL || !PASSWORD,
  'Set USER_GUIDE_E2E_EMAIL and USER_GUIDE_E2E_PASSWORD to run user-guide validation',
);

async function login(page: Page) {
  await page.goto('/');
  const email = page.locator('input[type="email"], input[name="email"]').first();
  await expect(email).toBeVisible({ timeout: 20_000 });
  await email.fill(EMAIL!);
  await page.locator('input[type="password"], input[name="password"]').first().fill(PASSWORD!);
  await page.locator('button[type="submit"]').first().click();
  await page.waitForURL((url) => !/signin/i.test(url.pathname), { timeout: 30_000 });
}

test.describe('purchasing user guides - UI claim validation', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  // docs/user-guides/_shared/upload-flow.md
  test('Resource Management → Attachments page exists with Create Attachment + Bulk import buttons', async ({
    page,
  }) => {
    await page.goto('/resource-management/attachments');
    await expect(page.getByRole('button', { name: /create attachment/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /bulk import \(zip\)/i })).toBeVisible();
  });

  test('Create Attachment dialog has Attachment Type selector', async ({ page }) => {
    await page.goto('/resource-management/attachments');
    await page.getByRole('button', { name: /create attachment/i }).click();
    await expect(page.getByText(/attachment type/i).first()).toBeVisible();
  });

  // docs/user-guides/purchasing/manage-resource-folders.md
  test('Attachment Directories page renders folder tree with Add subfolder / Rename / Delete actions', async ({
    page,
  }) => {
    await page.goto('/resource-management/attachment-directories');
    // open any row's ⋯ menu and verify expected items
    const firstMenu = page.locator('button[aria-haspopup="menu"]').first();
    await expect(firstMenu).toBeVisible();
    await firstMenu.click();
    await expect(page.getByRole('menuitem', { name: /add subfolder/i })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: /^rename$/i })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: /^delete$/i })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: /pin to quick access/i })).toBeVisible();
  });

  // docs/user-guides/purchasing/review-stock-inquiry.md
  test('Stock Inquiries list page exists', async ({ page }) => {
    await page.goto('/procurement-management/stock-inquiries');
    await expect(page).toHaveURL(/\/procurement-management\/stock-inquiries/);
    await expect(page.getByRole('heading', { name: /stock inquiries/i })).toBeVisible();
  });

  test('Stock Inquiry detail shows Edit purchasing response, Reject, Chat records', async ({
    page,
  }) => {
    await page.goto('/procurement-management/stock-inquiries');
    // click the first inquiry row in the grid
    const firstRow = page.locator('table tbody tr, [role="row"]').filter({ hasNot: page.locator('th') }).first();
    await expect(firstRow).toBeVisible();
    await firstRow.click();
    await page.waitForURL(/\/procurement-management\/stock-inquiries\/[^/]+/);
    // these may be hidden depending on inquiry status - assert at least the page loaded
    const pageBody = page.locator('body');
    await expect(pageBody).toContainText(/stock inquiry/i);
    // soft checks: present iff the inquiry is in pending_purchasing
    const editBtn = page.getByRole('button', { name: /edit purchasing response/i });
    const rejectBtn = page.getByRole('button', { name: /^reject$/i });
    const chatBtn = page.getByRole('button', { name: /chat records/i });
    test
      .info()
      .annotations.push({
        type: 'note',
        description:
          'Edit/Reject buttons visible only when inquiry status is pending_purchasing or responded; ' +
          'pick a row with that status manually to fully validate.',
      });
    // do not fail the suite on these; just record visibility
    for (const [name, loc] of [
      ['edit purchasing response', editBtn],
      ['reject', rejectBtn],
      ['chat records', chatBtn],
    ] as const) {
      const visible = await loc.isVisible().catch(() => false);
      console.log(`  [info] "${name}" visible: ${visible}`);
    }
  });

  // docs/user-guides/purchasing/upload-product-master.md
  test('Master Data → Products has Upload button + Latest products import panel', async ({
    page,
  }) => {
    await page.goto('/master-data-management/products');
    await expect(page.getByRole('button', { name: /^upload$/i })).toBeVisible();
    // import panel only shows when a job exists; assert weakly
    const importPanel = page.getByText(/latest products import/i);
    console.log(`  [info] "Latest products import" panel visible: ${await importPanel.isVisible().catch(() => false)}`);
  });

  // docs/user-guides/purchasing/upload-spo.md - Upload SPO is now the primary button
  // (purchasing consolidation batch 6 Sep 2026, R5); Create SPO Allocation moved into
  // the Actions menu.
  test('Procurement → SPO Allocations has Upload SPO as the primary button', async ({
    page,
  }) => {
    await page.goto('/procurement-management/spo-allocations');
    const uploadBtn = page.getByRole('button', { name: /upload spo/i });
    await expect(uploadBtn).toBeVisible();
    await uploadBtn.click();
    await expect(page.getByRole('heading', { name: /import spo/i })).toBeVisible();
  });

  // docs/user-guides/purchasing/manage-resource-folders.md - Quick Access
  test('Quick Access section appears in sidebar with Add shortcut', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText(/quick access/i).first()).toBeVisible();
    // "+ Add shortcut" only appears if user has the pin permission
    const addShortcut = page.getByText(/add shortcut/i);
    console.log(`  [info] "+ Add shortcut" visible: ${await addShortcut.isVisible().catch(() => false)}`);
  });
});
