'use client';

import { useCallback, useMemo, useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, Copy, Heading, SearchX, Tag } from 'lucide-react';
import type { ColumnDef } from '@tanstack/react-table';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { useHasPermission } from '@/hooks/usePermissions';

import type {
  CodeSuggestion,
  FlyerReadingStatus,
  MatchReport,
  MatchedCode,
  PageHeading,
} from '../../services/flyerReadingService';
import { FLYER_READINGS_QUERY_KEY } from '../hooks/useFlyerReadings';
import { AdoptCodeDialog } from './AdoptCodeDialog';
import { DimensionReviewSection } from './DimensionReviewSection';
import { SpecProposalSection } from './SpecProposalSection';
import { Empty, Section, printedOn } from './ReportSection';
import { ReportGrid } from './ReportGrid';
import { codeWithName, distinctName } from '@/lib/product-display';

/** The slug that authorises adopting a code, everywhere on this screen. */
const MASTER_DATA_EDIT = 'master_data.products.edit';

/**
 * One row of the "codes the master does not have" grid: either still
 * unmatched, or adopted (a `matched` entry with `adopted: true`). The two
 * live in ONE grid, in printed-page order, because an adopted row is the
 * before-and-after of an unmatched one rather than a different kind of thing
 * (AC-A.9).
 */
type AdoptionRow =
  | { kind: 'unmatched'; code: string; pages: number[]; suggestion: CodeSuggestion | null }
  | {
      kind: 'adopted';
      code: string;
      pages: number[];
      productId: string;
      productCode: string;
      productName: string;
    };

/**
 * What the system read, said plainly.
 *
 * This is the honest step, and the reason the slice exists. A designer is about
 * to turn 36 pages of finished artwork into a brochure without retyping 998
 * product codes, and the only question worth answering first is what the reader
 * got WRONG. So nothing here is a reassurance:
 *
 * - Unmatched codes are named as products the brochure will not contain, before
 *   the seed rather than after it.
 * - Sizes can now be applied to the product master, one ticked row at a time
 *   and never as a side effect of reading the flyer (S7.6). That section owns
 *   its own mutation and lives in `DimensionReviewSection`.
 * - Specifications work the same way one step removed: the flyer is READ for
 *   them here and reviewed in Master Data, because a batch spanning two hundred
 *   products is not something to judge on a page about one flyer.
 * - Every section renders even when it is empty, because "no promotion gaps" is
 *   an answer somebody needs, and a section that vanishes on zero looks like a
 *   section that failed to load.
 */

function Figure({
  label,
  value,
  tone = 'plain',
  testId,
}: {
  label: string;
  value: number;
  tone?: 'plain' | 'warn';
  testId: string;
}) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      {/* The id sits on the FIGURE, not the card: a reader of this element - a
          test, or anything else - wants the number, and the number beside its
          label reads as a number that is not a number. */}
      <p
        data-testid={testId}
        className={
          tone === 'warn'
            ? 'text-xl font-semibold text-amber-600'
            : 'text-xl font-semibold text-foreground'
        }
      >
        {value}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

export interface MatchReportSectionsProps {
  /** The reading these candidates came off, for the one section that acts. */
  readingId: string;
  /**
   * The promotion the report on screen was computed against. Threaded through
   * to the adopt dialog and the undo mutation so the response they get back
   * writes the SAME cache entry the screen is reading (no promotion-blind
   * flash - see `useFlyerReadings`).
   */
  promotionId?: string | null;
  report: MatchReport;
  /** How many codes were printed at all, from the reading rather than the report. */
  codeCount: number;
  /** The promotion the report was computed against, by NAME. Never an id. */
  promotionLabel: string | null;
  /**
   * What the reader called each page. Off the reading, not the report - see the
   * service docblock. Shown WRONG values and all: this section exists so the
   * misreads are visible before the seed rather than found in the builder after
   * it.
   */
  headings: PageHeading[];
  /**
   * Where the READING got to. Optional and `done` by default, because today the
   * only caller renders this whole component once the read has finished and the
   * existing tests pass no such prop - but the spec section below has to be able
   * to say "read the flyer first" rather than offer a button that cannot work.
   */
  readingStatus?: FlyerReadingStatus;
  /**
   * When a code was last adopted or undone on this reading. Passed straight
   * through to `SpecProposalSection`, which is the one place it is compared
   * against a proposal batch's `created_at` (AC-C.4). Optional, default null,
   * for the same reason `readingStatus` is - existing callers and tests pass
   * no such prop.
   */
  codeOverridesChangedAt?: string | null;
}

export function MatchReportSections({
  readingId,
  promotionId = null,
  report,
  codeCount,
  promotionLabel,
  headings,
  readingStatus = 'done',
  codeOverridesChangedAt = null,
}: MatchReportSectionsProps) {
  const canAdopt = useHasPermission(MASTER_DATA_EDIT);

  // Undo is a detach action (D7 / PRINCIPLES "Design mandates"): no
  // confirmation dialog. The button starts a countdown toast, the server
  // commits the same DELETE the route always did when the window lapses, and
  // Cancel is the only way back. `id` is synthetic - there is no row of its
  // own to key a pending action on, only a printed code inside this reading's
  // map - so it is the reading plus the code, which is also what keeps it
  // unique across every OTHER reading's adoptions.
  const undo = useDeferredRowAction({
    actionKey: 'flyer_code_adoption.undo',
    entityType: 'flyer_code_adoption',
    verb: 'Undoing',
    successMessage: 'Code unmatched again',
    invalidateKeys: [[FLYER_READINGS_QUERY_KEY, readingId]],
  });
  const undoRowId = useCallback((code: string) => `${readingId}:${code}`, [readingId]);

  // What the dialog is open ON. Null closes it - there is no separate `open`
  // boolean to fall out of sync with `null`.
  const [adoptTarget, setAdoptTarget] = useState<{
    code: string;
    pages: number[];
    suggestion: CodeSuggestion | null;
  } | null>(null);

  const duplicateRows = useMemo(
    () =>
      Object.entries(report.duplicates).map(([code, pages]) => ({
        code,
        pages: pages ?? [],
      })),
    [report.duplicates],
  );

  // An adopted row is the AFTER of an unmatched one, not a different kind of
  // row, so both live in one grid rather than the adopted ones vanishing into
  // the "matched" figure with nothing to undo from (AC-A.9). Sorted by first
  // printed page, the order a reviewer holding the paper works through it.
  const adoptedRows = useMemo(
    () => report.matched.filter((entry) => entry.adopted),
    [report.matched],
  );
  const adoptionRows = useMemo<AdoptionRow[]>(() => {
    const rows: AdoptionRow[] = [
      ...report.unmatched.map(
        (entry): AdoptionRow => ({
          kind: 'unmatched',
          code: entry.code,
          pages: entry.pages,
          suggestion: entry.suggestion,
        }),
      ),
      ...adoptedRows.map(
        (entry): AdoptionRow => ({
          kind: 'adopted',
          code: entry.code,
          pages: entry.pages,
          productId: entry.productId,
          productCode: entry.productCode,
          productName: entry.productName,
        }),
      ),
    ];
    return rows.sort((left, right) => {
      const leftPage = left.pages[0] ?? Number.MAX_SAFE_INTEGER;
      const rightPage = right.pages[0] ?? Number.MAX_SAFE_INTEGER;
      return leftPage !== rightPage ? leftPage - rightPage : left.code.localeCompare(right.code);
    });
  }, [report.unmatched, adoptedRows]);

  const unmatchedColumns = useMemo<ColumnDef<AdoptionRow>[]>(() => {
    const base: ColumnDef<AdoptionRow>[] = [
      {
        accessorKey: 'code',
        header: 'Printed code',
        cell: ({ row }) => (
          <div className="truncate font-mono text-sm" title={row.original.code}>
            {row.original.code}
          </div>
        ),
        size: 180,
        minSize: 120,
        meta: { headerTitle: 'Printed code' },
      },
      {
        id: 'pages',
        header: 'Printed on',
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground">{printedOn(row.original.pages)}</span>
        ),
        size: 130,
        minSize: 100,
        meta: { headerTitle: 'Printed on' },
      },
      {
        id: 'suggestion',
        header: 'Nearest existing code',
        cell: ({ row }) => {
          if (row.original.kind === 'adopted') {
            const named = distinctName(row.original.productCode, row.original.productName)?.trim() ?? null;
            return (
              <div className="flex min-w-0 items-center gap-2" data-testid="dk-fr-adopted-row">
                <Badge variant="outline" className="shrink-0 border-green-300 font-normal text-green-700">
                  Adopted
                </Badge>
                <span
                  className="truncate text-sm"
                  title={codeWithName(row.original.productCode, named)}
                >
                  Adopted as <span className="font-mono">{row.original.productCode}</span>
                  {named && <span className="text-muted-foreground"> {named}</span>}
                </span>
              </div>
            );
          }

          const suggestion = row.original.suggestion;
          if (!suggestion) {
            return (
              <span className="text-sm text-muted-foreground">
                Nothing close enough to suggest
              </span>
            );
          }
          // Plenty of products are named after their own code, and printing
          // "SRTWC8066 SRTWC8066" reads as a rendering fault rather than as a
          // product whose name is its code.
          const named = distinctName(suggestion.productCode, suggestion.productName)?.trim() ?? null;
          const label = codeWithName(suggestion.productCode, named);
          return (
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-sm" title={label}>
                <span className="font-mono">{suggestion.productCode}</span>
                {named && <span className="text-muted-foreground"> {named}</span>}
              </span>
              <Badge variant="outline" className="shrink-0 font-normal">
                {Math.round(suggestion.similarity * 100)}% alike
              </Badge>
            </div>
          );
        },
        size: 380,
        minSize: 200,
        meta: { headerTitle: 'Nearest existing code' },
      },
    ];

    // No permission, no buttons - the adopted state is still visible above.
    if (!canAdopt) return base;

    const actionColumn: ColumnDef<AdoptionRow> = {
      id: 'action',
      header: 'Action',
      cell: ({ row }) => {
        const original = row.original;
        if (original.kind === 'adopted') {
          const isUndoing = undo.targetId === undoRowId(original.code) && undo.isPending;
          return (
            <Button
              size="sm"
              variant="outline"
              data-testid="dk-fr-undo"
              disabled={isUndoing}
              onClick={() =>
                undo.run({
                  id: undoRowId(original.code),
                  subject: original.code,
                })
              }
            >
              {isUndoing ? 'Undoing' : 'Undo'}
            </Button>
          );
        }
        return (
          <Button
            size="sm"
            variant="outline"
            data-testid="dk-fr-adopt"
            onClick={() =>
              setAdoptTarget({
                code: original.code,
                pages: original.pages,
                suggestion: original.suggestion,
              })
            }
          >
            This is...
          </Button>
        );
      },
      size: 130,
      minSize: 110,
      meta: { headerTitle: 'Action' },
    };
    return [...base, actionColumn];
  }, [canAdopt, readingId, undo, undoRowId]);

  const notPromotedColumns = useMemo<ColumnDef<MatchedCode>[]>(
    () => [
      {
        accessorKey: 'code',
        header: 'Printed code',
        cell: ({ row }) => (
          <div className="truncate font-mono text-sm" title={row.original.code}>
            {row.original.code}
          </div>
        ),
        size: 180,
        minSize: 120,
        meta: { headerTitle: 'Printed code' },
      },
      {
        accessorKey: 'productName',
        header: 'Product',
        cell: ({ row }) => (
          <div className="truncate text-sm" title={row.original.productName}>
            {row.original.productName}
          </div>
        ),
        size: 380,
        minSize: 200,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'pages',
        header: 'Printed on',
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground">{printedOn(row.original.pages)}</span>
        ),
        size: 130,
        minSize: 100,
        meta: { headerTitle: 'Printed on' },
      },
    ],
    [],
  );

  const duplicateColumns = useMemo<ColumnDef<{ code: string; pages: number[] }>[]>(
    () => [
      {
        accessorKey: 'code',
        header: 'Printed code',
        cell: ({ row }) => (
          <div className="truncate font-mono text-sm" title={row.original.code}>
            {row.original.code}
          </div>
        ),
        size: 200,
        minSize: 120,
        meta: { headerTitle: 'Printed code' },
      },
      {
        id: 'pages',
        header: 'Printed on',
        cell: ({ row }) => (
          <span className="truncate text-sm text-muted-foreground" title={printedOn(row.original.pages)}>
            {printedOn(row.original.pages)}
          </span>
        ),
        size: 240,
        minSize: 120,
        meta: { headerTitle: 'Printed on' },
      },
    ],
    [],
  );

  const headingColumns = useMemo<ColumnDef<PageHeading>[]>(
    () => [
      {
        accessorKey: 'page',
        header: 'Flyer page',
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground">Page {row.original.page}</span>
        ),
        size: 140,
        minSize: 90,
        meta: { headerTitle: 'Flyer page' },
      },
      {
        id: 'text',
        header: 'Section will be called',
        cell: ({ row }) =>
          row.original.text ? (
            <div className="truncate text-sm text-foreground" title={row.original.text}>
              {row.original.text}
            </div>
          ) : (
            // Not an em dash and not a blank cell: a blank reads as a render
            // that failed, and the reviewer needs to know the seed will fall
            // back to the page number rather than leave the section unnamed.
            <span className="text-sm text-amber-600">
              No heading found - the section will be named after its page
            </span>
          ),
        size: 420,
        minSize: 200,
        meta: { headerTitle: 'Section will be called' },
      },
    ],
    [],
  );

  return (
    <div className="flex flex-col gap-8">
      <section className="flex flex-col gap-3" data-dk-fr-section="summary">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Figure testId="dk-fr-figure-printed" label="Codes printed" value={codeCount} />
          <Figure
            testId="dk-fr-figure-matched"
            label="Matched to a product"
            value={report.matched.length}
          />
          <Figure
            testId="dk-fr-figure-unmatched"
            label="Not in the product master"
            value={report.unmatched.length}
            tone={report.unmatched.length > 0 ? 'warn' : 'plain'}
          />
          <Figure
            testId="dk-fr-figure-duplicates"
            label="Printed more than once"
            value={duplicateRows.length}
          />
        </div>

        {report.unmatched.length > 0 && (
          <div
            className="flex items-start gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
            data-testid="dk-fr-unmatched-warning"
          >
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <p>
              {report.unmatched.length} of {codeCount} printed codes are not in the product
              master. They will <strong>not</strong> be in the brochure. Create the products
              first if the flyer is right, or leave the gap and correct the flyer.
            </p>
          </div>
        )}
      </section>

      <Section
        id="unmatched"
        icon={<SearchX className="size-4" />}
        title="Codes the product master does not have"
        description={
          <>
            <span data-testid="dk-fr-unmatched-counts">
              {adoptionRows.length} code{adoptionRows.length === 1 ? '' : 's'},{' '}
              {adoptedRows.length} adopted
            </span>
            {' - these will not be in the brochure until adopted. Suggestions are never applied'}
            {' for you.'}
          </>
        }
      >
        <ReportGrid
          data-testid="dk-fr-unmatched-grid"
          columns={unmatchedColumns}
          rows={adoptionRows}
          getRowId={(row) => row.code}
          emptyMessage={
            <Empty title="Every printed code resolved to a product">
              Nothing will be dropped from the brochure.
            </Empty>
          }
        />
      </Section>

      <AdoptCodeDialog
        readingId={readingId}
        promotionId={promotionId}
        open={adoptTarget !== null}
        onOpenChange={(open) => {
          if (!open) setAdoptTarget(null);
        }}
        code={adoptTarget?.code ?? null}
        pages={adoptTarget?.pages ?? []}
        suggestion={adoptTarget?.suggestion ?? null}
      />

      <Section
        id="not-promoted"
        icon={<Tag className="size-4" />}
        title="Printed, but not in this promotion"
        description={
          promotionLabel ? (
            <>
              These products show their list price to every reader, because{' '}
              <strong>{promotionLabel}</strong> has no row for them.
            </>
          ) : (
            'Choose the promotion that prices this flyer to see which printed products it does not carry.'
          )
        }
      >
        <ReportGrid
          data-testid="dk-fr-not-promoted-grid"
          columns={notPromotedColumns}
          rows={report.notPromoted}
          getRowId={(row) => row.code}
          emptyMessage={
            promotionLabel ? (
              <Empty title="This promotion carries every printed product">
                Every product on the flyer resolves an offer price for the audiences the
                promotion covers.
              </Empty>
            ) : (
              <Empty tone="neutral" title="No promotion chosen">
                Pick one above and this list fills with the printed products it does not price.
                With no promotion the brochure shows list prices to everybody, which is a
                finished answer rather than a missing one.
              </Empty>
            )
          }
        />
      </Section>

      {/* The only section that writes anything, so it owns its own mutation,
          its own confirmation and its own result. */}
      <DimensionReviewSection
        readingId={readingId}
        candidates={report.dimensionCandidates}
      />

      {/* Beside the sizes, and for the same reason: both are the flyer telling
          the product master something it may not know. */}
      <SpecProposalSection
        readingId={readingId}
        readingStatus={readingStatus}
        codeOverridesChangedAt={codeOverridesChangedAt}
      />

      <Section
        id="duplicates"
        icon={<Copy className="size-4" />}
        title="Printed on more than one page"
        description="Seeded once per page, exactly as the flyer prints it."
      >
        <ReportGrid
          data-testid="dk-fr-duplicates-grid"
          columns={duplicateColumns}
          rows={duplicateRows}
          getRowId={(row) => row.code}
          emptyMessage={
            <Empty title="Every code appears on one page only">
              Nothing to check twice.
            </Empty>
          }
        />
      </Section>

      <Section
        id="headings"
        icon={<Heading className="size-4" />}
        title="What each page will be called"
        description="Guessed from the page. Check them against the paper."
      >
        <ReportGrid
          columns={headingColumns}
          rows={headings}
          getRowId={(row) => String(row.page)}
          data-testid="dk-fr-headings"
          emptyMessage={
            <Empty tone="neutral" title="No pages were read">
              This reading has no pages, which means the upload produced nothing to review.
            </Empty>
          }
        />
      </Section>

      {/* Was four bullets and about a hundred words explaining how the
          extractor works - a feature explanation inside the UI, which the
          repo's own cursor rules ban. The WARNING does real work (a reviewer
          should know the draft needs corrections before they seed it), so it
          stays; the lesson on heuristics does not. The detail moved to the
          user guide, where the assistant can serve it on demand. */}
      <Section
        id="known-gaps"
        icon={<AlertTriangle className="size-4" />}
        title="The draft will need corrections"
        description="Known, and expected."
        action={
          <Button variant="outline" size="sm" asChild>
            <Link href="/dealer-kit/brochure-images">Choose brochure photos</Link>
          </Button>
        }
      >
        <p
          className="rounded-lg border border-border px-4 py-3 text-sm text-muted-foreground"
          data-testid="dk-fr-known-gaps"
        >
          Headings are guessed, tiles have no photo until one is chosen, and some printed rows
          split in two. Reading a flyer changes no product: sizes reach the master only for the
          rows you tick.
        </p>
      </Section>
    </div>
  );
}
