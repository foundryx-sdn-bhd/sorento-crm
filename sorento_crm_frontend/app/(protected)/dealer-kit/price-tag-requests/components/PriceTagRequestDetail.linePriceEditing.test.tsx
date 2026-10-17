/**
 * CRM detail: line price basis reads the SAVED line, and the manual price
 * input does not spam the network (F4/F7, Reviewer B2 / S7).
 *
 * UAC: `documentation/plans/dealer-kit/price-tag-line-promo-combo-subject-acceptance-criteria.md`
 *
 * F4 (Reviewer B2): `PriceTagRequestDetail`'s `linePriceOverrides` state
 * starts empty on every load, and `effectivePromotionId` falls back to
 * `linePricing[lineId]?.auto_promotion_id` until the viewer makes a fresh
 * pick this session (`override.locked`). A line saved with `promotion_id`
 * set (e.g. "Raya") is never read into that state, so the select opens on
 * the AUTO pick ("Clearance") instead of what is actually saved.
 *
 * F7 (Reviewer S7): the manual price `<Input>` calls `setManualLinePrice` on
 * every `onChange`, and `setManualLinePrice` writes `linePriceOverrides`,
 * which is a dependency of the `lookupLinePricing` recompute `useEffect`
 * (~line 493-522). So the recompute - a network call - fires once per
 * keystroke, not once on blur (`updatePriceTagLinePrice`, the actual save,
 * IS blur-only already; the recompute is the one still per-keystroke).
 */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// `dismiss` is here even though `useDeferredAction` is mocked below: the
// pending-entity store this hook would otherwise drive calls `toast.dismiss`
// on its own follow-through timer, and a mock missing the key throws if
// anything in this file ever exercises the real hook.
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), dismiss: vi.fn() } }));

vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/dealer-kit/price-tag-requests/req-1',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/components/common/DetailActionsMenu', () => ({
  DetailActionsMenu: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="gear-menu">{children}</div>
  ),
}));
vi.mock('@/components/ui/dropdown-menu', () => ({
  DropdownMenuItem: ({
    children,
    onSelect,
  }: {
    children: React.ReactNode;
    onSelect?: (event: { preventDefault: () => void }) => void;
  }) => (
    <div role="menuitem" onClick={() => onSelect?.({ preventDefault: () => {} })}>
      {children}
    </div>
  ),
}));

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: () => ({
    pending: null,
    isPending: false,
    isBlocked: false,
    start: vi.fn(),
    cancel: vi.fn(),
    countdown: null,
  }),
}));

// Native `<select>` stand-in - the real SearchableSelect is a Radix popover
// jsdom cannot open, and both F4/F7 need the value/options directly queryable.
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    id?: string;
    value: string;
    onChange?: (v: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      id={props.id}
      aria-label={props.id}
      value={props.value}
      onChange={(e) => props.onChange?.(e.target.value)}
    >
      <option value="">{props.placeholder ?? ''}</option>
      {(props.options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

const { mockLookupLinePricing } = vi.hoisted(() => ({
  mockLookupLinePricing: vi.fn(),
}));

vi.mock('../../services/priceTagRequestService', () => ({
  getPriceTagRequest: vi.fn(),
  getTagSheetDoc: vi.fn(async () => null),
  claimPriceTagRequest: vi.fn(),
  transitionPriceTagRequest: vi.fn(),
  exportTagSheet: vi.fn(),
  listPriceTagRequests: vi.fn(async () => ({
    data: [],
    pagination: { total: 0, page: 1, limit: 50 },
  })),
  getRequestDesignPayload: vi.fn(async () => null),
  lookupLinePricing: mockLookupLinePricing,
  updatePriceTagLinePrice: vi.fn(),
}));

import {
  getPriceTagRequest,
  updatePriceTagLinePrice,
} from '../../services/priceTagRequestService';
import PriceTagRequestDetail from './PriceTagRequestDetail';

const mockGet = vi.mocked(getPriceTagRequest);
const mockUpdateLinePrice = vi.mocked(updatePriceTagLinePrice);

function switchTab(name: string) {
  fireEvent.mouseDown(screen.getByRole('tab', { name }), { button: 0, ctrlKey: false });
}

function renderDetail(requestId = 'req-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <PriceTagRequestDetail requestId={requestId} />
    </QueryClientProvider>,
  );
}

const baseRequest = {
  id: 'req-1',
  doc_number: 'PT-202608-0001',
  debtor_code: 'ARD001',
  debtor_name: 'ARDENCY CONSTRUCTION',
  promotion_id: null,
  promotion_name: null,
  price_mode: 'selling' as const,
  needed_by_date: '2026-09-05',
  notes: null,
  status: 'designing',
  line_count: 1,
  created_at: '2026-08-30T02:00:00Z',
  assigned_to_id: 'user-1',
  assigned_to_name: 'Marketing Mei',
  contact_name: 'Sales Sam',
  contact_id: 'contact-1',
  attachments: [],
};

beforeEach(() => {
  vi.clearAllMocks();
});

// --------------------------------------------------------------------------- F4
describe('PriceTagRequestDetail - a line reads its SAVED promotion on load (F4, Reviewer B2)', () => {
  it('shows the line\'s saved promotion (Raya), not the auto-picked one (Clearance)', async () => {
    mockGet.mockResolvedValue({
      ...baseRequest,
      lines: [
        {
          id: 'line-1',
          line_type: 'product',
          product_id: 'prod-1',
          product_set_id: null,
          name: 'Kitchen Sink',
          code: 'SRT-1',
          show_promo_price: true,
          quantity: 1,
          included_accessories: null,
          sort_order: 0,
          list_price: 500,
          sell_price: 300,
          parts: [],
          package_warning: null,
          promotion_id: 'promo-raya',
          promotion_name: 'Raya',
          manual_sell_price: null,
          sell_price_basis: 'promotion',
          tags: [
            {
              id: 'line-1',
              sort_order: 0,
              label: '1a',
              quantity: 1,
              choices: {},
              choices_display: [],
              open_groups: [],
              marketing_price_override: null,
              marketing_override_reason: null,
              list_price: 500,
              sell_price: 300,
            },
          ],
        },
      ],
    } as never);
    mockLookupLinePricing.mockResolvedValue([
      {
        key: 'line-1',
        list_price: 500,
        promotion_options: [
          { id: 'promo-raya', description: 'Raya', sell_price: 300 },
          { id: 'promo-clearance', description: 'Clearance', sell_price: 250 },
        ],
        // The auto pick disagrees with the SAVED promotion on purpose.
        auto_promotion_id: 'promo-clearance',
        sell_price: 300,
        sell_price_basis: 'promotion',
        parts_at_list: [],
        candidates: [],
      },
    ]);

    renderDetail();
    await screen.findByRole('heading', { name: /PT-202608-0001/, level: 1 });
    switchTab('Lines');

    const select = await screen.findByLabelText('promotion-line-1');
    await waitFor(() => expect((select as HTMLSelectElement).value).not.toBe(''));
    expect((select as HTMLSelectElement).value).toBe('promo-raya');
  });
});

// --------------------------------------------------------------------------- F7
describe('PriceTagRequestDetail - manual price input does not fire a lookup per keystroke (F7, Reviewer S7)', () => {
  it('typing "1500" fires lookupLinePricing at most once more, not once per keystroke', async () => {
    mockGet.mockResolvedValue({
      ...baseRequest,
      lines: [
        {
          id: 'line-1',
          line_type: 'product',
          product_id: 'prod-1',
          product_set_id: null,
          name: 'Kitchen Sink',
          code: 'SRT-1',
          show_promo_price: false,
          quantity: 1,
          included_accessories: null,
          sort_order: 0,
          list_price: 500,
          sell_price: null,
          parts: [],
          package_warning: null,
          promotion_id: null,
          promotion_name: null,
          manual_sell_price: null,
          sell_price_basis: 'list',
          tags: [
            {
              id: 'line-1',
              sort_order: 0,
              label: '1a',
              quantity: 1,
              choices: {},
              choices_display: [],
              open_groups: [],
              marketing_price_override: null,
              marketing_override_reason: null,
              list_price: 500,
              sell_price: null,
            },
          ],
        },
      ],
    } as never);
    // No covering promotion at all - the "Type a price" input renders.
    mockLookupLinePricing.mockResolvedValue([
      {
        key: 'line-1',
        list_price: 500,
        promotion_options: [],
        auto_promotion_id: null,
        sell_price: null,
        sell_price_basis: 'list',
        parts_at_list: [],
        candidates: [],
      },
    ]);

    renderDetail();
    await screen.findByRole('heading', { name: /PT-202608-0001/, level: 1 });
    switchTab('Lines');

    const input = await screen.findByLabelText('Selling price for SRT-1');
    await waitFor(() => expect(mockLookupLinePricing).toHaveBeenCalledTimes(1));
    const callsBeforeTyping = mockLookupLinePricing.mock.calls.length;

    // Simulate real typing: one `change` event per character, exactly what
    // a keystroke produces (a single `fireEvent.change` with the final
    // string would not exercise this at all).
    fireEvent.change(input, { target: { value: '1' } });
    fireEvent.change(input, { target: { value: '15' } });
    fireEvent.change(input, { target: { value: '150' } });
    fireEvent.change(input, { target: { value: '1500' } });

    // Give any per-keystroke effect a tick to fire before asserting.
    await new Promise((resolve) => setTimeout(resolve, 50));

    const callsAfterTyping = mockLookupLinePricing.mock.calls.length - callsBeforeTyping;
    expect(callsAfterTyping).toBeLessThanOrEqual(1);
  });
});

// --------------------------------------------------------------------------- T2
describe('PriceTagRequestDetail - a line with a SAVED manual price is editable, not read-only (T2, browser pass 2)', () => {
  it('renders the manual input pre-filled with 175, the Sell Price column reads RM 175.00, and blurring "1500" saves it once', async () => {
    mockGet.mockResolvedValue({
      ...baseRequest,
      lines: [
        {
          id: 'line-1',
          line_type: 'product',
          product_id: 'prod-1',
          product_set_id: null,
          name: 'Kitchen Sink',
          code: 'SRT-1',
          show_promo_price: true,
          quantity: 1,
          included_accessories: null,
          sort_order: 0,
          list_price: 500,
          sell_price: 175,
          parts: [],
          package_warning: null,
          promotion_id: null,
          promotion_name: null,
          manual_sell_price: 175,
          sell_price_basis: 'manual',
          tags: [
            {
              id: 'line-1',
              sort_order: 0,
              label: '1a',
              quantity: 1,
              choices_display: [],
              open_groups: [],
              marketing_price_override: null,
              marketing_override_reason: null,
              list_price: 500,
              sell_price: 175,
            },
          ],
        },
      ],
    } as never);
    mockLookupLinePricing.mockResolvedValue([
      {
        key: 'line-1',
        list_price: 500,
        promotion_options: [],
        auto_promotion_id: null,
        sell_price: 175,
        sell_price_basis: 'manual',
        parts_at_list: [],
        candidates: [],
      },
    ]);
    mockUpdateLinePrice.mockResolvedValue({} as never);

    renderDetail();
    await screen.findByRole('heading', { name: /PT-202608-0001/, level: 1 });
    switchTab('Lines');

    // The main lines table's own "Sell Price" column, not the promotion
    // sub-row's "Selling price" label - a line with a saved manual price and
    // `show_promo_price: true` prints its tag's own resolved figure there.
    expect(await screen.findByText('RM 175.00')).toBeInTheDocument();

    const input = (await screen.findByLabelText(
      'Selling price for SRT-1',
    )) as HTMLInputElement;
    // Pre-filled with the SAVED figure - not a static read-only paragraph
    // (browser pass 2: a line like this rendered no input at all).
    expect(input.value).toBe('175');

    fireEvent.change(input, { target: { value: '1500' } });
    fireEvent.blur(input);

    await waitFor(() => expect(mockUpdateLinePrice).toHaveBeenCalledTimes(1));
    expect(mockUpdateLinePrice).toHaveBeenCalledWith('req-1', 'line-1', {
      promotion_id: null,
      manual_sell_price: 1500,
    });
  });
});

// --------------------------------------------------------------------------- columns
// Owner ruling after #948: Promotion / Selling price are real table columns
// on the desktop table (992px and up, `useIsMobile`'s `MOBILE_BREAKPOINT`),
// not a `bg-muted/20` sub-row under the line. Below 992px they still stack
// under the line - since jsdom has no CSS breakpoints this is asserted only
// via a `data-testid="line-pricing-stack"` element's presence, never its
// visibility.
describe('PriceTagRequestDetail - Promotion/Selling price are table columns, not a colSpan sub-row', () => {
  it('the Lines header has Promotion and Selling price columns, and the line row (not a following colSpan row) holds the select and value', async () => {
    mockGet.mockResolvedValue({
      ...baseRequest,
      lines: [
        {
          id: 'line-1',
          line_type: 'product',
          product_id: 'prod-1',
          product_set_id: null,
          name: 'Kitchen Sink',
          code: 'SRT-1',
          show_promo_price: true,
          quantity: 1,
          included_accessories: null,
          sort_order: 0,
          list_price: 500,
          sell_price: 300,
          parts: [],
          package_warning: null,
          promotion_id: 'promo-raya',
          promotion_name: 'Raya',
          manual_sell_price: null,
          sell_price_basis: 'promotion',
          tags: [
            {
              id: 'line-1',
              sort_order: 0,
              label: '1a',
              quantity: 1,
              choices: {},
              choices_display: [],
              open_groups: [],
              marketing_price_override: null,
              marketing_override_reason: null,
              list_price: 500,
              sell_price: 300,
            },
          ],
        },
      ],
    } as never);
    mockLookupLinePricing.mockResolvedValue([
      {
        key: 'line-1',
        list_price: 500,
        promotion_options: [{ id: 'promo-raya', description: 'Raya', sell_price: 300 }],
        auto_promotion_id: 'promo-raya',
        sell_price: 300,
        sell_price_basis: 'promotion',
        parts_at_list: [],
        candidates: [],
      },
    ]);

    renderDetail();
    await screen.findByRole('heading', { name: /PT-202608-0001/, level: 1 });
    switchTab('Lines');

    const headers = (await screen.findAllByRole('columnheader')).map((h) =>
      h.textContent?.trim(),
    );
    expect(headers).toContain('Promotion');
    expect(headers).toContain('Selling price');

    // The line's own row (found via its code, a fixed per-line anchor)
    // carries the Promotion select and the Selling price value in `<td>`
    // cells of THAT row - not a following `bg-muted/20` sub-row.
    const rows = screen.getAllByRole('row');
    const lineRow = rows.find((row) => within(row).queryByText('SRT-1'));
    expect(lineRow).toBeTruthy();
    expect(within(lineRow!).getByLabelText('promotion-line-1')).toBeInTheDocument();

    // No sub-row: every pricing cell lives in the line's own row, so no
    // `<td>` anywhere carries a `colspan` wider than 1.
    const colSpanCells = Array.from(document.querySelectorAll('td[colspan]')).filter(
      (cell) => Number(cell.getAttribute('colspan')) > 1,
    );
    expect(colSpanCells).toHaveLength(0);
  });
});

// --------------------------------------------------------------------------- kill: columns

// Owner ruling after #948: a line's PARTS still render as their own sub-row
// (`parts.map(...)`, ~:1326-1340) - the parts sub-row's leading `<td />` is
// the Type column placeholder, and the SECOND `<td colSpan={columns - 1}>`
// spans everything after it, so its colspan is the header count MINUS ONE,
// not the full header count the edit table's own sub-rows use. `columns`
// itself is `(showActions ? 9 : 8) + (showPriceColumns ? 2 : 0)` - a
// hard-coded arithmetic that a header column change can silently drift
// from with every other test in this suite green.
describe('PriceTagRequestDetail - a parts sub-row spans the header width minus its own leading placeholder cell', () => {
  it('Selling mode, a line with a part: the part sub-row colspan equals the header column count minus one', async () => {
    mockGet.mockResolvedValue({
      ...baseRequest,
      lines: [
        {
          id: 'line-1',
          line_type: 'product',
          product_id: 'prod-1',
          product_set_id: null,
          name: 'Kitchen Sink',
          code: 'SRT-1',
          show_promo_price: true,
          quantity: 1,
          included_accessories: null,
          sort_order: 0,
          list_price: 500,
          sell_price: 300,
          parts: [
            {
              id: 'part-1',
              product_id: 'prod-part',
              code: 'ZZT-PART-1',
              name: 'ZZT Part',
              role: null,
              candidates: [],
            },
          ],
          package_warning: null,
          promotion_id: 'promo-raya',
          promotion_name: 'Raya',
          manual_sell_price: null,
          sell_price_basis: 'promotion',
          tags: [
            {
              id: 'line-1',
              sort_order: 0,
              label: '1a',
              quantity: 1,
              choices: {},
              choices_display: [],
              open_groups: [],
              marketing_price_override: null,
              marketing_override_reason: null,
              list_price: 500,
              sell_price: 300,
            },
          ],
        },
      ],
    } as never);
    mockLookupLinePricing.mockResolvedValue([
      {
        key: 'line-1',
        list_price: 500,
        promotion_options: [{ id: 'promo-raya', description: 'Raya', sell_price: 300 }],
        auto_promotion_id: 'promo-raya',
        sell_price: 300,
        sell_price_basis: 'promotion',
        parts_at_list: [],
        candidates: [],
      },
    ]);

    renderDetail();
    await screen.findByRole('heading', { name: /PT-202608-0001/, level: 1 });
    switchTab('Lines');

    // The part row prints `${code} - ${name}` as ONE text node (font-mono
    // span), so it is found by the whole string.
    await screen.findByText('ZZT-PART-1 - ZZT Part');

    const linesTable = screen.getByRole('table');
    const headerCount = within(linesTable).getAllByRole('columnheader').length;
    const partRow = screen
      .getAllByRole('row')
      .find((row) => within(row).queryByText('ZZT-PART-1 - ZZT Part'));
    expect(partRow).toBeTruthy();
    const spannedCell = within(partRow!)
      .getByText('ZZT-PART-1 - ZZT Part')
      .closest('td[colspan]');
    expect(spannedCell).not.toBeNull();
    expect(Number(spannedCell!.getAttribute('colspan'))).toBe(headerCount - 1);
  });
});

// --------------------------------------------------------------------------- N3
describe('PriceTagRequestDetail - a refused manual price stays until the value is valid (N3)', () => {
  it('keeps the sentence through a still-invalid keystroke, clears it once valid', async () => {
    mockGet.mockResolvedValue({
      ...baseRequest,
      lines: [
        {
          id: 'line-1',
          line_type: 'product',
          product_id: 'prod-1',
          product_set_id: null,
          name: 'Kitchen Sink',
          code: 'SRT-1',
          show_promo_price: false,
          quantity: 1,
          included_accessories: null,
          sort_order: 0,
          list_price: 500,
          sell_price: null,
          parts: [],
          package_warning: null,
          promotion_id: null,
          promotion_name: null,
          manual_sell_price: null,
          sell_price_basis: 'list',
          tags: [],
        },
      ],
    } as never);
    mockLookupLinePricing.mockResolvedValue([
      {
        key: 'line-1',
        list_price: 500,
        promotion_options: [],
        auto_promotion_id: null,
        sell_price: null,
        sell_price_basis: 'list',
        parts_at_list: [],
        candidates: [],
      },
    ]);
    renderDetail();
    await screen.findByRole('heading', { name: /PT-202608-0001/, level: 1 });
    switchTab('Lines');
    const input = await screen.findByLabelText('Selling price for SRT-1');

    fireEvent.change(input, { target: { value: '-1' } });
    fireEvent.blur(input);
    const sentence = 'Enter a number of at least 0.01, or leave it blank.';
    expect(await screen.findByText(sentence)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: '-12' } });
    expect(screen.getByText(sentence)).toBeInTheDocument();

    fireEvent.change(input, { target: { value: '12' } });
    expect(screen.queryByText(sentence)).not.toBeInTheDocument();
  });
});
