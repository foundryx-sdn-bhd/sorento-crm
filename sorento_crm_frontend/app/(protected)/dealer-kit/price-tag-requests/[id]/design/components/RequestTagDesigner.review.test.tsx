/**
 * The designer's two r9 overlays: change requests and the data gate
 * (AC-S2-6, AC-S5-3, AC-S5-4).
 *
 * This file REPLACES `RequestTagDesigner.refresh.test.tsx`. r4's S2 added a
 * silent refresh-on-focus that re-resolved every line whenever the tab regained
 * focus, which is the exact behaviour D18 retires: master data must not walk
 * onto a design behind a person's back. What stands in its place is a red dot
 * on the LINES rail and a dialog that asks, so the assertion "focusing the
 * window changes nothing" belongs here beside the thing that replaced it.
 *
 * The canvas is stood in for - Konva needs a browser - so what is asserted is
 * RequestTagDesigner's own wiring: which pins it hands the canvas, what the
 * Comments toggle does to them, the rail's badge and dot, and that a decision
 * reaches the service.
 */
import React from 'react';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
// The designer mounts a react-query mutation (the deferred tag Remove), so a
// bare `render` throws "No QueryClient set" before the component exists.
import { renderWithQueryClient as render } from './testQueryClient';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  ToolbarButton,
  ToolbarDropdownButton,
  type ToolbarTrailingAction,
} from '@/app/(protected)/dealer-kit/tag-templates/components/CanvasToolbar';
import type { CanvasReviewPin } from '@/lib/dealer-kit/review-comments';
import type {
  LineTagData,
  TagLayer,
  TagTemplateDoc,
} from '@/lib/dealer-kit/tag-template-types';

vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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

/** Every `reviewPins` array the canvas was handed, in order. */
const pinRenders: CanvasReviewPin[][] = [];

vi.mock('@/app/(protected)/dealer-kit/tag-templates/components/TagCanvasEditor', () => ({
  TagCanvasEditor: ({
    doc,
    onLayersChange,
    leftRail,
    toolbarTrailing,
    reviewPins,
    onReviewPinResolve,
  }: {
    doc: TagTemplateDoc;
    onLayersChange?: (layers: TagLayer[]) => void;
    leftRail?: React.ReactNode;
    toolbarTrailing?: ToolbarTrailingAction[];
    reviewPins?: CanvasReviewPin[];
    onReviewPinResolve?: (pinId: string, resolved: boolean) => void | Promise<void>;
  }) => {
    pinRenders.push(reviewPins ?? []);
    React.useEffect(() => {
      onLayersChange?.(doc.layers);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return (
      <div data-testid="canvas-editor">
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
        <ul data-testid="canvas-pins">
          {(reviewPins ?? []).map((pin) => (
            <li key={pin.id} data-testid={`canvas-pin-${pin.number}`}>
              {pin.body} / {pin.caption}
              {/* AC-S9-2/S9-3: the mock's stand-in for the real popover's
                  Done button (RequestTagDesigner.tags.test.tsx and
                  TagCanvasEditor.test.tsx pin the popover UI itself) - what
                  belongs HERE is that the designer wires the callback and
                  reacts to it correctly. */}
              {onReviewPinResolve && !pin.resolved && (
                <button
                  type="button"
                  onClick={() => void onReviewPinResolve(pin.id, true)}
                >
                  Done
                </button>
              )}
            </li>
          ))}
        </ul>
      </div>
    );
  },
}));

vi.mock('./ArrangeSheetView', () => ({
  ArrangeSheetView: () => <div data-testid="arrange-view">arrange open</div>,
}));

vi.mock('../../../../services/tagTemplateService', () => ({
  listPublishedTemplates: vi.fn(async () => []),
  createTemplateFromTag: vi.fn(),
  updateTemplate: vi.fn(),
  publishTemplate: vi.fn(),
}));

vi.mock('../../../../services/priceTagRequestService', () => ({
  // One row per TAG since the combos slice - `resolveRequestLines` is gone with
  // the line-keyed document. The two beside it are what the post-save reload calls.
  resolveRequestTags: vi.fn(),
  getPriceTagRequest: vi.fn(),
  updateRequestTag: vi.fn(),
  transitionPriceTagRequest: vi.fn(),
  exportTagSheet: vi.fn(),
}));

vi.mock('../../../../services/priceTagReviewService', () => ({
  listReviewComments: vi.fn(),
  setReviewCommentResolved: vi.fn(),
}));

vi.mock('../../../../services/priceTagDataService', () => ({
  listTagDataChanges: vi.fn(),
  resolveTagPin: vi.fn(),
  updateAllTagPins: vi.fn(),
  listRequestVersions: vi.fn(async () => []),
  getRequestVersion: vi.fn(),
  restoreRequestVersion: vi.fn(),
  // Owner round finding 3: "Check product data" re-runs the comparison.
  recheckTagDataChanges: vi.fn(),
}));

/** Owner round finding 2: what the lightbox was actually handed, per open. */
const lightboxPayloads: unknown[] = [];

vi.mock('@/components/dealer-kit/DesignLightbox', () => ({
  __esModule: true,
  default: ({
    title,
    payload,
  }: {
    title: string;
    payload: {
      version: number;
      doc?: { sheets?: { tags?: { request_tag_id?: string }[] }[] } | null;
      resolvedData?: Record<string, { list_price?: number | null }>;
    } | null;
  }) => {
    lightboxPayloads.push(payload);
    // The same lookup `TagSheetRenderer` makes for real - `resolvedData` is
    // keyed by the TAG, never the line (owner live finding, PT-202609-0015:
    // History View showed "Price TBC" because the payload builder keyed it
    // by `line_id`, so this exact lookup came back empty for every version).
    const tagId = payload?.doc?.sheets?.[0]?.tags?.[0]?.request_tag_id;
    const price = tagId ? payload?.resolvedData?.[tagId]?.list_price : undefined;
    return (
      <div data-testid="version-lightbox" data-version={payload?.version}>
        {title}
        <span data-testid="version-price">{price ?? 'Price TBC'}</span>
      </div>
    );
  },
}));

vi.mock('../../../../tag-sizes/hooks/useTagSizes', () => ({
  useTagSizesQuery: () => ({ data: [] }),
  useDeleteTagSizePreset: () => ({ run: vi.fn(), targetId: null, isPending: false }),
  useCreateTagSize: () => ({ mutateAsync: vi.fn(async () => ({})), isPending: false }),
}));

import { resolveRequestTags } from '../../../../services/priceTagRequestService';
import {
  listReviewComments,
  setReviewCommentResolved,
} from '../../../../services/priceTagReviewService';
import {
  listTagDataChanges,
  listRequestVersions,
  getRequestVersion,
  recheckTagDataChanges,
  resolveTagPin,
} from '../../../../services/priceTagDataService';
import { RequestTagDesigner } from './RequestTagDesigner';
import type {
  PriceTagRequestDetail,
  PriceTagRequestLine,
  PriceTagRequestTag,
} from '../../../../services/priceTagRequestService';

const mockResolveTags = vi.mocked(resolveRequestTags);
const mockComments = vi.mocked(listReviewComments);
const mockSetResolved = vi.mocked(setReviewCommentResolved);
const mockChanges = vi.mocked(listTagDataChanges);
const mockDecide = vi.mocked(resolveTagPin);
const mockListVersions = vi.mocked(listRequestVersions);
const mockGetVersion = vi.mocked(getRequestVersion);
const mockRecheck = vi.mocked(recheckTagDataChanges);

/**
 * The one tag a line carries by default.
 *
 * Its id IS the line id, so every id these tests already assert on stays the id
 * they assert on: submit mints exactly one tag per line, and only a Split ever
 * gives a line a second one.
 */
function requestTag(lineId: string): PriceTagRequestTag {
  return {
    id: lineId,
    sort_order: 0,
    label: '1a',
    quantity: 1,
    choices_display: [],
    open_groups: [],
    marketing_price_override: null,
    marketing_override_reason: null,
    list_price: 1599,
    sell_price: null,
  };
}

function line(id: string, code: string, order: number): PriceTagRequestLine {
  return {
    id,
    line_type: 'product',
    product_id: `prod-${id}`,
    product_set_id: null,
    name: 'Kitchen Sink',
    code,
    show_promo_price: false,
    quantity: 1,
    included_accessories: null,
    sort_order: order,
    list_price: 1599,
    sell_price: null,
    parts: [],
    package_warning: null,
    tags: [requestTag(id)],
  } as PriceTagRequestLine;
}

function tagData(id: string, code: string): LineTagData {
  return {
    // One row per TAG, and the everyday request has one tag per line whose id
    // is the line's - so a row named by `line_id` keys on the same id it always
    // did.
    tag_id: id,
    tag_label: '1a',
    open_groups: [],
    parts: [],
    line_id: id,
    code,
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
  };
}

const REQUEST: PriceTagRequestDetail = {
  id: 'req-1',
  doc_number: 'PT-000001',
  debtor_code: null,
  debtor_name: null,
  needed_by_date: null,
  notes: null,
  status: 'changes_requested',
  line_count: 2,
  created_at: '2026-09-01T00:00:00Z',
  assigned_to_id: 'user-1',
  assigned_to_name: 'Jayson',
  contact_name: 'CONTACT AI',
  contact_id: 'contact-1',
  lines: [line('line-1', 'SRT-1234', 0), line('line-2', 'SRT-5678', 1)],
};

function comment(overrides: Record<string, unknown> = {}) {
  return {
    id: 'comment-1',
    request_id: 'req-1',
    tag_id: 'line-1',
    round: 1,
    x: 0.25,
    y: 0.5,
    w: 0,
    h: 0,
    body: 'Make the price bigger',
    author_name: 'CONTACT AI',
    created_at: '2026-09-14T00:00:00Z',
    resolved_at: null,
    resolved_by_name: null,
    ...overrides,
  } as never;
}

async function renderDesigner() {
  const result = render(
    <RequestTagDesigner
      request={REQUEST}
      initialDoc={null}
      onSave={vi.fn(async () => {})}
      onAutosave={vi.fn(async () => {})}
    />,
  );
  await waitFor(() =>
    expect(screen.getByTestId('canvas-editor')).toBeInTheDocument(),
  );
  return result;
}

beforeEach(() => {
  pinRenders.length = 0;
  lightboxPayloads.length = 0;
  vi.clearAllMocks();
  mockResolveTags.mockResolvedValue([
    tagData('line-1', 'SRT-1234'),
    tagData('line-2', 'SRT-5678'),
  ]);
  mockComments.mockResolvedValue([]);
  mockChanges.mockResolvedValue([]);
  mockListVersions.mockResolvedValue([]);
});

// ---------------------------------------------------------------------------
// AC-S2-6 - the change-request markers
// ---------------------------------------------------------------------------

describe('change-request markers on the canvas (AC-S2-6)', () => {
  it('hands the canvas only the SELECTED tag pins', async () => {
    mockComments.mockResolvedValue([
      comment({ id: 'c1', tag_id: 'line-1' }),
      comment({ id: 'c2', tag_id: 'line-2', body: 'Other line' }),
    ]);
    await renderDesigner();

    await waitFor(() =>
      expect(screen.getByTestId('canvas-pin-1')).toBeInTheDocument(),
    );
    expect(screen.getByTestId('canvas-pin-1')).toHaveTextContent(
      'Make the price bigger',
    );
    expect(screen.queryByText(/Other line/)).toBeNull();
  });

  it('numbers a marker the same way every other surface numbers it', async () => {
    mockComments.mockResolvedValue([
      comment({ id: 'c1', tag_id: 'line-2', body: 'First sent' }),
      comment({ id: 'c2', tag_id: 'line-1', body: 'Second sent' }),
    ]);
    await renderDesigner();

    // line-1 is selected; its comment was the SECOND sent, so it wears 2.
    await waitFor(() =>
      expect(screen.getByTestId('canvas-pin-2')).toHaveTextContent('Second sent'),
    );
  });

  it('the Comments toolbar toggle hides and shows them', async () => {
    mockComments.mockResolvedValue([comment()]);
    await renderDesigner();
    await waitFor(() =>
      expect(screen.getByTestId('canvas-pin-1')).toBeInTheDocument(),
    );

    fireEvent.click(
      screen.getByRole('button', { name: /Hide change requests \(1 open\)/ }),
    );

    expect(screen.queryByTestId('canvas-pin-1')).toBeNull();

    fireEvent.click(
      screen.getByRole('button', { name: /Show change requests \(1 open\)/ }),
    );
    expect(screen.getByTestId('canvas-pin-1')).toBeInTheDocument();
  });

  it('is ON by default while a pin is still open', async () => {
    mockComments.mockResolvedValue([comment()]);
    await renderDesigner();

    expect(
      await screen.findByRole('button', { name: /Hide change requests \(1 open\)/ }),
    ).toBeInTheDocument();
  });

  it('is OFF by default once every pin has been ticked Done', async () => {
    // D6: the toggle is armed BY an open pin. A round that is fully worked off
    // leaves markers sitting over the artwork marketing is now editing, with a
    // count of zero beside them - clutter that has to be turned off by hand
    // every time the designer is opened.
    mockComments.mockResolvedValue([
      comment({ resolved_at: '2026-09-14T02:00:00Z', resolved_by_name: 'Mei' }),
    ]);
    await renderDesigner();

    expect(
      await screen.findByRole('button', { name: /Show change requests \(0 open\)/ }),
    ).toBeInTheDocument();
    expect(screen.queryByTestId('canvas-pin-1')).toBeNull();
  });

  it('there is no toggle at all on a request nobody commented on', async () => {
    await renderDesigner();

    expect(screen.queryByRole('button', { name: /change requests/i })).toBeNull();
  });

  it('a resolved pin still draws, greyed by its caption, so the round reads whole', async () => {
    // An OPEN pin alongside it, because the Comments toggle is armed by an
    // open pin and a fully worked-off round starts hidden (the test below).
    // A mixed round is also the state this actually matters in: marketing has
    // ticked one off and is looking at the other.
    mockComments.mockResolvedValue([
      comment({
        id: 'c1',
        resolved_at: '2026-09-14T02:00:00Z',
        resolved_by_name: 'Mei',
      }),
      comment({ id: 'c2', body: 'Still open' }),
    ]);
    await renderDesigner();

    await waitFor(() =>
      expect(screen.getByTestId('canvas-pin-1')).toHaveTextContent(/Done/),
    );
    expect(screen.getByTestId('canvas-pin-2')).toHaveTextContent('Still open');
    // D13 (PLAN-price-tag-ai-extract-resolver.md): every OPEN pin now offers
    // its own "Done" button (AC-S9-1), so the whole row legitimately carries
    // that word - what this pins is the CAPTION: an unresolved pin reads
    // "Round 1", never "Round 1 / Done".
    expect(screen.getByTestId('canvas-pin-2').textContent).not.toMatch(
      /Round \d+ \/ Done/,
    );
  });

  it('the LINES rail badges the tags that have open pins', async () => {
    mockComments.mockResolvedValue([
      comment({ id: 'c1', tag_id: 'line-1' }),
      comment({ id: 'c2', tag_id: 'line-1', body: 'And this' }),
      comment({ id: 'c3', tag_id: 'line-2', resolved_at: '2026-09-14T02:00:00Z' }),
    ]);
    await renderDesigner();

    expect(
      await screen.findByTitle('2 open change requests'),
    ).toBeInTheDocument();
    // line-2's only comment is Done, so it carries no badge.
    expect(screen.queryByTitle('1 open change request')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC-S5-3 / AC-S5-4 - the data gate replaces refresh-on-focus
// ---------------------------------------------------------------------------

describe('the product data gate (AC-S5-3, AC-S5-4)', () => {
  const CHANGED = {
    tag_id: 'line-1',
    tag_label: '1a',
    line_id: 'line-1',
    code: 'SRT-1234',
    name: 'Kitchen Sink',
    changes: [
      { field: 'list_price', label: 'List price', old: 'RM 1,599', new: 'RM 1,799' },
    ],
  };

  it('marks only the changed tag with a red dot', async () => {
    mockChanges.mockResolvedValue([CHANGED]);
    await renderDesigner();

    expect(
      await screen.findByRole('button', {
        name: 'Review product data changes on SRT-1234 1a',
      }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('button', {
        name: 'Review product data changes on SRT-5678 1a',
      }),
    ).toBeNull();
  });

  it('a line with an EMPTY change list carries no dot', async () => {
    mockChanges.mockResolvedValue([{ ...CHANGED, changes: [] }]);
    await renderDesigner();

    expect(
      screen.queryByRole('button', { name: /Review product data changes/ }),
    ).toBeNull();
  });

  it('"Check product data" re-runs the comparison and the red dot returns (owner round finding 3)', async () => {
    // Nothing on load - the salesperson already chose Keep current once.
    mockChanges.mockResolvedValue([]);
    mockRecheck.mockResolvedValue([CHANGED]);
    await renderDesigner();

    expect(
      screen.queryByRole('button', { name: /Review product data changes/ }),
    ).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Check product data' }));

    await waitFor(() => expect(mockRecheck).toHaveBeenCalledWith('req-1'));

    expect(
      await screen.findByRole('button', {
        name: 'Review product data changes on SRT-1234 1a',
      }),
    ).toBeInTheDocument();
  });

  it('the dot opens the review dialog with that line old and new values', async () => {
    mockChanges.mockResolvedValue([CHANGED]);
    await renderDesigner();

    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Review product data changes on SRT-1234 1a',
      }),
    );

    expect(await screen.findByText('Product data changed')).toBeInTheDocument();
    expect(screen.getByText('RM 1,599')).toBeInTheDocument();
    expect(screen.getByText('RM 1,799')).toBeInTheDocument();
  });

  it('Update tag sends the decision for that line', async () => {
    mockChanges.mockResolvedValue([CHANGED]);
    mockDecide.mockResolvedValue(undefined as never);
    await renderDesigner();
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Review product data changes on SRT-1234 1a',
      }),
    );
    await screen.findByText('Product data changed');

    fireEvent.click(screen.getByRole('button', { name: /Update tag/ }));

    await waitFor(() =>
      expect(mockDecide).toHaveBeenCalledWith('req-1', 'line-1', 'update'),
    );
  });

  it('Keep current sends the other decision', async () => {
    mockChanges.mockResolvedValue([CHANGED]);
    mockDecide.mockResolvedValue(undefined as never);
    await renderDesigner();
    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Review product data changes on SRT-1234 1a',
      }),
    );
    await screen.findByText('Product data changed');

    fireEvent.click(screen.getByRole('button', { name: /Keep current/ }));

    await waitFor(() =>
      expect(mockDecide).toHaveBeenCalledWith('req-1', 'line-1', 'keep'),
    );
  });

  it('focusing the window resolves NOTHING (D18 retires the r4 refresh)', async () => {
    await renderDesigner();
    await waitFor(() => expect(mockResolveTags).toHaveBeenCalledTimes(1));

    window.dispatchEvent(new Event('focus'));
    document.dispatchEvent(new Event('visibilitychange'));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(mockResolveTags).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Owner test round, finding 2 - History View draws the VERSION's own data
// ---------------------------------------------------------------------------

describe('History View draws the version, not the live doc (owner round finding 2)', () => {
  it('View calls getRequestVersion and hands the lightbox that payload, never the live rows', async () => {
    mockListVersions.mockResolvedValue([
      {
        version: 3,
        commit_message: 'Marked proof ready',
        created_by_name: 'Mei',
        created_at: '2026-09-14T00:00:00Z',
      },
    ]);
    const VERSION_PAYLOAD = {
      page_id: 'page-1',
      version: 3,
      source: 'version',
      doc: { kind: 'tag_sheet', imposition: { page_width_mm: 210, page_height_mm: 297 }, sheets: [] },
      resolvedData: {},
      assets: {},
      images: {},
      fonts: [],
    } as never;
    mockGetVersion.mockResolvedValue(VERSION_PAYLOAD);

    await renderDesigner();

    fireEvent.click(screen.getByRole('button', { name: 'History' }));

    const viewButton = await screen.findByRole('button', { name: /View/ });
    fireEvent.click(viewButton);

    await waitFor(() =>
      expect(mockGetVersion).toHaveBeenCalledWith('req-1', 3),
    );

    const lightbox = await screen.findByTestId('version-lightbox');
    expect(lightbox).toHaveAttribute('data-version', '3');
    // The mocked version payload reached the lightbox - not a payload built
    // from the live `doc` / `resolvedRows` (Phase 1's `designPayloadFromResponse`
    // call), which never calls the version service at all.
    expect(lightboxPayloads.at(-1)).toEqual(VERSION_PAYLOAD);
  });

  it('renders the version\'s own list price, keyed by its tag id, not "Price TBC" (owner live finding, PT-202609-0015)', async () => {
    // Deliberately NOT one of the request's line ids (`line-1`/`line-2`): a
    // resolvedData map still keyed by line_id would answer nothing at this
    // key and the lightbox would draw "Price TBC" instead of RM 1,260.
    mockListVersions.mockResolvedValue([
      {
        version: 1,
        commit_message: 'First save',
        created_by_name: 'Mei',
        created_at: '2026-09-12T00:00:00Z',
      },
    ]);
    mockGetVersion.mockResolvedValue({
      page_id: 'page-1',
      version: 1,
      source: 'version',
      doc: {
        kind: 'tag_sheet',
        imposition: { page_width_mm: 210, page_height_mm: 297 },
        sheets: [
          {
            id: 'sheet-1',
            tags: [{ id: 'placed-1', request_tag_id: 'tag-9', request_line_id: 'line-1' }],
          },
        ],
      },
      resolvedData: { 'tag-9': { list_price: 1260 } },
      assets: {},
      images: {},
      fonts: [],
    } as never);

    await renderDesigner();

    fireEvent.click(screen.getByRole('button', { name: 'History' }));
    const viewButton = await screen.findByRole('button', { name: /View/ });
    fireEvent.click(viewButton);

    const lightbox = await screen.findByTestId('version-lightbox');
    expect(within(lightbox).getByTestId('version-price')).toHaveTextContent('1260');
  });
});

// ---------------------------------------------------------------------------
// AC-S9-2/S9-3 (PLAN-price-tag-ai-extract-resolver.md D13) - Done from the
// pin, wired through to a re-fetch: the rail count and the CTA both read off
// the SAME `reviewComments` state the markers do, so resolving one has to
// move all three together.
// ---------------------------------------------------------------------------

describe('Done from the designer pin popover (AC-S9-2, AC-S9-3)', () => {
  it('AC-S9-2: clicking Done calls setReviewCommentResolved, then the rail count and CTA drop', async () => {
    // `vi.clearAllMocks()` in the file's `beforeEach` does NOT drain a
    // queued `mockResolvedValueOnce` - reset explicitly so a value queued
    // here can never leak into the next test.
    mockComments.mockReset();
    mockSetResolved.mockReset();
    mockComments.mockResolvedValueOnce([comment({ id: 'c1', tag_id: 'line-1' })]);
    mockSetResolved.mockResolvedValue(
      comment({ id: 'c1', tag_id: 'line-1', resolved_at: '2026-09-15T00:00:00Z' }) as never,
    );
    await renderDesigner();

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /Mark design ready \(1 open\)/ }),
      ).toBeInTheDocument(),
    );

    // The re-fetch after Done answers with the comment now resolved.
    mockComments.mockResolvedValueOnce([
      comment({ id: 'c1', tag_id: 'line-1', resolved_at: '2026-09-15T00:00:00Z' }),
    ]);

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));

    await waitFor(() =>
      expect(mockSetResolved).toHaveBeenCalledWith('req-1', 'c1', true),
    );
    await waitFor(() => expect(mockComments).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Mark design ready' }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole('button', { name: /Mark design ready \(1 open\)/ }),
    ).toBeNull();
  });

  it('AC-S9-3: a rejected PATCH toasts and the pin stays open', async () => {
    mockComments.mockReset();
    mockSetResolved.mockReset();
    mockComments.mockResolvedValue([comment({ id: 'c1', tag_id: 'line-1' })]);
    mockSetResolved.mockRejectedValue(new Error('boom'));
    await renderDesigner();
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /Mark design ready \(1 open\)/ }),
      ).toBeInTheDocument(),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Done' }));

    await waitFor(() => expect(mockSetResolved).toHaveBeenCalled());
    const { toast } = await import('@/lib/toast');
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Could not update the change request'),
    );
    expect(
      screen.getByRole('button', { name: /Mark design ready \(1 open\)/ }),
    ).toBeInTheDocument();
  });
});
