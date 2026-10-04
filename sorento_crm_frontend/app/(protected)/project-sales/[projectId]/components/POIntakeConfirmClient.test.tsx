/**
 * P4 - the confirm screen end to end, one state per test.
 *
 * The states are the point. A queued extraction, a failed one, a SHORT one, an empty read and
 * a clean read all look different, and the short read is the one that must never be mistaken
 * for success. The confirm gate is tested where it lives: on the screen, beside the button,
 * rather than in a toast that disappears.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fakePdfJs } from '@/test-utils/fakePdfJs';
import type { POVersion, POVersionLine } from '../../_shared/types/poIntake.types';

vi.mock('@/components/common/pdf-viewer/pdfjs', async () =>
  (await import('@/test-utils/fakePdfJs')).fakePdfJsModule,
);

const push = vi.fn();
let originParam: string | null = null;
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => '/project-sales/p1/purchase-orders/v1',
  useSearchParams: () => ({ get: (key: string) => (key === 'from' ? originParam : null) }),
}));

// The shared DataGrid holds its skeleton rows until the column-preferences query settles, and
// under jsdom it never does.
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

vi.mock('@/app/(protected)/master-data-management/products/services/productService', () => ({
  getProductsForVariantSelect: vi.fn(async () => []),
}));

const getPOVersion = vi.fn();
const confirmPOVersion = vi.fn();
const approvePurchaseOrder = vi.fn();
const countersignPurchaseOrder = vi.fn();

vi.mock('../../_shared/services/poIntakeService', () => ({
  getPOVersion: (versionId: string) => getPOVersion(versionId),
  listPOVersions: vi.fn(async () => null),
  uploadPurchaseOrderDocument: vi.fn(),
  updatePOVersionLine: vi.fn(async () => null),
  updatePOVersionHeader: vi.fn(async () => null),
  confirmPOVersion: (versionId: string) => confirmPOVersion(versionId),
  approvePurchaseOrder: (poId: string) => approvePurchaseOrder(poId),
  countersignPurchaseOrder: (poId: string) => countersignPurchaseOrder(poId),
  acceptPOAnnotation: vi.fn(async () => {}),
  editPOAnnotation: vi.fn(async () => {}),
  rejectPOAnnotation: vi.fn(async () => {}),
}));

const getProject = vi.fn(async () => ({
  id: 'p1',
  project_code: 'PRJ-000001',
  title: 'Tuju Residences',
  outcome: 'open',
  can_edit: true,
}));

vi.mock('../../_shared/services/projectService', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../_shared/services/projectService')>();
  return { ...actual, getProject: () => getProject() };
});

import { POIntakeConfirmClient } from './POIntakeConfirmClient';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

function line(overrides: Partial<POVersionLine> = {}): POVersionLine {
  return {
    id: 'l1',
    line_no: 1,
    stock_code_raw: 'SRTWC8613-RL',
    description_raw: 'RIMLESS CLOSE COUPLED WC',
    qty: '927',
    uom_raw: 'SETS',
    unit_price: '392.85',
    amount: '364171.95',
    arithmetic_ok: true,
    is_cancelled: false,
    resolved_product_id: 'prod-1',
    resolved_product_code: 'SRTWC8613-RL',
    resolution_source: 'code',
    page_no: 1,
    ...overrides,
  };
}

function version(overrides: Partial<POVersion> = {}): POVersion {
  return {
    id: 'v1',
    purchase_order_id: 'po1',
    version_no: 1,
    extraction_state: 'done',
    extraction_error: null,
    extraction_model: 'gemini-2.5-flash',
    page_count: 10,
    document_url: 'https://example.test/po.pdf',
    header: {
      po_number: 'HQ/26/01/041',
      po_date: '2026-01-19',
      term_days: 60,
      sales_person: 'Ali Hassan',
      customer_order_ref: 'BUI/TR/2026/0114',
      admin_ref: 'PS26-0143',
      remark: null,
    },
    totals: {
      extracted_total: '364171.95',
      lines_total: '364171.95',
      arithmetic_passed: 1,
      arithmetic_total: 1,
    },
    lines: [line()],
    annotations: [],
    confirmed_at: null,
    ...overrides,
  };
}

function annotation(overrides = {}) {
  return {
    id: 'a1',
    page_no: 4,
    crop_url: null,
    raw_text: 'cancel item (7), refer to new P/O HQ/26/05/087',
    written_date: '15/5/26',
    refers_to_lines: [1],
    interpretation: 'cancel_line' as const,
    interpretation_json: { line_nos: [1] },
    state: 'proposed' as const,
    actioned_by_name: null,
    actioned_at: null,
    action_note: null,
    ...overrides,
  };
}

function renderConfirm() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <POIntakeConfirmClient projectId="p1" versionId="v1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fakePdfJs.reset();
  fakePdfJs.setNumPages(10);
  vi.clearAllMocks();
  originParam = null;
  getProject.mockResolvedValue({
    id: 'p1',
    project_code: 'PRJ-000001',
    title: 'Tuju Residences',
    outcome: 'open',
    can_edit: true,
  } as never);
});

describe('POIntakeConfirmClient', () => {
  it('shows a skeleton shaped like the screen it becomes', () => {
    getPOVersion.mockReturnValue(new Promise(() => {}));

    renderConfirm();

    expect(document.querySelectorAll('[data-slot="skeleton"]').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Lines/)).toBeNull();
  });

  it('says the document could not be loaded, with a way back', async () => {
    getPOVersion.mockRejectedValue(new Error('You do not have access to this project'));

    renderConfirm();

    expect(
      await screen.findByText(/This PO document could not be loaded/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText('You do not have access to this project'),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Back to the project/i })).toHaveAttribute(
      'href',
      '/project-sales/p1',
    );
  });

  it('reports a queued extraction honestly instead of blocking the screen', async () => {
    getPOVersion.mockResolvedValue(
      version({
        extraction_state: 'queued',
        lines: [],
        totals: {
          extracted_total: null,
          lines_total: '0.00',
          arithmetic_passed: 0,
          arithmetic_total: 0,
        },
      }),
    );

    renderConfirm();

    // Twice on purpose: as a pill in the header, and where the lines will be.
    expect(await screen.findAllByText('Waiting to be read')).toHaveLength(2);
    expect(screen.getByText(/This page updates itself/i)).toBeInTheDocument();
  });

  it('names the reason when extraction failed, and offers another upload', async () => {
    getPOVersion.mockResolvedValue(
      version({
        extraction_state: 'failed',
        extraction_error: 'Page 1 came back blank at 300 dpi.',
        lines: [],
      }),
    );

    renderConfirm();

    expect(
      await screen.findByText('This document could not be read'),
    ).toBeInTheDocument();
    expect(screen.getByText('Page 1 came back blank at 300 dpi.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Upload it again/i })).toBeInTheDocument();
  });

  it('does not let a SHORT read look like a finished one', async () => {
    getPOVersion.mockResolvedValue(
      version({
        pages_extracted: 7,
        failed_pages: [8, 9, 10],
        extraction_error: 'Pages 8, 9 and 10 could not be read (scan too dark).',
        totals: {
          extracted_total: '1810640.62',
          lines_total: '364171.95',
          arithmetic_passed: 1,
          arithmetic_total: 1,
        },
      }),
    );

    renderConfirm();

    expect(await screen.findByText('Only 7 of 10 pages were read')).toBeInTheDocument();
    expect(screen.getByText(/Pages 8, 9, 10 produced nothing/i)).toBeInTheDocument();
    // The money gap is still stated on its own, because that is what a person can act on.
    expect(
      screen.getByText(/RM 1,446,468\.67 below the total printed on the document/i),
    ).toBeInTheDocument();
  });

  it('says nothing can be confirmed from an empty read', async () => {
    getPOVersion.mockResolvedValue(
      version({
        lines: [],
        totals: {
          extracted_total: null,
          lines_total: '0.00',
          arithmetic_passed: 0,
          arithmetic_total: 0,
        },
      }),
    );

    renderConfirm();

    expect(
      await screen.findByText(/No lines came back from this document/i),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Confirm this PO/i })).toBeDisabled();
  });

  /**
   * S6: no Header card and no separate Findings card - a note naming a line lives on that
   * line, inline, in the Lines tab; the PDF lives alone on the Documents tab (owner hand test
   * 25 Sep 2026, item 4: the annotations grid that used to sit below it is gone), reached
   * with one click.
   */
  it('puts the header, the lines and each note on its own line on one screen', async () => {
    getPOVersion.mockResolvedValue(version({ annotations: [annotation()] }));
    const user = userEvent.setup();

    renderConfirm();

    expect(await screen.findByText('PO HQ/26/01/041 v1')).toBeInTheDocument();
    expect(screen.queryByLabelText('PO number')).toBeNull();
    expect(screen.getByLabelText('Quantity on line 1')).toHaveValue('927');
    // The note names line 1, so its compact indicator shows there, on the Lines tab -
    // owner hand test 25 Sep 2026, item 2: the text itself is one click away in a popover,
    // not always open under the row.
    const notesIndicator = screen.getByRole('button', {
      name: '1 note to review on line 1',
    });
    expect(screen.queryByText('cancel item (7), refer to new P/O HQ/26/05/087')).toBeNull();
    await user.click(notesIndicator);
    expect(
      screen.getByText('cancel item (7), refer to new P/O HQ/26/05/087'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Purchase order page 1' })).toBeNull();

    const documentsTab = screen.getByRole('tab', { name: 'Documents' });
    documentsTab.focus();
    fireEvent.click(documentsTab);

    expect(
      await screen.findByRole('group', { name: 'Purchase order page 1' }),
    ).toBeInTheDocument();
    expect(screen.queryByText('No document-level notes')).toBeNull();
    expect(screen.queryByRole('columnheader', { name: 'State' })).toBeNull();
    expect(screen.queryByLabelText('Quantity on line 1')).toBeNull();
  });

  it('moves the scan to the page a note was written on, and to the Documents tab that shows it', async () => {
    getPOVersion.mockResolvedValue(version({ annotations: [annotation()] }));
    const user = userEvent.setup();

    renderConfirm();

    await user.click(
      await screen.findByRole('button', { name: '1 note to review on line 1' }),
    );
    expect(screen.queryByRole('group', { name: /Purchase order page/ })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Page 4' }));

    expect(
      await screen.findByRole('group', { name: 'Purchase order page 4' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Page 4 of 10')).toBeInTheDocument();
  });

  /**
   * The read takes minutes and the person waiting deserves to be told how long it took, but
   * the version response carries no elapsed time yet (nothing on `GET /api/v1/project-sales`
   * returns it). Until it does, the screen says nothing rather than inventing a number, and
   * the spinner is never accompanied by a duration.
   */
  it('never shows a duration beside the spinner while the document is still being read', async () => {
    getPOVersion.mockResolvedValue(
      version({ extraction_state: 'running', lines: [], annotations: [] }),
    );

    renderConfirm();

    expect(await screen.findByText('Reading the document')).toBeInTheDocument();
    expect(screen.getByText('Being read')).toBeInTheDocument();
    expect(screen.queryByText(/read in/i)).toBeNull();
  });

  /**
   * B3 (19 Aug follow-up): pages land as they finish rather than all at once at the
   * end, so the progress card counts up instead of sitting on a static page total for
   * the whole read.
   */
  it('shows which page is being read as pages land, not just the total', async () => {
    getPOVersion.mockResolvedValue(
      version({
        extraction_state: 'running',
        pages_extracted: 3,
        lines: [],
        annotations: [],
      }),
    );

    renderConfirm();

    expect(await screen.findByText('Reading the document')).toBeInTheDocument();
    expect(screen.getByText('Page 4 of 10')).toBeInTheDocument();
  });

  it('shows the exceptions on the lines card, once, and still reaches them', async () => {
    getPOVersion.mockResolvedValue(
      version({
        lines: [line(), line({ id: 'l2', line_no: 2, amount: '1.00', arithmetic_ok: false })],
        totals: {
          extracted_total: '728343.90',
          lines_total: '364172.95',
          arithmetic_passed: 1,
          arithmetic_total: 2,
        },
      }),
    );

    renderConfirm();

    expect(
      await screen.findByRole('radio', { name: 'Need attention (1)' }),
    ).toBeInTheDocument();
    // The old count in the card header is gone: two numbers on one card teaches people to
    // read neither.
    expect(screen.queryByText('1 of 2 multiply out')).toBeNull();

    // The mismatch sentence on the meta line is itself the jump-to-problem action now (S6-1);
    // there is no separate banner or button for it.
    fireEvent.click(
      screen.getByRole('button', { name: /below the total printed on the document/i }),
    );
    expect(screen.getByLabelText('Amount on line 2')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: /Need attention \(1\)/ }));
    expect(screen.getByLabelText('Amount on line 2')).toBeInTheDocument();
    expect(screen.queryByLabelText('Amount on line 1')).toBeNull();
  });

  it('still reaches the problem line when the mismatch link is clicked from the Documents tab (Nit)', async () => {
    getPOVersion.mockResolvedValue(
      version({
        lines: [line(), line({ id: 'l2', line_no: 2, amount: '1.00', arithmetic_ok: false })],
        totals: {
          extracted_total: '728343.90',
          lines_total: '364172.95',
          arithmetic_passed: 1,
          arithmetic_total: 2,
        },
      }),
    );

    renderConfirm();

    const documentsTab = await screen.findByRole('tab', { name: 'Documents' });
    documentsTab.focus();
    fireEvent.click(documentsTab);

    await screen.findByRole('group', { name: 'Purchase order page 1' });

    fireEvent.click(
      screen.getByRole('button', { name: /below the total printed on the document/i }),
    );

    // Visibility of the row alone would pass on the default filter; "Line 2 in focus" only
    // renders once focusLine actually ran.
    expect(await screen.findByText('Line 2 in focus')).toBeInTheDocument();
  });

  it('names an accepted cancellation as the reason for the gap, not a mismatch (S7)', async () => {
    getPOVersion.mockResolvedValue(
      version({
        lines: [
          line(),
          line({
            id: 'l2',
            line_no: 7,
            stock_code_raw: 'SRTFV1001',
            amount: '4733.60',
            is_cancelled: true,
          }),
        ],
        totals: {
          extracted_total: '368905.55',
          lines_total: '364171.95',
          arithmetic_passed: 2,
          arithmetic_total: 2,
        },
      }),
    );

    renderConfirm();

    expect(
      await screen.findByRole('button', { name: /RM 4,733\.60 short, 1 cancelled line/i }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/below the total printed on the document/i)).toBeNull();
  });

  it('refuses to confirm while a note is unreviewed, and "Review them" reaches its line', async () => {
    getPOVersion.mockResolvedValue(version({ annotations: [annotation()] }));

    renderConfirm();

    expect(await screen.findByRole('button', { name: /Confirm this PO/i })).toBeDisabled();
    expect(
      screen.getByText('1 handwritten note still unreviewed'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Review them/i }));

    // The note names line 1, so "Review them" lands on the line, not on the (empty)
    // document-level card below.
    expect(screen.getByText('Line 1 in focus')).toBeInTheDocument();
  });

  /**
   * Owner hand test 25 Sep 2026, item 4: the annotations grid (the only surface a
   * document-level note ever had) is gone. A note naming no line - a signature, "Continue
   * To Next Page", delivery instructions - has nowhere left to be reviewed, so it no longer
   * blocks Confirm and is not counted in the header's unreviewed tally. A note naming a line
   * still blocks Confirm exactly as before, through the row's own indicator.
   */
  it('does not let an unreviewed note naming no line block Confirm (F4)', async () => {
    getPOVersion.mockResolvedValue(
      version({ annotations: [annotation({ id: 'doc-note', refers_to_lines: [] })] }),
    );

    renderConfirm();

    expect(await screen.findByRole('button', { name: /Confirm this PO/i })).toBeEnabled();
    expect(screen.queryByText(/handwritten notes? still unreviewed/)).toBeNull();
    expect(screen.queryByRole('button', { name: /Review them/i })).toBeNull();
  });

  /**
   * Owner re-test 25 Sep 2026: the server refused Confirm over notes this screen did not
   * show. N notes naming K lines: the header counts N, "Need attention" lists every one of
   * the K lines, and a note naming a line this document does not have counts nowhere (the
   * server's `blocking_annotations` is the same rule).
   */
  it('counts every blocking note and lists each of its lines under Need attention', async () => {
    const clean = (n: number) =>
      line({ id: `l${n}`, line_no: n, stock_code_raw: `CLEAN-${n}`, description_raw: `Clean ${n}` });
    getPOVersion.mockResolvedValue(
      version({
        lines: [1, 2, 3, 4, 5, 6, 7, 8].map(clean),
        annotations: [
          annotation({ id: 'n1', refers_to_lines: [2] }),
          annotation({ id: 'n2', refers_to_lines: [5] }),
          annotation({ id: 'n3', refers_to_lines: [5], raw_text: 'second note on 5' }),
          annotation({ id: 'n4', refers_to_lines: [7] }),
          annotation({ id: 'ghost', refers_to_lines: [99] }),
          annotation({ id: 'doc', refers_to_lines: [] }),
        ],
      }),
    );

    renderConfirm();

    expect(await screen.findByRole('button', { name: /Confirm this PO/i })).toBeDisabled();
    expect(screen.getByText('4 handwritten notes still unreviewed')).toBeInTheDocument();
    expect(screen.getByText('Need attention (3)')).toBeInTheDocument();
    for (const n of [2, 5, 7]) expect(screen.getByDisplayValue(`CLEAN-${n}`)).toBeInTheDocument();
    for (const n of [1, 3, 4, 6, 8]) expect(screen.queryByDisplayValue(`CLEAN-${n}`)).toBeNull();
  });

  it('confirms once every note has been looked at', async () => {
    getPOVersion.mockResolvedValue(
      version({
        annotations: [
          annotation({ state: 'accepted', actioned_by_name: 'CONTACT AH Abdullah' }),
        ],
      }),
    );

    renderConfirm();

    const confirmButton = await screen.findByRole('button', { name: /Confirm this PO/i });
    expect(confirmButton).toBeEnabled();
    fireEvent.click(confirmButton);
    await waitFor(() => expect(confirmPOVersion).toHaveBeenCalledWith('v1'));
  });

  it('returns to the origin after a successful Confirm, when the review page carries one (S4-2)', async () => {
    originParam = '/project-sales/pipeline?from=p1';
    getPOVersion.mockResolvedValue(
      version({
        annotations: [annotation({ state: 'accepted', actioned_by_name: 'CONTACT AH Abdullah' })],
      }),
    );
    confirmPOVersion.mockResolvedValue(version());

    renderConfirm();

    fireEvent.click(await screen.findByRole('button', { name: /Confirm this PO/i }));

    await waitFor(() => expect(push).toHaveBeenCalledWith('/project-sales/pipeline?from=p1'));
  });

  it('stays on the page after Confirm with no origin, a deep link or a bookmark (S4-3)', async () => {
    originParam = null;
    getPOVersion.mockResolvedValue(
      version({
        annotations: [annotation({ state: 'accepted', actioned_by_name: 'CONTACT AH Abdullah' })],
      }),
    );
    confirmPOVersion.mockResolvedValue(version());

    renderConfirm();

    fireEvent.click(await screen.findByRole('button', { name: /Confirm this PO/i }));

    await waitFor(() => expect(confirmPOVersion).toHaveBeenCalledWith('v1'));
    expect(push).not.toHaveBeenCalled();
  });

  it('shows confirm, approve and countersign as three stamps, absent ones included', async () => {
    getPOVersion.mockResolvedValue(
      version({
        confirmed_at: '2026-05-15T03:02:00',
        confirmed_by_name: 'CONTACT AH Abdullah',
      }),
    );

    renderConfirm();

    expect(await screen.findByText(/CONTACT AH Abdullah/)).toBeInTheDocument();
    // Approved and Countersigned are still rendered, as "Not yet" rather than hidden.
    expect(screen.getByText('Approved')).toBeInTheDocument();
    expect(screen.getByText('Countersigned')).toBeInTheDocument();
    expect(screen.getAllByText('Not yet')).toHaveLength(2);
    const approve = screen.getByRole('button', { name: /^Approve$/ });
    expect(approve).toBeInTheDocument();
    // S3: the next step is the one primary button, not an outline button.
    expect(approve.className).not.toMatch(/\bborder-input\b/);
  });

  it('renders the status trail as Badge pills, not a hand-rolled coloured span (S4)', async () => {
    getPOVersion.mockResolvedValue(
      version({
        confirmed_at: '2026-05-15T03:02:00',
        confirmed_by_name: 'CONTACT AH Abdullah',
        purchase_order: {
          po_number: 'HQ/26/01/041',
          status: 'approved',
          approved_by_name: 'CONTACT AH Abdullah',
          approved_at: '2026-05-15T03:05:00',
          countersigned_by_name: null,
          countersigned_at: null,
        },
      }),
    );

    renderConfirm();

    // "Confirmed" appears twice - the header StatusPill and the trail - so the trail's
    // own pill is the last one.
    const confirmedPills = await screen.findAllByText('Confirmed');
    const confirmedBadge = confirmedPills[confirmedPills.length - 1].closest('[data-slot="badge"]');
    expect(confirmedBadge).not.toBeNull();
    expect(confirmedBadge?.className).not.toMatch(/border-emerald/);

    const approvedBadge = screen.getByText('Approved').closest('[data-slot="badge"]');
    expect(approvedBadge).not.toBeNull();
    expect(approvedBadge?.className).not.toMatch(/border-emerald/);
  });

  it('offers countersign only once the PO is approved, and stops editing after confirm', async () => {
    getPOVersion.mockResolvedValue(
      version({
        confirmed_at: '2026-05-15T03:02:00',
        confirmed_by_name: 'CONTACT AH Abdullah',
        purchase_order: {
          po_number: 'HQ/26/01/041',
          status: 'approved',
          approved_by_name: 'CONTACT AH Abdullah',
          approved_at: '2026-05-15T03:05:00',
          countersigned_by_name: null,
          countersigned_at: null,
        },
      }),
    );

    renderConfirm();

    const countersign = await screen.findByRole('button', { name: /Countersign/i });
    // S3: the next step is the one primary button, not an outline button.
    expect(countersign.className).not.toMatch(/\bborder-input\b/);
    fireEvent.click(countersign);
    await waitFor(() => expect(countersignPurchaseOrder).toHaveBeenCalledWith('po1'));

    expect(screen.queryByLabelText('Quantity on line 1')).toBeNull();
    expect(screen.queryByRole('button', { name: /Confirm this PO/i })).toBeNull();
  });

  it('offers a reader no write affordance at all', async () => {
    getProject.mockResolvedValue({
      id: 'p1',
      project_code: 'PRJ-000001',
      title: 'Tuju Residences',
      outcome: 'open',
      can_edit: false,
    } as never);
    getPOVersion.mockResolvedValue(version({ annotations: [annotation()] }));

    renderConfirm();

    expect(await screen.findByText('Read only')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Confirm this PO/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Accept$/ })).toBeNull();
    expect(screen.queryByLabelText('Quantity on line 1')).toBeNull();
  });

  /** S6-1: no "Back to the project" link anywhere on the steady-state screen. */
  it('carries no "Back to the project" link once the version has loaded', async () => {
    getPOVersion.mockResolvedValue(version());

    renderConfirm();

    await screen.findByText('PO HQ/26/01/041 v1');
    expect(screen.queryByRole('link', { name: /Back to the project/i })).toBeNull();
  });

  /** S6-2: exactly two tabs, Lines and Documents - no Findings, no Header. */
  it('carries exactly two tabs, Lines and Documents, never a Findings or Header tab', async () => {
    getPOVersion.mockResolvedValue(version());

    renderConfirm();

    await screen.findByText('PO HQ/26/01/041 v1');
    expect(screen.getAllByRole('tab')).toHaveLength(2);
    expect(screen.getByRole('tab', { name: /^Lines/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Documents' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Findings/i })).toBeNull();
    expect(screen.queryByRole('tab', { name: /Header/i })).toBeNull();
  });

  /**
   * Owner hand test 25 Sep 2026, item 4: "just show me the entire document" - the PDF viewer
   * takes the Documents tab's full height, not a short strip, with every page scrollable.
   */
  it('gives the PDF viewer the Documents tab\'s full height, not a short strip', async () => {
    getPOVersion.mockResolvedValue(version());

    renderConfirm();

    await screen.findByText('PO HQ/26/01/041 v1');
    const documentsTab = screen.getByRole('tab', { name: 'Documents' });
    documentsTab.focus();
    fireEvent.click(documentsTab);

    const viewer = await screen.findByRole('group', { name: 'Purchase order viewer' });
    // No short fixed-vh strip left on the viewer - it fills whatever height its tab gives it.
    expect(viewer.className).not.toMatch(/h-\[45vh\]/);
    expect(viewer.className).toMatch(/\bflex-1\b/);
    const tabPanel = viewer.closest('[role="tabpanel"]');
    expect(tabPanel).not.toBeNull();
    expect(tabPanel!.className).toMatch(/h-\[calc\(100dvh-14rem\)\]/);
  });

  /**
   * S6-5/R13: a version with no stored document renders the plain empty state instead of
   * POIntakeDocumentViewer's own "not available to preview" fallback - never an error code.
   */
  it('shows the R13 empty state on Documents when the PDF cannot be found, with an upload action', async () => {
    getPOVersion.mockResolvedValue(version({ document_url: null }));

    renderConfirm();

    await screen.findByText('PO HQ/26/01/041 v1');
    const documentsTab = screen.getByRole('tab', { name: 'Documents' });
    documentsTab.focus();
    fireEvent.click(documentsTab);

    expect(await screen.findByText('This PDF is not available yet')).toBeInTheDocument();
    expect(
      screen.getByText('The source file has not finished uploading, or could not be found.'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/404/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Upload the PO again' }));
    expect(
      screen.getByText('Upload a new document for HQ/26/01/041'),
    ).toBeInTheDocument();
  });

  it('does not offer to upload again from the R13 empty state without edit rights', async () => {
    getProject.mockResolvedValue({
      id: 'p1',
      project_code: 'PRJ-000001',
      title: 'Tuju Residences',
      outcome: 'open',
      can_edit: false,
    } as never);
    getPOVersion.mockResolvedValue(version({ document_url: null }));

    renderConfirm();

    await screen.findByText('PO HQ/26/01/041 v1');
    const documentsTab = screen.getByRole('tab', { name: 'Documents' });
    documentsTab.focus();
    fireEvent.click(documentsTab);

    expect(await screen.findByText('This PDF is not available yet')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Upload the PO again' })).toBeNull();
  });
});
