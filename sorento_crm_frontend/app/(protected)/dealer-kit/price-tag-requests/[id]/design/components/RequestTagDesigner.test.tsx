/**
 * The design page's own state machine: it must never dead-end (#476, S3).
 *
 * `TagCanvasEditor` needs Konva, fonts and the asset library - none of which
 * exist in jsdom - so it is stood in for by a thin stub that records what it
 * was asked to draw. What is under test here is entirely RequestTagDesigner's
 * OWN logic: which of "loading templates / templates failed / resolving
 * prices / no lines / the canvas" it shows, and which template - a real one or
 * the product-block starter (D6/D13) - a line's tag gets cloned from.
 */

import React from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
// The designer mounts a react-query mutation (the deferred tag Remove), so a
// bare `render` throws "No QueryClient set" before the component exists.
import { renderWithQueryClient as render } from './testQueryClient';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PRODUCT_BLOCK_SIZE } from '@/lib/dealer-kit/product-block';
import type { TagLayer, TagTemplateDoc } from '@/lib/dealer-kit/tag-template-types';
import {
  ToolbarButton,
  ToolbarDropdownButton,
  type ToolbarTrailingAction,
} from '@/app/(protected)/dealer-kit/tag-templates/components/CanvasToolbar';

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { toast } from '@/lib/toast';
const mockToastSuccess = vi.mocked(toast.success);

const push = vi.fn();
const replace = vi.fn();
// Empty by default; the ?line= preselection tests below build their own
// URLSearchParams and stub this per-test.
let searchParams = new URLSearchParams();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace, refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/dealer-kit/price-tag-requests/req-1/design',
  useSearchParams: () => searchParams,
}));

vi.mock('@/app/(protected)/dealer-kit/tag-templates/components/useTagBindings', () => ({
  useKitLibrary: () => ({
    assetUrls: {},
    fonts: [],
    specKeys: [],
    fontOptions: [],
    reload: vi.fn(async () => {}),
    remember: vi.fn(),
  }),
}));

// The stand-in records the doc it was asked to draw so a test can tell a real
// template's clone apart from the starter's, the same idiom
// TagCanvasEditor.preview.test.tsx uses for react-konva/KonvaTagLayer.
//
// It also mirrors the ONE real behaviour Fix B (design lost on Design ->
// Arrange -> Design) turns on: the real editor reads `doc` once, on mount,
// keeps the layers in its OWN state from then on, and its mount effect fires
// `onLayersChange` with whatever it was handed. A stub that just rendered
// `doc.layers` directly would never reproduce a stale remount snapshot.
type TagSheetDocCapture = { doc: TagTemplateDoc };
const canvasDocs: TagSheetDocCapture[] = [];
// A MOUNT counter (S9 review B1) - distinct from `canvasDocs.length`, which
// grows on every RE-RENDER too. Only the mount-only effect below increments
// this, so a test can prove a resize (or anything else) re-rendered the
// stand-in with new props WITHOUT unmounting and remounting a fresh one -
// which is exactly what keying the editor on tag size used to do.
let canvasMountCount = 0;
vi.mock('@/app/(protected)/dealer-kit/tag-templates/components/TagCanvasEditor', () => ({
  TagCanvasEditor: ({
    doc,
    onLayersChange,
    leftRail,
    toolbarTrailing,
  }: {
    doc: TagTemplateDoc;
    onLayersChange?: (layers: TagLayer[]) => void;
    leftRail?: React.ReactNode;
    // S7 moved Full screen / the Template dropdown / Save INTO the toolbar's
    // own trailing group - the stand-in has to actually render it (not just
    // accept and drop it) for a test to reach those buttons. S2 review
    // turned `toolbarTrailing` into a data array (real arrow-key nav + the
    // menu closing itself once an item is picked, S2), so the stand-in
    // renders it through the SAME real `ToolbarButton`/`ToolbarDropdownButton`
    // `CanvasToolbar` itself uses for its inline group, rather than reinventing it.
    toolbarTrailing?: ToolbarTrailingAction[];
  }) => {
    canvasDocs.push({ doc });
    const [layers, setLayers] = React.useState<TagLayer[]>(doc.layers);
    React.useEffect(() => {
      canvasMountCount += 1;
      onLayersChange?.(doc.layers);
      // Mount-only, matching "reads its document once, on mount" - doc is
      // deliberately excluded from the deps so a doc PROP update after mount
      // (which does not happen for the real editor either) cannot re-fire it.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div data-testid="canvas-editor">
        canvas open
        {leftRail}
        <div data-testid="toolbar-trailing">
          {toolbarTrailing?.map((action) =>
            action.kind === 'menu' ? (
              <ToolbarDropdownButton
                key={action.id}
                icon={action.icon}
                label={action.label}
                disabled={action.disabled}
              >
                {action.items}
              </ToolbarDropdownButton>
            ) : (
              <ToolbarButton
                key={action.id}
                icon={action.icon}
                iconClassName={action.iconClassName}
                label={action.label}
                onClick={action.onClick}
                disabled={action.disabled}
                active={action.active}
              />
            ),
          )}
        </div>
        <button
          type="button"
          onClick={() => {
            const next = [...layers, addedLayer(layers.length)];
            setLayers(next);
            onLayersChange?.(next);
          }}
        >
          Add layer
        </button>
        <button
          type="button"
          onClick={() => {
            setLayers([]);
            onLayersChange?.([]);
          }}
        >
          Clear layers
        </button>
      </div>
    );
  },
}));

/** The designer's autosave prop, typed so a test can read back the document
 *  and the `keepalive` flag it was called with. */
type AutosaveFn = (doc: TagSheetDoc, options?: { keepalive?: boolean }) => Promise<void>;

/** A minimal, valid text layer for the "add a layer" stub button above. */
function addedLayer(index: number): TagLayer {
  return {
    id: `added-${index}`,
    type: 'text',
    x_mm: 0,
    y_mm: 0,
    width_mm: 20,
    height_mm: 10,
    rotation_deg: 0,
    z_index: index,
    locked: false,
    visible: true,
    slot_binding: null,
    text_override: null,
    props: {
      kind: 'text',
      text: 'Added',
      align: 'left',
      color: '#000',
      fontSize: 10,
      fontFamily: 'Jost',
      fontWeight: 400,
      lineHeight: 1.2,
      letterSpacing: 0,
    },
  };
}

// Arrange draws through real react-konva/Konva, which needs a real canvas -
// unavailable in jsdom. Toggling to Arrange is exercised by the Fix B tests
// below, so it needs a stand-in the same way TagCanvasEditor does above.
//
// AC-S7-16 (designer-level shape): the stand-in also renders the SAME
// "N sheet(s) / M tag(s)" summary the real `ArrangeSheetView` toolbar does,
// off the real `doc` prop it was handed - so a test at this level can prove
// a tag went missing before it ever reaches Konva, without needing a real
// canvas.
vi.mock('./ArrangeSheetView', () => ({
  ArrangeSheetView: ({ doc }: { doc: TagSheetDoc }) => {
    const totalTags = doc.sheets.reduce((sum, sheet) => sum + sheet.tags.length, 0);
    return (
      <div data-testid="arrange-view">
        arrange open
        <span>
          {doc.sheets.length} sheet{doc.sheets.length === 1 ? '' : 's'} / {totalTags} tag
          {totalTags === 1 ? '' : 's'}
        </span>
      </div>
    );
  },
}));

vi.mock('../../../../services/tagTemplateService', () => ({
  listPublishedTemplates: vi.fn(),
  createTemplateFromTag: vi.fn(),
  // S6 "Update <template>": PUT the draft, then POST publish. Both unused by
  // most tests in this file (the Template dropdown's own flow is covered by
  // RequestTagDesigner.update-template.test.tsx), present here only so
  // importing them does not crash a render that merely shows the dropdown.
  updateTemplate: vi.fn(),
  publishTemplate: vi.fn(),
}));
vi.mock('../../../../services/priceTagRequestService', () => ({
  // One row per TAG since S3 (D3), auto-split at save so no Split / Pick one
  // action exists any more. The three below it are what the post-save
  // reload calls.
  resolveRequestTags: vi.fn(),
  getPriceTagRequest: vi.fn(),
  updateRequestTag: vi.fn(),
  transitionPriceTagRequest: vi.fn(),
  exportTagSheet: vi.fn(),
  // AC-S10-3 (PLAN-price-tag-ai-extract-resolver.md D15): the approved+office
  // bar's primary button - not imported by RequestTagDesigner.tsx yet.
  markReadyForCollection: vi.fn(),
}));
// Tag Size control's "Saved sizes" group (S4): a react-query hook this suite
// has no QueryClientProvider for. `useTagSizesQuery` is a `vi.fn()` so the
// "TagSizeControl - saved sizes group (S5)" tests below can override its
// return value per test; every other test gets the empty-list default,
// which keeps every existing assertion about the "Custom" dropdown and the
// resize flow unchanged.
const mockUseTagSizesQuery = vi.fn(() => ({ data: [] as unknown[] }));
const mockDeleteRun = vi.fn();
const mockUseDeleteTagSizePreset = vi.fn(() => ({
  run: mockDeleteRun,
  targetId: null as string | null,
  isPending: false,
}));
vi.mock('../../../../tag-sizes/hooks/useTagSizes', () => ({
  useTagSizesQuery: (...args: unknown[]) => mockUseTagSizesQuery(...(args as [])),
  useDeleteTagSizePreset: () => mockUseDeleteTagSizePreset(),
  useCreateTagSize: () => ({ mutateAsync: vi.fn(async () => ({})), isPending: false }),
}));

import { listPublishedTemplates } from '../../../../services/tagTemplateService';
import {
  resolveRequestTags,
  exportTagSheet,
  markReadyForCollection,
} from '../../../../services/priceTagRequestService';
import { RequestTagDesigner } from './RequestTagDesigner';
import type {
  PriceTagRequestDetail,
  PriceTagRequestLine,
  PriceTagRequestTag,
} from '../../../../services/priceTagRequestService';
import type { TagSizeRecord } from '../../../../services/tagSizeService';
import type { LineTagData, TagSheetDoc, TagTemplate } from '@/lib/dealer-kit/tag-template-types';

const mockListTemplates = vi.mocked(listPublishedTemplates);
const mockResolveRequestTags = vi.mocked(resolveRequestTags);
const mockExportTagSheet = vi.mocked(exportTagSheet);
const mockMarkReadyForCollection = vi.mocked(markReadyForCollection);

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/**
 * The rail row label for a line's default tag.
 *
 * The product builds "1a"/"1b" from the line's position plus a letter; a
 * fixture only needs two lines' tag rows to be separately clickable, so the
 * label reuses the line id's own suffix ('line-b' -> 'ba'). The rail selects a
 * TAG now, not a line - the line header is no longer a button - so every test
 * that used to click a line's code or name clicks its tag row instead.
 */
function tagLabelFor(lineId: string): string {
  return `${lineId.split('-').pop() ?? '1'}a`;
}

/**
 * The one tag a line carries by default (S3, AC-S3-1).
 *
 * Its id IS the line id, so every id these tests already assert on stays the
 * id they assert on: submit mints exactly one tag per line, and only a Split
 * ever gives a line a second one.
 */
function requestTag(
  lineId: string,
  quantity: number,
  overrides: Partial<PriceTagRequestTag> = {},
): PriceTagRequestTag {
  return {
    id: lineId,
    sort_order: 0,
    label: tagLabelFor(lineId),
    quantity,
    choices_display: [],
    open_groups: [],
    marketing_price_override: null,
    marketing_override_reason: null,
    list_price: 1599,
    sell_price: null,
    ...overrides,
  };
}

function line(overrides: Partial<PriceTagRequestLine> = {}): PriceTagRequestLine {
  const merged = {
    id: 'line-1',
    line_type: 'product' as const,
    product_id: 'prod-1' as string | null,
    product_set_id: null as string | null,
    name: 'Kitchen Sink',
    code: 'SRT-1234',
    show_promo_price: false,
    quantity: 1,
    included_accessories: null as string | null,
    sort_order: 0,
    list_price: 1599 as number | null,
    sell_price: null as number | null,
    parts: [],
    package_warning: null,
    ...overrides,
  };
  return {
    ...merged,
    tags: overrides.tags ?? [requestTag(merged.id, merged.quantity)],
  } as PriceTagRequestLine;
}

function request(overrides: Partial<PriceTagRequestDetail> = {}): PriceTagRequestDetail {
  return {
    id: 'req-1',
    doc_number: 'PT-000001',
    debtor_code: null,
    debtor_name: null,
    needed_by_date: null,
    notes: null,
    status: 'designing',
    line_count: 1,
    created_at: '2026-09-01T00:00:00Z',
    assigned_to_id: 'user-1',
    assigned_to_name: 'Jayson',
    contact_name: 'CONTACT AI',
    contact_id: 'contact-1',
    lines: [line()],
    ...overrides,
  };
}

function lineTagData(overrides: Partial<LineTagData> = {}): LineTagData {
  // One row per TAG since S3. The everyday request has one tag per line and the
  // tag's id is the line's, so a row named by `line_id` keys on the same id it
  // always did.
  const lineId = overrides.line_id ?? 'line-1';
  return {
    tag_id: lineId,
    tag_label: '1a',
    open_groups: [],
    parts: [],
    line_id: 'line-1',
    code: 'SRT-1234',
    name: 'Kitchen Sink',
    dimensions: '800 x 500 x 220 mm',
    spec_lines: 'Stainless steel',
    specs: [],
    set_members: '',
    images: [],
    list_price: 1599,
    sell_price: null,
    show_promo_price: false,
    included_accessories: '',
    quantity: 1,
    barcode: null,
    ...overrides,
  };
}

function realTemplate(overrides: Partial<TagTemplate> = {}): TagTemplate {
  return {
    id: 'tpl-1',
    name: 'Ala carte',
    family: 'ala_carte',
    doc: {
      layers: [
        {
          id: 'l1',
          type: 'text',
          x_mm: 0,
          y_mm: 0,
          width_mm: 20,
          height_mm: 10,
          rotation_deg: 0,
          z_index: 0,
          locked: false,
          visible: true,
          slot_binding: 'code',
          text_override: null,
          props: {
            kind: 'text',
            text: '',
            align: 'left',
            color: '#000',
            fontSize: 10,
            fontFamily: 'Jost',
            fontWeight: 400,
            lineHeight: 1.2,
            letterSpacing: 0,
          },
        },
      ],
      // A distinct size from PRODUCT_BLOCK_SIZE, so a test can tell "cloned
      // from the real template" apart from "built the starter" on sight.
      width_mm: 60,
      height_mm: 40,
    },
    print_size: { width_mm: 60, height_mm: 40 },
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  };
}

/** A promise the test controls the resolution of, to pin transient states. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function renderDesigner(req: PriceTagRequestDetail = request()) {
  return render(
    <RequestTagDesigner
      request={req}
      initialDoc={null}
      onSave={vi.fn(async () => {})}
      onAutosave={vi.fn<AutosaveFn>(async () => {})}
    />,
  );
}

beforeEach(() => {
  canvasDocs.length = 0;
  canvasMountCount = 0;
  mockListTemplates.mockReset();
  mockResolveRequestTags.mockReset();
  searchParams = new URLSearchParams();
  push.mockReset();
  replace.mockReset();
  mockToastSuccess.mockReset();
  // D9: TagSizeControl's open/closed state persists in localStorage across
  // mounts by design - cleared per test so one test opening the panel does
  // not leave it open (or a later click's toggle closing it) for the next.
  window.localStorage.clear();
});

// ---------------------------------------------------------------------------
// Which template a line's tag is cloned from
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - which template a line clones', () => {
  it('clones the product-block starter exactly when templates loaded empty (AC-S3-1)', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    const drawn = canvasDocs[canvasDocs.length - 1].doc;
    expect(drawn.width_mm).toBe(PRODUCT_BLOCK_SIZE.width_mm);
    expect(drawn.height_mm).toBe(PRODUCT_BLOCK_SIZE.height_mm);
  });

  it('clones the real published template when one exists (AC-S3-4)', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    const drawn = canvasDocs[canvasDocs.length - 1].doc;
    // The template's own print size (60x40), never the starter's (85x58).
    expect(drawn.width_mm).toBe(60);
    expect(drawn.height_mm).toBe(40);
  });

  it('binds the starter to the line\'s REAL product id, not the line id (review #2, #3)', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner(request({ lines: [line({ id: 'line-1', product_id: 'prod-1' })] }));

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    const drawn = canvasDocs[canvasDocs.length - 1].doc;
    const group = drawn.layers.find((l) => l.props.kind === 'group');
    expect(group?.props).toMatchObject({ binding: { product_id: 'prod-1' } });
    expect(group?.props).not.toMatchObject({ binding: { product_id: 'line-1' } });
  });

  it('builds a set-block starter, bound to the real set id, for a product_set line (review #1)', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({
        line_id: 'line-2',
        code: 'BF-SET-01',
        name: 'Bathroom Set',
        set_members: '- A1 (Basin)\n- A2 (Tap)',
      }),
    ]);

    renderDesigner(
      request({
        lines: [
          line({
            id: 'line-2',
            line_type: 'product_set',
            product_id: null,
            product_set_id: 'set-1',
          }),
        ],
      }),
    );

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    const drawn = canvasDocs[canvasDocs.length - 1].doc;
    // The set footprint (85x62), not the product footprint (85x58).
    expect(drawn.width_mm).toBe(85);
    expect(drawn.height_mm).toBe(62);

    const membersLayer = drawn.layers.find((l) => l.slot_binding === 'set_members');
    expect(membersLayer?.props).toMatchObject({ text: '- A1 (Basin)\n- A2 (Tap)' });

    const group = drawn.layers.find((l) => l.props.kind === 'group');
    expect(group?.props).toMatchObject({ binding: { product_set_id: 'set-1' } });
  });
});

// ---------------------------------------------------------------------------
// Full screen (S7, AC-S6-1): the shared FocusShell, same as the room designer.
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - full screen (AC-S6-1)', () => {
  it('wraps the canvas in the shared FocusShell, and Escape exits', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    expect(document.querySelector('[data-dk-focus-mode]')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Full screen/ }));
    expect(document.querySelector('[data-dk-focus-mode]')).not.toBeNull();
    expect(screen.getByTestId('canvas-editor')).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.querySelector('[data-dk-focus-mode]')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// One CTA (S7, AC-S7-1/S7-6): the request bar's right side holds the Saved
// indicator and exactly one button, Mark design ready (r7 renamed "proof"
// to "design" throughout - PLAN-price-tag-r7-request-ux D9). Full screen,
// the Template dropdown and Save moved into the canvas toolbar's own
// trailing group.
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - one CTA in the request bar (S7, AC-S7-1)', () => {
  it('the request bar holds exactly one button in designing state - Mark design ready', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner(request({ status: 'designing' }));
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    // The request bar is everything OUTSIDE the mocked canvas editor (and
    // therefore outside its toolbar-trailing stand-in) - the page's own
    // chrome, not the canvas's.
    const canvas = screen.getByTestId('canvas-editor');
    const barButtons = Array.from(document.body.querySelectorAll('button')).filter(
      (btn) => !canvas.contains(btn),
    );
    const names = barButtons.map((btn) => btn.textContent?.trim());

    expect(names.filter((t) => t?.includes('Mark design ready'))).toHaveLength(1);
    // Nothing else button-shaped sits out here - Back and the Design/Arrange
    // toggle are navigation, not "the one action", and are asserted
    // separately below by name.
    expect(names.some((t) => t?.includes('Full screen'))).toBe(false);
    expect(names.some((t) => t === 'Save')).toBe(false);
  });

  it('Full screen, the Template dropdown and Save are all found INSIDE the canvas toolbar (AC-S7-6)', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    const trailing = screen.getByTestId('toolbar-trailing');
    expect(within(trailing).getByRole('button', { name: /Full screen/ })).toBeInTheDocument();
    expect(within(trailing).getByRole('button', { name: /Save/ })).toBeInTheDocument();
    expect(within(trailing).getByRole('button', { name: 'Template' })).toBeInTheDocument();
  });

  it('the Design/Arrange mode toggle and Back stay in the request bar - navigation, not the CTA', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    expect(screen.getByRole('button', { name: 'Design' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Arrange' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /PT-000001/ })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC-S10-3/S10-4 (PLAN-price-tag-ai-extract-resolver.md D15): the request
// bar at `approved` - print and hand-over live here now, not just export.
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - the approved request bar (AC-S10-3, AC-S10-4)', () => {
  function barButtonNames() {
    const canvas = screen.getByTestId('canvas-editor');
    return Array.from(document.body.querySelectorAll('button'))
      .filter((btn) => !canvas.contains(btn))
      .map((btn) => btn.textContent?.trim());
  }

  it('AC-S10-3: office - Export PDF and the primary Mark ready for collection, no Mark design ready', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);
    mockExportTagSheet.mockResolvedValue(undefined as never);
    mockMarkReadyForCollection.mockResolvedValue(undefined as never);

    renderDesigner(request({ status: 'approved', print_by: 'office' } as never));
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    const names = barButtonNames();
    expect(names.some((t) => t?.includes('Export PDF'))).toBe(true);
    expect(names.some((t) => t?.includes('Mark ready for collection'))).toBe(true);
    expect(names.some((t) => t?.includes('Mark design ready'))).toBe(false);

    fireEvent.click(screen.getByRole('button', { name: /Export PDF/ }));
    await waitFor(() => expect(mockExportTagSheet).toHaveBeenCalledWith('req-1'));
    expect(mockExportTagSheet).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole('button', { name: /Mark ready for collection/ }));
    await waitFor(() =>
      expect(mockMarkReadyForCollection).toHaveBeenCalledWith('req-1'),
    );
    await waitFor(() =>
      expect(mockToastSuccess).toHaveBeenCalledWith('Marked ready for collection'),
    );
  });

  it('AC-S10-4: self - Export PDF only, no Mark ready for collection', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner(request({ status: 'approved', print_by: 'self' } as never));
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    const names = barButtonNames();
    expect(names.some((t) => t?.includes('Export PDF'))).toBe(true);
    expect(names.some((t) => t?.includes('Mark ready for collection'))).toBe(false);
    expect(names.some((t) => t?.includes('Mark design ready'))).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// A design must survive Design -> Arrange -> Design (Fix B).
//
// `docRef` snapshots the open tag's layers once, keyed by tag id, so the
// editor can remount on the SAME tag without replaying every keystroke.
// Toggling to Arrange unmounts the editor without changing the tag id, so
// without invalidating the snapshot on that unmount, the remount handed the
// editor - and, through its mount effect, the host's OWN `tags` state -
// whatever the snapshot held before the toggle, silently discarding
// anything added since. Save afterwards would then persist the loss.
// ---------------------------------------------------------------------------

/** The layers of the doc the canvas was last handed. */
const drawnLayers = () => canvasDocs[canvasDocs.length - 1].doc.layers;
const hasAddedLayer = () =>
  drawnLayers().some((l) => l.id.startsWith('added-'));

describe('RequestTagDesigner - design survives a mode toggle (Fix B)', () => {
  it('keeps a layer added in Design after toggling to Arrange and back', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));

    fireEvent.click(screen.getByRole('button', { name: 'Arrange' }));
    expect(screen.queryByTestId('canvas-editor')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Design' }));
    // The editor being back on screen is not the claim: its FIRST render after
    // the remount can still carry the pre-toggle doc, and the mount effect that
    // reconciles the host lands a tick later. Wait for the doc itself.
    await waitFor(() => expect(hasAddedLayer()).toBe(true));
  });

  it('keeps a design cleared to 0 layers, then switched to another line and back, then rebuilt to 3 layers, across a mode toggle', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-1', code: 'SRT-1' }),
      lineTagData({ line_id: 'line-2', code: 'SRT-2', name: 'Bath Tap' }),
    ]);

    renderDesigner(
      request({
        lines: [
          line({ id: 'line-1', code: 'SRT-1' }),
          line({ id: 'line-2', code: 'SRT-2', product_id: 'prod-2' }),
        ],
      }),
    );

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    // Clear line-1's tag to zero layers.
    fireEvent.click(screen.getByRole('button', { name: 'Clear layers' }));

    // Switch to line-2 (clones its own starter) and back to line-1 - the
    // canvas remounts on the tag id both times (existing behaviour, not part
    // of Fix B), so the clear must still be there when line-1 reopens.
    // D8: one tag, no parts - each line folds to ONE block, selected by
    // clicking its code rather than the ordinal text a folded row no
    // longer renders.
    fireEvent.click(screen.getByText('SRT-2').closest('button') as HTMLElement);
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    fireEvent.click(screen.getByText('SRT-1').closest('button') as HTMLElement);
    await waitFor(() => expect(drawnLayers()).toHaveLength(0));

    // Add 3 layers back.
    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));

    // Toggle to Arrange and back - this is the step that lost the design
    // before Fix B.
    fireEvent.click(screen.getByRole('button', { name: 'Arrange' }));
    fireEvent.click(screen.getByRole('button', { name: 'Design' }));
    await waitFor(() => expect(drawnLayers()).toHaveLength(3));
  });
});

// ---------------------------------------------------------------------------
// The canvas's own placeholder states
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - explicit canvas states (AC-S3-2, AC-S3-3)', () => {
  it('says "Loading templates..." while the template fetch is in flight', async () => {
    const templatesGate = deferred<TagTemplate[]>();
    mockListTemplates.mockReturnValue(
      templatesGate.promise as unknown as ReturnType<typeof listPublishedTemplates>,
    );
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();

    expect(await screen.findByText('Loading templates...')).toBeInTheDocument();
    expect(screen.queryByTestId('canvas-editor')).not.toBeInTheDocument();

    // Clean up: let the pending promise resolve so React has nothing left in
    // flight after the assertion.
    templatesGate.resolve([]);
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
  });

  it('says "Resolving prices..." once templates have loaded but prices have not', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    const pricesGate = deferred<LineTagData[]>();
    mockResolveRequestTags.mockReturnValue(
      pricesGate.promise as unknown as ReturnType<typeof resolveRequestTags>,
    );

    renderDesigner();

    expect(await screen.findByText('Resolving prices...')).toBeInTheDocument();
    expect(screen.queryByTestId('canvas-editor')).not.toBeInTheDocument();

    pricesGate.resolve([lineTagData()]);
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
  });

  it('shows an explicit error with Retry when the template fetch fails, and Retry recovers it', async () => {
    mockListTemplates.mockRejectedValueOnce(new Error('network down'));
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();

    expect(await screen.findByText('Failed to load tag templates.')).toBeInTheDocument();
    const retryButton = screen.getByRole('button', { name: /retry/i });
    expect(retryButton).toBeInTheDocument();
    expect(screen.queryByTestId('canvas-editor')).not.toBeInTheDocument();

    mockListTemplates.mockResolvedValueOnce([]);
    fireEvent.click(retryButton);

    expect(mockListTemplates).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    expect(screen.queryByText('Failed to load tag templates.')).not.toBeInTheDocument();
  });

  it('shows an explicit error with Retry when price resolution fails, and Retry recovers it (review #5)', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    mockResolveRequestTags.mockRejectedValueOnce(new Error('network down'));

    renderDesigner();

    expect(await screen.findByText('Failed to resolve prices.')).toBeInTheDocument();
    const retryButton = screen.getByRole('button', { name: /retry/i });
    expect(retryButton).toBeInTheDocument();
    // Not "opens with blank data and no cause" - the canvas must not appear
    // at all until prices are either resolved or explicitly retried.
    expect(screen.queryByTestId('canvas-editor')).not.toBeInTheDocument();

    mockResolveRequestTags.mockResolvedValueOnce([lineTagData()]);
    fireEvent.click(retryButton);

    expect(mockResolveRequestTags).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    expect(screen.queryByText('Failed to resolve prices.')).not.toBeInTheDocument();
  });

  it('says there is nothing to design when the request has no lines', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([]);

    renderDesigner(request({ lines: [], line_count: 0 }));

    expect(
      await screen.findByText('This request has no lines, so there is nothing to design.'),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('canvas-editor')).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// A line whose product could not be resolved (457_ptag_line_xco_repair): the
// prices resolve skips a line whose product 404s under the request's
// company - the rail must say so plainly, without a toast, instead of
// showing "Resolving..." forever.
// ---------------------------------------------------------------------------

describe("RequestTagDesigner - a line the request's company cannot resolve", () => {
  it('shows a "Product not found in this company" chip once prices have loaded, for the line resolveRequestTags skipped', async () => {
    mockListTemplates.mockResolvedValue([]);
    // Only line-1 comes back - line-2's product 404d under this request's
    // company and resolve_request_line_data silently skips it.
    mockResolveRequestTags.mockResolvedValue([lineTagData({ line_id: 'line-1' })]);

    renderDesigner(
      request({
        lines: [line({ id: 'line-1' }), line({ id: 'line-2', product_id: 'prod-2' })],
        line_count: 2,
      }),
    );

    expect(
      await screen.findByText('Product not found in this company'),
    ).toBeInTheDocument();
    // The resolved line never shows the chip.
    expect(screen.getByText('Kitchen Sink')).toBeInTheDocument();
  });

  it('the chip never appears while prices are still resolving (the rail is gated behind pricesStatus === loaded)', async () => {
    mockListTemplates.mockResolvedValue([]);
    const prices = deferred<LineTagData[]>();
    mockResolveRequestTags.mockReturnValue(
      prices.promise as unknown as ReturnType<typeof resolveRequestTags>,
    );

    renderDesigner(
      request({
        lines: [line({ id: 'line-1' }), line({ id: 'line-2', product_id: 'prod-2' })],
        line_count: 2,
      }),
    );

    expect(await screen.findByText('Resolving prices...')).toBeInTheDocument();
    expect(screen.queryByText('Product not found in this company')).not.toBeInTheDocument();

    await act(async () => {
      prices.resolve([lineTagData({ line_id: 'line-1' })]);
    });

    expect(
      await screen.findByText('Product not found in this company'),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// LINES rail - a name that only repeats the code is redundant (S2, AC-S2-1)
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - LINES rail duplicate name (S2, AC-S2-1)', () => {
  it('shows the code once for a line whose name equals its code', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ code: 'SK-1234', name: 'SK-1234' }),
    ]);

    renderDesigner(request({ lines: [line({ code: 'SK-1234', name: 'SK-1234' })] }));

    expect(await screen.findByText('SK-1234')).toBeInTheDocument();
    // No second element repeats it as a name line underneath.
    expect(screen.getAllByText('SK-1234')).toHaveLength(1);
  });

  it('still shows both the code and the name for a line whose name differs (AC-S2-1)', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);

    renderDesigner();

    expect(await screen.findByText('SRT-1234')).toBeInTheDocument();
    expect(screen.getByText('Kitchen Sink')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Autosave (D22, S8, AC-S8-3)
//
// Autosave and manual Save are two different acts on two different routes
// (B1/B3): `onAutosave` writes the request's DRAFT and says nothing, `onSave`
// snapshots an immutable version and toasts. Every test here is about which
// of the two ran, how many times, and what the header said about it.
//
// The starter-tag clone a line gets on open is NOT one of them (S3), so
// nothing has to be drained before the behaviour under test - the first
// assertion in every case below is that opening the page saved nothing.
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - autosave (D22, AC-S8-3)', () => {
  /** Mount with templates and prices settled, and nothing saved yet. */
  async function mountQuiet(overrides: Partial<PriceTagRequestDetail> = {}) {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);
    const onSave = vi.fn(async () => {});
    const onAutosave = vi.fn<AutosaveFn>(async () => {});

    render(
      <RequestTagDesigner
        request={request(overrides)}
        initialDoc={null}
        onSave={onSave}
        onAutosave={onAutosave}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    return { onSave, onAutosave };
  }

  it('collapses rapid edits into a single autosave call, ~1s after the LAST one', async () => {
    const { onSave, onAutosave } = await mountQuiet();

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500);
      });
      // A second edit before the first's debounce would have fired resets
      // the clock rather than queuing a second save.
      fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(999);
      });
      expect(onAutosave).not.toHaveBeenCalled();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(onAutosave).toHaveBeenCalledTimes(1);
      // The DRAFT route, never the version route.
      expect(onSave).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('says nothing on a successful autosave - the indicator is the whole report (D22)', async () => {
    const { onAutosave } = await mountQuiet();

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(onAutosave).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/^Saved/)).toBeInTheDocument();
      // A toast roughly once a second while somebody designs is noise, not
      // feedback. `onAutosave` is silent by contract and nothing here adds one.
      expect(mockToastSuccess).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('a mode switch (Design -> Arrange) flushes the pending autosave immediately', async () => {
    const { onAutosave } = await mountQuiet();

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
      expect(onAutosave).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole('button', { name: 'Arrange' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(onAutosave).toHaveBeenCalledTimes(1);

      // The debounce the flush cut short must not ALSO fire later.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000);
      });
      expect(onAutosave).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows Save failed with Retry, and Retry resends the same doc', async () => {
    const { onAutosave } = await mountQuiet();
    onAutosave.mockRejectedValueOnce(new Error('network down'));

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(onAutosave).toHaveBeenCalledTimes(1);
      // The failure reaches the indicator because `onAutosave` rethrows (B2) -
      // a swallowed one left the header reading "Saved" over lost work.
      expect(screen.getByText('Save failed')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(onAutosave).toHaveBeenCalledTimes(2);
      expect(onAutosave.mock.calls[1][0]).toEqual(onAutosave.mock.calls[0][0]);
      expect(screen.queryByText('Save failed')).not.toBeInTheDocument();
      expect(screen.getByText(/^Saved/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('RequestTagDesigner - old imposition presets migrate on load (S3, AC-S6-4)', () => {
  it("normalises a pre-S6 'a4_3up' preset to 'auto' so the next autosave catches it up", async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);
    const onAutosave = vi.fn<AutosaveFn>(async () => {});

    render(
      <RequestTagDesigner
        request={request()}
        initialDoc={{
          kind: 'tag_sheet',
          imposition: {
            preset: 'a4_3up',
            page_width_mm: 210,
            page_height_mm: 297,
            bleed_mm: 3,
            gap_mm: 2,
          },
          sheets: [],
        }}
        onSave={vi.fn(async () => {})}
        onAutosave={onAutosave}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    vi.useFakeTimers();
    try {
      fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
      expect(onAutosave).toHaveBeenCalledTimes(1);
      expect(onAutosave.mock.calls[0][0].imposition.preset).toBe('auto');
    } finally {
      vi.useRealTimers();
    }
  });
});

// ---------------------------------------------------------------------------
// Opening and browsing persist NOTHING (S3)
//
// The starter/template clone a line gets when it has no tag yet is the page
// deciding what to draw, not the user deciding anything. Before this, merely
// opening a request with undesigned lines - or clicking down the rail to look
// at them - wrote a design for every line the eye landed on.
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - the starter clone is not a user change (S3)', () => {
  it('saves nothing on open, and nothing while browsing undesigned lines', async () => {
    const lineA = line({ id: 'line-a', product_id: 'prod-a', code: 'AAA-1' });
    const lineB = line({ id: 'line-b', product_id: 'prod-b', code: 'BBB-2', name: 'Basin' });
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
      lineTagData({ line_id: 'line-b', code: 'BBB-2', name: 'Basin' }),
    ]);
    const onSave = vi.fn(async () => {});
    const onAutosave = vi.fn<AutosaveFn>(async () => {});

    render(
      <RequestTagDesigner
        request={request({ lines: [lineA, lineB], line_count: 2 })}
        initialDoc={null}
        onSave={onSave}
        onAutosave={onAutosave}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    // Line B has no tag either - switching to it clones one, which must be as
    // silent as opening was. D8: one tag, no parts - line B folds to ONE
    // block, selected by its code rather than the ordinal text a folded row
    // no longer renders.
    fireEvent.click(screen.getByText('BBB-2').closest('button') as HTMLElement);
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    // Well past the debounce, on real timers, so a scheduled save would have
    // had every chance to fire.
    await new Promise((resolve) => setTimeout(resolve, 1300));

    expect(onAutosave).not.toHaveBeenCalled();
    expect(onSave).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Manual Save is one deliberate version, flushed clear of the autosave (S4)
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - manual Save (S4)', () => {
  it('flushes the pending autosave, then snapshots exactly once', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);
    const order: string[] = [];
    const onSave = vi.fn(async () => {
      order.push('version');
    });
    const onAutosave = vi.fn<AutosaveFn>(async () => {
      order.push('draft');
    });

    render(
      <RequestTagDesigner
        request={request()}
        initialDoc={null}
        onSave={onSave}
        onAutosave={onAutosave}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    // The pending draft went first, so a late draft write cannot land after
    // the snapshot and resurrect the draft the snapshot just cleared.
    expect(order).toEqual(['draft', 'version']);
    expect(onAutosave).toHaveBeenCalledTimes(1);

    // And the debounce the flush consumed does not fire a second time.
    await new Promise((resolve) => setTimeout(resolve, 1300));
    expect(onAutosave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it('leaving the page flushes the last edit, keepalive so it survives teardown', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);
    const onSave = vi.fn(async () => {});
    const onAutosave = vi.fn<AutosaveFn>(async () => {});

    const { unmount } = render(
      <RequestTagDesigner
        request={request()}
        initialDoc={null}
        onSave={onSave}
        onAutosave={onAutosave}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    // An edit whose ~1s debounce has NOT fired - a sidebar click or the
    // browser's Back here is exactly how the edit used to be lost. The route
    // change is this component unmounting, so that is what the test does.
    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
    expect(onAutosave).not.toHaveBeenCalled();

    unmount();

    await waitFor(() => expect(onAutosave).toHaveBeenCalledTimes(1));
    expect(onAutosave.mock.calls[0][1]).toEqual({ keepalive: true });
  });

  it('with nothing pending, Save is a single request', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([lineTagData()]);
    const onSave = vi.fn(async () => {});
    const onAutosave = vi.fn<AutosaveFn>(async () => {});

    render(
      <RequestTagDesigner
        request={request()}
        initialDoc={null}
        onSave={onSave}
        onAutosave={onAutosave}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onAutosave).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// ?line= preselection (S10): the detail page's Lines tab opens the designer
// on a SPECIFIC line rather than whichever one this page would default to.
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - ?line= preselection', () => {
  const lineA = line({ id: 'line-a', product_id: 'prod-a', code: 'AAA-1' });
  const lineB = line({ id: 'line-b', product_id: 'prod-b', code: 'BBB-2' });

  it('defaults to the first line when there is no ?line= param', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
      lineTagData({ line_id: 'line-b', code: 'BBB-2' }),
    ]);

    renderDesigner(request({ lines: [lineA, lineB] }));

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    const group = canvasDocs[canvasDocs.length - 1].doc.layers.find(
      (l) => l.props.kind === 'group',
    );
    expect(group?.props).toMatchObject({ binding: { product_id: 'prod-a' } });
  });

  it('preselects the line named by ?line=, not the first one', async () => {
    searchParams = new URLSearchParams('line=line-b');
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
      lineTagData({ line_id: 'line-b', code: 'BBB-2' }),
    ]);

    renderDesigner(request({ lines: [lineA, lineB] }));

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    const group = canvasDocs[canvasDocs.length - 1].doc.layers.find(
      (l) => l.props.kind === 'group',
    );
    expect(group?.props).toMatchObject({ binding: { product_id: 'prod-b' } });
  });

  it('falls back to the first line when ?line= names a line not on this request', async () => {
    searchParams = new URLSearchParams('line=not-a-real-line');
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
      lineTagData({ line_id: 'line-b', code: 'BBB-2' }),
    ]);

    renderDesigner(request({ lines: [lineA, lineB] }));

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    const group = canvasDocs[canvasDocs.length - 1].doc.layers.find(
      (l) => l.props.kind === 'group',
    );
    expect(group?.props).toMatchObject({ binding: { product_id: 'prod-a' } });
  });

  it('strips ?line= from the URL once applied, so a refresh does not snap back to it', async () => {
    searchParams = new URLSearchParams('line=line-b');
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
      lineTagData({ line_id: 'line-b', code: 'BBB-2' }),
    ]);

    renderDesigner(request({ lines: [lineA, lineB] }));

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    expect(replace).toHaveBeenCalledWith(
      '/dealer-kit/price-tag-requests/req-1/design',
      { scroll: false },
    );
  });

  it('leaves the URL alone when there was no ?line= to strip', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
      lineTagData({ line_id: 'line-b', code: 'BBB-2' }),
    ]);

    renderDesigner(request({ lines: [lineA, lineB] }));

    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    expect(replace).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Tag size control (D24, S9, AC-S9-3)
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - tag size control (D24, AC-S9-3)', () => {
  const lineA = line({ id: 'line-a', product_id: 'prod-a', code: 'AAA-1' });
  const lineB = line({ id: 'line-b', product_id: 'prod-b', code: 'BBB-2' });

  it('editing W/H resizes the selected line\'s tag and every one of its copies', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
    ]);
    const onSave = vi.fn<(doc: TagSheetDoc) => Promise<void>>(async () => {});

    render(
      <RequestTagDesigner
        request={request({ lines: [lineA] })}
        initialDoc={null}
        onSave={onSave}
        onAutosave={vi.fn<AutosaveFn>(async () => {})}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));

    const wInput = screen.getByLabelText('Tag width (mm)');
    const hInput = screen.getByLabelText('Tag height (mm)');
    fireEvent.change(wInput, { target: { value: '95' } });
    fireEvent.blur(wInput);
    fireEvent.change(hInput, { target: { value: '44.5' } });
    fireEvent.blur(hInput);

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());

    const doc = onSave.mock.calls[onSave.mock.calls.length - 1][0];
    const tag = doc.sheets[0].tags.find((t) => t.request_tag_id === 'line-a');
    expect(tag).toMatchObject({ width_mm: 95, height_mm: 44.5 });
  });

  it('"Apply to all lines" resizes every line\'s tag, not only the selected one', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
      lineTagData({ line_id: 'line-b', code: 'BBB-2' }),
    ]);
    const onSave = vi.fn<(doc: TagSheetDoc) => Promise<void>>(async () => {});

    render(
      <RequestTagDesigner
        request={request({ lines: [lineA, lineB] })}
        initialDoc={null}
        onSave={onSave}
        onAutosave={vi.fn<AutosaveFn>(async () => {})}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));
    // Line B needs a tag of its own before "apply to all" has two lines to
    // reach - selecting it clones one the same way selecting line A already
    // did. D8: one tag, no parts - the line folds to ONE block, selected by
    // its code rather than the ordinal text a folded row no longer renders.
    fireEvent.click(screen.getByText('BBB-2').closest('button') as HTMLElement);
    await waitFor(() =>
      expect(canvasDocs[canvasDocs.length - 1].doc.width_mm).toBe(60),
    );

    const wInput = screen.getByLabelText('Tag width (mm)');
    const hInput = screen.getByLabelText('Tag height (mm)');
    fireEvent.change(wInput, { target: { value: '95' } });
    fireEvent.blur(wInput);
    fireEvent.change(hInput, { target: { value: '44.5' } });
    fireEvent.blur(hInput);
    fireEvent.click(screen.getByRole('button', { name: 'Apply to all lines' }));

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());

    const doc = onSave.mock.calls[onSave.mock.calls.length - 1][0];
    const allTags = doc.sheets.flatMap((s) => s.tags);
    expect(allTags.length).toBeGreaterThan(0);
    for (const tag of allTags) {
      expect(tag).toMatchObject({ width_mm: 95, height_mm: 44.5 });
    }
  });

  // ---------------------------------------------------------------------
  // S9 review B1: typing must commit on blur, never per keystroke - the
  // control used to call onResize on every change, which changed a doc key
  // the editor was mounted on and remounted it (and this very input) mid-
  // keystroke, dropping characters ("95" -> "9").
  // ---------------------------------------------------------------------

  it('typing into H commits on blur, never remounts the editor, and never loses focus (S9 review B1)', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
    ]);
    const onSave = vi.fn<(doc: TagSheetDoc) => Promise<void>>(async () => {});

    render(
      <RequestTagDesigner
        request={request({ lines: [lineA] })}
        initialDoc={null}
        onSave={onSave}
        onAutosave={vi.fn<AutosaveFn>(async () => {})}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));

    const mountsBeforeTyping = canvasMountCount;
    const hInput = screen.getByLabelText('Tag height (mm)');

    await userEvent.clear(hInput);
    await userEvent.type(hInput, '44.5');

    // The SAME DOM node, still focused, still mid-edit - a remount partway
    // through typing (keying the editor on width/height, B1's actual bug)
    // would have swapped this element out from under the keystrokes.
    expect(screen.getByLabelText('Tag height (mm)')).toBe(hInput);
    expect(document.activeElement).toBe(hInput);
    expect(canvasMountCount).toBe(mountsBeforeTyping);
    expect(hInput).toHaveValue(44.5);

    fireEvent.blur(hInput);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());

    const doc = onSave.mock.calls[onSave.mock.calls.length - 1][0];
    const tag = doc.sheets[0].tags.find((t) => t.request_tag_id === 'line-a');
    expect(tag?.height_mm).toBe(44.5);
    // Committing the resize (a live prop update, not a remount) still must
    // not have unmounted/remounted the editor.
    expect(canvasMountCount).toBe(mountsBeforeTyping);
  });

  // ---------------------------------------------------------------------
  // S9 review B2: "Apply to all lines" must also reach a line that has not
  // been opened yet - resizeAllTags alone only touches already-cloned tags.
  // ---------------------------------------------------------------------

  it('applying a size to all lines also sets the default for a line opened LATER (S9 review B2)', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    const lineC = line({ id: 'line-c', product_id: 'prod-c', code: 'CCC-3' });
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
      lineTagData({ line_id: 'line-b', code: 'BBB-2' }),
      lineTagData({ line_id: 'line-c', code: 'CCC-3' }),
    ]);
    const onSave = vi.fn<(doc: TagSheetDoc) => Promise<void>>(async () => {});

    render(
      <RequestTagDesigner
        request={request({ lines: [lineA, lineB, lineC] })}
        initialDoc={null}
        onSave={onSave}
        onAutosave={vi.fn<AutosaveFn>(async () => {})}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));
    // Only line A has opened so far - B and C are untouched.

    const wInput = screen.getByLabelText('Tag width (mm)');
    const hInput = screen.getByLabelText('Tag height (mm)');
    fireEvent.change(wInput, { target: { value: '95' } });
    fireEvent.blur(wInput);
    fireEvent.change(hInput, { target: { value: '44.5' } });
    fireEvent.blur(hInput);
    fireEvent.click(screen.getByRole('button', { name: 'Apply to all lines' }));

    // Open line C for the FIRST time - it must clone at the request's
    // default size (95x44.5), not its template's own print_size (60x40).
    // D8: one tag, no parts - the line folds to ONE block, selected by its
    // code rather than the ordinal text a folded row no longer renders.
    fireEvent.click(screen.getByText('CCC-3').closest('button') as HTMLElement);
    await waitFor(() => {
      const drawn = canvasDocs[canvasDocs.length - 1].doc;
      expect(drawn.width_mm).toBe(95);
      expect(drawn.height_mm).toBe(44.5);
    });
  });

  // ---------------------------------------------------------------------
  // S9 review S3: a size has to fit the CURRENT imposition sheet - refused
  // inline (no toast), not silently redrawn to something else.
  // ---------------------------------------------------------------------

  it('refuses a size that does not fit the sheet, with an inline reason and no toast (400mm refused)', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
    ]);
    const onSave = vi.fn<(doc: TagSheetDoc) => Promise<void>>(async () => {});

    render(
      <RequestTagDesigner
        request={request({ lines: [lineA] })}
        initialDoc={null}
        onSave={onSave}
        onAutosave={vi.fn<AutosaveFn>(async () => {})}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));

    const wInput = screen.getByLabelText('Tag width (mm)');
    fireEvent.change(wInput, { target: { value: '400' } });
    fireEvent.blur(wInput);

    expect(await screen.findByText(/largest that fits this sheet/i)).toBeInTheDocument();
    expect(toast.error).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const doc = onSave.mock.calls[onSave.mock.calls.length - 1][0];
    const tag = doc.sheets[0].tags.find((t) => t.request_tag_id === 'line-a');
    // Refused - the tag keeps its ORIGINAL width (the template's print_size).
    expect(tag?.width_mm).toBe(60);
  });
});

// ---------------------------------------------------------------------------
// TagSizeControl - Template sizes / Saved sizes / Custom grouping (S4, S5,
// AC-S4-3/S4-4). `TagSizeControl` is a private component of this file (not
// its own module), so this is that promised coverage.
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - tag size dropdown grouping (S5, AC-S4-3/S4-4)', () => {
  const lineA = line({ id: 'line-a', product_id: 'prod-a', code: 'AAA-1' });

  function savedSize(overrides: Partial<TagSizeRecord> = {}): TagSizeRecord {
    return {
      id: 'size-1',
      name: 'My favourite',
      width_mm: 80,
      height_mm: 50,
      created_by: null,
      created_by_name: null,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
      ...overrides,
    };
  }

  beforeEach(() => {
    mockUseTagSizesQuery.mockReturnValue({ data: [] });
    mockUseDeleteTagSizePreset.mockReturnValue({ run: mockDeleteRun, targetId: null, isPending: false });
    mockDeleteRun.mockClear();
  });

  async function mountWithSavedSizes(saved: TagSizeRecord[]) {
    mockUseTagSizesQuery.mockReturnValue({ data: saved });
    mockListTemplates.mockResolvedValue([realTemplate()]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1' }),
    ]);

    render(
      <RequestTagDesigner
        request={request({ lines: [lineA] })}
        initialDoc={null}
        onSave={vi.fn(async () => {})}
        onAutosave={vi.fn<AutosaveFn>(async () => {})}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());
    // D9 (PLAN-price-tag-ai-extract-resolver.md): the Tag Size panel is
    // collapsed by default - open it before any test here reads its select
    // or inputs, the same way `TagSizeControl.test.tsx` does.
    fireEvent.click(screen.getByRole('button', { name: /Tag Size/ }));
  }

  it('groups the dropdown into Template sizes and Saved sizes (AC-S4-4)', async () => {
    await mountWithSavedSizes([savedSize()]);

    fireEvent.click(screen.getByRole('combobox'));

    expect(await screen.findByText('Template sizes')).toBeInTheDocument();
    expect(screen.getByText('Saved sizes')).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: /Ala carte \(60 x 40 mm\)/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: /My favourite \(80 x 50 mm\)/ }),
    ).toBeInTheDocument();
  });

  it('hides the Saved sizes group entirely when there are none yet', async () => {
    await mountWithSavedSizes([]);

    fireEvent.click(screen.getByRole('combobox'));

    expect(await screen.findByText('Template sizes')).toBeInTheDocument();
    expect(screen.queryByText('Saved sizes')).not.toBeInTheDocument();
  });

  it('a saved-size row carries a delete x; a template-size row does not (AC-S4-4)', async () => {
    await mountWithSavedSizes([savedSize()]);

    fireEvent.click(screen.getByRole('combobox'));
    await screen.findByText('Saved sizes');

    expect(screen.getByLabelText('Delete saved size My favourite')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Delete saved size Ala carte/)).not.toBeInTheDocument();
  });

  it('clicking a saved size\'s x runs the deferred delete, not the select (does not resize the tag)', async () => {
    await mountWithSavedSizes([savedSize()]);

    fireEvent.click(screen.getByRole('combobox'));
    fireEvent.click(await screen.findByLabelText('Delete saved size My favourite'));

    expect(mockDeleteRun).toHaveBeenCalledWith({ id: 'size-1', subject: 'My favourite' });
    // The tag's own width is untouched - the template's starting 60mm, not
    // the saved size's 80mm the click would have applied had it fallen
    // through to the option's own onClick.
    expect(screen.getByLabelText('Tag width (mm)')).toHaveValue(60);
  });

  it('dims/disables the saved-size x while ITS OWN delete counts down (N4)', async () => {
    mockUseDeleteTagSizePreset.mockReturnValue({
      run: mockDeleteRun,
      targetId: 'size-1',
      isPending: true,
    });
    await mountWithSavedSizes([savedSize()]);

    fireEvent.click(screen.getByRole('combobox'));
    const deleteButton = await screen.findByLabelText('Delete saved size My favourite');

    expect(deleteButton).toBeDisabled();
    expect(deleteButton.className).toMatch(/opacity-50/);
  });

  it('leaves an UNRELATED saved-size x untouched while a different one counts down (N4)', async () => {
    mockUseDeleteTagSizePreset.mockReturnValue({
      run: mockDeleteRun,
      targetId: 'size-other',
      isPending: true,
    });
    await mountWithSavedSizes([savedSize()]);

    fireEvent.click(screen.getByRole('combobox'));
    const deleteButton = await screen.findByLabelText('Delete saved size My favourite');

    expect(deleteButton).not.toBeDisabled();
    expect(deleteButton.className).not.toMatch(/opacity-50/);
  });

  it('shows "Save as size" once the tag has been resized off every known size (Custom, AC-S4-3)', async () => {
    await mountWithSavedSizes([]);

    expect(screen.queryByRole('button', { name: 'Save as size' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Tag width (mm)'), { target: { value: '77' } });
    fireEvent.blur(screen.getByLabelText('Tag width (mm)'));

    expect(await screen.findByRole('button', { name: 'Save as size' })).toBeInTheDocument();
  });

  it('hides "Save as size" once the tag matches a saved size exactly, not only a template size', async () => {
    await mountWithSavedSizes([savedSize({ width_mm: 60, height_mm: 40 })]);

    // The template's own print_size (60x40) already matches - Custom never
    // shows, so there is nothing to save.
    expect(screen.queryByRole('button', { name: 'Save as size' })).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC-S7-16 (captain's ruling, phase 3 review, designer-level shape): a live
// browser finding after `lib/dealer-kit/request-tags.test.ts`'s own
// `autoArrange` proved green in isolation - two DIFFERENT request tags off
// ONE line (D6: an open group resolved into its own tag per candidate at
// submit) must both reach the Arrange doc. `autoArrange` itself places
// whatever `ArrangeItem[]` it is handed correctly; the tag goes missing one
// layer up, in `RequestTagDesigner`'s own `arrangeItems` - built by filtering
// `tagRefs` through the `tags` state record (`tag ? [...] : []`), and that
// record is filled ONE TAG AT A TIME by the auto-clone effect keyed on
// `selectedRequestTagId`, which starts (and, absent a click into the second
// tag, stays) on only the FIRST request tag overall. The second tag of a
// split line that nobody has opened on the canvas yet never gets a `tags[id]`
// entry, so it never reaches `arrangeItems`, `autoArrange`, or the saved doc.
// ---------------------------------------------------------------------------

describe('RequestTagDesigner - both tags of a split line reach Arrange (AC-S7-16)', () => {
  it('two tags on one line: the Arrange toolbar reads "1 sheet / 2 tags", not 1', async () => {
    mockListTemplates.mockResolvedValue([]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ tag_id: 'tag-a', line_id: 'line-1', code: 'SRT-1' }),
      lineTagData({ tag_id: 'tag-b', line_id: 'line-1', code: 'SRT-1' }),
    ]);

    renderDesigner(
      request({
        lines: [
          line({
            id: 'line-1',
            code: 'SRT-1',
            tags: [
              requestTag('line-1', 1, { id: 'tag-a', label: '1a' }),
              requestTag('line-1', 1, { id: 'tag-b', label: '1b' }),
            ],
          }),
        ],
      }),
    );

    // Only the first tag (whichever the auto-select effect lands on) is ever
    // opened on the canvas - exactly the everyday path: a designer opens the
    // line, sees ITS tag, and switches straight to Arrange without ever
    // clicking into the second candidate's own row.
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Arrange' }));

    await waitFor(() => expect(screen.getByTestId('arrange-view')).toBeInTheDocument());
    expect(screen.getByText('1 sheet / 2 tags')).toBeInTheDocument();
  });
});
