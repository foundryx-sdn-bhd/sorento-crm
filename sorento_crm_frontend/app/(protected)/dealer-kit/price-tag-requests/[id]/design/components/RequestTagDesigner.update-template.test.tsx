/**
 * "Update <template>" from a request tag (S6, PLAN D6, AC-S6-1/2/3/4/5/6).
 *
 * A `PlacedTag` carries the `template_id` it was cloned from. The toolbar's
 * Template dropdown offers "Update <name>" (published templates only) next
 * to "Save as new template"; confirming runs, in order:
 *   1. PUT the template's draft (the selected tag's current layers + size)
 *   2. POST publish, noted "Updated from <doc_number>"
 *   3. IF the sibling checkbox is on, spread the same design onto every
 *      OTHER line in this request that is currently on the same template
 *      (`applyDesignToSiblings`, pinned in isolation in
 *      `request-tags.test.ts`) - this file is the WIRING: the two service
 *      calls fire in that order, and the sibling apply happens ONLY when
 *      the checkbox stays on.
 *
 * Same stand-in idiom as `RequestTagDesigner.test.tsx` (Konva needs a real
 * canvas, unavailable in jsdom).
 */

import React from 'react';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
// The designer mounts a react-query mutation (the deferred tag Remove), so a
// bare `render` throws "No QueryClient set" before the component exists.
import { renderWithQueryClient as render } from './testQueryClient';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ToolbarButton,
  ToolbarDropdownButton,
  type ToolbarTrailingAction,
} from '@/app/(protected)/dealer-kit/tag-templates/components/CanvasToolbar';

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
import { toast } from '@/lib/toast';
const mockToastSuccess = vi.mocked(toast.success);

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/dealer-kit/price-tag-requests/req-1/design',
  useSearchParams: () => new URLSearchParams(),
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

type TagSheetDocCapture = { lineId: string; doc: TagTemplateDoc };
const canvasDocs: TagSheetDocCapture[] = [];
let currentLineId = '';

/** Where the browser repro dragged the barcode to before hitting Update. */
const MOVED_X_MM = 24.76;
const MOVED_Y_MM = 20.71;

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
    // S2 review turned `toolbarTrailing` into a data array (real arrow-key
    // nav + the menu closing itself once an item is picked) - the stand-in
    // renders it through the SAME real `ToolbarButton`/`ToolbarDropdownButton`
    // `CanvasToolbar` itself uses, rather than reinventing it.
    toolbarTrailing?: ToolbarTrailingAction[];
  }) => {
    canvasDocs.push({ lineId: currentLineId, doc });
    const [layers, setLayers] = React.useState<TagLayer[]>(doc.layers);
    React.useEffect(() => {
      onLayersChange?.(doc.layers);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div data-testid="canvas-editor">
        canvas: {layers.length} layers
        {/* R2 (#726): the canvas's own INTERNAL state (never reset by a
            new `doc` prop unless the component actually remounts) is what
            the user sees - a test checking "did the move survive" has to
            read THIS, not the `doc` prop RequestTagDesigner seeds it with,
            which is frozen at whatever it was on mount/remount and stays
            stale by design once the canvas takes over. */}
        first layer x={layers[0]?.x_mm} y={layers[0]?.y_mm}
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
        {/* B1: types a value into EVERY current layer's text_override,
            bound or not - the PUT map is what is supposed to tell them
            apart, not this button. */}
        <button
          type="button"
          onClick={() => {
            const next = layers.map((l) => ({ ...l, text_override: 'typed value' }));
            setLayers(next);
            onLayersChange?.(next);
          }}
        >
          Type into every layer
        </button>
        {/* R2 (#726): moves the FIRST layer's own x/y, the same shape a real
            drag produces on the canvas - onLayersChange fires immediately
            (no debounce of its own), the same as it does for the buttons
            above. The numbers are the ones measured in the browser repro
            (barcode dragged to 24.76 / 20.71). */}
        <button
          type="button"
          onClick={() => {
            const next = layers.map((l, i) =>
              i === 0 ? { ...l, x_mm: MOVED_X_MM, y_mm: MOVED_Y_MM } : l,
            );
            setLayers(next);
            onLayersChange?.(next);
          }}
        >
          Move first layer
        </button>
      </div>
    );
  },
}));

vi.mock('./ArrangeSheetView', () => ({
  ArrangeSheetView: () => <div data-testid="arrange-view">arrange open</div>,
}));

vi.mock('../../../../services/tagTemplateService', () => ({
  listPublishedTemplates: vi.fn(),
  createTemplateFromTag: vi.fn(),
  updateTemplate: vi.fn(),
  publishTemplate: vi.fn(),
}));
vi.mock('../../../../services/priceTagRequestService', () => ({
  // One row per TAG since S3 (D3), auto-split at save so no Split / Pick one
  // action exists any more. The two beside it are what the post-save
  // reload calls.
  resolveRequestTags: vi.fn(),
  getPriceTagRequest: vi.fn(),
  updateRequestTag: vi.fn(),
  transitionPriceTagRequest: vi.fn(),
  exportTagSheet: vi.fn(),
}));
vi.mock('../../../../tag-sizes/hooks/useTagSizes', () => ({
  useTagSizesQuery: () => ({ data: [] as unknown[] }),
  useDeleteTagSizePreset: () => ({ run: vi.fn(), targetId: null, isPending: false }),
  useCreateTagSize: () => ({ mutateAsync: vi.fn(async () => ({})), isPending: false }),
}));

import {
  listPublishedTemplates,
  publishTemplate,
  updateTemplate,
} from '../../../../services/tagTemplateService';
import { resolveRequestTags } from '../../../../services/priceTagRequestService';
import { RequestTagDesigner } from './RequestTagDesigner';
import type {
  PriceTagRequestDetail,
  PriceTagRequestLine,
  PriceTagRequestTag,
} from '../../../../services/priceTagRequestService';
import type {
  LineTagData,
  TagLayer,
  TagSheetDoc,
  TagTemplate,
  TagTemplateDoc,
} from '@/lib/dealer-kit/tag-template-types';

const mockListTemplates = vi.mocked(listPublishedTemplates);
const mockResolveRequestTags = vi.mocked(resolveRequestTags);
const mockUpdateTemplate = vi.mocked(updateTemplate);
const mockPublishTemplate = vi.mocked(publishTemplate);

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
    id: 'line-a',
    line_type: 'product' as const,
    product_id: 'prod-a' as string | null,
    product_set_id: null as string | null,
    name: 'Kitchen Sink',
    code: 'AAA-1',
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
    line_count: 2,
    created_at: '2026-09-01T00:00:00Z',
    assigned_to_id: 'user-1',
    assigned_to_name: 'Jayson',
    contact_name: 'CONTACT AI',
    contact_id: 'contact-1',
    lines: [
      line({ id: 'line-a', product_id: 'prod-a', code: 'AAA-1', name: 'Kitchen Sink' }),
      line({ id: 'line-b', product_id: 'prod-b', code: 'BBB-2', name: 'Basin' }),
    ],
    ...overrides,
  };
}

function lineTagData(overrides: Partial<LineTagData> = {}): LineTagData {
  // One row per TAG since S3. The everyday request has one tag per line and the
  // tag's id is the line's, so a row named by `line_id` keys on the same id it
  // always did.
  const lineId = overrides.line_id ?? 'line-a';
  return {
    tag_id: lineId,
    tag_label: '1a',
    open_groups: [],
    parts: [],
    line_id: 'line-a',
    code: 'AAA-1',
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
    name: 'DIY Tag',
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
      width_mm: 60,
      height_mm: 40,
    },
    print_size: { width_mm: 60, height_mm: 40 },
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    published_version_id: 'v1',
    published_version_no: 1,
    ...overrides,
  } as TagTemplate;
}

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

beforeEach(() => {
  canvasDocs.length = 0;
  currentLineId = '';
  vi.clearAllMocks();
  mockUpdateTemplate.mockResolvedValue(realTemplate() as never);
  mockPublishTemplate.mockResolvedValue(
    realTemplate({ published_version_no: 2 }) as never,
  );
});

/**
 * Both lines resolve, and both clone from the SAME published template - the
 * natural way two lines end up "siblings" on the same template_id.
 *
 * `tags[lineId]` only exists once a line has been the SELECTED one at least
 * once (the auto-clone effect fires on selection, not up front) - so a
 * "sibling" line the test never visited would read as no tag at all, and
 * `updateSiblingCount` would see 0. This visits line B once, then returns to
 * line A, so both tags exist before the Template dropdown ever opens.
 */
async function mountBothOnSameTemplate() {
  mockListTemplates.mockResolvedValue([realTemplate()]);
  mockResolveRequestTags.mockResolvedValue([
    lineTagData({ line_id: 'line-a', code: 'AAA-1', name: 'Kitchen Sink' }),
    lineTagData({ line_id: 'line-b', code: 'BBB-2', name: 'Basin' }),
  ]);

  const onSave = vi.fn<(doc: TagSheetDoc) => Promise<void>>(async () => {});
  const onAutosave = vi.fn<(doc: TagSheetDoc) => Promise<void>>(async () => {});
  render(
    <RequestTagDesigner
      request={request()}
      initialDoc={null}
      onSave={onSave}
      onAutosave={onAutosave}
    />,
  );
  await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

  // D8 (PLAN-price-tag-ai-extract-resolver.md): one tag, no parts - each
  // line folds to ONE block, selected by its code rather than the ordinal
  // text a folded row no longer renders.
  fireEvent.click(screen.getByText('BBB-2').closest('button') as HTMLElement);
  await waitFor(() => expect(screen.getByText(/canvas: 1 layers/)).toBeInTheDocument());
  fireEvent.click(screen.getByText('AAA-1').closest('button') as HTMLElement);
  await waitFor(() => expect(screen.getByText(/canvas: 1 layers/)).toBeInTheDocument());
  return { onSave, onAutosave };
}

/** Radix opens its DropdownMenu on pointerdown, not click. */
function openTemplateMenu() {
  const trailing = within(screen.getByTestId('toolbar-trailing'));
  const trigger = trailing.getByRole('button', { name: 'Template' });
  fireEvent.pointerDown(trigger, new MouseEvent('pointerdown', { bubbles: true, button: 0 }));
}

function openUpdateDialog() {
  openTemplateMenu();
  fireEvent.click(screen.getByRole('menuitem', { name: 'Update "DIY Tag"' }));
}

describe('RequestTagDesigner - Update template (S6, AC-S6-1/2/3)', () => {
  it('the Template dropdown offers "Update <name>" and "Save as new template"', async () => {
    await mountBothOnSameTemplate();

    openTemplateMenu();

    expect(screen.getByRole('menuitem', { name: 'Update "DIY Tag"' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Save as new template' })).toBeInTheDocument();
  });

  it('the dialog names the template, the next version and the sibling count (AC-S6-2)', async () => {
    await mountBothOnSameTemplate();

    openUpdateDialog();

    expect(screen.getByText(/Publish this design as v2 of DIY Tag/)).toBeInTheDocument();
    expect(
      screen.getByText(/Also apply to the 1 other line in this request that use DIY Tag/),
    ).toBeInTheDocument();
  });

  it('confirms PUT then POST publish, in that order, noted with the doc number (AC-S6-3)', async () => {
    await mountBothOnSameTemplate();

    openUpdateDialog();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mockPublishTemplate).toHaveBeenCalled());
    expect(mockUpdateTemplate).toHaveBeenCalledWith(
      'tpl-1',
      expect.objectContaining({ layers: expect.any(Array), width_mm: 60, height_mm: 40 }),
    );
    expect(mockPublishTemplate).toHaveBeenCalledWith('tpl-1', 'Updated from PT-000001');
    expect(mockUpdateTemplate.mock.invocationCallOrder[0]).toBeLessThan(
      mockPublishTemplate.mock.invocationCallOrder[0],
    );
  });

  it('Cancel changes nothing (AC-S6-6)', async () => {
    await mountBothOnSameTemplate();

    openUpdateDialog();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));

    expect(mockUpdateTemplate).not.toHaveBeenCalled();
    expect(mockPublishTemplate).not.toHaveBeenCalled();
    // Radix's own close animation unmounts the dialog a tick after the
    // click, not synchronously with it.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});

describe('RequestTagDesigner - Update template strips bound text_override (B1)', () => {
  it('a bound layer\'s text_override is null in the PUT payload, an unbound layer keeps its text', async () => {
    mockListTemplates.mockResolvedValue([realTemplate()]);
    mockResolveRequestTags.mockResolvedValue([
      lineTagData({ line_id: 'line-a', code: 'AAA-1', name: 'Kitchen Sink' }),
    ]);

    render(
      <RequestTagDesigner
        request={request({ lines: [line({ id: 'line-a' })], line_count: 1 })}
        initialDoc={null}
        onSave={vi.fn(async () => {})}
        onAutosave={vi.fn(async () => {})}
      />,
    );
    await waitFor(() => expect(screen.getByTestId('canvas-editor')).toBeInTheDocument());

    // The mocked canvas starts on the template's own single bound ("code")
    // layer; add an unbound one, then type a value into EVERY layer's
    // text_override - a real editor session typing a line-specific price or
    // name would leave the SAME shape (both bound and unbound layers
    // carrying a value), so this is what the PUT map has to tell apart.
    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
    expect(screen.getByText(/canvas: 2 layers/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Type into every layer' }));

    openUpdateDialog();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mockUpdateTemplate).toHaveBeenCalled());
    const [, payload] = mockUpdateTemplate.mock.calls[0];
    const bound = payload.layers.find((l: TagLayer) => l.slot_binding === 'code');
    const unbound = payload.layers.find((l: TagLayer) => l.id === 'added-1');

    expect(bound?.text_override).toBeNull();
    expect(unbound?.text_override).toBe('typed value');
  });
});

describe('RequestTagDesigner - Update template reads the live layers, never re-applies to the source line (R2, #726)', () => {
  it('a moved layer reaches the PUT payload, and the source line is neither reverted nor duplicated after publish', async () => {
    await mountBothOnSameTemplate();

    // Move the layer on line A (the source, currently selected) - the same
    // shape a real canvas drag produces via onLayersChange.
    fireEvent.click(screen.getByRole('button', { name: 'Move first layer' }));

    openUpdateDialog();
    // Checkbox defaults ON - there IS a sibling (line-b), so
    // applyDesignToSiblings runs too (S6, AC-S6-4); the source line must
    // stay untouched by it (r2, suspect b).
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mockUpdateTemplate).toHaveBeenCalled());
    const [, payload] = mockUpdateTemplate.mock.calls[0];
    expect(payload.layers[0].x_mm).toBe(MOVED_X_MM);
    expect(payload.layers[0].y_mm).toBe(MOVED_Y_MM);

    await waitFor(() => expect(mockPublishTemplate).toHaveBeenCalled());
    // Publish is awaited before the dialog closes, so waiting for the
    // dialog to unmount is the same "everything since has settled" signal
    // `RequestTagDesignerShell.test.tsx` and this file's own Cancel test
    // already use.
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // Line A - still selected throughout - shows the SAME layer count
    // (no duplicate) at the MOVED position (not reverted). Reads the
    // canvas's own rendered state, not `canvasDocs`/the `doc` prop
    // RequestTagDesigner seeds it with - that seed is frozen at mount and
    // never reflects a live edit, by design, so asserting against it would
    // catch a REMOUNT but say nothing about whether an ALREADY-MOUNTED
    // canvas's own edit survived, which is what actually happened on screen
    // in the browser repro.
    expect(screen.getByText(/canvas: 1 layers/)).toBeInTheDocument();
    expect(screen.getByText(/first layer x=24\.76 y=20\.71/)).toBeInTheDocument();
  });
});

/**
 * The wire trace measured in the browser, twice (R2, #726):
 *
 *   1. PUT .../design/draft   barcode at 24.76 / 20.71   (the autosave BEFORE
 *      Update - correct)
 *   2. PUT tag-templates/{id} layers at 24.76 / 20.71    (correct)
 *   3. POST tag-templates/{id}/publish -> v4             (correct)
 *   4. GET tag-templates?published=1                     (the refresh)
 *   5. PUT .../design/draft   barcode BACK at 8.87 / 5   (stale - and this is
 *      what the server keeps, so the Inspector reads 8.87 / 5 afterwards)
 *
 * The step the older guard above was missing is 4 taking any TIME. With
 * `mockResolvedValue` the refetch settles inside the same microtask drain, so
 * React never renders the "Loading templates..." gate and the canvas is never
 * torn down. A real network round trip does render it: the editor unmounts,
 * comes back, and re-seeds its layers from the `doc` prop, which is step 5's
 * payload. So the refetch here resolves on the test's own schedule.
 */
describe('RequestTagDesigner - a templates refresh never reverts the design (R2, #726)', () => {
  it('the canvas is not torn down by the refresh, and keeps the moved layer', async () => {
    const { onSave } = await mountBothOnSameTemplate();
    const canvasBefore = screen.getByTestId('canvas-editor');

    // The refresh `handleUpdateTemplate` fires after publish, held open until
    // this test releases it.
    let releaseRefresh: (rows: TagTemplate[]) => void = () => {};
    mockListTemplates.mockImplementationOnce(
      () =>
        new Promise<TagTemplate[]>((resolve) => {
          releaseRefresh = resolve;
        }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Move first layer' }));
    expect(screen.getByText(/first layer x=24\.76 y=20\.71/)).toBeInTheDocument();

    openUpdateDialog();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Publish' }));
    await waitFor(() => expect(mockPublishTemplate).toHaveBeenCalled());
    await waitFor(() => expect(mockListTemplates).toHaveBeenCalledTimes(2));

    // The published list comes back a version later, exactly as it does after
    // a real publish.
    await act(async () => {
      releaseRefresh([realTemplate({ published_version_no: 2 })]);
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    // The barcode is where the user left it, not back at its template
    // position. This is the assertion that went red on the browser repro.
    expect(screen.getByText(/canvas: 1 layers/)).toBeInTheDocument();
    expect(screen.getByText(/first layer x=24\.76 y=20\.71/)).toBeInTheDocument();
    // Same DOM node: a refresh mid-session is a data refresh, not a remount.
    // A remount is what loses the selection, the zoom and the undo history,
    // and it is where the stale re-seed came from.
    expect(screen.getByTestId('canvas-editor')).toBe(canvasBefore);

    // And what a save would send is the moved layer too - the autosave that
    // fired at step 5 reads the same `tags` state this does (`flush` +
    // `onSave` is the synchronous way to read it without waiting out the
    // autosave debounce).
    fireEvent.click(
      within(screen.getByTestId('toolbar-trailing')).getByRole('button', { name: 'Save' }),
    );
    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const saved = onSave.mock.calls[0][0];
    const savedTag = saved.sheets
      .flatMap((sheet) => sheet.tags)
      .find((tag) => tag.request_tag_id === 'line-a');
    expect(savedTag?.layers[0].x_mm).toBe(MOVED_X_MM);
    expect(savedTag?.layers[0].y_mm).toBe(MOVED_Y_MM);
  });

  it('a refresh that FAILS keeps the canvas and the list already on screen', async () => {
    await mountBothOnSameTemplate();
    const canvasBefore = screen.getByTestId('canvas-editor');

    mockListTemplates.mockRejectedValueOnce(new Error('network'));

    fireEvent.click(screen.getByRole('button', { name: 'Move first layer' }));
    openUpdateDialog();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mockListTemplates).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    expect(screen.getByTestId('canvas-editor')).toBe(canvasBefore);
    expect(screen.getByText(/first layer x=24\.76 y=20\.71/)).toBeInTheDocument();
    // The template the tag came from is still resolvable, so the Template
    // menu still offers Update rather than collapsing to "Save as new".
    openTemplateMenu();
    expect(screen.getByRole('menuitem', { name: 'Update "DIY Tag"' })).toBeInTheDocument();
  });
});

describe('RequestTagDesigner - Update template sibling checkbox (S6, AC-S6-4/5)', () => {
  it('with the checkbox ON (default), every other line on this template gets the new design', async () => {
    await mountBothOnSameTemplate();

    // A deliberate edit on line A - what "the same design" actually means.
    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
    expect(screen.getByText(/canvas: 2 layers/)).toBeInTheDocument();

    openUpdateDialog();
    // Checkbox defaults ON - confirm without touching it.
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Publish' }));

    await waitFor(() =>
      expect(mockToastSuccess).toHaveBeenCalledWith(
        expect.stringContaining('applied to 1 other tag'),
        expect.anything(),
      ),
    );

    // Switch to the sibling line - its tag now carries the 2-layer design,
    // not the template's original 1-layer clone.
    fireEvent.click(screen.getByText('BBB-2').closest('button') as HTMLElement);
    await waitFor(() =>
      expect(screen.getByText(/canvas: 2 layers/)).toBeInTheDocument(),
    );
  });

  it('with the checkbox OFF, the sibling line is left exactly as it was (AC-S6-5)', async () => {
    await mountBothOnSameTemplate();

    fireEvent.click(screen.getByRole('button', { name: 'Add layer' }));
    expect(screen.getByText(/canvas: 2 layers/)).toBeInTheDocument();

    openUpdateDialog();
    const dialog = within(screen.getByRole('dialog'));
    fireEvent.click(dialog.getByRole('checkbox'));
    fireEvent.click(dialog.getByRole('button', { name: 'Publish' }));

    await waitFor(() => expect(mockPublishTemplate).toHaveBeenCalled());
    // No "applied to" phrasing when the checkbox was off.
    expect(mockToastSuccess).toHaveBeenCalledWith(expect.stringContaining('Updated "DIY Tag"'));
    expect(mockToastSuccess).not.toHaveBeenCalledWith(
      expect.stringContaining('applied to'),
    );

    // The sibling line's own tag is UNCHANGED - still its own 1-layer clone.
    fireEvent.click(screen.getByText('BBB-2').closest('button') as HTMLElement);
    await waitFor(() =>
      expect(screen.getByText(/canvas: 1 layers/)).toBeInTheDocument(),
    );
  });
});
