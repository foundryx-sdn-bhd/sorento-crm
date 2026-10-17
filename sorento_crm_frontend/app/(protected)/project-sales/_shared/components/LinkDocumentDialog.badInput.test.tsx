/** NUMBER-FIELDS round 3 (S8): bad input in a Take box is never read as 0 (which drops the line). */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { OrderInquiryPoCandidate } from '../types/orderInquiry.types';

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const getOrderInquiryPoCandidates = vi.fn();
const placeOrderInquiryRowOnPoAllocations = vi.fn();

vi.mock('../services/orderInquiryService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/orderInquiryService')>();
  return {
    ...actual,
    getOrderInquiryPoCandidates: (...args: unknown[]) => getOrderInquiryPoCandidates(...args),
    placeOrderInquiryRowOnPoAllocations: (...args: unknown[]) =>
      placeOrderInquiryRowOnPoAllocations(...args),
  };
});

import { LinkDocumentDialog } from './LinkDocumentDialog';

const EARLY: OrderInquiryPoCandidate = {
  kind: 'po',
  po_line_id: 'po-line-early',
  tier: 1,
  location: 'BRW-IB',
  issue_date: '2026-08-01',
  cited: false,
  po_number: 'ZZT-PO-0001',
  supplier_name: 'Dafuyuan',
  expected_date: '2026-09-01',
  qty_ordered: '15',
  qty_received: '0',
  already_tagged: '0',
  remaining: '15',
  covers: false,
  recommended: false,
  default_take: '15',
  claims: [],
};

const LATER: OrderInquiryPoCandidate = {
  kind: 'po',
  po_line_id: 'po-line-later',
  tier: 3,
  location: 'BRW',
  issue_date: '2026-08-05',
  cited: false,
  po_number: 'ZZT-PO-0002',
  supplier_name: 'Another Factory',
  expected_date: '2026-09-15',
  qty_ordered: '20',
  qty_received: '0',
  already_tagged: '0',
  remaining: '20',
  covers: true,
  recommended: false,
  default_take: '10',
  claims: [],
};

function renderDialog(node: React.ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

const onDone = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  getOrderInquiryPoCandidates.mockReset();
  placeOrderInquiryRowOnPoAllocations.mockReset();
});

import { typeBadInput } from '@/test-utils/badInput';

describe('LinkDocumentDialog bad number input (S8)', () => {
  it('a Take with bad input shows the sentence and Link posts nothing', async () => {
    getOrderInquiryPoCandidates.mockResolvedValue({ candidates: [EARLY, LATER], still_to_link: '25' });
    placeOrderInquiryRowOnPoAllocations.mockResolvedValue({});
    renderDialog(<LinkDocumentDialog rowId="row-1" itemCode="BASIN-001" qty="25" onDone={onDone} />);
    await screen.findByTestId('po-candidates-table');
    typeBadInput(screen.getByLabelText('Take off ZZT-PO-0001'));
    expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
    const link = screen.getByRole('button', { name: 'Link' });
    if (!(link as HTMLButtonElement).disabled) fireEvent.click(link);
    await waitFor(() => expect(placeOrderInquiryRowOnPoAllocations).not.toHaveBeenCalled());
  });
});
