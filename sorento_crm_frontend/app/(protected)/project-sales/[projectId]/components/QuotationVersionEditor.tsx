'use client';

import * as React from 'react';
import { RefreshCw } from 'lucide-react';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import { useUOMSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query';
import { useBrandSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query';
// The shared products `/select` mapper, keeping the fields a picked product decides.
import { getProductsForLineSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import type { ProductLineRef } from '@/app/(protected)/master-data-management/products/types/product.types';
import {
  useQuotationLines,
  useQuotationRecomputeMutation,
  useQuotationVersions,
  useSeriesProductRows,
} from '../../_shared/hooks/useProjects';
import type {
  Project,
  ProjectQuotation,
  QuotationLine,
  QuotationLineBulkItem,
  QuotationRecomputeResult,
  StagedQuotationLine,
} from '../../_shared/types/project.types';
import { ReviseQuotationDialog } from './ReviseQuotationDialog';
import { QuotationLinesGrid } from './QuotationLinesGrid';
import { StockAtSelect, useQuotationStockGroup } from './QuotationStockChip';
import {
  lineErrors,
  serverToDraft,
  toBody,
  totalFromDrafts,
  type LineDraft,
} from './quotationLineDraft';

/** Staged lines that are not ready to be written. Counted so a refusal can say how many. */
export function unfinishedStagedLines(lines: StagedQuotationLine[]): number {
  return lines.filter(
    (line) => !line.removed && Object.keys(lineErrors(line.draft)).length > 0,
  ).length;
}

/**
 * A staged set as the body of the whole-set write.
 *
 * Removed lines are simply left out, which is exactly how the endpoint deletes them, and a line
 * added in this session carries no `id` so the server reads it as new. Order is array position;
 * `sort_order` is not sent at all. Exported because the SAVE lives on the document screen: one
 * button covers every scope, so it cannot live inside one scope's editor.
 */
export function stagedLinesToBody(lines: StagedQuotationLine[]): QuotationLineBulkItem[] {
  return lines
    .filter((line) => !line.removed)
    .map((line) =>
      line.id ? { id: line.id, ...toBody(line.draft, line.line) } : toBody(line.draft, line.line),
    );
}

/**
 * What the scope comes to, off the staged drafts, by the same rule the footer and the backend
 * both apply. Exported so the document header can sum the scopes itself rather than being told a
 * figure by whichever editor happens to be mounted.
 */
export function stagedScopeTotal(lines: StagedQuotationLine[]): string | null {
  return totalFromDrafts(lines.filter((line) => !line.removed).map((line) => line.draft));
}

/**
 * What the quotation form page hands down so one scope can be edited without writing anything.
 *
 * Its presence IS edit mode for this scope. Absent, the table is a clean read: no inputs, no
 * per-row saves, nothing to press by accident. That was the client's complaint in one sentence -
 * "every addition of line doesn't trigger a save ... very annoying" - and the answer is a view
 * that is a view, plus Edit quotation, whose form page stages every scope and saves them once
 * (#1341: the same editor, moved under the form's Lines tab).
 */
export type QuotationScopeEditing = {
  /** The scope's staged lines, or null until it has been seeded from the server's rows. */
  staged: StagedQuotationLine[] | null;
  seed: (versionId: string, lines: StagedQuotationLine[]) => void;
  stage: (lines: StagedQuotationLine[]) => void;
  toggleRemoved: (key: string) => void;
};

/**
 * The versions of one scope, and the lines of whichever version is being looked at.
 *
 * Only the newest version is editable, and the editor works that out from the server's
 * `is_current` rather than from a local guess. Everything below it is history the
 * customer already holds (AC-E3), so an older version renders read-only with the reason
 * stated, not merely with its buttons missing.
 *
 * With no `edit` it is a READ. Every field is text, there is nothing to add and nothing to save,
 * because a screen that saves on blur cannot also be the screen somebody reads a quotation on.
 */
export function QuotationVersionEditor({
  project,
  quotation,
  seriesId,
  edit,
}: {
  project: Project;
  /**
   * The scope. Null on the form page for a scope added there and not saved yet (#1341): it has no
   * version, so there is nothing to read, and its lines start empty and are staged like any other.
   */
  quotation: ProjectQuotation | null;
  /** The series the form has staged for this scope, over the saved one. */
  seriesId?: string | null;
  /** Set by the form page, which holds the staged lines. See `QuotationScopeEditing`. */
  edit?: QuotationScopeEditing | null;
}) {
  const isUnsaved = quotation === null;
  const versions = useQuotationVersions(quotation?.id);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [revising, setRevising] = React.useState(false);
  const recompute = useQuotationRecomputeMutation(project.id);
  const [recomputed, setRecomputed] = React.useState<QuotationRecomputeResult | null>(null);
  const {
    value: lineSearchInput,
    setValue: setLineSearchInput,
    debouncedValue: lineSearch,
  } = useDebouncedSearch();

  const rows = React.useMemo(
    () => [...(versions.data ?? [])].sort((a, b) => b.version_no - a.version_no),
    [versions.data],
  );
  const current = rows.find((version) => version.is_current) ?? null;
  const selected = rows.find((version) => version.id === selectedId) ?? current;

  const lines = useQuotationLines(selected?.id);

  // `is_editable` from the server, NOT `is_current`. An issued version is still the highest
  // version until somebody revises, so gating on `is_current` left the fields open on a
  // quotation the customer already holds: the user typed a price, the server refused with 422,
  // and the number stayed on screen looking saved.
  // A scope added on the form has no version yet, and is editable by the one who is adding it.
  const editable =
    (isUnsaved || Boolean(selected?.is_editable ?? selected?.is_current)) && project.can_edit;
  const sortedLines = React.useMemo(
    () => [...(lines.data ?? [])].sort((a, b) => a.sort_order - b.sort_order),
    [lines.data],
  );

  // The line stores the CODE, not the id, so the option value is the code. Cached with an
  // infinite staleTime by the shared hook, so opening five versions costs one request.
  const uoms = useUOMSelectQuery();
  const uomOptions = React.useMemo(
    () =>
      (uoms.data ?? []).map((unit) => ({
        value: unit.uom_code,
        label: unit.uom_code,
        description: unit.uom_name,
      })),
    [uoms.data],
  );
  // The line snapshots the brand as TEXT (what was quoted, not who owns the SKU today), so the
  // product's brand_id has to be resolved to a name before it can go on a row. Same cached
  // master-data hook the rest of the app uses; no UUID reaches the cell.
  const brands = useBrandSelectQuery();
  // The brands master, offered by NAME: the line stores the name it was quoted under.
  const brandOptions = React.useMemo<SearchableSelectOption[]>(
    () => (brands.data ?? []).map((row) => ({ value: row.brand_name, label: row.brand_name })),
    [brands.data],
  );
  const stock = useQuotationStockGroup();

  /**
   * The products the picker has shown, by id.
   *
   * The dropdown already holds the whole record when somebody chooses one, so the fill reads it
   * from here instead of asking the server again for a row it just rendered.
   */
  const productsById = React.useRef(new Map<string, ProductLineRef>());

  const fetchProducts = React.useCallback(async (query: string) => {
    const products = await getProductsForLineSelect(query || undefined);
    products.forEach((product) => productsById.current.set(product.id, product));
    return products.map((product) => ({
      value: product.id,
      label: product.product_code,
      description: product.product_name,
    }));
  }, []);

  /**
   * What picking a product decides for the rest of its line.
   *
   * Every one of these fields is the product's, so a line quoting SRT-WC-01 reads the same
   * whoever typed it. The shared table applies this AS STATED, over hand-typed text included
   * (the client's call: one product, one set of fields, every time), so this returns a key even
   * when the product has nothing for it - that is how the previous product's wording is cleared.
   *
   * `list_price` has no column: it is the annotation under the unit price, and it must follow
   * the product from the moment it is picked rather than waiting for the save to come back.
   *
   * NOT filled, both deliberately:
   * - `technical_spec`. The client asked for it, and the products table has no such column -
   *   only the quotation line does. Nothing to copy, so the cell is left for the salesperson
   *   rather than filled with a guess, and returning it blank would wipe what they wrote.
   * - `unit_price`. It is the QUOTED price, not the product's, and under an always-overwrite
   *   fill a re-pick would throw away a negotiated figure. The list price is shown beside it
   *   so the discount is visible; the number that gets charged stays a decision.
   */
  /**
   * What THIS scope's series sells each product for (T5, AC-C1).
   *
   * Only fetched when the scope is actually quoted from a series; most are not, and an
   * unconditional request would be a round trip for an answer of "nothing".
   */
  const seriesRows = useSeriesProductRows(
    (seriesId !== undefined ? seriesId : quotation?.series_id) ?? undefined,
  );
  const seriesPriceByProduct = React.useMemo(() => {
    const map = new Map<string, string>();
    (seriesRows.data ?? []).forEach((row) => {
      if (row.selling_price) map.set(row.product_id, row.selling_price);
    });
    return map;
  }, [seriesRows.data]);

  const fillFromProduct = React.useCallback(
    (option: { value: string }): LineDraft => {
      const product = productsById.current.get(option.value);
      if (!product) return {};
      const brand = (brands.data ?? []).find((row) => row.id === product.brand_id);
      const unit = (uoms.data ?? []).find((row) => row.id === product.base_uom_id);
      const filled: LineDraft = {
        // The rule the backend snapshots by, so what shows now is what gets stored.
        description: product.description || product.product_name || '',
        brand_snapshot: brand?.brand_name ?? '',
        uom: unit?.uom_code ?? '',
        list_price: product.list_price ?? '',
      };
      // The SERIES price, where this scope is quoted from one that names the product
      // (AC-C1). That is the price actually agreed for this scope, so it is the one the
      // line should open at - the salesperson can still type over it, and the below-floor
      // check then judges whatever they typed. Where the series says nothing, `unit_price`
      // is left untouched and the line behaves exactly as it did before (AC-C2).
      const agreed = seriesPriceByProduct.get(option.value);
      if (agreed) filled.unit_price = agreed;
      return filled;
    },
    [brands.data, seriesPriceByProduct, uoms.data],
  );

  /**
   * The product the picker trigger names, read off the DRAFT: nothing refetches during an edit
   * session, so resolving it from the stored line would name the product that was there before
   * the pick, right up until Save. Async mode fetches a page at a time, so a saved row's own
   * product has to be handed in or it would read as empty until somebody searched for it.
   */
  const resolveProduct = React.useCallback(
    (stored: QuotationLine | null, draft: LineDraft): SearchableSelectOption | undefined => {
      const productId = draft.product_id;
      if (!productId) return undefined;
      const picked = productsById.current.get(productId);
      if (picked) {
        return {
          value: productId,
          label: picked.product_code,
          description: picked.product_name ?? undefined,
        };
      }
      return {
        value: productId,
        label:
          stored?.product_id === productId
            ? (stored.product_code ?? 'Selected product')
            : 'Selected product',
        description:
          stored?.product_id === productId ? (stored.description ?? undefined) : undefined,
      };
    },
    [],
  );

  /**
   * Edit mode for THIS scope: the screen asked for it, and the server still allows it.
   *
   * Both halves matter. A frozen version stays a read even inside an open edit session - the
   * customer holds it, and the way to change it is a revision, which the shell offers.
   */
  const isEditing = Boolean(edit) && editable;
  const staged = edit?.staged ?? null;

  /**
   * The scope's starting point, taken from the server's rows the first time it is opened for
   * editing. Seeded through the shell, never held here: this component unmounts on a tab switch
   * and on a scope switch, and the staged work has to survive both.
   */
  const seedScope = edit?.seed;
  React.useEffect(() => {
    if (!seedScope || !isEditing || staged !== null) return;
    // Nothing on the server to start from: an unsaved scope starts with no lines.
    if (isUnsaved) {
      seedScope('', []);
      return;
    }
    if (!selected || lines.isLoading) return;
    seedScope(
      selected.id,
      sortedLines.map((line) => ({
        id: line.id,
        key: line.id,
        line,
        draft: serverToDraft(line),
        removed: false,
      })),
    );
  }, [isEditing, isUnsaved, lines.isLoading, seedScope, selected, sortedLines, staged]);

  /**
   * What the grid draws, in display order: the staged set while editing, the server's rows
   * otherwise, in one shape so there is one set of columns rather than two that can drift apart.
   * Until the session has seeded this scope the server's rows show as a read.
   */
  const gridEditing = isEditing && staged !== null;
  const gridLines = React.useMemo<StagedQuotationLine[]>(
    () =>
      gridEditing && staged
        ? staged
        : sortedLines.map((line) => ({
            id: line.id,
            key: line.id,
            line,
            draft: serverToDraft(line),
            removed: false,
          })),
    [gridEditing, sortedLines, staged],
  );

  // Matched on the LIVE draft, so a description typed seconds ago is already findable, plus the
  // saved code the draft does not carry as text.
  const needle = lineSearch.trim().toLowerCase();
  const rowFilter = React.useMemo(
    () =>
      needle
        ? (line: StagedQuotationLine) =>
            [
              line.line?.product_code,
              line.draft.description,
              line.draft.brand_snapshot,
              line.draft.band_label,
            ].some((value) => (value ?? '').toLowerCase().includes(needle))
        : undefined,
    [needle],
  );

  const stageLines = edit?.stage;
  const toggleRemoved = edit?.toggleRemoved;

  if (versions.isLoading) return <Skeleton className="h-24 w-full" />;

  // `min-w-0` so a wide line table scrolls in its own gutter instead of stretching the
  // panel it sits in, which at 375px would take the whole page sideways with it.
  return (
    <div className="min-w-0 space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-1.5">
          {rows.map((version) => (
            <button
              key={version.id}
              type="button"
              onClick={() => setSelectedId(version.id)}
              aria-current={selected?.id === version.id}
              className={
                selected?.id === version.id
                  ? 'rounded-md border border-primary bg-primary/10 px-2.5 py-1 text-xs font-medium'
                  : 'rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground hover:bg-muted'
              }
            >
              {`v${version.version_no}`}
              {version.is_current ? '' : ' (frozen)'}
            </button>
          ))}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Re-ask both alerts against today's master data (S19). On the version that is
              being looked at, and only while it is still open: the flags on a frozen
              version are what was true when the customer was sent the paper.

              Withheld while an edit session is open on this scope. Recompute writes to the
              server, and the staged rows on screen would silently be answering for a state
              the database no longer holds. */}
          {project.can_edit && editable && selected && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={recompute.isPending || isEditing}
              title={
                isEditing
                  ? 'Save or cancel your changes first'
                  : 'Re-check every line against the series and price floors as they stand today'
              }
              onClick={async () => {
                try {
                  setRecomputed(await recompute.mutateAsync(selected.id));
                } catch {
                  // The mutation already toasted the reason.
                }
              }}
            >
              <RefreshCw className="size-4" aria-hidden />
              {recompute.isPending ? 'Rechecking...' : 'Recheck alerts'}
            </Button>
          )}

          {/* Not on the form page: a revise writes a new version at once, under lines the form
              has staged against the current one and saves by scope. The quotation page's Edit
              quotation already asks to revise before it opens the form. */}
          {project.can_edit && current && !edit && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setRevising(true)}
            >
              {`Revise to v${current.version_no + 1}`}
            </Button>
          )}
        </div>
      </div>

      {recomputed && (
        <RecomputeSummary result={recomputed} onDismiss={() => setRecomputed(null)} />
      )}

      {/* No "Issued by / Opened" strip above the table: the owner moved it into the Header tab's
          details ("this one should be in header details", #1341). */}
      {/* Why this version cannot be edited, and the way out, in one sentence. The old copy
          ("The customer holds this version, so its lines cannot be changed. Open a revision to
          re-price it.") described a state and offered no move, which is exactly what the client
          read and still could not act on. Edit quotation in the gear is the move now. */}
      {selected?.is_issued && selected.is_current && (
        <p className="text-xs text-muted-foreground">
          {`The customer holds v${selected.version_no}. Edit quotation opens v${selected.version_no + 1} and leaves what was sent untouched.`}
        </p>
      )}

      {selected && !selected.is_current && (
        <p className="text-xs text-muted-foreground">
          {`Frozen. Make changes on ${current ? `v${current.version_no}` : 'the current version'}.`}
        </p>
      )}

      {/* Search over the lines. Hides rows rather than removing them, so item numbers hold,
          totals still sum the whole version, and an unsaved draft on a hidden line survives
          the search that hid it. Ctrl-F cannot do this job: it only finds what is scrolled
          into the DOM, so on a 59-line version it answers 0/0 for lines that plainly exist. */}
      {gridLines.length > 0 && (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <ListSearchInput
            value={lineSearchInput}
            onChange={setLineSearchInput}
            placeholder="Search lines"
            aria-label="Search lines by product or description"
            className="w-full sm:max-w-xs"
          />
          <StockAtSelect groups={stock.groups} value={stock.group} onChange={stock.choose} />
        </div>
      )}

      {lines.isLoading && !isUnsaved ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <QuotationLinesGrid
          // A fresh grid when the session opens and again when it closes, so Cancel puts every
          // row back the way the server has it.
          key={gridEditing ? 'editing' : 'reading'}
          lines={gridLines}
          editing={gridEditing}
          quotationId={quotation?.id ?? null}
          rowFilter={rowFilter}
          emptyHint={
            gridEditing
              ? 'Nothing quoted yet.'
              : editable
                ? 'No lines yet.'
                : 'This version was frozen without any lines.'
          }
          filterEmptyHint={`No line matches "${lineSearch.trim()}".`}
          fetchProducts={fetchProducts}
          resolveProduct={resolveProduct}
          fillFromProduct={fillFromProduct}
          uomOptions={uomOptions}
          uomError={uoms.error}
          onUomRetry={() => void uoms.refetch()}
          brandOptions={brandOptions}
          stockGroup={stock.group}
          onStage={gridEditing ? stageLines : undefined}
          onToggleRemoved={gridEditing ? toggleRemoved : undefined}
        />
      )}

      {revising && current && quotation && (
        <ReviseQuotationDialog
          projectId={project.id}
          quotation={quotation}
          currentVersionNo={current.version_no}
          lineCount={sortedLines.length}
          onDone={(newVersionId) => {
            setRevising(false);
            // Land on the version that was just opened, not the one being read a moment ago.
            if (newVersionId) setSelectedId(newVersionId);
          }}
        />
      )}

    </div>
  );
}

/**
 * What a recompute changed, in the words a reader would use (S19).
 *
 * Composed here rather than on the server: the counts are facts, the sentence is copy, and
 * copy belongs on the screen that shows it. "Nothing changed" is a REAL answer and gets its
 * own sentence - a reader has to be able to tell "I checked and it was already right" from
 * "I pressed a button and nothing happened".
 */
export function describeRecompute(result: QuotationRecomputeResult): string {
  const parts: string[] = [];
  if (result.no_longer_non_standard > 0) {
    parts.push(
      `${result.no_longer_non_standard} ${result.no_longer_non_standard === 1 ? 'line is' : 'lines are'} no longer non-standard`,
    );
  }
  if (result.now_non_standard > 0) {
    parts.push(
      `${result.now_non_standard} ${result.now_non_standard === 1 ? 'line is' : 'lines are'} now non-standard`,
    );
  }
  if (result.no_longer_below_floor > 0) {
    parts.push(
      `${result.no_longer_below_floor} ${result.no_longer_below_floor === 1 ? 'line is' : 'lines are'} no longer below floor`,
    );
  }
  if (result.now_below_floor > 0) {
    parts.push(
      `${result.now_below_floor} ${result.now_below_floor === 1 ? 'line is' : 'lines are'} now below floor`,
    );
  }
  if (result.floor_changed > 0) {
    parts.push(
      `${result.floor_changed} ${result.floor_changed === 1 ? 'line' : 'lines'} picked up a different floor`,
    );
  }
  if (parts.length === 0) {
    return `Nothing changed. All ${result.line_count} ${
      result.line_count === 1 ? 'line' : 'lines'
    } already match the series and price floors as they stand today.`;
  }
  return `${parts.join(', ')}.`;
}

/** The report, on the page rather than in a toast that takes it away after four seconds. */
function RecomputeSummary({
  result,
  onDismiss,
}: {
  result: QuotationRecomputeResult;
  onDismiss: () => void;
}) {
  return (
    <div
      role="status"
      className="flex flex-col gap-2 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs sm:flex-row sm:items-start sm:justify-between"
    >
      <div className="min-w-0">
        <p className="font-medium">{describeRecompute(result)}</p>
        {result.changed_lines.length > 0 && (
          <p className="mt-0.5 break-words text-muted-foreground">
            {result.changed_lines.join(', ')}
          </p>
        )}
        {/* Why some lines could not move. Without this the reader gets "nothing changed"
            over a set of lines naming products this company does not stock, which is true
            and tells them nothing about what to do next. */}
        {result.unresolved_products > 0 && (
          <p className="mt-0.5 text-muted-foreground">
            {`${result.unresolved_products} ${
              result.unresolved_products === 1 ? 'line names a product' : 'lines name products'
            } this company's catalogue does not carry, so ${
              result.unresolved_products === 1 ? 'it reads as a custom item' : 'they read as custom items'
            }. Re-pick the product to clear it.`}
          </p>
        )}
      </div>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className="shrink-0 self-start"
        onClick={onDismiss}
      >
        Dismiss
      </Button>
    </div>
  );
}
