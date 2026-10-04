/**
 * S7-04 pinned ten by name; M5-01 (`PLAN-ui-motion-round2.md` 3.5) widened that to a
 * walk: every route segment under `app/(protected)` whose directory renders a
 * `<DataGrid` needs a `loading.tsx`, or Next.js holds the LAST page's rows on screen
 * while the next segment's chunk and first page load - the exact gap this slice closes.
 *
 * M5 run 2 review (B1/S1) rewrote the predicate itself. The run-2 version counted a
 * segment's own `.tsx` files OR everything in its `components/` subdir - which false-
 * positived on detail routes whose `components/` folder happens to hold an unrelated
 * list component (e.g. `scm/purchase-orders/[id]` picked up a line-items grid used by
 * a DIFFERENT tab), and under-counted real list routes whose grid lives in `_shared/`
 * or a sibling directory reached through a `*Client`/`*View` wrapper.
 *
 * `segmentRendersDataGrid` now:
 * - For a segment whose OWN directory name is a dynamic param (`[id]`) - a detail/
 *   record route, never a list - only `page.tsx` itself, and files it imports that live
 *   in that SAME directory, count. `components/` is never consulted for these, so a
 *   detail page embedding a grid in one tab does not make the whole route "a list".
 * - For every other segment, `page.tsx` and every file reachable from it by following
 *   relative/`@/` imports - bounded to files under `app/(protected)` so the walk never
 *   wanders into shared primitives - count. This is what finds a grid that lives one
 *   or two hops away through a wrapper component.
 *
 * Source scan, not a render test, for the same reason `raw-table.inventory.test.ts` is:
 * what it asserts is a property of the whole tree, and a render test can only speak for
 * the one page it mounted. The render half below (kept from S7-04) still proves each
 * FOUND loading.tsx actually renders the shared skeleton, content-only, no shell of its
 * own.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';

vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const PROTECTED_ROOT = path.join(process.cwd(), 'app', '(protected)');

/**
 * Metronic template directories - the demo shell this app was built on top
 * of, never wired to a real feature or a permission - excluded the same way
 * `raw-table.inventory.test.ts` excludes them.
 */
const EXCLUDED_DIRS = [
  'app/(protected)/public-profile',
  'app/(protected)/account',
  'app/(protected)/network',
];

/** A JSX open tag for `DataGrid` or `DataGridTable` themselves, not a sibling like `DataGridPagination`. */
const RENDERS_DATA_GRID = /<DataGrid(?:Table)?(?![A-Za-z])/;
/** A route segment directory named as a Next.js dynamic param, e.g. `[id]`, `[projectId]`. */
const DYNAMIC_SEGMENT_NAME = /^\[.+\]$/;

function isDynamicSegment(dir: string): boolean {
  return DYNAMIC_SEGMENT_NAME.test(path.basename(dir));
}

/** Every `import ... from '<specifier>'` string literal in a source file, in appearance order. */
function importSpecifiers(source: string): string[] {
  const specifiers: string[] = [];
  const re = /\bimport\s+(?:[\s\S]*?\bfrom\s+)?['"]([^'"]+)['"]/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(source))) specifiers.push(match[1]);
  return specifiers;
}

/**
 * Resolve a relative (`./`, `../`) or `@/`-aliased import specifier to a file on disk,
 * trying `.tsx`, `.ts`, `/index.tsx`, `/index.ts` in that order. `null` for a bare
 * package specifier (`react`, `lucide-react`, ...) - there is nothing under those to
 * walk into - or a path that resolves to nothing.
 */
function resolveImport(spec: string, fromFile: string): string | null {
  let base: string;
  if (spec.startsWith('.')) {
    base = path.resolve(path.dirname(fromFile), spec);
  } else if (spec.startsWith('@/')) {
    base = path.join(process.cwd(), spec.slice(2));
  } else {
    return null;
  }
  const candidates = [`${base}.tsx`, `${base}.ts`, path.join(base, 'index.tsx'), path.join(base, 'index.ts')];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

/** True when `file`'s path, relative to the repo root, is under `app/(protected)`. */
function isWithinProtectedTree(file: string): boolean {
  const relative = path.relative(process.cwd(), file).split(path.sep).join('/');
  return relative.startsWith('app/(protected)/');
}

/** See the module doc comment for the two rules this implements. */
function segmentRendersDataGrid(dir: string): boolean {
  const pageFile = path.join(dir, 'page.tsx');
  if (!fs.existsSync(pageFile)) return false;

  if (isDynamicSegment(dir)) {
    const source = fs.readFileSync(pageFile, 'utf8');
    if (RENDERS_DATA_GRID.test(source)) return true;
    return importSpecifiers(source).some((spec) => {
      const resolved = resolveImport(spec, pageFile);
      // Same directory as page.tsx only - components/ is a subdirectory, excluded.
      return !!resolved && path.dirname(resolved) === dir && RENDERS_DATA_GRID.test(fs.readFileSync(resolved, 'utf8'));
    });
  }

  const visited = new Set<string>();
  const queue: string[] = [pageFile];
  while (queue.length) {
    const file = queue.shift()!;
    if (visited.has(file)) continue;
    visited.add(file);
    const source = fs.readFileSync(file, 'utf8');
    if (RENDERS_DATA_GRID.test(source)) return true;
    for (const spec of importSpecifiers(source)) {
      const resolved = resolveImport(spec, file);
      if (!resolved || visited.has(resolved) || !isWithinProtectedTree(resolved)) continue;
      queue.push(resolved);
    }
  }
  return false;
}

/** Every directory under `root` that directly contains a `page.tsx`. */
function pageDirs(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    const rel = path.relative(process.cwd(), dir).split(path.sep).join('/');
    if (EXCLUDED_DIRS.includes(rel)) return;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    if (entries.some((entry) => entry.isFile() && entry.name === 'page.tsx')) {
      out.push(dir);
    }
    for (const entry of entries) {
      if (entry.isDirectory() && entry.name !== 'node_modules') {
        walk(path.join(dir, entry.name));
      }
    }
  };
  walk(root);
  return out;
}

function relFromProtectedRoot(dir: string): string {
  return path.relative(PROTECTED_ROOT, dir).split(path.sep).join('/');
}

const listSegments = pageDirs(PROTECTED_ROOT).filter(segmentRendersDataGrid);
const listSegmentNames = listSegments.map(relFromProtectedRoot);

/**
 * Segments whose loading.tsx renders `<ListPageSkeleton bodyOnly />` instead of the
 * default (M5-01 review B1) - a manually curated set, not derived from
 * `segmentRendersDataGrid`, because the reason is about the HEADER, not about whether
 * the page itself is list-shaped. Two groups, each with its own reason:
 *
 * - A parent `layout.tsx` already renders the `PageHeader` for this segment (and every
 *   sibling under it). The default variant draws its OWN title+crumb bar, which would
 *   sit as a second one under the real header while the skeleton is up.
 * - The segment has no title bar anywhere - own page or parent layout. The default
 *   variant's title bar would be for a heading that never lands.
 *
 * `user-management/contacts/[id]` is the one entry `segmentRendersDataGrid` itself does
 * not find (post B1, a `[id]`-named leaf only counts its own page.tsx, and this one has
 * no `<DataGrid` of its own - the grid B1 found under its `components/` folder belongs
 * to the sibling `access` tab, not this one). It keeps a loading.tsx anyway: it is a
 * record page under a header the parent layout supplies, the same "a record page under
 * one of these lists is held by the same shape" reasoning `ListPageSkeleton`'s own doc
 * comment gives for using this component on non-list children, `bodyOnly` for the
 * parent-layout-header reason above.
 */
const BODY_ONLY_SEGMENTS: Record<string, string> = {
  'user-management/contacts/[id]':
    "parent layout.tsx renders PageHeader for every tab under this contact record; the page itself has no DataGrid of its own (the access tab's grid is a sibling route)",
  'user-management/users/[id]/logs':
    'parent layout.tsx renders PageHeader for every tab under this user record',
  'user-management/account/logs': 'parent layout.tsx renders PageHeader ("Account") for every tab',
  'user-management/settings/portal-revisions':
    'parent layout.tsx renders PageHeader ("Settings") for every settings page',
  'inventory-management/stock-batches': 'headerless - no PageHeader anywhere, own page or parent layout',
  'marketing-management/campaigns': 'headerless - no PageHeader anywhere, own page or parent layout',
  'store-admin/dashboard': 'headerless - no PageHeader anywhere, own page or parent layout',
  'store-admin/inventory/all-products': 'headerless - no PageHeader anywhere, own page or parent layout',
  'system-management/mcp-tools':
    "headerless - no PageHeader anywhere; McpToolsList titles itself with a CardTitle, not a route header",
  'user-management/settings/notifications':
    'parent layout.tsx renders PageHeader ("Settings") for every settings page',
};

/**
 * A `loading.tsx` also covers descendants that have no `loading.tsx` of their own
 * (Next.js reuses the nearest ancestor's as the Suspense fallback). `dealer-kit/loading.tsx`
 * is a real list's skeleton (the dealer-kit index route), but these five descendants are a
 * design canvas / a page designer / a bundle builder, not lists - inheriting the 10-row
 * `ListPageSkeleton` flashed a table shape over a canvas. Each gets its own `loading.tsx`
 * rendering `SectionSkeleton` so the canvas does not flash a table (M5-01 review S1 blast
 * radius).
 */
const SECTION_SKELETON_SEGMENTS = [
  'dealer-kit/design',
  'dealer-kit/design/summary',
  'dealer-kit/pages/[pageId]',
  'dealer-kit/bundles',
  'dealer-kit/price-tag-requests/[id]/design',
];

// Every segment that must carry a loading.tsx: what the walk finds, plus the manually
// curated bodyOnly overrides above (see that constant's doc comment for why they are
// not found by the walk itself).
const requiredSegmentNames = Array.from(
  new Set([...listSegmentNames, ...Object.keys(BODY_ONLY_SEGMENTS)]),
).sort();

describe('every DataGrid list segment has a loading.tsx (M5-01)', () => {
  it('no segment that renders a DataGrid (or is a bodyOnly override) is missing its loading.tsx', () => {
    const missingNames = requiredSegmentNames.filter(
      (name) => !fs.existsSync(path.join(PROTECTED_ROOT, name, 'loading.tsx')),
    );

    // Failure message names the count AND the segments, so a future addition
    // that forgets its loading.tsx fails loudly with the exact path to fix,
    // not just a number.
    expect(
      missingNames,
      `${missingNames.length} of ${requiredSegmentNames.length} segments missing loading.tsx`,
    ).toEqual([]);
  });

  it('renders the shared ListPageSkeleton, content-only, for every segment found', async () => {
    // M5 run 2: 123. M5 run 2 review (B1/S1): the walk's own predicate finds 129
    // (deleted 5 detail routes with no DataGrid of their own, added 12 real list
    // routes the run-2 walk missed); +1 manual bodyOnly override
    // (`user-management/contacts/[id]`) that the predicate itself does not find.
    // M5 run 3: the DataGrid migration batch (attachments, complaints, procurement,
    // orders line tables) turned 10 more segments into list segments the walk now
    // finds - the procurement purchase-request/sponsorship-form forms, three
    // system-management pages, tickets, and settings/notifications. Two of the
    // ten (`system-management/mcp-tools`, `user-management/settings/notifications`)
    // are also added to BODY_ONLY_SEGMENTS below, which does not change this count
    // - both were already found by the walk itself. Total: 139.
    // Purchasing consolidation batch (lane C, R16): `system-management/translations`
    // is a new DataGrid list segment with its own `loading.tsx`, found by the walk
    // itself - no BODY_ONLY_SEGMENTS entry needed. Total: 140.
    // Local-supplier-oi-routing lane: `master-data-management/countries` is a new
    // DataGrid list segment with its own `loading.tsx`, found by the walk itself -
    // no BODY_ONLY_SEGMENTS entry needed. Total: 141.
    // Chatbot turn re-architecture (S1): Chatbot Domains and Entity kinds are two
    // new DataGrid list segments, found by the walk itself - no BODY_ONLY_SEGMENTS
    // entry needed. Total: 143.
    // Sales module S6 (PLAN-sales-targets-opportunities-26sep.md): `sales/teams` is a new
    // DataGrid list segment with its own `loading.tsx`, found by the walk itself - no
    // BODY_ONLY_SEGMENTS entry needed. Total: 144.
    // Sales module S1 and S2 (same plan): `sales/targets` and `sales/opportunities` are new
    // DataGrid list segments with their own `loading.tsx`, found by the walk itself - no
    // BODY_ONLY_SEGMENTS entry needed. Total: 146.
    // Chatbot memory lane A: `user-management/contacts/[id]/chatbot` is a new segment
    // with its own `loading.tsx`, found by the walk itself - no BODY_ONLY_SEGMENTS entry
    // needed.
    // Cost price Lane A (PLAN-cost-price-supplier-26sep.md, #1288):
    // `procurement-management/cost-price-uploads` is a new DataGrid list segment with its
    // own `loading.tsx`, found by the walk itself - no BODY_ONLY_SEGMENTS entry needed.
    // Chatbot stock ask v2 S5: `order-management/customers/[id]/edit` now carries the
    // customer's Asks tab (`CustomerAsksTab`, a server-paged DataGrid), so the walk finds
    // it. Its `loading.tsx` renders `SectionSkeleton` (a form with a grid tab, the
    // purchase-request edit precedent) - no BODY_ONLY_SEGMENTS entry needed.
    // With all three lanes merged: 146 + 1 (contacts chatbot) + 1 (cost-price-uploads)
    // + 1 (customer Asks tab) = 149.
    // AutoCount DO / GRN ingest (#1356): `order-management/branches` is a new DataGrid list
    // segment (Customer Branches) with its own `loading.tsx`, found by the walk itself - no
    // BODY_ONLY_SEGMENTS entry needed. Total: 150.
    // CUSTOMER-GROUP: `order-management/customer-groups` is a new DataGrid list segment with
    // its own `loading.tsx` (ListPageSkeleton), found by the walk itself. Total: 151.
    expect(requiredSegmentNames.length).toBe(151);

    for (const name of requiredSegmentNames) {
      const dir = path.join(PROTECTED_ROOT, name);
      const loadingFile = path.join(dir, 'loading.tsx');
      if (!fs.existsSync(loadingFile)) continue; // already failed above; do not double-report here

      const relativeFromTest = './' + name + '/loading';
      const mod = await import(/* @vite-ignore */ relativeFromTest);
      const Loading = mod.default;
      expect(typeof Loading, `${relativeFromTest} has no default export`).toBe('function');

      const { container, unmount } = render(<Loading />);
      // Content-only: no `ScreenLoader` shell of its own (the app-boot splash
      // - a different loading state, never nested inside a route's own
      // skeleton). `ListPageSkeleton`/`SectionSkeleton` DO carry `role="status"`
      // themselves (M5-01 review S5), so that role is not what distinguishes
      // "content-only" here - `ScreenLoader`'s own `data-slot` is.
      expect(container.querySelector('[data-slot="screen-loader"]')).not.toBeInTheDocument();
      expect(container.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
      unmount();
    }
  });

  it('renders bodyOnly exactly for the curated segments, nothing else (M5-01 review B1)', () => {
    for (const name of requiredSegmentNames) {
      const loadingFile = path.join(PROTECTED_ROOT, name, 'loading.tsx');
      if (!fs.existsSync(loadingFile)) continue; // already failed above

      const source = fs.readFileSync(loadingFile, 'utf8');
      const shouldBeBodyOnly = name in BODY_ONLY_SEGMENTS;
      expect(
        source.includes('bodyOnly'),
        `${name}: expected bodyOnly=${shouldBeBodyOnly}, reason: ${BODY_ONLY_SEGMENTS[name] ?? 'n/a - not a curated segment'}`,
      ).toBe(shouldBeBodyOnly);
    }
  });

  it('gives each blast-radius descendant its own SectionSkeleton, not the inherited list shape (M5-01 review S1)', () => {
    for (const name of SECTION_SKELETON_SEGMENTS) {
      const dir = path.join(PROTECTED_ROOT, name);
      expect(fs.existsSync(dir), `${name} no longer exists - drop it from SECTION_SKELETON_SEGMENTS`).toBe(true);
      // Not a list segment - it must not appear in the walk's own findings.
      expect(listSegmentNames, `${name} is now a DataGrid segment - move it out of the blast-radius list`).not.toContain(name);

      const loadingFile = path.join(dir, 'loading.tsx');
      expect(fs.existsSync(loadingFile), `${name}/loading.tsx is missing`).toBe(true);
      const source = fs.readFileSync(loadingFile, 'utf8');
      expect(source).not.toMatch(/ListPageSkeleton/);
      expect(source).toMatch(/SectionSkeleton/);
    }
  });
});
