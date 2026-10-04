'use client';

import * as React from 'react';
import { AlertTriangle, RefreshCw, TriangleAlert } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useUOMSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query';
import { useBrandSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query';
// The shared products `/select` mapper, keeping the fields a picked product decides.
import { getProductsForLineSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import type { ProductLineRef } from '@/app/(protected)/master-data-management/products/types/product.types';
import {
  useLineVerdict,
  useQuotationLines,
  useQuotationRecomputeMutation,
  useQuotationVersions,
  useSeriesProductRows,
} from '../../_shared/hooks/useProjects';
import type {
  Project,
  ProjectQuotation,
  QuotationLine,
  QuotationLineVerdict,
  QuotationLineBulkItem,
  QuotationRecomputeResult,
  StagedQuotationLine,
  UnitType,
} from '../../_shared/types/project.types';
import {
  InlineLineTable,
  INLINE_CHECKED,
  isInlineChecked,
  type InlineDraft,
  type InlineLineColumn,
  type InlineStaging,
} from '../../_shared/components/InlineLineTable';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import { QuotationLinePhoto } from './QuotationLinePhoto';
import { ReviseQuotationDialog } from './ReviseQuotationDialog';
import { formatMyr } from './QuotationsPanel';
import { formatMyrExact, isDecimalString, multiplyMoney, sumMoney } from '../../_shared/lib/money';

const FLOOR_LEVEL_LABELS: Record<string, string> = {
  product: 'this product',
  category: 'its category',
  category_ancestor: 'a parent category',
  system: 'the company default',
};

const UNIT_TYPE_OPTIONS: { value: UnitType; label: string; description: string }[] = [
  { value: 'house_unit', label: 'Per house unit', description: 'Multiplied by the unit count' },
  { value: 'bathroom', label: 'Per bathroom', description: 'Multiplied by bathrooms per unit' },
  { value: 'facility', label: 'Per facility', description: 'Gym, pool, surau' },
  { value: 'common_area', label: 'Common area', description: 'Priced once for the whole site' },
];

/**
 * A row of the line table: the stored line, or a stand-in for one added in this edit session.
 *
 * `id` is the row's identity - the line's own id when it has one, the staged key when it does
 * not - so one set of columns serves both the read and the edit.
 */
type LineRow = { id: string; line: QuotationLine | null };

/** A stored line as an editable draft. Every field the bulk write sends has to be in here. */
function serverToDraft(line: QuotationLine): InlineDraft {
  return {
    product_id: line.product_id ?? '',
    description: line.description ?? '',
    technical_spec: line.technical_spec ?? '',
    // The response names this one `brand`, the request `brand_snapshot`. The draft holds the
    // request's name so the payload stays a copy rather than a translation.
    brand_snapshot: line.brand ?? '',
    quantity: line.quantity ?? '1',
    uom: line.uom ?? '',
    unit_price: line.unit_price ?? '',
    complete_set: line.complete_set ?? '',
    unit_type: line.unit_type ?? '',
    is_rate_only: line.is_rate_only ? INLINE_CHECKED : '',
    band_label: line.band_label ?? '',
    notes: line.notes ?? '',
    // No column of its own: read under the unit price, and never sent back (the server
    // snapshots it from the product it belongs to).
    list_price: line.list_price ?? '',
  };
}

/** A brand-new row's starting draft. Also the table's "untouched" comparison. */
function emptyDraft(): InlineDraft {
  return {
    product_id: '',
    description: '',
    technical_spec: '',
    brand_snapshot: '',
    quantity: '1',
    uom: '',
    unit_price: '',
    complete_set: '',
    unit_type: '',
    is_rate_only: '',
    band_label: '',
    notes: '',
    list_price: '',
  };
}

/**
 * The one rule a quotation line has to satisfy before it can be stored.
 *
 * Off-catalog lines carry the description the customer reads, so it is the one thing that cannot
 * be left out. Written once and used twice - to mark the cell at fault, and to stop a Save that
 * would only come back as a 422 - so the desk and the button cannot disagree about what is wrong.
 */
function lineErrors(draft: InlineDraft): Record<string, string> {
  return !draft.product_id && !draft.description.trim()
    ? { description: 'Needed on an off-catalog line' }
    : {};
}

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
    .map((line) => (line.id ? { id: line.id, ...toBody(line.draft) } : toBody(line.draft)));
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
    (option: { value: string } | null): InlineDraft => {
      if (!option) return {};
      const product = productsById.current.get(option.value);
      if (!product) return {};
      const brand = (brands.data ?? []).find((row) => row.id === product.brand_id);
      const unit = (uoms.data ?? []).find((row) => row.id === product.base_uom_id);
      const filled: InlineDraft = {
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
   * The photo viewer, opened from the Photo cell and scrolled with the arrows in its own header.
   *
   * The list it scrolls through is assembled further down this component, but `columns` (which
   * needs the opener) is built here, above it. Hence the ref: the opener reads it on a CLICK,
   * never during render, so its identity stays stable and the column memo does not churn on
   * every keystroke. The alternative was reordering a 900-line component to satisfy a lookup.
   */
  const photoLinesRef = React.useRef<QuotationLine[]>([]);
  const [previewIndex, setPreviewIndex] = React.useState<number | null>(null);
  const openPreview = React.useCallback((lineId: string) => {
    const index = photoLinesRef.current.findIndex((line) => line.id === lineId);
    if (index >= 0) setPreviewIndex(index);
  }, []);

  // The order the printed quotation uses, so a line is entered reading left to right the way
  // the customer will read it back: item number, what it is, how much, then what it comes to.
  const columns = React.useMemo<InlineLineColumn<LineRow>[]>(
    () => [
      {
        key: 'item_no',
        header: 'Item',
        width: 70,
        /**
         * The row's position, not a box to type in.
         *
         * It was free text ("Item A"), which asked the salesperson to number 52 lines by hand
         * and to renumber all of them after every insert, and let two lines claim the same
         * letter. It counts CONTINUOUSLY through the scope - 1, 2 under the first heading and
         * 3, 4 under the second - because that is how the customer's own bill of quantities
         * reads (the client's decision); a section is a heading over the sequence, not a
         * restart of it.
         */
        kind: 'derived',
        derive: (_draft, index) => index + 1,
      },
      {
        /**
         * The product's photograph, in the printed document's own position: the client's issued
         * quotation has PRODUCT IMAGE in column B, immediately after ITEM.
         *
         * Read-only and server-decided. There is exactly ONE image decision in the system -
         * `product_attachments.is_primary`, which the brochure and 3D-model generation already
         * read - and it is recorded on the PRODUCT, not pinned to a quotation line. Letting this
         * cell set a picture would be a second answer to the same question, which is the defect
         * that flag exists to remove. So the empty state LINKS to where the decision is made
         * rather than offering to make it here.
         */
        key: 'product_image',
        header: 'Photo',
        width: 96,
        kind: 'derived',
        derive: (_draft, _index, row) => (
          <QuotationLinePhoto
            line={row?.line ?? null}
            onPreview={row?.line ? () => openPreview(row.line!.id) : undefined}
          />
        ),
      },
      {
        key: 'product_id',
        header: 'Product',
        width: 200,
        kind: 'searchable-select',
        placeholder: 'Off-catalog line',
        fetchOptions: fetchProducts,
        // One decision fills the row: the description, brand, unit and list price all come off
        // the product record rather than being retyped beside it.
        onOptionSelected: (option) => fillFromProduct(option),
        /**
         * Async mode fetches a page at a time, so the row's own product has to be handed in or
         * a saved row would read as empty until somebody searched for it.
         *
         * Read off the DRAFT, not off the stored line. Under the old save-per-row the two could
         * not disagree for long, because a refetch followed every pick. Nothing refetches during
         * an edit session, so resolving from the stored line would leave the trigger naming the
         * product that was there before the pick, right up until Save.
         */
        resolveSelected: (row, draft) => {
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
          const stored = row?.line;
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
        annotate: (row, draft) => (
          <LineFlags quotationId={quotation?.id ?? null} line={row?.line ?? null} draft={draft} />
        ),
      },
      {
        key: 'description',
        header: 'Description',
        width: 240,
        kind: 'text',
        placeholder: "The product's own description",
      },
      {
        key: 'technical_spec',
        header: 'Tech spec',
        width: 200,
        kind: 'text',
        placeholder: 'Rimless, 4/2.6L dual flush',
      },
      {
        // The draft key is the REQUEST field. The response calls the same value `brand`, and
        // naming the cell after what is sent keeps `toBody` a straight copy of the draft.
        key: 'brand_snapshot',
        header: 'Brand',
        width: 140,
        kind: 'text',
        placeholder: 'SORENTO',
        maxLength: 100,
      },
      {
        key: 'quantity',
        header: 'Qty',
        width: 96,
        kind: 'number',
        align: 'end',
        validate: (value) =>
          value.trim() === '' || isDecimalString(value) ? null : 'Must be a number',
        formatReadOnly: (value) => trimAmount(value),
      },
      {
        key: 'uom',
        header: 'UOM',
        width: 110,
        // A dropdown off the master list, not free text: a line typed "pcs" and another
        // "PCS" are the same unit to a reader and two different strings to every report.
        kind: 'select',
        placeholder: 'PCS',
        options: uomOptions,
        loadError: uoms.error,
        onRetry: () => void uoms.refetch(),
        resolveSelected: (_line, draft) =>
          uomOptions.find((option) => option.value === draft.uom),
      },
      {
        key: 'unit_price',
        header: 'Unit price',
        width: 128,
        kind: 'number',
        align: 'end',
        placeholder: '0.00',
        validate: (value) =>
          value.trim() === '' || isDecimalString(value) ? null : 'Must be a number',
        formatReadOnly: (value) => formatMyr(value),
        // The DRAFT's list price first: picking a product must show its price at once, and
        // reading the saved row instead is what left "List RM 0.00" beside a real product
        // until the save came back.
        annotate: (row, draft) => {
          const listPrice = draft.list_price || row?.line?.list_price;
          return listPrice ? (
            <span className="mt-0.5 block text-end text-xs text-muted-foreground">
              {`List ${formatMyr(listPrice)}`}
            </span>
          ) : null;
        },
      },
      {
        key: 'complete_set',
        header: 'Complete set',
        width: 160,
        kind: 'text',
        placeholder: 'c/w seat cover, flush valve',
        maxLength: 100,
      },
      {
        key: 'unit_type',
        header: 'Counts per',
        width: 168,
        kind: 'select',
        placeholder: 'Not counted per unit',
        options: UNIT_TYPE_OPTIONS,
        resolveSelected: (_line, draft) =>
          UNIT_TYPE_OPTIONS.find((option) => option.value === draft.unit_type),
      },
      {
        // In the row, next to the money it governs, rather than behind the notes popover: a
        // reader scanning the Total column has to be able to see WHY a line says "rate only"
        // without opening anything, and five of them in the sample quotation is normal.
        key: 'is_rate_only',
        header: 'Rate only',
        width: 92,
        kind: 'checkbox',
      },
      {
        key: 'line_total',
        header: 'Total',
        width: 128,
        kind: 'derived',
        align: 'end',
        // Recomputed from the draft rather than read off the row, so the number moves
        // while a quantity is being typed instead of after the save lands. A rate-only line
        // prints the words: blank reads as "we forgot" and RM 0.00 reads as "free", while the
        // customer is in fact being quoted a rate that nobody adds up (AC-C2, AC-D3).
        derive: (draft) =>
          isInlineChecked(draft.is_rate_only) ? (
            <Badge variant="secondary" appearance="light">
              rate only
            </Badge>
          ) : (
            // Grouped from the STRING `multiplyMoney` produced, like the footer below it, so
            // one column is never rendered by two different formatters.
            formatMyrExact(multiplyMoney(draft.quantity, draft.unit_price) ?? '0')
          ),
        /**
         * The version's total, under the column it sums. It used to sit in the metadata strip
         * beside "Issued by" and "Opened", where it read as one more fact about the version
         * rather than as the sum of the numbers directly above it.
         *
         * Summed from the LIVE drafts, so it moves with the numbers above it. Reading the saved
         * rows instead left the footer stating last save's answer while the cells said something
         * else, and the first thing anybody does after changing a quantity is look down here.
         * Off the STRINGS, to the cent, and skipping rate-only lines exactly as the backend's
         * total does, so this footer and the document's grand total cannot drift apart.
         */
        footer: (drafts) => {
          const total = totalFromDrafts(drafts);
          return total === null ? '-' : formatMyrExact(total);
        },
      },
    ],
    [fetchProducts, fillFromProduct, openPreview, uomOptions, uoms.error, uoms.refetch],
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
   * What the table draws, in display order: the staged set while editing, the server's rows
   * otherwise. One row type either way, so there is one set of columns rather than two that can
   * drift apart.
   */
  const lineRows = React.useMemo<LineRow[]>(
    () =>
      isEditing && staged
        ? staged.map((line) => ({ id: line.key, line: line.line }))
        : sortedLines.map((line) => ({ id: line.id, line })),
    [isEditing, sortedLines, staged],
  );

  /**
   * Every line that actually HAS a photograph, in table order - what the viewer's next/previous
   * scroll through. Lines without one are left out rather than shown as blanks: paging through
   * forty empty frames to reach the second picture is not a gallery.
   */
  const photoLines = React.useMemo(
    () =>
      lineRows
        .map((row) => row.line)
        .filter((line): line is QuotationLine =>
          Boolean(
            line &&
              line.product_image?.state === 'chosen' &&
              (line.product_image.preview_url || line.product_image.url),
          ),
        ),
    [lineRows],
  );
  photoLinesRef.current = photoLines;

  const photoItems = React.useMemo<AttachmentPreviewItem[]>(
    () =>
      photoLines.map((line) => {
        const image = line.product_image!;
        return {
          id: image.attachment_id ?? line.id,
          name: image.filename ?? `${line.product_code ?? 'Product'} photo`,
          // The ORIGINAL, falling back to the thumbnail only if the original could not be
          // signed - a blurry preview beats an empty one.
          url: image.preview_url || image.url || '',
          // The same authenticated route Resource Management downloads through, so the button
          // in the viewer's header behaves identically to the one on the Files page.
          downloadUrl: image.attachment_id
            ? `/api/v1/resource-management/attachments/${image.attachment_id}/download`
            : undefined,
        };
      }),
    [photoLines],
  );

  const stagedByKey = React.useMemo(() => {
    const map = new Map<string, StagedQuotationLine>();
    (staged ?? []).forEach((line) => map.set(line.key, line));
    return map;
  }, [staged]);

  const toDraft = React.useCallback(
    (row: LineRow): InlineDraft =>
      // The staged draft when there is one, so an edit made before a tab switch is what comes
      // back afterwards rather than the server's untouched row.
      stagedByKey.get(row.id)?.draft ?? (row.line ? serverToDraft(row.line) : emptyDraft()),
    [stagedByKey],
  );

  const stageLines = edit?.stage;
  const toggleRemoved = edit?.toggleRemoved;
  const staging = React.useMemo<InlineStaging<LineRow> | undefined>(() => {
    if (!isEditing || !stageLines || !toggleRemoved) return undefined;
    return {
      onChange: (reported) =>
        stageLines(
          reported.map(({ rowKey, draft }) => {
            // A key the shell has not seen is a row the table has just added: no id, no stored
            // line, and the bulk write will read it as new.
            const held = stagedByKey.get(rowKey);
            return {
              id: held?.id ?? null,
              key: rowKey,
              line: held?.line ?? null,
              draft,
              removed: held?.removed ?? false,
            };
          }),
        ),
      isRemoved: (row) => stagedByKey.get(row.id)?.removed ?? false,
      toggleRemove: (rowKey) => toggleRemoved(rowKey),
    };
  }, [isEditing, stageLines, stagedByKey, toggleRemoved]);

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
      {lineRows.length > 0 && (
        <ListSearchInput
          value={lineSearchInput}
          onChange={setLineSearchInput}
          placeholder="Search lines"
          aria-label="Search lines by product or description"
          className="w-full sm:max-w-xs"
        />
      )}

      <InlineLineTable<LineRow>
        /**
         * A fresh table when the session opens and again when it closes.
         *
         * The table keeps its own drafts and deliberately refuses to overwrite a dirty row from
         * `rows` - that is what stops a neighbour's refetch wiping what somebody is typing. It
         * also means Cancel could not put a row back: the staged set disappears and the table
         * carries on showing the edit. Remounting is the honest reset.
         */
        key={isEditing ? 'editing' : 'reading'}
        rows={lineRows}
        getRowId={(row) => row.id}
        columns={columns}
        toDraft={toDraft}
        emptyDraft={emptyDraft}
        // A view is a view: outside an edit session there is nothing to type into, nothing to
        // add and nothing that can be saved by moving the caret.
        readOnly={!isEditing}
        isLoading={lines.isLoading}
        addLabel="Add a line"
        addSectionLabel="Add a section"
        staging={staging}
        emptyHint={
          isEditing
            ? 'Nothing quoted yet. Add a line and pick a product to freeze its code, description and list price onto it.'
            : editable
              ? undefined
              : 'This version was frozen without any lines.'
        }
        describeRow={(row, index) =>
          row?.line?.product_code ?? row?.line?.description ?? `line ${index + 1}`
        }
        rowDetail={{
          key: 'notes',
          label: 'Notes',
          placeholder: 'Why this price, what was agreed, what it replaces',
        }}
        // Free text off the customer's own bill of quantities. The CRM does not model bill and
        // page numbers because the next customer numbers their BQ differently (AC-C3).
        band={{
          key: 'band_label',
          label: 'Section heading',
          placeholder: 'e.g. BILL NO 3 PAGE 15/4, OPTIONAL ITEMS',
          maxLength: 150,
        }}
        validateRow={lineErrors}
        // Matched on the LIVE draft, so a description typed seconds ago is already
        // findable, plus the saved code/brand the draft does not carry as text.
        rowFilter={
          lineSearch.trim()
            ? (row, draft) => {
                const needle = lineSearch.trim().toLowerCase();
                return [
                  row?.line?.product_code,
                  draft.description,
                  draft.brand_snapshot,
                  draft.band_label,
                ].some((value) => (value ?? '').toLowerCase().includes(needle));
              }
            : undefined
        }
        filterEmptyHint={`No line matches "${lineSearch.trim()}".`}
      />

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

      {/* The same viewer Resource Management uses, so a product photograph zooms, scrolls and
          downloads exactly the way every other file in the system does. Mounted only while
          open: it holds a carousel, and there is no reason for it to exist behind the table. */}
      {previewIndex !== null && photoItems.length > 0 && (
        <AttachmentPreviewModal
          open
          onOpenChange={(next) => {
            if (!next) setPreviewIndex(null);
          }}
          items={photoItems}
          startIndex={previewIndex}
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
              result.unresolved_products === 1 ? 'it stays' : 'they stay'
            } flagged as off-catalog. Re-pick the product to clear it.`}
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

/**
 * What is true about the line, ON THE SPOT.
 *
 * The client's requirement, verbatim: "the computation of whether it is non standard or off
 * catalog needs to be on the spot, cannot wait until I save". BM107 must read Non-standard
 * the moment it is picked; C-FH14 must read standard the same way.
 *
 * Three facts, three sources, one moment:
 *
 * **Off-catalog** is a fact about the row on screen - "no product is linked" - so it is read
 * off the LIVE draft, no request needed.
 *
 * **Non-standard and below-floor** cannot be computed here (series membership counts
 * nominated CATEGORIES the browser never fetched; the floor walks the category ancestry),
 * so while the draft matches the saved row the SAVED verdicts show, and the moment it
 * diverges the server is ASKED - debounced, via `useLineVerdict` - with the same functions
 * the save runs. One implementation, asked at two moments, so the badge before the save and
 * the flag after it cannot disagree.
 *
 * While an answer about the exact current draft is still in flight, the previous verdict
 * holds (placeholderData) rather than blinking off: a badge that flickers per keystroke
 * reads as the system changing its mind.
 */
function LineFlags({
  quotationId,
  line,
  draft,
}: {
  /** Null on a scope not saved yet: the verdict is asked of a saved scope, so only Off-catalog shows. */
  quotationId: string | null;
  line: QuotationLine | null;
  draft: InlineDraft;
}) {
  const productId = (draft.product_id ?? '').trim();
  const price = (draft.unit_price ?? '').trim();
  // The saved verdict still answers for an untouched row - no request needed for the 59
  // lines of a version nobody is editing.
  const pristine =
    line != null &&
    (line.product_id ?? '') === productId &&
    (price || '') === (line.unit_price ?? '');
  const verdict = useLineVerdict(
    quotationId ?? '',
    { product_id: productId || undefined, unit_price: price || undefined },
    !pristine && quotationId !== null,
  );

  const belowFloor = pristine ? line.is_below_floor : Boolean(verdict.data?.is_below_floor);
  const nonStandard = pristine
    ? line.is_non_standard
    : Boolean(verdict.data?.is_non_standard);
  const floorText = pristine
    ? line
      ? describeFloor(line)
      : ''
    : describeVerdictFloor(verdict.data ?? null);

  const anything = !productId || belowFloor || nonStandard;
  if (!anything) return null;

  return (
    <div className="mt-1 space-y-0.5">
      <div className="flex flex-wrap items-center gap-1">
        {!productId && (
          <Badge variant="outline" className="text-[11px]">
            Off-catalog
          </Badge>
        )}
        {belowFloor && (
          <Badge variant="destructive" className="gap-1 text-[11px]" title={floorText}>
            <AlertTriangle className="size-3" aria-hidden />
            Below floor
          </Badge>
        )}
        {nonStandard && (
          <Badge
            variant="secondary"
            className="gap-1 text-[11px]"
            title="Outside the series this scope is quoted from"
          >
            <TriangleAlert className="size-3" aria-hidden />
            Non-standard
          </Badge>
        )}
      </div>
      {belowFloor && floorText && <p className="text-xs text-destructive">{floorText}</p>}
    </div>
  );
}

/** The live twin of `describeFloor`, off a verdict instead of a saved line. */
function describeVerdictFloor(verdict: QuotationLineVerdict | null): string {
  if (!verdict?.floor_value) return 'Below the floor for this item.';
  const level = verdict.floor_level
    ? (FLOOR_LEVEL_LABELS[verdict.floor_level] ?? verdict.floor_level)
    : 'this item';
  return `Floor is ${formatMyr(verdict.floor_value)}, set on ${level}.`;
}

/**
 * The scope's money, off the LIVE drafts.
 *
 * Rate-only lines contribute nothing, the same rule the PDF and the backend total apply: the
 * customer is being shown a rate, and adding the sample quotation's five alternates would have
 * overstated it by RM 235,000. A cell that is not a number yet counts as zero rather than
 * blanking the whole total, so the figure survives a half-typed price.
 */
function totalFromDrafts(drafts: InlineDraft[]): string | null {
  return sumMoney(
    drafts
      .filter((draft) => !isInlineChecked(draft.is_rate_only))
      .map((draft) => multiplyMoney(draft.quantity, draft.unit_price) ?? '0'),
  );
}

/**
 * Draft to the body the per-line endpoint takes.
 *
 * `brand_snapshot` is the request's name for the field the response calls `brand`. The
 * asymmetry is the API's and is deliberate (the column snapshots the brand at the moment the
 * line was priced), so it is honoured here rather than smoothed over.
 *
 * `item_label` is NOT sent any more: the ITEM column is the row's position now, so a stored
 * label could only ever disagree with what is on screen.
 */
function toBody(draft: InlineDraft) {
  return {
    product_id: draft.product_id || null,
    description_snapshot: draft.description.trim() || null,
    unit_price: draft.unit_price.trim() || '0',
    quantity: draft.quantity.trim() || '1',
    uom: draft.uom.trim() || null,
    unit_type: (draft.unit_type || null) as UnitType | null,
    notes: draft.notes.trim() || null,
    brand_snapshot: draft.brand_snapshot.trim() || null,
    technical_spec: draft.technical_spec.trim() || null,
    complete_set: draft.complete_set.trim() || null,
    band_label: draft.band_label.trim() || null,
    is_rate_only: isInlineChecked(draft.is_rate_only),
  };
}

/** Says which rule bit, so the salesperson knows whose policy to argue with. */
function describeFloor(line: QuotationLine): string {
  if (!line.floor_value_applied) return 'Below the floor for this item.';
  const level = line.floor_level_applied
    ? FLOOR_LEVEL_LABELS[line.floor_level_applied] ?? line.floor_level_applied
    : 'policy';
  return `Floor was ${formatMyr(line.floor_value_applied)}, set on ${level}.`;
}

/** Quantities come back as "2.00"; nobody writes two bathrooms as 2.00. */
function trimAmount(value: string): string {
  const amount = Number(value);
  if (Number.isNaN(amount)) return value;
  return String(amount);
}
