/**
 * SR-CORE portal form, red-first (Phase 2). Component: components/SampleRequestForm.tsx
 * (default export, props { slug, requestId? }).
 *
 * UI hooks this file fixes for the coder, because the mock names no markup:
 *  - the line product picker is a SearchableSelect whose trigger has the accessible name
 *    "Product" (role=combobox);
 *  - every line renders one element with data-testid="sample-request-line";
 *  - section headers are the collapsible FormSection triggers, in the order named below.
 *
 * Service module: app/(auth)/portal/lib/sample-request-service.ts (beside the price tag one).
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock('@/lib/toast', () => ({
  toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
}));

vi.mock('../lib/sample-request-service', () => ({
  searchSampleRequestProducts: vi.fn(async () => []),
  createSampleRequest: vi.fn(),
  updateSampleRequest: vi.fn(),
  submitSampleRequest: vi.fn(),
  getSampleRequest: vi.fn(),
}));

vi.mock('../lib/portal-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/portal-client')>();
  return {
    ...actual,
    lookupDebtors: vi.fn(async () => []),
    lookupProjects: vi.fn(async () => []),
    fetchSubmissionNeighbours: vi.fn(async () => ({ prev: null, next: null })),
  };
});

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: () => null,
}));

vi.mock('@/components/common/DetailActionsMenu', () => ({
  __esModule: true,
  DetailActionsMenu: () => null,
}));

import {
  createSampleRequest,
  getSampleRequest,
  searchSampleRequestProducts,
  submitSampleRequest,
} from '../lib/sample-request-service';
import SampleRequestForm from './SampleRequestForm';

const asMock = (fn: unknown) => fn as ReturnType<typeof vi.fn>;

function renderForm(props: { requestId?: string } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SampleRequestForm slug="zzt-space" {...props} />
    </QueryClientProvider>,
  );
}

const openProductPicker = () =>
  fireEvent.click(screen.getByRole('combobox', { name: /product/i }));
const typeInPicker = (text: string) =>
  fireEvent.change(screen.getByPlaceholderText('Search...'), { target: { value: text } });
const lineRows = () => document.querySelectorAll('[data-testid="sample-request-line"]');

beforeEach(() => {
  vi.clearAllMocks();
  asMock(searchSampleRequestProducts).mockResolvedValue([]);
});

describe('SampleRequestForm sections', () => {
  it('renders Customer & project, Lines, Delivery, Additional Information in that order', async () => {
    renderForm();
    const titles = ['Customer & project', 'Lines', 'Delivery', 'Additional Information'];
    const nodes = await Promise.all(titles.map((t) => screen.findByText(t)));
    for (let i = 0; i < nodes.length - 1; i += 1) {
      expect(
        nodes[i].compareDocumentPosition(nodes[i + 1]) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    }
  });
});

describe('AC-SR-12 product not in the list', () => {
  it('shows the card sentence and adds no line', async () => {
    renderForm();
    openProductPicker();
    typeInPicker('B2155-BLUE-NL');

    expect(
      await screen.findByText(
        'B2155-BLUE-NL is not in the product list. Pick the product you mean, or ask purchasing to add it as a new product before you request it.',
      ),
    ).toBeTruthy();
    expect(searchSampleRequestProducts).toHaveBeenCalledWith('B2155-BLUE-NL');
    expect(lineRows().length).toBe(0);
  });
});

describe('AC-SR-11 nothing is preselected', () => {
  it('lists the hit but adds no line until it is clicked', async () => {
    asMock(searchSampleRequestProducts).mockResolvedValue([
      { id: 'p-1', product_code: 'TPE-9101', product_name: 'ZZT Tape', brand_name: 'ZZT' },
    ]);
    renderForm();
    openProductPicker();
    typeInPicker('tpe9101');

    await waitFor(() =>
      expect(document.querySelectorAll('[role="option"]').length).toBeGreaterThan(0),
    );
    expect(lineRows().length).toBe(0);

    fireEvent.click(document.querySelectorAll('[role="option"]')[0]);
    await waitFor(() => expect(lineRows().length).toBe(1));
  });
});

describe('AC-SR-02 submit names the missing field', () => {
  it('shows a delivery mode error and does not call the service', async () => {
    renderForm();
    fireEvent.click(await screen.findByRole('button', { name: /^submit$/i }));

    expect(await screen.findByText(/choose a delivery mode/i)).toBeTruthy();
    expect(createSampleRequest).not.toHaveBeenCalled();
    expect(submitSampleRequest).not.toHaveBeenCalled();
  });
});

describe('read-only view of an existing request', () => {
  it('shows the request number and status for a submitted request', async () => {
    asMock(getSampleRequest).mockResolvedValue({
      id: 'sr-1',
      request_no: 'SR-000007',
      status: 'submitted',
      status_label: 'Submitted',
      lines: [],
      board_mounting: false,
    });
    renderForm({ requestId: 'sr-1' });
    expect(await screen.findByText('SR-000007')).toBeTruthy();
    expect(screen.getByText('Submitted')).toBeTruthy();
    expect(getSampleRequest).toHaveBeenCalledWith('sr-1');
  });

  it('prints the created date the way staff screens and the PDF do (dd/mm/yyyy, Malaysia day)', async () => {
    // A naive UTC stamp from the API: 15 Oct 23:30 UTC is already 16 Oct in Malaysia.
    asMock(getSampleRequest).mockResolvedValue({
      id: 'sr-2',
      request_no: 'SR-000008',
      status: 'submitted',
      status_label: 'Submitted',
      created_at: '2026-10-15T23:30:00',
      lines: [],
      board_mounting: false,
    });
    renderForm({ requestId: 'sr-2' });
    expect(await screen.findByText('Created 16/10/2026')).toBeTruthy();
  });
});
