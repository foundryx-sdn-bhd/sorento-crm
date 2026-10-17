/**
 * PLAN-portal-price-tag-journey-r8, D-P1/AC-P11: the read-only view renders
 * the same FOUR SECTIONS, in the same order, as the edit form - Customer;
 * Sales Order & Lines; Price; Additional Information - all open, headers
 * still able to toggle.
 *
 * r7's flat seven-field order (Customer, Promotion, Price, Need by, Notes,
 * Lines, Sales Order) is retired: r8 groups those same fields under the four
 * section cards above (D-P1), so this test now asserts the SECTION order via
 * each `FormSection` header's accessible name (the collapsible trigger
 * button), plus the field order WITHIN that structure via the rendered
 * `Label` (`data-slot="label"`) text nodes in document order - the same
 * order a screen reader or a human scanning top-to-bottom encounters them
 * in, not the order they appear in the source.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock('../lib/price-tag-request-service', async () => {
  const { computeLinePricing } = await import('@/app/(auth)/portal/components/__fixtures__/line-pricing');
  return {
  lookupLinePricing: vi.fn(async (mode: string, lines: unknown[]) =>
    computeLinePricing(mode as 'list' | 'selling', lines as never),
  ),
  lookupDebtors: vi.fn(async () => []),
  lookupPromotions: vi.fn(async () => []),
  lookupProductCombos: vi.fn(async () => ({ host_guarded: false, combos: [] })),
  lookupTagItems: vi.fn(async () => []),
  getRequest: vi.fn(),
  createRequest: vi.fn(),
  updateRequest: vi.fn(),
  deleteRequest: vi.fn(),
  submitRequest: vi.fn(),
  approveRequest: vi.fn(),
  requestChanges: vi.fn(),
  listReviewComments: vi.fn(async () => []),
  collectRequest: vi.fn(),
  downloadPriceTagPdf: vi.fn(),
  };
});

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

// Same reasoning as the sibling read-only suites: the real DetailActionsMenu
// mounts a Radix DropdownMenu (Portal + `motion/react` AnimatePresence),
// which is unrelated to what this file asserts (section order) and flickers
// in jsdom.
vi.mock('@/components/common/DetailActionsMenu', () => ({
  __esModule: true,
  DetailActionsMenu: () => null,
}));

import { getRequest } from '../lib/price-tag-request-service';
import { PriceTagRequestForm } from './PriceTagRequestForm';

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
});

describe('read-only view section order (AC-P11)', () => {
  it('renders the four sections Customer, Sales Order & Lines, Price, Additional Information in that order', async () => {
    asMock(getRequest).mockResolvedValue({
      id: 'req-1',
      doc_number: 'PT-202609-0001',
      debtor_code: 'ZZTD01',
      debtor_name: 'ZZT Dealer',
      promotion_id: 'promo-1',
      promotion_name: 'ZZT August Promo',
      needed_by_date: '2026-09-10',
      notes: 'Handle with care',
      // A non-editable, non-design-preview status: r7 widened the design
      // preview to proof_ready|changes_requested|approved|ready (D11), so
      // this now has to pick a status OUTSIDE that set to stay isolated to
      // the base layout order, which is all this test asserts (AC-S2-2 /
      // AC-S4-1 cover the design preview section itself).
      status: 'rejected',
      line_count: 1,
      created_at: '2026-09-01T00:00:00Z',
      portal_draft_at: null,
      contact_id: 'contact-1',
      has_completed_export: false,
      lines: [
        {
          id: 'line-1',
          line_type: 'product',
          product_id: 'prod-1',
          product_set_id: null,
          name: 'ZZT Kitchen Sink',
          code: 'ZZT-SINK-1',
          show_promo_price: true,
          quantity: 2,
          alternatives: [],
          included_accessories: null,
        },
      ],
      attachments: [],
    });

    const { container } = render(<PriceTagRequestForm requestId="req-1" />);

    await screen.findByText('PT-202609-0001');

    // The four section headers (`FormSection`'s collapsible trigger button),
    // in document order - the section-level half of AC-P11.
    const sectionTitles = Array.from(
      container.querySelectorAll('[data-slot="collapsible-trigger"]'),
    ).map((n) => n.textContent?.trim() ?? '');
    expect(sectionTitles).toEqual([
      'Customer',
      'Sales Order & Lines',
      'Price',
      'Additional Information',
    ]);

    // Within that structure, the field order is unchanged from r7 (`Label`
    // text nodes, `data-slot="label"`) - just regrouped under the sections
    // above rather than laid out flat.
    const nodes = Array.from(container.querySelectorAll('[data-slot="label"]'));
    const texts = nodes.map((n) => n.textContent?.trim() ?? '');
    // `Lines` renders as `Lines (1)`; normalized to the bare word.
    const normalized = texts.map((t) => (t.startsWith('Lines') ? 'Lines' : t));

    expect(normalized).toEqual([
      'Customer',
      'Sales Order',
      'Lines',
      'Price',
      // r10 S1: the "Who prints" control (and its `Printing` label) is gone -
      // the portal never asks, `printBy` is a constant 'self'. Additional
      // Information now opens directly on Need by / Notes.
      'Need by',
      'Notes',
      // FORMS-PROJECT: the linked project closes Additional Information.
      'Project',
    ]);
  });
});
