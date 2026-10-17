/**
 * M5-06 - no product file imports `@/components/ui/table`.
 *
 * `DataGrid` is the primitive: sticky header, movable/resizable columns and a
 * bounded scroller by default (M5-05), none of which a raw `<Table>` gets.
 * This walk is what keeps a 27th (28th, ...) raw table from creeping back in
 * while the 27 counted here migrate one module per commit (M5 run 3).
 *
 * Source scan, not a render test, for the same reason
 * `data-grid-scroller.inventory.test.ts` is: what it asserts is a property of
 * the whole tree, and a render test can only speak for the one page it
 * mounted.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

/** Roots scanned: everything a product page or component can live in. */
const ROOTS = ['app', 'components'];

/**
 * Metronic template directories - the demo shell this app was built on top
 * of, never wired to a real feature or a permission - excluded from the walk
 * entirely rather than allowlisted file-by-file, so the allowlist stays a
 * list of genuine product debt.
 *
 * `app/(protected)/dark-sidebar`, `app/(protected)/components`,
 * `app/(protected)/store-admin`, `app/(protected)/store-client`,
 * `app/(protected)/auth`, `app/(protected)/i18n-test` and
 * `app/(protected)/ideas` are the same kind of template page, but none of
 * them import `@/components/ui/table` today, so excluding them changes
 * nothing observable - they are left OUT of this list (and therefore IN the
 * walk) so a future raw-table import under one of them still fails here.
 */
const EXCLUDED_DIRS = [
  'app/(protected)/public-profile',
  'app/(protected)/account',
  'app/(protected)/network',
];

/**
 * Every current importer of `@/components/ui/table` outside `components/ui`
 * itself, each with a one-line reason. Two are DECIDED, permanent exemptions
 * (the captain's ruling, M5 review run 1): the two `app/(auth)` portal pages
 * and `ReportPivotTable.tsx`, below. Every other entry's reason is "pending
 * migration, M5 run 3" (run 3 drains them one module per commit).
 */
const ALLOWLIST: Record<string, string> = {
  // Public portal surfaces outside the authenticated shell, out of M5's scope
  // entirely - not "not yet migrated", never migrating under this rule.
  'app/(auth)/approval/page.tsx':
    'public portal page outside (protected); the DataGrid rule covers product list surfaces inside the shell',
  'app/(auth)/view/request/page.tsx':
    'public portal page outside (protected); the DataGrid rule covers product list surfaces inside the shell',

  // Not a list: a pivot report reshapes rows into a matrix (rows x measures),
  // which is not what a DataGrid's one-row-per-record model expresses.
  // Permanently exempt (captain's ruling, M5 review run 1).
  'components/reports/ReportPivotTable.tsx':
    'pivot matrix, not a record list - permanently exempt, not a DataGrid candidate',

  // Pending migration (PLANNING-HISTORY #1600 shipped them on the raw table). Both are a
  // few lines inside another surface: an expanded History timeline event (and the stored
  // email's body rows) and the Confirm refusal block. Moving them onto PanelDataGrid adds
  // its card, search and pager inside each one, which is an owner-visible change.
  'app/(protected)/project-sales/fulfilment-planning/components/PlanningHistorySheet.tsx':
    'pending migration: an expanded timeline event\'s lines and the stored email body, inside a sheet',
  'app/(protected)/project-sales/fulfilment-planning/components/PlanningRefusalTable.tsx':
    'pending migration: the Confirm refusal block\'s lines, one row per refused line with its fix',

  // Pending migration (OI-STAGED-NOTIFY #1684 shipped it on the raw table). The send-all
  // dialog's recipient summary: one row per email that picks the preview tab below it, a
  // "No recipient" row spanning To and Cc per unresolved group, and a totals row. Moving it
  // onto PanelDataGrid adds its card, search and pager inside the dialog, an owner-visible change.
  'components/common/ParkedSendDialog.tsx':
    'pending migration: the send-all dialog\'s recipient summary, with spanning "No recipient" rows and a totals row',

  // Not a list (OI-LINE-SPLIT v4, owner-approved mock): an Excel-like sheet whose split rows
  // stack under their SO line with the line's own cells merged across them (rowSpan), one
  // <tbody> per line as its drop target. A DataGrid's one-row-per-record model cannot merge
  // cells across rows; precedent scm/components/SupplierSheet.tsx.
  'app/(protected)/project-sales/fulfilment-planning/components/LineSplitSheet.tsx':
    'merged rowSpan split rows under each SO line cannot be a DataGrid row model (precedent scm/components/SupplierSheet.tsx)',
};

/**
 * Every `.tsx` AND `.ts` under the scanned roots, tests and `components/ui`
 * excluded. `.ts` joined the walk in M5 review run 1 (nit): a raw `<Table>`
 * import realistically only shows up in a `.tsx`, but a `.ts` file re-exporting
 * or re-rendering one (a service module returning JSX, a `.ts`-suffixed hook
 * file) would otherwise sit outside the walk entirely rather than failing it -
 * `.d.ts` is excluded, it declares types, never imports a component.
 */
function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      const rel = full.split(path.sep).join('/');
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name === '.next') continue;
        if (rel === 'components/ui') continue;
        if (EXCLUDED_DIRS.includes(rel)) continue;
        walk(full);
      } else if (
        (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) &&
        !entry.name.endsWith('.d.ts') &&
        !/\.(test|spec)\./.test(entry.name)
      ) {
        out.push(rel);
      }
    }
  };
  for (const root of ROOTS) walk(root);
  return out;
}

const TABLE_IMPORT = /from ['"]@\/components\/ui\/table['"]/;

describe('No product file imports @/components/ui/table (M5-06)', () => {
  it('every importer outside components/ui is allowlisted with a reason', () => {
    const offenders = sourceFiles().filter((file) => TABLE_IMPORT.test(fs.readFileSync(file, 'utf8')));
    const unlisted = offenders.filter((file) => !(file in ALLOWLIST));

    expect(unlisted).toEqual([]);
  });

  it('every allowlist entry still exists and still imports the table', () => {
    const stale = Object.keys(ALLOWLIST).filter((file) => {
      if (!fs.existsSync(file)) return true;
      return !TABLE_IMPORT.test(fs.readFileSync(file, 'utf8'));
    });

    expect(stale).toEqual([]);
  });
});
