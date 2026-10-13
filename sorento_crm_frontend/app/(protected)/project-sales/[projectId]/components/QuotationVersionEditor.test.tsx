/**
 * S3 + S11 - QuotationVersionEditor (AC-E2, AC-E3, AC-E4, AC-E7).
 *
 * Two rules are pinned here.
 *
 * The first is unchanged: editability comes from the SERVER's `is_current` / `is_editable`, never
 * from a local guess such as "the highest number I can see". A superseded version is a document
 * the customer already holds, so it renders read-only WITH the reason, not merely with its
 * buttons missing.
 *
 * The second is S11's, and it replaces the per-row saving these specs used to assert. The editor
 * no longer writes ANYTHING: without an edit session it is a clean read, and with one it stages.
 * So every claim that used to end in "and it called updateQuotationLine" now ends in "and this is
 * the body the one bulk write will carry", which is the same guarantee one level up. The write
 * itself is the document screen's, and is pinned in `QuotationDocumentClient.test.tsx`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  Project,
  ProjectQuotation,
  QuotationLine,
  QuotationVersion,
} from '../../_shared/types/project.types';
import {
  useQuotationEditSession,
  type QuotationEditSession,
} from '../quotation-documents/[documentId]/components/useQuotationEditSession';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const listQuotationVersions = vi.fn();
const listQuotationLines = vi.fn();
const reviseQuotation = vi.fn();
const createQuotationLine = vi.fn();
const updateQuotationLine = vi.fn();
const deleteQuotationLine = vi.fn();
const replaceQuotationLines = vi.fn();
const recomputeQuotationVersion = vi.fn();
const judgeQuotationLine = vi.fn();
// Fix round 2: the service the coder must add to _shared/services/projectService.ts, read through
// the hook `useQuotationLineImages(productId)` in _shared/hooks/useProjects.ts.
const getQuotationLineImages = vi.fn();

vi.mock('../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../_shared/services/projectService')
  >();
  return {
    ...actual,
    listQuotationVersions: (...args: unknown[]) => listQuotationVersions(...args),
    listQuotationLines: (...args: unknown[]) => listQuotationLines(...args),
    reviseQuotation: (...args: unknown[]) => reviseQuotation(...args),
    createQuotationLine: (...args: unknown[]) => createQuotationLine(...args),
    updateQuotationLine: (...args: unknown[]) => updateQuotationLine(...args),
    deleteQuotationLine: (...args: unknown[]) => deleteQuotationLine(...args),
    replaceQuotationLines: (...args: unknown[]) => replaceQuotationLines(...args),
    recomputeQuotationVersion: (...args: unknown[]) => recomputeQuotationVersion(...args),
    judgeQuotationLine: (...args: unknown[]) => judgeQuotationLine(...args),
    getQuotationLineImages: (...args: unknown[]) => getQuotationLineImages(...args),
  };
});

// The product picker hits the shared products `/select` endpoint when it opens. The rows it
// returns are the ones a pick has to fill the line from, so the fetch is stubbed rather than
// the component replaced.
const PRODUCTS = [
  {
    id: 'p9',
    product_code: 'SRT-BASIN-02',
    product_name: 'Counter basin',
    description: 'Vitreous china counter basin',
    brand_id: 'b1',
    base_uom_id: 'u1',
    list_price: '560.00',
  },
];
const getProductsForLineSelect = vi.fn(async () => PRODUCTS);

vi.mock('@/app/(protected)/master-data-management/products/services/productService', () => ({
  getProductsForLineSelect: () => getProductsForLineSelect(),
  getProductsForVariantSelect: vi.fn(async () => []),
}));

// Master data behind the fill: the line snapshots a brand NAME and a unit CODE, so both ids
// have to resolve before anything lands on a row. Both hooks are cached-forever queries in
// real life; here they are answered outright so a fill is deterministic.
vi.mock('@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query', () => ({
  useBrandSelectQuery: () => ({
    data: [
      { id: 'b1', brand_name: 'SORENTO' },
      { id: 'b2', brand_name: 'MOCHA' },
    ],
  }),
}));
vi.mock('@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query', () => ({
  useUOMSelectQuery: () => ({
    data: [
      { id: 'u1', uom_code: 'PCS', uom_name: 'Pieces' },
      { id: 'u2', uom_code: 'SET', uom_name: 'Sets' },
    ],
  }),
}));

// QUOTATION-UX (S6/S7). The stock chip reads the board's stock detail per fulfilment-planning
// group, and the "Stock at" dropdown derives its groups from the warehouse list. ASSUMED sources
// the coder must read: `useStockDetail` from `_shared/hooks/useFulfilmentPlanning` and
// `getWarehouses` from the inventory warehouseService (the one OrderInquiryStockGrid uses).
const stockByGroup: Record<string, unknown> = {};
const useStockDetailMock = vi.fn(
  (_productId: string, _wh: string | null, _lineIds?: string[], group?: string | null) => ({
    data: group ? stockByGroup[group] : undefined,
    isLoading: false,
    isError: false,
    error: null,
  }),
);
vi.mock('../../_shared/hooks/useFulfilmentPlanning', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../_shared/hooks/useFulfilmentPlanning')>();
  return {
    ...actual,
    useStockDetail: (...args: Parameters<typeof useStockDetailMock>) => useStockDetailMock(...args),
  };
});
vi.mock('@/app/(protected)/inventory-management/warehouses/services/warehouseService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('@/app/(protected)/inventory-management/warehouses/services/warehouseService')
  >();
  return {
    ...actual,
    getWarehouses: vi.fn(async () => ({
      data: [
        { id: 'w1', warehouse_code: 'MWH-IB' },
        { id: 'w2', warehouse_code: 'DC1-BB' },
        { id: 'w3', warehouse_code: 'BRW' },
      ],
      total: 3,
    })),
  };
});
// The viewer is the existing AttachmentPreviewModal; a stub says whether it opened and on what.
vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  default: (props: { items: { name: string }[]; startIndex?: number }) => (
    <div data-testid="attachment-preview-modal">
      {props.items.map((item) => item.name).join('|')}
    </div>
  ),
}));

// Round-1 fix tests: the real rows component, with the table's own drop handler captured so a
// drag can be fired without dnd-kit's layout measuring (same idea as SalesOrderLinesTable.reorder).
const dnd: { dragEnd: ((event: unknown) => void) | null } = { dragEnd: null };
vi.mock('@/components/ui/data-grid-table-dnd-rows', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/ui/data-grid-table-dnd-rows')>();
  const Actual = actual.DataGridTableDndRows;
  return {
    ...actual,
    DataGridTableDndRows: (props: React.ComponentProps<typeof Actual>) => {
      dnd.dragEnd = props.handleDragEnd as unknown as (event: unknown) => void;
      return <Actual {...props} />;
    },
  };
});

// The real hook, watched: AC-QLG02 asks which listing key the grid remembers its columns under.
const listingKeysSeen: string[] = [];
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('@/lib/listing-column-preferences/useListingColumnPreferences')
  >();
  return {
    ...actual,
    useListingColumnPreferences: (args: { listingKey?: string }) => {
      if (args?.listingKey) listingKeysSeen.push(args.listingKey);
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      return (actual.useListingColumnPreferences as any)(args);
    },
  };
});

import {
  QuotationVersionEditor,
  describeRecompute,
  unfinishedStagedLines,
  stagedLinesToBody,
  stagedScopeTotal,
} from './QuotationVersionEditor';

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    project_code: 'PRJ-000001',
    title: 'Menara Test',
    outcome: 'open',
    is_critical: false,
    brands: [],
    brand_ids: [],
    next_action_overdue: false,
    stale_level: 0,
    is_unattended: false,
    open_task_count: 0,
    can_edit: true,
    ...overrides,
  };
}

const QUOTATION: ProjectQuotation = {
  id: 'q1',
  project_id: 'p1',
  scope_label: 'House Units',
  outcome: 'open',
  version_count: 2,
  current_version_id: 'v2',
  current_version_no: 2,
  current_total: '9000.00',
  below_floor_count: 1,
  non_standard_count: 0,
  line_count: 1,
};

function version(overrides: Partial<QuotationVersion>): QuotationVersion {
  return {
    id: 'v1',
    quotation_id: 'q1',
    version_no: 1,
    is_current: false,
    total_amount: '0.00',
    ...overrides,
  };
}

function line(overrides: Partial<QuotationLine> = {}): QuotationLine {
  return {
    id: 'l1',
    version_id: 'v2',
    product_code: 'SRT-WC-01',
    description: 'Wall-hung WC',
    unit_price: '900.00',
    quantity: '10.00',
    line_total: '9000.00',
    is_non_standard: false,
    is_below_floor: false,
    sort_order: 0,
    ...overrides,
  };
}

const VERSIONS = [
  version({ id: 'v1', version_no: 1, frozen_at: '2026-07-01T02:00:00', total_amount: '8000.00' }),
  version({ id: 'v2', version_no: 2, is_current: true, total_amount: '9000.00' }),
];

/**
 * The document screen, reduced to the one thing the editor depends on: a real edit session,
 * living OUTSIDE the editor.
 *
 * The real hook rather than a hand-rolled stub, because the round trip is the thing under test -
 * the table reports, the session holds, and the editor is re-seeded from what the session holds.
 * A stub that simply echoed would prove none of that.
 */
let session: QuotationEditSession | null = null;

function Harness({
  projectOverrides,
  editing,
  unsaved = false,
}: {
  projectOverrides: Partial<Project>;
  editing: boolean;
  /** A scope added on the quotation form and not saved yet (#1341, round 3). */
  unsaved?: boolean;
}) {
  const held = useQuotationEditSession();
  session = held;
  const { begin, isEditing, scopes, seedScope, stageScope, toggleRemoved } = held;

  React.useEffect(() => {
    if (editing) begin();
  }, [begin, editing]);

  const edit = React.useMemo(
    () =>
      isEditing
        ? {
            staged: scopes[QUOTATION.id]?.lines ?? null,
            seed: (versionId: string, lines: Parameters<typeof seedScope>[2]) =>
              seedScope(QUOTATION.id, versionId, lines),
            stage: (lines: Parameters<typeof stageScope>[1]) =>
              stageScope(QUOTATION.id, lines),
            toggleRemoved: (key: string) => toggleRemoved(QUOTATION.id, key),
          }
        : null,
    [isEditing, scopes, seedScope, stageScope, toggleRemoved],
  );

  return (
    <QuotationVersionEditor
      project={project(projectOverrides)}
      quotation={unsaved ? null : QUOTATION}
      edit={edit}
    />
  );
}

function renderEditor(overrides: Partial<Project> = {}, editing = false, unsaved = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Harness projectOverrides={overrides} editing={editing} unsaved={unsaved} />
    </QueryClientProvider>,
  );
}

/**
 * The screen in an open edit session, which is the only place cells exist.
 *
 * WAITING ON `Add a line` IS NOT ENOUGH, and that is what made this file the deploy gate's
 * flakiest: the button belongs to the table's CHROME, which renders a commit BEFORE the rows do.
 * `InlineLineTable` mirrors its `rows` prop into internal per-row state in an effect and skips
 * any row that state has not reached yet, so on the commit where the lines query lands there is
 * a real intermediate DOM with the toolbar, the header, the footer and NOT ONE line row - and no
 * empty-state row either, because the table does have rows, it just cannot draw them yet. On an
 * idle machine the next commit follows too fast to observe. On a loaded CI runner it does not,
 * and whichever spec queried a cell first lost, which is why a DIFFERENT one failed each time.
 *
 * So wait for a line ROW. Every caller here opens a version that has at least one.
 */
async function renderEditing(overrides: Partial<Project> = {}) {
  const result = renderEditor(overrides, true);
  await screen.findByRole('button', { name: /Add a line/i });
  await waitFor(() => expect(itemNumbers().length).toBeGreaterThan(0));
  // The rows being drawn and the session holding them are two different things: staging a scope
  // the session has not seeded yet is a no-op, so a keystroke landing in that gap is silently
  // dropped. Specs here type immediately, so the seed is part of "ready", not an extra.
  await waitFor(() => expect(session?.scopes[QUOTATION.id]?.lines ?? null).not.toBeNull());
  return result;
}

/** What the one bulk write would carry if Save were pressed right now. */
function stagedBody() {
  return stagedLinesToBody(session?.scopes[QUOTATION.id]?.lines ?? []);
}

/** What the table's footer says right now, add buttons and all. */
function footerText(): string {
  return screen.getByRole('table').querySelector('tfoot')?.textContent ?? '';
}

/** The ITEM cell of every LINE row, skipping the section headings that span the table. */
function itemNumbers(): (string | null | undefined)[] {
  return Array.from(screen.getByRole('table').querySelectorAll('tbody tr'))
    .filter((tr) => !tr.querySelector('td[colspan]'))
    // The grid may put a drag-handle cell first in edit mode: the number is the first cell
    // that reads as one.
    .map((tr) =>
      Array.from(tr.querySelectorAll('td'))
        .map((td) => td.textContent?.trim())
        .find((text) => /^\d+$/.test(text ?? '')),
    );
}

/** Nothing left the browser. Every write path the editor used to own is asserted silent. */
function expectNothingWritten() {
  expect(createQuotationLine).not.toHaveBeenCalled();
  expect(updateQuotationLine).not.toHaveBeenCalled();
  expect(deleteQuotationLine).not.toHaveBeenCalled();
  expect(replaceQuotationLines).not.toHaveBeenCalled();
}

beforeEach(() => {
  vi.clearAllMocks();
  session = null;
  Object.keys(stockByGroup).forEach((key) => delete stockByGroup[key]);
  window.localStorage.clear();
  listQuotationVersions.mockResolvedValue(VERSIONS);
  listQuotationLines.mockResolvedValue([line()]);
  reviseQuotation.mockResolvedValue(version({ id: 'v3', version_no: 3, is_current: true }));
  createQuotationLine.mockResolvedValue(line({ id: 'l2' }));
  updateQuotationLine.mockResolvedValue(line());
  // The default live verdict: clean. Tests that need a flag override it.
  judgeQuotationLine.mockResolvedValue({
    is_non_standard: false,
    is_below_floor: false,
    floor_value: null,
    floor_level: null,
  });
});

describe('QuotationVersionEditor', () => {
  it('lands on the current version and marks the older one frozen', async () => {
    renderEditor();

    expect(await screen.findByRole('button', { name: 'v2' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'v1 (frozen)' })).toBeInTheDocument();
    // Lines are asked for on v2, not v1: current is the server's answer, not the first row.
    expect(listQuotationLines).toHaveBeenCalledWith('v2');
  });

  it('reads as a document until somebody opens an edit session', async () => {
    // The client's complaint in one assertion: a screen you are reading must not be a screen
    // that saves under you. There is nothing to type into and nothing to press by accident.
    renderEditor();

    expect(await screen.findByText('Wall-hung WC')).toBeInTheDocument();
    expect(screen.getByText('RM 900.00')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Qty on SRT-WC-01' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Add a line/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Remove SRT-WC-01/i })).toBeNull();
  });

  it('lets the current version be edited in place, without a dialog', async () => {
    await renderEditing();

    // The line IS the row: every field is a cell, so there is nothing to open.
    expect(screen.getByRole('textbox', { name: 'Description on SRT-WC-01' })).toHaveValue(
      'Wall-hung WC',
    );
    expect(screen.getByRole('textbox', { name: 'Qty on SRT-WC-01' })).toHaveValue('10.00');
    expect(screen.queryByRole('button', { name: /Edit SRT-WC-01/i })).toBeNull();
    // And no per-row save affordance, because there is one Save for the whole document now.
    expect(screen.queryByRole('button', { name: /^Save SRT-WC-01$/ })).toBeNull();
  });

  it('turns a superseded version read-only and says where to edit instead', async () => {
    await renderEditing();

    fireEvent.click(screen.getByRole('button', { name: 'v1 (frozen)' }));

    // One line, not a paragraph on why versions freeze: the consequence is the useful part.
    expect(await screen.findByText(/Frozen\. Make changes on v2\./i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add a line/i })).toBeNull();
    expect(screen.queryByRole('textbox', { name: 'Qty on SRT-WC-01' })).toBeNull();
    // Frozen lines still read as money and quantities, not as raw API strings.
    expect(await screen.findByText('RM 900.00')).toBeInTheDocument();
  });

  it('names the revision as the way out of a version the customer holds', async () => {
    // The sentence that replaced "its lines cannot be changed. Open a revision to re-price it.",
    // which stated a fact and offered no move. Reason and next action, in one line.
    listQuotationVersions.mockResolvedValue([
      version({ id: 'v1', version_no: 1, frozen_at: '2026-07-01T02:00:00' }),
      version({ id: 'v2', version_no: 2, is_current: true, is_issued: true, is_editable: false }),
    ]);

    renderEditor();

    expect(
      await screen.findByText(/The customer holds v2\. Edit quotation opens v3/i),
    ).toBeInTheDocument();
  });

  it('says what a revise will freeze before doing it', async () => {
    renderEditor();

    // The button is chrome and arrives with the version list, but the sentence it opens counts
    // the LINES - so clicking the moment it appears asks "how many?" of a query still in flight
    // and gets nought. Wait for a line to be on screen first.
    await screen.findByText('Wall-hung WC');
    fireEvent.click(await screen.findByRole('button', { name: /Revise to v3/i }));

    expect(await screen.findByText(/frozen for good/i)).toBeInTheDocument();
    expect(screen.getByText(/its 1 line is/i)).toBeInTheDocument();
    expect(reviseQuotation).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /Freeze v2 and continue/i }));
    await waitFor(() => expect(reviseQuotation).toHaveBeenCalledWith('q1'));
  });

  it('names the rule behind a below-floor line rather than just flagging it', async () => {
    listQuotationLines.mockResolvedValue([
      line({
        unit_price: '400.00',
        list_price: '1000.00',
        is_below_floor: true,
        floor_value_applied: '700.00',
        floor_level_applied: 'category_ancestor',
      }),
    ]);

    renderEditor();

    expect(await screen.findByText('Below floor')).toBeInTheDocument();
    expect(
      screen.getByText(/Floor was RM 700\.00, set on a parent category/),
    ).toBeInTheDocument();
    expect(screen.getByText('List RM 1,000.00')).toBeInTheDocument();
  });

  it('marks a custom item as such, since it can never be standard', async () => {
    listQuotationLines.mockResolvedValue([
      line({
        product_code: null,
        product_id: null,
        description: 'Bespoke vanity top',
        is_non_standard: true,
      }),
    ]);

    renderEditor();

    expect(await screen.findByText('Custom item')).toBeInTheDocument();
    expect(screen.queryByText('Off-catalog')).toBeNull();
    expect(screen.getByText('Non-standard')).toBeInTheDocument();
  });

  /**
   * The badges have to describe the row on screen, not the row as it was saved.
   *
   * This is the complaint the client raised on a real quotation: they picked BT009 from the
   * dropdown and the line kept insisting it was off-catalog, and non-standard, until the
   * save and the refetch. Both badges were being read off the stored line.
   */
  it('clears Custom item the moment a product is picked, before any save', async () => {
    listQuotationLines.mockResolvedValue([
      line({ product_code: null, product_id: null, description: 'BT009', is_non_standard: true }),
    ]);
    await renderEditing();

    expect(screen.getByText('Custom item')).toBeInTheDocument();

    // Pick a product on the line. `Off-catalog` means "no product is linked" and nothing
    // else, so it is a fact about the draft and can be answered here.
    fireEvent.click(screen.getByRole('combobox', { name: /^Product on / }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-BASIN-02/ }));

    await waitFor(() => expect(screen.queryByText('Custom item')).not.toBeInTheDocument());
  });

  it('asks the server for a fresh verdict the moment the product changes - on the spot, not at save', async () => {
    // The client's requirement verbatim: "cannot wait until I save then only compute". The
    // verdicts still come from the SERVER (series membership counts nominated categories the
    // browser never fetched), but they are asked for per settled draft, debounced, with the
    // same functions the save runs. Here the picked product is outside the series, so the
    // flag must appear BEFORE any save - the BM107 case.
    listQuotationLines.mockResolvedValue([
      line({
        product_id: 'p-old',
        product_code: 'SRTWC8608-SC',
        is_non_standard: false,
        is_below_floor: false,
      }),
    ]);
    judgeQuotationLine.mockResolvedValue({
      is_non_standard: true,
      is_below_floor: false,
      floor_value: null,
      floor_level: null,
    });
    await renderEditing();

    expect(screen.queryByText('Non-standard')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('combobox', { name: /^Product on / }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-BASIN-02/ }));

    // Debounced 400ms, then judged. NOTHING was saved on the way to the badge.
    expect(await screen.findByText('Non-standard', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(judgeQuotationLine).toHaveBeenCalledWith(
      QUOTATION.id,
      expect.objectContaining({ product_id: 'p9' }),
    );
    expect(replaceQuotationLines).not.toHaveBeenCalled();
    expect(updateQuotationLine).not.toHaveBeenCalled();
  });

  it('flags a price below the floor as it is typed, with the floor named', async () => {
    listQuotationLines.mockResolvedValue([line({ unit_price: '900.00' })]);
    judgeQuotationLine.mockResolvedValue({
      is_non_standard: false,
      is_below_floor: true,
      floor_value: '94.00',
      floor_level: 'series',
    });
    await renderEditing();

    expect(screen.queryByText('Below floor')).not.toBeInTheDocument();

    fireEvent.change(screen.getByRole('textbox', { name: 'Unit price on SRT-WC-01' }), {
      target: { value: '90.00' },
    });

    expect(await screen.findByText('Below floor', {}, { timeout: 5000 })).toBeInTheDocument();
    // The floor is NAMED, so the refusal can be argued with rather than merely obeyed.
    expect(screen.getByText(/Floor is RM ?94\.00/)).toBeInTheDocument();
    expect(judgeQuotationLine).toHaveBeenCalledWith(
      QUOTATION.id,
      expect.objectContaining({ unit_price: '90.00' }),
    );
  });


  /**
   * Search over the lines - a 59-line version cannot be found in by eye, and Ctrl-F only
   * finds what is scrolled into the DOM.
   *
   * The design claim under test: a hidden row is HIDDEN, not removed. Item numbers hold and
   * the footer total does not move, because "item 12" on the customer's paper must not
   * become "item 3", and a total that shrank with the view would read as lines lost.
   */
  it('filters the lines by search without renumbering items or changing the total', async () => {
    listQuotationLines.mockResolvedValue([
      line({ id: 'l1', product_code: 'SRT-WC-01', description: 'Wall-hung WC', sort_order: 0 }),
      line({
        id: 'l2',
        product_code: 'BM107',
        description: 'Basin tap body',
        unit_price: '100.00',
        quantity: '2.00',
        line_total: '200.00',
        sort_order: 1,
      }),
    ]);
    renderEditor();

    await screen.findByText('Wall-hung WC');
    const total = screen.getByRole('table').querySelector('tfoot')?.textContent ?? '';

    fireEvent.change(screen.getByLabelText(/search lines/i), {
      target: { value: 'bm107' },
    });

    await waitFor(() => expect(screen.queryByText('Wall-hung WC')).not.toBeInTheDocument());
    expect(screen.getByText('Basin tap body')).toBeInTheDocument();
    // The surviving row keeps its own item number: it is still line 2.
    expect(itemNumbers()).toEqual(['2']);
    // And the money did not move - the hidden line is hidden, not gone.
    expect(screen.getByRole('table').querySelector('tfoot')?.textContent).toBe(total);
  });

  it('says the search found nothing rather than looking like an empty version', async () => {
    listQuotationLines.mockResolvedValue([line()]);
    renderEditor();

    await screen.findByText('Wall-hung WC');
    fireEvent.change(screen.getByLabelText(/search lines/i), {
      target: { value: 'zzt-no-such-line' },
    });

    expect(
      await screen.findByText(/no line matches "zzt-no-such-line"/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/frozen without any lines|nothing quoted yet/i)).not.toBeInTheDocument();
  });

  it('lays the line out as the grid columns, with no Tech spec, Counts per or Rate only column', async () => {
    await renderEditing();

    // UAC grid: the shared DataGrid, columns per the approved mock section 1.
    expect(document.querySelector('[data-slot="data-grid-table"]')).not.toBeNull();
    for (const header of ['#', 'Photo', 'Drawing', 'Product', 'Description', 'Brand', 'Qty', 'Rate (RM)', 'Total (RM)']) {
      expect(await screen.findByRole('columnheader', { name: header })).toBeInTheDocument();
    }
    for (const gone of ['Counts per', 'Tech spec', 'Rate only', 'Complete set']) {
      expect(screen.queryByRole('columnheader', { name: gone })).toBeNull();
    }
    // Notes moved into the Line details row, behind the row menu, so no Notes popover button.
    expect(screen.queryByRole('button', { name: 'Notes on SRT-WC-01' })).toBeNull();
  });

  it('holds the printed columns as cells, and stages every one of them', async () => {
    listQuotationLines.mockResolvedValue([
      line({
        brand: 'SORENTO',
        technical_spec: 'Rimless, 4/2.6L dual flush',
        complete_set: 'c/w seat cover',
      }),
    ]);

    await renderEditing();

    // What the server sent is on screen, under the name the RESPONSE uses for it.
    expect(screen.getByRole('combobox', { name: 'Brand on SRT-WC-01' })).toHaveTextContent(
      'SORENTO',
    );
    // Tech spec is no longer a column: the legacy text is carried through the save untouched.
    expect(screen.queryByRole('textbox', { name: 'Tech spec on SRT-WC-01' })).toBeNull();
    // Complete set lives in the Line details row.
    fireEvent.click(screen.getByRole('button', { name: 'Line actions on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Line details' }));
    expect(screen.getByRole('textbox', { name: 'Complete set on SRT-WC-01' })).toHaveValue(
      'c/w seat cover',
    );

    // Brand is a picker over the brand list now; the pick sends the brand NAME.
    fireEvent.click(screen.getByRole('combobox', { name: 'Brand on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('option', { name: 'MOCHA' }));

    // The REQUEST calls the brand `brand_snapshot` while the response calls it `brand`. The
    // asymmetry is the API's, and the editor honours it rather than sending its own name.
    await waitFor(() =>
      expect(stagedBody()[0]).toMatchObject({
        id: 'l1',
        brand_snapshot: 'MOCHA',
        technical_spec: 'Rimless, 4/2.6L dual flush',
        complete_set: 'c/w seat cover',
      }),
    );
    // The item number is the row's position, so there is nothing to send: a stored label
    // could only ever disagree with what is printed.
    expect(stagedBody()[0]).not.toHaveProperty('item_label');
    expectNothingWritten();
  });

  it('prints the words on a rate-only line, and leaves it out of the footer sum', async () => {
    listQuotationLines.mockResolvedValue([
      line(),
      line({
        id: 'l2',
        product_code: 'SRT-BIDET-09',
        unit_price: '500.00',
        quantity: '1.00',
        // Stored and printed, because the customer IS being shown a rate. It just does not
        // count: adding the sample's five alternates would have overstated it by RM 235,000.
        line_total: '500.00',
        is_rate_only: true,
      }),
    ]);

    await renderEditing();

    expect(screen.getByText('rate only')).toBeInTheDocument();
    // Never RM 0.00, which reads as free, and never blank, which reads as a fault.
    expect(screen.queryByText('RM 0.00')).toBeNull();
    expect(screen.queryByText('RM 500.00')).toBeNull();
    // The footer under the money column is the other line alone, not RM 9,500.00.
    expect(footerText()).toContain('RM 9,000.00');
    expect(footerText()).not.toContain('9,500');
    expect(
      screen.getByRole('checkbox', { name: 'Rate only on SRT-BIDET-09' }),
    ).toBeChecked();
  });

  it('marks a line rate-only from its own row, and says so in the total column', async () => {
    await renderEditing();

    const toggle = screen.getByRole('checkbox', { name: 'Rate only on SRT-WC-01' });
    expect(toggle).not.toBeChecked();
    fireEvent.click(toggle);

    // The total column answers immediately, off the draft, before anything is saved.
    expect(screen.getByText('rate only')).toBeInTheDocument();

    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ is_rate_only: true }));
    expectNothingWritten();
  });

  it('opens a section with one heading above the line that carries it', async () => {
    listQuotationLines.mockResolvedValue([
      line({ band_label: 'BILL NO 3 PAGE 15/4' }),
      line({ id: 'l2', product_code: 'SRT-BIDET-09', sort_order: 10 }),
    ]);

    await renderEditing();

    const heading = screen.getByRole('textbox', { name: 'Section heading on SRT-WC-01' });
    expect(heading).toHaveValue('BILL NO 3 PAGE 15/4');
    // ONCE, and only for the line that carries it: the line below is inside the section, it
    // does not repeat the heading.
    expect(screen.getAllByDisplayValue('BILL NO 3 PAGE 15/4')).toHaveLength(1);
    expect(
      screen.queryByRole('textbox', { name: 'Section heading on SRT-BIDET-09' }),
    ).toBeNull();

    // Directly above its own line, inside the same table, so the two cannot drift apart.
    const bandRow = heading.closest('tr');
    const lineRow = screen.getByRole('textbox', { name: 'Qty on SRT-WC-01' }).closest('tr');
    expect(bandRow?.nextElementSibling).toBe(lineRow);
  });

  it('opens a section from the footer, beside adding a line', async () => {
    await renderEditing();

    // Two buttons, side by side, where "Add a line" already was. The per-row icon that used to
    // turn a line into a heading is gone: the client called it counterintuitive.
    expect(screen.getByRole('button', { name: 'Add a line' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Add a section' }));

    const heading = await screen.findByRole('textbox', {
      name: 'Section heading on line 2',
    });
    await waitFor(() => expect(document.activeElement).toBe(heading));
    fireEvent.change(heading, { target: { value: 'OPTIONAL ITEMS FOR OKU TOILET' } });
    // The line under the heading is ready to fill in: a section IS a line.
    fireEvent.change(screen.getByRole('textbox', { name: 'Description on line 2' }), {
      target: { value: 'Grab bar' },
    });

    await waitFor(() =>
      expect(stagedBody()[1]).toMatchObject({
        band_label: 'OPTIONAL ITEMS FOR OKU TOILET',
        description_snapshot: 'Grab bar',
      }),
    );
    expectNothingWritten();
  });

  it('clears a band by emptying its heading', async () => {
    listQuotationLines.mockResolvedValue([line({ band_label: 'OPTION' })]);

    await renderEditing();

    const heading = screen.getByRole('textbox', { name: 'Section heading on SRT-WC-01' });
    fireEvent.change(heading, { target: { value: '' } });

    // Null, not an empty string: the column is nullable and a blank heading is no heading.
    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ band_label: null }));
  });

  it("reads a frozen version's bands and rate-only lines without offering an editor", async () => {
    listQuotationLines.mockResolvedValue([
      line({
        version_id: 'v1',
        band_label: 'BILL NO 3 PAGE 15/4',
        is_rate_only: true,
      }),
    ]);

    await renderEditing();
    fireEvent.click(screen.getByRole('button', { name: 'v1 (frozen)' }));

    expect(await screen.findByText('BILL NO 3 PAGE 15/4')).toBeInTheDocument();
    expect(screen.getByText('rate only')).toBeInTheDocument();
    expect(
      screen.queryByRole('textbox', { name: 'Section heading on SRT-WC-01' }),
    ).toBeNull();
    expect(screen.queryByRole('checkbox', { name: 'Rate only on SRT-WC-01' })).toBeNull();
  });

  it('moves the line total while the quantity is typed, before anything is saved', async () => {
    await renderEditing();

    const qty = screen.getByRole('textbox', { name: 'Qty on SRT-WC-01' });
    fireEvent.change(qty, { target: { value: '3' } });

    // In the ROW's own Total cell. The footer says the same figure now that it tracks the
    // drafts too, so this pins where the number is rather than that it exists somewhere.
    const body = screen.getByRole('table').querySelector('tbody') as HTMLElement;
    expect(within(body).getByText('RM 2,700.00')).toBeInTheDocument();
    expectNothingWritten();
  });

  it('stages an edited line with the body the bulk write will carry', async () => {
    await renderEditing();

    const qty = screen.getByRole('textbox', { name: 'Qty on SRT-WC-01' });
    fireEvent.change(qty, { target: { value: '12' } });

    await waitFor(() =>
      expect(stagedBody()).toEqual([
        {
          // The id is what tells the whole-set write this line already exists. Without it the
          // server would insert a second copy and delete the original.
          id: 'l1',
          product_id: null,
          description_snapshot: 'Wall-hung WC',
          unit_price: '900.00',
          quantity: '12',
          uom: null,
          unit_type: null,
          notes: null,
          // Every printed column travels with the save, whether or not it was typed into: a body
          // that omitted them would leave the document's own fields behind on an unrelated edit.
          brand_snapshot: null,
          technical_spec: null,
          complete_set: null,
          band_label: null,
          is_rate_only: false,
        },
      ]),
    );
    expectNothingWritten();
  });

  it('stages an added line with no id, so the write reads it as new', async () => {
    await renderEditing();

    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    const description = await screen.findByRole('textbox', {
      name: 'Description on line 2',
    });
    fireEvent.change(description, { target: { value: 'Bespoke vanity top' } });
    fireEvent.change(screen.getByRole('textbox', { name: 'Unit price on line 2' }), {
      target: { value: '1250.00' },
    });

    await waitFor(() => expect(stagedBody()).toHaveLength(2));
    expect(stagedBody()[1]).toEqual({
      product_id: null,
      description_snapshot: 'Bespoke vanity top',
      unit_price: '1250.00',
      quantity: '1',
      uom: null,
      unit_type: null,
      notes: null,
      brand_snapshot: null,
      technical_spec: null,
      complete_set: null,
      band_label: null,
      is_rate_only: false,
    });
    // Position in the array is the order, so there is no sort_order to disagree with it.
    expect(stagedBody()[1]).not.toHaveProperty('sort_order');
    expect(stagedBody()[1]).not.toHaveProperty('id');
    expectNothingWritten();
  });

  it('marks the cell that stops an off-catalog line from being saved', async () => {
    await renderEditing();

    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    const description = await screen.findByRole('textbox', {
      name: 'Description on line 2',
    });
    // Typed into, so it is real data rather than a mis-click, but it has neither a product
    // nor a description to stand in for one. Marked as it is typed, not held back until Save:
    // hunting for the bad row afterwards, in a scope of fifty, is the worse of the two.
    fireEvent.change(screen.getByRole('textbox', { name: 'Qty on line 2' }), {
      target: { value: '4' },
    });

    expect(await screen.findByText('A custom item needs a description')).toBeInTheDocument();
    expect(description).toHaveAttribute('aria-invalid', 'true');
    expectNothingWritten();
  });

  it('leaves an added row nobody has typed into unmarked', async () => {
    await renderEditing();

    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    await screen.findByRole('textbox', { name: 'Description on line 2' });

    // Empty is not wrong. Marking a row red the instant it appears would be the screen
    // shouting at somebody for pressing the button it offered them.
    expect(screen.queryByText('A custom item needs a description')).toBeNull();
  });

  it('fills the line from the product that was picked', async () => {
    await renderEditing();

    fireEvent.click(screen.getByRole('combobox', { name: 'Product on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-BASIN-02/ }));

    // One decision answers the rest of the row, off the product record rather than off memory.
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Description on SRT-WC-01' })).toHaveValue(
        'Vitreous china counter basin',
      ),
    );
    expect(screen.getByRole('combobox', { name: 'Brand on SRT-WC-01' })).toHaveTextContent(
      'SORENTO',
    );
    expect(screen.getByRole('combobox', { name: 'UOM on SRT-WC-01' })).toHaveTextContent('PCS');
    // And the list price beside the unit price is the picked product's, at once: reading the
    // saved row instead is what left "List RM 0.00" next to a real product.
    expect(screen.getByText('List RM 560.00')).toBeInTheDocument();
    // The trigger names the product that was PICKED. Nothing refetches during an edit session,
    // so resolving it from the stored line would leave the old code on screen until Save.
    expect(screen.getByRole('combobox', { name: 'Product on SRT-WC-01' })).toHaveTextContent(
      'SRT-BASIN-02',
    );
    expectNothingWritten();
  });

  it("lets the picked product overwrite a description somebody typed", async () => {
    // The client chose predictability: one product means one set of fields, every time,
    // including over an edit made before the re-pick. The cost was stated and accepted.
    await renderEditing();

    const description = screen.getByRole('textbox', { name: 'Description on SRT-WC-01' });
    fireEvent.change(description, { target: { value: 'Wording agreed with the QS' } });

    fireEvent.click(screen.getByRole('combobox', { name: 'Product on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-BASIN-02/ }));

    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Description on SRT-WC-01' })).toHaveValue(
        'Vitreous china counter basin',
      ),
    );
  });

  it('numbers the items 1, 2, 3, 4 straight through the sections', async () => {
    // Continuous within the SCOPE, not restarted per heading: that is how the customer's own
    // bill of quantities reads, and a per-section restart is the easy thing to write by
    // accident. Two sections of two lines read 1, 2 then 3, 4.
    listQuotationLines.mockResolvedValue([
      line({ id: 'l1', product_code: 'A-1', band_label: 'BILL NO 3', sort_order: 0 }),
      line({ id: 'l2', product_code: 'A-2', sort_order: 10 }),
      line({ id: 'l3', product_code: 'B-1', band_label: 'OPTIONAL ITEMS', sort_order: 20 }),
      line({ id: 'l4', product_code: 'B-2', sort_order: 30 }),
    ]);

    await renderEditing();

    expect(itemNumbers()).toEqual(['1', '2', '3', '4']);
    // Nothing to type into: the number is the row's position, not a field.
    expect(screen.queryByRole('textbox', { name: 'Item on A-1' })).toBeNull();
  });

  it('moves the footer total while a quantity is typed, before anything is saved', async () => {
    await renderEditing();

    const qty = screen.getByRole('textbox', { name: 'Qty on SRT-WC-01' });
    expect(footerText()).toContain('RM 9,000.00');

    fireEvent.change(qty, { target: { value: '3' } });

    // The bottom line follows the cells above it. Off the STRINGS, to the cent.
    expect(footerText()).toContain('RM 2,700.00');
    expectNothingWritten();
  });

  it('drops a line out of the live total the moment it is marked rate only', async () => {
    listQuotationLines.mockResolvedValue([
      line(),
      line({
        id: 'l2',
        product_code: 'SRT-BIDET-09',
        unit_price: '500.00',
        quantity: '1.00',
        line_total: '500.00',
      }),
    ]);

    await renderEditing();

    expect(footerText()).toContain('RM 9,500.00');

    fireEvent.click(screen.getByRole('checkbox', { name: 'Rate only on SRT-BIDET-09' }));

    // The customer is still shown the rate; nobody adds it up. Same rule as the PDF.
    expect(footerText()).toContain('RM 9,000.00');
    expectNothingWritten();
  });

  it('leaves the staged set and the footer summing to the same figure', async () => {
    // The header outside this editor sums the STAGED lines itself now, instead of being told a
    // figure on the way in and having it cleared on the way out. One mechanism, so the claim
    // worth pinning is that the two readings of the same drafts agree.
    await renderEditing();

    fireEvent.change(screen.getByRole('textbox', { name: 'Qty on SRT-WC-01' }), {
      target: { value: '3' },
    });

    await waitFor(() =>
      expect(stagedScopeTotal(session?.scopes[QUOTATION.id]?.lines ?? [])).toBe('2700.00'),
    );
    expect(footerText()).toContain('RM 2,700.00');
  });

  it('strikes a removed line through instead of asking, and puts it back on request', async () => {
    // Removing inside an edit session destroys nothing, so it asks nothing. The row stays where
    // it is, struck through, because a removal that is invisible cannot be taken back.
    await renderEditing();

    fireEvent.click(screen.getByRole('button', { name: 'Remove SRT-WC-01' }));

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(await screen.findByText('Removed on save')).toBeInTheDocument();
    expect(screen.getByText('Wall-hung WC')).toBeInTheDocument();
    // Out of the money the moment it is marked: the footer states what will actually be charged.
    expect(footerText()).toContain('RM 0.00');
    // And out of the body the write would carry.
    await waitFor(() => expect(stagedBody()).toEqual([]));

    fireEvent.click(screen.getByRole('button', { name: 'Restore SRT-WC-01' }));

    await waitFor(() => expect(stagedBody()).toHaveLength(1));
    expect(footerText()).toContain('RM 9,000.00');
    expectNothingWritten();
  });

  it('offers no write affordance to a reader', async () => {
    renderEditor({ can_edit: false });

    expect(await screen.findByRole('button', { name: 'v2' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Revise/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Add a line/i })).toBeNull();
  });

  it('keeps a version the server froze read-only even inside an edit session', async () => {
    // Edit is a screen state; `is_editable` is the server's answer. The screen state never wins.
    listQuotationVersions.mockResolvedValue([
      version({ id: 'v2', version_no: 2, is_current: true, is_issued: true, is_editable: false }),
    ]);

    renderEditor({}, true);

    expect(await screen.findByText('Wall-hung WC')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Qty on SRT-WC-01' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Add a line/i })).toBeNull();
  });

  /**
   * S19 - the recompute control.
   *
   * The point is not that a request goes out; it is that the ANSWER is on screen. The
   * client's own words were "a refresh button that recompute this", and a silent success
   * toast over 46 corrected flags tells them nothing about what moved.
   */
  it('re-checks the open version against today\'s master data and says what moved', async () => {
    recomputeQuotationVersion.mockResolvedValue({
      version_id: 'v2',
      version_no: 2,
      quotation_id: 'q1',
      scope_label: 'House Units',
      line_count: 52,
      changed_count: 7,
      now_non_standard: 0,
      no_longer_non_standard: 6,
      now_below_floor: 1,
      no_longer_below_floor: 0,
      floor_changed: 0,
      unresolved_products: 0,
      changed_lines: ['SRT-WC-01', 'CWB-242'],
    });

    renderEditor();

    fireEvent.click(await screen.findByRole('button', { name: /Recheck alerts/i }));

    await waitFor(() => expect(recomputeQuotationVersion).toHaveBeenCalledWith('v2'));
    expect(
      await screen.findByText(
        '6 lines are no longer non-standard, 1 line is now below floor.',
      ),
    ).toBeInTheDocument();
    // And WHICH lines, because "6 lines" is not something anybody can go and check.
    expect(screen.getByText('SRT-WC-01, CWB-242')).toBeInTheDocument();
  });

  it('says nothing changed rather than reporting a bare success', async () => {
    recomputeQuotationVersion.mockResolvedValue({
      version_id: 'v2',
      version_no: 2,
      quotation_id: 'q1',
      scope_label: 'House Units',
      line_count: 3,
      changed_count: 0,
      now_non_standard: 0,
      no_longer_non_standard: 0,
      now_below_floor: 0,
      no_longer_below_floor: 0,
      floor_changed: 0,
      unresolved_products: 0,
      changed_lines: [],
    });

    renderEditor();

    fireEvent.click(await screen.findByRole('button', { name: /Recheck alerts/i }));

    expect(await screen.findByText(/Nothing changed\. All 3 lines already match/i)).toBeInTheDocument();
  });

  it('withholds the recheck from a frozen version, whose flags are what the customer was sent', async () => {
    listQuotationVersions.mockResolvedValue([
      version({ id: 'v2', version_no: 2, is_current: true, is_issued: true, is_editable: false }),
    ]);

    renderEditor();

    expect(await screen.findByText('Wall-hung WC')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Recheck alerts/i })).toBeNull();
  });

  it('withholds the recheck from a reader', async () => {
    renderEditor({ can_edit: false });

    expect(await screen.findByRole('button', { name: 'v2' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Recheck alerts/i })).toBeNull();
  });

  it('disables the recheck while an edit session is open, so staged rows cannot go stale', async () => {
    await renderEditing();

    expect(screen.getByRole('button', { name: /Recheck alerts/i })).toBeDisabled();
  });

  it('explains the lines that stayed flagged because their product is unreadable here', async () => {
    // The live shape: 46 lines of one quotation name a product row belonging to another
    // company, so this company's catalogue cannot see it and the line reads as off-catalog.
    // Nothing changes, which is correct and completely unhelpful on its own.
    recomputeQuotationVersion.mockResolvedValue({
      version_id: 'v2',
      version_no: 2,
      quotation_id: 'q1',
      scope_label: 'House Units',
      line_count: 59,
      changed_count: 0,
      now_non_standard: 0,
      no_longer_non_standard: 0,
      now_below_floor: 0,
      no_longer_below_floor: 0,
      floor_changed: 0,
      unresolved_products: 46,
      changed_lines: [],
    });

    renderEditor();

    fireEvent.click(await screen.findByRole('button', { name: /Recheck alerts/i }));

    expect(
      await screen.findByText(
        /46 lines name products this company's catalogue does not carry.*custom item/i,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/off-catalog/i)).toBeNull();
  });
});

describe('describeRecompute', () => {
  const base = {
    version_id: 'v2',
    version_no: 2,
    quotation_id: 'q1',
    line_count: 10,
    changed_count: 0,
    now_non_standard: 0,
    no_longer_non_standard: 0,
    now_below_floor: 0,
    no_longer_below_floor: 0,
    floor_changed: 0,
    unresolved_products: 0,
    changed_lines: [] as string[],
  };

  it('counts one line in the singular', () => {
    expect(describeRecompute({ ...base, no_longer_non_standard: 1, changed_count: 1 })).toBe(
      '1 line is no longer non-standard.',
    );
  });

  it('reports both directions of both alerts in one sentence', () => {
    expect(
      describeRecompute({
        ...base,
        changed_count: 4,
        no_longer_non_standard: 6,
        now_non_standard: 2,
        no_longer_below_floor: 3,
        now_below_floor: 1,
      }),
    ).toBe(
      '6 lines are no longer non-standard, 2 lines are now non-standard, 3 lines are no longer below floor, 1 line is now below floor.',
    );
  });

  it('names the floor moving under a line that did not cross it', () => {
    expect(describeRecompute({ ...base, changed_count: 2, floor_changed: 2 })).toBe(
      '2 lines picked up a different floor.',
    );
  });
});

/**
 * #1341 round 3: the quotation form's Lines tab reuses this editor, and a scope added on the form
 * has no saved record yet. The one adaptation: it starts seeded with no lines and edits exactly
 * like a saved scope, asking the server for nothing it cannot answer.
 */
describe('QuotationVersionEditor on a scope not saved yet (AC-QF073)', () => {
  it('starts with no lines, stages an added line, and reads no version or verdict', async () => {
    renderEditor({}, true, true);

    await waitFor(() => expect(session?.scopes[QUOTATION.id]?.lines).toEqual([]));
    fireEvent.click(await screen.findByRole('button', { name: 'Add a line' }));
    fireEvent.change(await screen.findByRole('textbox', { name: 'Description on line 1' }), {
      target: { value: 'Grab bar' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: 'Unit price on line 1' }), {
      target: { value: '80.00' },
    });

    await waitFor(() =>
      expect(stagedBody()).toEqual([
        expect.objectContaining({ description_snapshot: 'Grab bar', unit_price: '80.00' }),
      ]),
    );
    expect(screen.getByText('Custom item')).toBeInTheDocument();
    expect(listQuotationVersions).not.toHaveBeenCalled();
    expect(listQuotationLines).not.toHaveBeenCalled();
    expect(judgeQuotationLine).not.toHaveBeenCalled();
    // A revise would write at once; the form never offers it.
    expect(screen.queryByRole('button', { name: /Revise to/ })).toBeNull();
    expectNothingWritten();
  });
});

// ---------------------------------------------------------------------------------------------
// QUOTATION-UX: the lines grid (S6), stock chip (S7), picker, custom item. Through the editor.
// ---------------------------------------------------------------------------------------------

function ext(overrides: Record<string, unknown>): QuotationLine {
  return line(overrides as Partial<QuotationLine>);
}

/** Opens a row/popover trigger the way a user does; works for Radix dropdowns and popovers. */
function openTrigger(name: string) {
  const trigger = screen.getByRole('button', { name });
  fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
  fireEvent.click(trigger);
}

async function chooseMenuItem(label: string, item: string) {
  openTrigger(`Line actions on ${label}`);
  fireEvent.click(await screen.findByText(item));
}

function descriptionOrder(): string[] {
  return screen
    .getAllByRole('textbox', { name: /^Description on / })
    .map((el) => (el as HTMLTextAreaElement).value);
}

const TWO_LINES = [
  line({ id: 'l1', product_code: 'A-1', description: 'First', sort_order: 0 }),
  line({ id: 'l2', product_code: 'A-2', description: 'Second', sort_order: 10 }),
];

describe('QuotationVersionEditor lines grid (QUOTATION-UX)', () => {
  it('draws the lines in the shared DataGrid with a drag handle per line in edit mode only', async () => {
    listQuotationLines.mockResolvedValue(TWO_LINES);
    await renderEditing();
    expect(document.querySelector('[data-slot="data-grid-table"]')).not.toBeNull();
    expect(screen.getAllByRole('button', { name: 'Drag to reorder' })).toHaveLength(2);
  });

  it('offers no drag handles and no grid editors in the read view', async () => {
    listQuotationLines.mockResolvedValue(TWO_LINES);
    renderEditor();
    expect(await screen.findByText('First')).toBeInTheDocument();
    expect(document.querySelector('[data-slot="data-grid-table"]')).not.toBeNull();
    expect(screen.queryByRole('button', { name: 'Drag to reorder' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Line actions on / })).toBeNull();
  });

  it('edits the description in a textarea and sends its line breaks in the save body', async () => {
    await renderEditing();
    const description = screen.getByRole('textbox', { name: 'Description on SRT-WC-01' });
    expect(description.tagName).toBe('TEXTAREA');
    fireEvent.change(description, { target: { value: 'Wall-hung WC\nc/w soft-close seat' } });
    await waitFor(() =>
      expect(stagedBody()[0].description_snapshot).toBe('Wall-hung WC\nc/w soft-close seat'),
    );
  });

  it('keeps the footer total off rate-only lines and live with edits', async () => {
    listQuotationLines.mockResolvedValue([
      line(),
      line({ id: 'l2', product_code: 'SRT-BIDET-09', unit_price: '500.00', quantity: '1.00', line_total: '500.00', is_rate_only: true }),
    ]);
    await renderEditing();
    expect(footerText()).toContain('RM 9,000.00');
    fireEvent.change(screen.getByRole('textbox', { name: 'Qty on SRT-WC-01' }), { target: { value: '12' } });
    await waitFor(() => expect(footerText()).toContain('RM 10,800.00'));
  });

  it('picks a brand from the brand list, sends its name, and clears it', async () => {
    listQuotationLines.mockResolvedValue([ext({ brand: 'SORENTO' })]);
    await renderEditing();
    fireEvent.click(screen.getByRole('combobox', { name: 'Brand on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('option', { name: 'MOCHA' }));
    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ brand_snapshot: 'MOCHA' }));
  });

  it('moves a line down and up from its row menu, reordering the staged body and the numbers', async () => {
    listQuotationLines.mockResolvedValue(TWO_LINES);
    await renderEditing();
    await chooseMenuItem('A-1', 'Move down');
    await waitFor(() => expect(stagedBody().map((l) => l.id)).toEqual(['l2', 'l1']));
    expect(descriptionOrder()).toEqual(['Second', 'First']);
    expect(itemNumbers()).toEqual(['1', '2']);
    await chooseMenuItem('A-1', 'Move up');
    await waitFor(() => expect(stagedBody().map((l) => l.id)).toEqual(['l1', 'l2']));
    expect(descriptionOrder()).toEqual(['First', 'Second']);
  });

  it('inserts a line below, a section above, and removes a line from the row menu', async () => {
    listQuotationLines.mockResolvedValue(TWO_LINES);
    await renderEditing();
    await chooseMenuItem('A-1', 'Insert a line below');
    await waitFor(() => expect(stagedBody()).toHaveLength(3));
    expect(stagedBody()[1]).not.toHaveProperty('id');
    expect(stagedBody().map((l) => l.id)).toEqual(['l1', undefined, 'l2']);

    await chooseMenuItem('A-2', 'Insert a section above');
    expect(await screen.findByRole('textbox', { name: 'Section heading on A-2' })).toBeInTheDocument();

    await chooseMenuItem('A-1', 'Remove line');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restore A-1' })).toBeInTheDocument());
    expectNothingWritten();
  });

  it('lists the six row actions', async () => {
    await renderEditing();
    openTrigger('Line actions on SRT-WC-01');
    for (const item of ['Line details', 'Move up', 'Move down', 'Insert a line below', 'Insert a section above', 'Remove line']) {
      expect(await screen.findByText(item)).toBeInTheDocument();
    }
  });

  it('reveals Complete set, Priced per and Notes under Line details, and stages them', async () => {
    listQuotationLines.mockResolvedValue([ext({ unit_type: 'bathroom', complete_set: 'c/w seat', notes: 'Ex-stock' })]);
    await renderEditing();
    expect(screen.queryByRole('textbox', { name: 'Notes on SRT-WC-01' })).toBeNull();
    await chooseMenuItem('SRT-WC-01', 'Line details');
    expect(screen.getByRole('textbox', { name: 'Complete set on SRT-WC-01' })).toHaveValue('c/w seat');
    expect(screen.getByRole('textbox', { name: 'Notes on SRT-WC-01' })).toHaveValue('Ex-stock');
    const per = screen.getByRole('combobox', { name: 'Priced per on SRT-WC-01' });
    expect(per).toHaveTextContent('Each bathroom');
    fireEvent.click(per);
    for (const option of ['Each house unit', 'Each bathroom', 'Each facility', 'Whole site (common area)']) {
      expect(await screen.findByRole('option', { name: option })).toBeInTheDocument();
    }
    fireEvent.click(screen.getByRole('option', { name: 'Whole site (common area)' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Notes on SRT-WC-01' }), { target: { value: 'Ex-works' } });
    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ unit_type: 'common_area', notes: 'Ex-works', complete_set: 'c/w seat' }));
  });

  it('shows Not set when a line has no Priced per', async () => {
    await renderEditing();
    await chooseMenuItem('SRT-WC-01', 'Line details');
    expect(screen.getByRole('combobox', { name: 'Priced per on SRT-WC-01' })).toHaveTextContent('Not set');
  });
});

describe('QuotationVersionEditor custom item (QUOTATION-UX)', () => {
  it('words a line with no product as Custom item with a Pick a product placeholder, never Off-catalog', async () => {
    listQuotationLines.mockResolvedValue([line({ product_code: null, product_id: null, description: 'Bespoke top', is_non_standard: true })]);
    await renderEditing();
    expect(screen.getByText('Custom item')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: /^Product on / })).toHaveTextContent('Pick a product');
    expect(document.body.textContent).not.toMatch(/off-catalog/i);
  });

  it('flags a typed-into custom item with no description, and still counts it as unfinished', async () => {
    await renderEditing();
    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    await screen.findByRole('textbox', { name: 'Description on line 2' });
    fireEvent.change(screen.getByRole('textbox', { name: 'Qty on line 2' }), { target: { value: '4' } });
    expect(await screen.findByText('A custom item needs a description')).toBeInTheDocument();
    expect(unfinishedStagedLines(session?.scopes[QUOTATION.id]?.lines ?? [])).toBeGreaterThan(0);
  });
});

const PHOTO = (extra: Record<string, unknown> = {}) => ({
  state: 'chosen',
  url: 'https://cdn.test/p.jpg',
  preview_url: 'https://cdn.test/p-full.jpg',
  attachment_id: 'a1',
  filename: 'wc front.jpg',
  candidate_count: 2,
  candidates: [
    { attachment_id: 'a1', filename: 'wc front.jpg', url: 'https://cdn.test/p.jpg', preview_url: 'https://cdn.test/p-full.jpg' },
    { attachment_id: 'a2', filename: 'wc side.jpg', url: 'https://cdn.test/s.jpg', preview_url: 'https://cdn.test/s-full.jpg' },
  ],
  ...extra,
});
const DRAWING = (extra: Record<string, unknown> = {}) => ({
  state: 'default',
  url: 'https://cdn.test/d.png',
  preview_url: 'https://cdn.test/d-full.png',
  attachment_id: 'd1',
  filename: 'wc drawing 1.png',
  candidate_count: 2,
  candidates: [
    { attachment_id: 'd1', filename: 'wc drawing 1.png', url: 'https://cdn.test/d.png', preview_url: 'https://cdn.test/d-full.png' },
    { attachment_id: 'd2', filename: 'wc drawing 2.png', url: 'https://cdn.test/d2.png', preview_url: 'https://cdn.test/d2-full.png' },
  ],
  ...extra,
});

describe('QuotationVersionEditor photo and drawing picker (QUOTATION-UX)', () => {
  it('shows the drawing thumbnail and opens the existing preview modal on click', async () => {
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1', product_image: PHOTO(), technical_drawing: DRAWING() })]);
    renderEditor();
    const drawing = await screen.findByRole('img', { name: 'SRT-WC-01 technical drawing' });
    expect(drawing).toHaveAttribute('src', 'https://cdn.test/d.png');
    fireEvent.click(drawing);
    expect(await screen.findByTestId('attachment-preview-modal')).toBeInTheDocument();
  });

  it('says no drawing when the product has none', async () => {
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1', product_image: PHOTO({ candidates: [], candidate_count: 1 }), technical_drawing: { state: 'no_drawings', candidate_count: 0, candidates: [] } })]);
    renderEditor();
    expect(await screen.findByText('no drawing')).toBeInTheDocument();
  });

  it('offers Change photo and Change drawing when several candidates exist, and stages the pick', async () => {
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1', product_image: PHOTO(), technical_drawing: DRAWING() })]);
    await renderEditing();
    fireEvent.click(screen.getByRole('button', { name: 'Change photo on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use wc side.jpg' }));
    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ image_attachment_id: 'a2' }));

    fireEvent.click(screen.getByRole('button', { name: 'Change drawing on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use wc drawing 2.png' }));
    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ image_attachment_id: 'a2', drawing_attachment_id: 'd2' }));
    expectNothingWritten();
  });

  it('says Choose photo when no photo is chosen yet', async () => {
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1', product_image: PHOTO({ state: 'not_chosen', url: null, preview_url: null, attachment_id: null, filename: null }), technical_drawing: DRAWING() })]);
    await renderEditing();
    expect(screen.getByRole('button', { name: 'Choose photo on SRT-WC-01' })).toBeInTheDocument();
  });

  it('clears both picks when the product changes', async () => {
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1', image_attachment_id: 'a2', drawing_attachment_id: 'd2', product_image: PHOTO(), technical_drawing: DRAWING() })]);
    await renderEditing();
    // The stored picks travel with the save until the product changes.
    expect(stagedBody()[0]).toMatchObject({ image_attachment_id: 'a2', drawing_attachment_id: 'd2' });
    fireEvent.click(screen.getByRole('combobox', { name: 'Product on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-BASIN-02/ }));
    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ product_id: 'p9' }));
    expect(stagedBody()[0].image_attachment_id ?? null).toBeNull();
    expect(stagedBody()[0].drawing_attachment_id ?? null).toBeNull();
  });

  it('offers no Choose or Change buttons in the read view or on a frozen version', async () => {
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1', product_image: PHOTO(), technical_drawing: DRAWING() })]);
    renderEditor();
    await screen.findByRole('img', { name: 'SRT-WC-01 technical drawing' });
    expect(screen.queryByRole('button', { name: /^(Choose|Change) (photo|drawing) on / })).toBeNull();
  });

  it('offers none when the product has a single candidate', async () => {
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1', product_image: PHOTO({ candidate_count: 1, candidates: [PHOTO().candidates[0]] }), technical_drawing: DRAWING({ candidate_count: 1, candidates: [DRAWING().candidates[0]] }) })]);
    await renderEditing();
    expect(screen.queryByRole('button', { name: /^(Choose|Change) (photo|drawing) on / })).toBeNull();
  });
});

const STOCK_IB = {
  available_qty: '25',
  locations: [{ location: 'MWH-IB', where: 'group', available_qty: '25', so_qty: '0', spo_qty: '0', qty_on_hand: '25' }],
  incoming: [{ spo_number: 'SPO-1001', spo_qty: '5', expected_date: '2026-11-01' }],
  sales_orders: [],
  bins: [],
};
const STOCK_POOLS = {
  available_qty: '9',
  locations: [{ location: 'BRW', where: 'site_pool', available_qty: '9', so_qty: '0', spo_qty: '0', qty_on_hand: '9' }],
  incoming: [],
  sales_orders: [],
  bins: [],
};

function withStock(ib: Record<string, unknown> = STOCK_IB) {
  stockByGroup.IB = ib;
  stockByGroup.BB = { ...STOCK_IB, available_qty: '3' };
  stockByGroup.pools = STOCK_POOLS;
}

describe('QuotationVersionEditor stock chip (QUOTATION-UX)', () => {
  beforeEach(() => {
    // 10 wanted, a real product behind the line (the chip only exists on a product line).
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1' })]);
  });

  it('shows available stock for the chosen group, toned by the line quantity', async () => {
    withStock();
    await renderEditing();
    const chip = await screen.findByRole('button', { name: 'Stock on SRT-WC-01' });
    expect(chip).toHaveTextContent(/25 avail/);
    expect(chip).toHaveAttribute('data-tone', 'ok');
    expect(useStockDetailMock).toHaveBeenCalledWith('p1' as never, null, null as never, 'IB');
  });

  it.each([
    ['4', 'part'],
    ['0', 'none'],
    ['-3', 'none'],
  ])('tones %s available against 10 wanted as %s', async (available, tone) => {
    withStock({ ...STOCK_IB, available_qty: available });
    listQuotationLines.mockResolvedValue([ext({ product_id: 'p1' })]);
    await renderEditing();
    expect(await screen.findByRole('button', { name: 'Stock on SRT-WC-01' })).toHaveAttribute('data-tone', tone);
  });

  it('lists the groups in Stock at, remembers the pick, and restores it on remount', async () => {
    withStock();
    const first = await renderEditing();
    const select = await screen.findByRole('combobox', { name: 'Stock at' });
    fireEvent.click(select);
    fireEvent.click(await screen.findByRole('option', { name: /BB/ }));
    await waitFor(() => expect(window.localStorage.getItem('quotation-lines.stock-group')).toBe('BB'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Stock on SRT-WC-01' })).toHaveTextContent(/3 avail/));
    first.unmount();
    session = null;
    await renderEditing();
    expect(await screen.findByRole('button', { name: 'Stock on SRT-WC-01' })).toHaveTextContent(/3 avail/);
  });

  it('still renders when localStorage throws', async () => {
    withStock();
    const get = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied'); });
    const set = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied'); });
    try {
      await renderEditing();
      expect(await screen.findByRole('combobox', { name: 'Stock at' })).toBeInTheDocument();
    } finally {
      get.mockRestore();
      set.mockRestore();
    }
  });

  it('opens a popover with the group and pool locations and the incoming SPO numbers', async () => {
    withStock();
    await renderEditing();
    const chip = await screen.findByRole('button', { name: 'Stock on SRT-WC-01' });
    fireEvent.pointerDown(chip, { button: 0, ctrlKey: false });
    fireEvent.click(chip);
    expect((await screen.findAllByText(/MWH-IB/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/BRW/).length).toBeGreaterThan(0);
    expect(screen.getByText(/SPO-1001/)).toBeInTheDocument();
    expect(useStockDetailMock).toHaveBeenCalledWith('p1' as never, null, null as never, 'pools');
  });

  it('gives a custom item no stock button', async () => {
    withStock();
    listQuotationLines.mockResolvedValue([line({ product_code: null, product_id: null, description: 'Bespoke top' })]);
    await renderEditing();
    expect(screen.queryByRole('button', { name: /^Stock on / })).toBeNull();
  });
});

describe('QuotationVersionEditor lines grid, fix round 1 (QUOTATION-UX)', () => {
  const THREE = [
    line({ id: 'l1', product_code: 'A-1', description: 'First', sort_order: 0 }),
    line({ id: 'l2', product_code: 'A-2', description: 'Second', sort_order: 10 }),
    line({ id: 'l3', product_code: 'A-3', description: 'Third', sort_order: 20, band_label: 'BILL NO 3' }),
  ];

  it('AC-QLG01: a row drag moves line 3 above line 2, renumbers, and carries its section bar', async () => {
    listQuotationLines.mockResolvedValue(THREE);
    await renderEditing();
    expect(dnd.dragEnd).not.toBeNull();

    act(() => {
      dnd.dragEnd!({ active: { id: 'l3' }, over: { id: 'l2' } });
    });

    await waitFor(() => expect(stagedBody().map((l) => l.id)).toEqual(['l1', 'l3', 'l2']));
    expect(itemNumbers()).toEqual(['1', '2', '3']);
    // The heading that opened on line 3 travels with line 3: still directly above its row.
    const heading = screen.getByRole('textbox', { name: 'Section heading on A-3' });
    const lineRow = screen.getByRole('textbox', { name: 'Qty on A-3' }).closest('tr');
    expect(heading.closest('tr')?.nextElementSibling).toBe(lineRow);
    expectNothingWritten();
  });

  it('AC-QLG01: a drop on itself or outside the table changes nothing', async () => {
    listQuotationLines.mockResolvedValue(THREE);
    await renderEditing();
    act(() => {
      dnd.dragEnd!({ active: { id: 'l3' }, over: { id: 'l3' } });
      dnd.dragEnd!({ active: { id: 'l3' }, over: null });
    });
    expect(stagedBody().map((l) => l.id)).toEqual(['l1', 'l2', 'l3']);
  });

  it('shows no stale pick after the product goes A -> B -> A, and saves both picks null', async () => {
    getProductsForLineSelect.mockResolvedValue([
      ...PRODUCTS,
      {
        id: 'p1',
        product_code: 'SRT-WC-01',
        product_name: 'Wall-hung WC',
        description: 'Wall-hung WC',
        brand_id: 'b1',
        base_uom_id: 'u1',
        list_price: '900.00',
      },
    ]);
    listQuotationLines.mockResolvedValue([
      ext({
        product_id: 'p1',
        image_attachment_id: 'a2',
        drawing_attachment_id: 'd2',
        product_image: PHOTO(),
        technical_drawing: DRAWING(),
      }),
    ]);
    await renderEditing();
    expect(screen.getByRole('img', { name: 'SRT-WC-01 product photo' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('combobox', { name: 'Product on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-BASIN-02/ }));
    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ product_id: 'p9' }));

    fireEvent.click(screen.getByRole('combobox', { name: /^Product on / }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-WC-01/ }));
    await waitFor(() => expect(stagedBody()[0]).toMatchObject({ product_id: 'p1' }));

    expect(stagedBody()[0].image_attachment_id ?? null).toBeNull();
    expect(stagedBody()[0].drawing_attachment_id ?? null).toBeNull();
    // The cells claim nothing the save will not carry.
    expect(screen.queryByRole('img', { name: /product photo$/ })).toBeNull();
    expect(screen.queryByRole('img', { name: /technical drawing$/ })).toBeNull();
  });

  it('AC-QLG02: the grid remembers its columns under projects.projects.view::quotation-lines', async () => {
    listQuotationLines.mockResolvedValue(THREE);
    renderEditor();
    await screen.findByText('First');
    expect(listingKeysSeen).toContain('projects.projects.view::quotation-lines');
  });

  it('a frozen line with several candidates shows no count badge and no Choose or Change', async () => {
    listQuotationLines.mockResolvedValue([
      ext({
        version_id: 'v1',
        product_id: 'p1',
        product_image: PHOTO({ candidate_count: 3 }),
        technical_drawing: DRAWING({ candidate_count: 3 }),
      }),
    ]);
    await renderEditing();
    fireEvent.click(screen.getByRole('button', { name: 'v1 (frozen)' }));
    const photo = await screen.findByRole('img', { name: 'SRT-WC-01 product photo' });
    const drawing = screen.getByRole('img', { name: 'SRT-WC-01 technical drawing' });

    expect(photo.closest('span')?.textContent?.trim()).toBe('');
    expect(drawing.closest('span')?.textContent?.trim()).toBe('');
    expect(screen.queryByRole('button', { name: /^(Choose|Change) (photo|drawing) on / })).toBeNull();
  });
});

// ------------------------------------------------------------------ fix round 2 (QUOTATION-UX)

const NO_PHOTO = () =>
  PHOTO({ state: 'not_chosen', url: null, preview_url: null, attachment_id: null, filename: null });

describe('QuotationVersionEditor fix round 2 (QUOTATION-UX)', () => {
  beforeEach(() => {
    getProductsForLineSelect.mockResolvedValue(PRODUCTS);
    getQuotationLineImages.mockResolvedValue({
      product_image: NO_PHOTO(),
      technical_drawing: DRAWING(),
    });
  });

  it('fills Description, Brand and UOM from a select row that carries them (defect 1)', async () => {
    getProductsForLineSelect.mockResolvedValue([
      {
        id: 'p9',
        product_code: 'SRTWC8356-RL',
        product_name: 'SORENTO ONE PIECE TOILET',
        description: 'SORENTO ONE PIECE (RIMLESS) TOILET (S-TRAP 250MM)  SRTWC8356-RL',
        brand_id: 'b1',
        base_uom_id: 'u1',
        list_price: '560.00',
      },
    ]);
    await renderEditing();
    fireEvent.click(screen.getByRole('combobox', { name: 'Product on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('option', { name: /SRTWC8356-RL/ }));
    await waitFor(() =>
      expect(screen.getByRole('textbox', { name: 'Description on SRT-WC-01' })).toHaveValue(
        'SORENTO ONE PIECE (RIMLESS) TOILET (S-TRAP 250MM)  SRTWC8356-RL',
      ),
    );
    expect(screen.getByRole('combobox', { name: 'Brand on SRT-WC-01' })).toHaveTextContent('SORENTO');
    expect(screen.getByRole('combobox', { name: 'UOM on SRT-WC-01' })).toHaveTextContent('PCS');
  });

  it('shows photo and drawing on a new line once a product is picked, and stages a pick (defect 2)', async () => {
    await renderEditing();
    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    fireEvent.click(await screen.findByRole('combobox', { name: 'Product on line 2' }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-BASIN-02/ }));

    await waitFor(() => expect(getQuotationLineImages).toHaveBeenCalledWith('p9'));
    // The drawing resolves to its default; two photos and none chosen asks for a choice.
    expect(await screen.findByRole('img', { name: /technical drawing$/ })).toHaveAttribute(
      'src',
      'https://cdn.test/d.png',
    );
    fireEvent.click(await screen.findByRole('button', { name: /^Choose photo on / }));
    fireEvent.click(await screen.findByRole('button', { name: 'Use wc side.jpg' }));
    await waitFor(() =>
      expect(stagedBody()[1]).toMatchObject({ product_id: 'p9', image_attachment_id: 'a2' }),
    );
    expectNothingWritten();
  });

  it('a product change on a saved line resolves the new product\'s cells from the route (defect 2)', async () => {
    await renderEditing();
    fireEvent.click(screen.getByRole('combobox', { name: 'Product on SRT-WC-01' }));
    fireEvent.click(await screen.findByRole('option', { name: /SRT-BASIN-02/ }));
    await waitFor(() => expect(getQuotationLineImages).toHaveBeenCalledWith('p9'));
    expect(await screen.findByRole('img', { name: /technical drawing$/ })).toBeInTheDocument();
  });

  it('a picked photo and drawing on a saved line show, reach the save body and dirty the scope (defect 3)', async () => {
    listQuotationLines.mockResolvedValue([
      ext({ product_id: 'p1', product_image: NO_PHOTO(), technical_drawing: DRAWING() }),
    ]);
    await renderEditing();
    const user = userEvent.setup();

    await user.click(screen.getByRole('button', { name: 'Choose photo on SRT-WC-01' }));
    await user.click(await screen.findByRole('button', { name: 'Use wc side.jpg' }));
    expect(await screen.findByRole('img', { name: 'SRT-WC-01 product photo' })).toHaveAttribute(
      'src',
      'https://cdn.test/s.jpg',
    );

    await user.click(screen.getByRole('button', { name: 'Change drawing on SRT-WC-01' }));
    await user.click(await screen.findByRole('button', { name: 'Use wc drawing 2.png' }));
    expect(screen.getByRole('img', { name: 'SRT-WC-01 technical drawing' })).toHaveAttribute(
      'src',
      'https://cdn.test/d2.png',
    );

    await waitFor(() =>
      expect(stagedBody()[0]).toMatchObject({ image_attachment_id: 'a2', drawing_attachment_id: 'd2' }),
    );
    expect(session?.isDirty).toBe(true);
    expect(session?.changedScopes).toHaveLength(1);
    expectNothingWritten();
  });

  it('marks an empty custom description as soon as it is left, before anything is typed (defect 4)', async () => {
    await renderEditing();
    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    const description = await screen.findByRole('textbox', { name: 'Description on line 2' });
    expect(screen.queryByText('A custom item needs a description')).toBeNull();
    fireEvent.focus(description);
    fireEvent.blur(description);
    expect(await screen.findByText('A custom item needs a description')).toBeInTheDocument();
    expect(description).toHaveAttribute('aria-invalid', 'true');
  });

  it('carries Complete set typed on a never-saved line in the first save body (defect 5)', async () => {
    await renderEditing();
    fireEvent.click(screen.getByRole('button', { name: 'Add a line' }));
    await screen.findByRole('textbox', { name: 'Description on line 2' });
    await chooseMenuItem('line 2', 'Line details');
    fireEvent.change(screen.getByRole('textbox', { name: 'Complete set on line 2' }), {
      target: { value: 'c/w seat cover' },
    });
    await waitFor(() => expect(stagedBody()[1]).toMatchObject({ complete_set: 'c/w seat cover' }));
  });

  describe('console', () => {
    let errors: ReturnType<typeof vi.spyOn>;
    beforeEach(() => {
      errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    });
    afterEach(() => errors.mockRestore());

    it('logs no DOM-nesting error with a section bar and a footer in edit mode (defect 6)', async () => {
      listQuotationLines.mockResolvedValue([
        line({ id: 'l1', product_code: 'A-1', description: 'First', band_label: 'BILL NO 1' }),
        line({ id: 'l2', product_code: 'A-2', description: 'Second', sort_order: 10 }),
      ]);
      await renderEditing();
      expect(footerText()).not.toBe('');
      const nesting = errors.mock.calls
        .map((call) => call.map(String).join(' '))
        .filter((text) => /cannot contain a nested|validateDOMNesting/.test(text));
      expect(nesting).toEqual([]);
    });
  });
});
