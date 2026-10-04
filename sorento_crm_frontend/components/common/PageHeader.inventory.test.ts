/**
 * S5-01, S5-02, S5-05 - the wayfinding inventory, checked against the source tree.
 *
 * The reason this is a source scan and not a render test: what S5-01 and S5-02
 * assert is a property of the WHOLE tree ("every page title comes from one
 * component", "no page types its own Home crumb"), and a render test can only
 * speak for the page it mounted. The 244th hand-typed `<BreadcrumbLink href="/">
 * Home</BreadcrumbLink>` would pass every component test in the repo and fail
 * here, which is the point: 243 of them are how the trail drifted from the
 * sidebar in the first place, and how "Home" outlived a menu that says
 * "Dashboards".
 *
 * If you are adding a page: render `<PageHeader title=... />` and let the trail
 * derive itself. Pass `crumbs` only where the sidebar names nothing (a nested
 * tab, a token-scoped portal page). Do not type an `<h1>`; do not import
 * `@/components/ui/breadcrumb`.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

/**
 * Roots scanned. `app/(auth)`, `app/(public)` and the Metronic demo layouts are
 * out: the portal and the login card are outside the sidebar's world, and the
 * demo layouts (`app/components/layouts/demo1..10`, `app/components/partials`)
 * are vendor shell code no page of ours renders.
 *
 * `app/components/common` IS ours, so it is scanned: it held an `<h1>` that
 * escaped the whole sweep by sitting in a directory nobody looked at.
 */
const ROOTS = ['app/(protected)', 'app/components/common', 'components'];

/** The one file allowed to own a page title and a crumb trail. */
const PAGE_HEADER = 'components/common/PageHeader.tsx';

/**
 * The five `<h1>` that are not page titles, each for a reason a sweep must not
 * erase.
 */
const H1_EXEMPT = new Set([
  PAGE_HEADER,
  // A catalogue page rendered for PRINT, headless, with no app chrome around
  // it: its h1 is the document's own title, not this app's.
  'app/(protected)/dealer-kit/components/CatalogueRenderer.tsx',
  // A translation scratch page, reachable by URL only.
  'app/(protected)/i18n-test/page.tsx',
  // The permission guard's refusal, rendered INSTEAD of a page. It is the only
  // heading on screen and it has no trail to sit under, because the page the
  // user asked for never rendered.
  'app/components/common/AccessDenied.tsx',
  // The app-wide crash screen (`app/global-error.tsx`). It replaces the ROOT
  // layout, so there is no app chrome, no PageHeader and no trail: the only
  // heading on a page that could not start.
  'components/common/GlobalErrorView.tsx',
]);

/** Every `.ts`/`.tsx` under the scanned roots, tests excluded. */
function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || entry.name === '.next') continue;
        walk(full);
      } else if (
        (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) &&
        !entry.name.includes('.test.')
      ) {
        out.push(full);
      }
    }
  };
  for (const root of ROOTS) walk(root);
  return out;
}

describe('Wayfinding inventory (S5)', () => {
  it('S5-01: no page hand-rolls its own <h1>', () => {
    const offenders = sourceFiles().filter(
      (file) =>
        !H1_EXEMPT.has(file) && /<h1[\s>]/.test(fs.readFileSync(file, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });

  it('S5-01: no page renders ToolbarTitle; the title is PageHeader\'s', () => {
    const offenders = sourceFiles().filter((file) =>
      /<ToolbarTitle[\s>]/.test(fs.readFileSync(file, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });

  it('S5-02: no page builds its own breadcrumb', () => {
    const offenders = sourceFiles().filter(
      (file) =>
        file !== PAGE_HEADER &&
        fs.readFileSync(file, 'utf8').includes('@/components/ui/breadcrumb'),
    );
    expect(offenders).toEqual([]);
  });

  it('S5-02: no "Home" crumb survives; the root is the menu\'s "Dashboards"', () => {
    const offenders = sourceFiles().filter((file) =>
      /BreadcrumbLink[^>]*href="\/"/.test(fs.readFileSync(file, 'utf8')),
    );
    expect(offenders).toEqual([]);
  });

  it('S5-05: no title or confirmation falls back to a UUID fragment', () => {
    // The four sites the audit found: a promotion with no description, an
    // allocation with no SPO number, that allocation's delete dialog, and the
    // portal's submitted-ticket line. Each now falls back to a human field or
    // to "Untitled ...", so nothing reads "Promotion 6d5f1a2b" at a user.
    //
    // `_?id` rather than `\bid`, because `_` is a word character: the first
    // version of this scan could not see `policy_id.slice(0, 8)` and let a
    // truncated UUID sit in the SLA config list for the whole sweep.
    //
    // The cut has to start at a NUMBER. `directoryId.slice(PREFIX.length)`
    // strips a namespace off an internal key and renders nothing; only a fixed
    // offset is somebody shortening an id to show it.
    const offenders: string[] = [];
    for (const file of sourceFiles()) {
      const src = fs.readFileSync(file, 'utf8');
      for (const line of src.split('\n')) {
        if (/_?id\.(slice|substring)\(\s*-?\d/i.test(line) && !line.includes('//')) {
          offenders.push(`${file}: ${line.trim().slice(0, 80)}`);
        }
      }
    }
    // Only the technical references stay: a source message id and an import
    // session id, both shown as monospace evidence in a log row, neither a
    // title and neither inside a confirmation.
    const titles = offenders.filter(
      (hit) =>
        !hit.includes('TicketSourceCard.tsx') &&
        !hit.includes('IntegrationLogsList.tsx') &&
        !hit.includes('SmartLinkageList.tsx') &&
        !hit.includes('UploadSessionRow.tsx') &&
        !hit.includes('AttachmentDirectoriesView.tsx'),
    );
    expect(titles).toEqual([]);
  });
});
