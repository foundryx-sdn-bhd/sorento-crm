/**
 * S2 - QuotationsPanel, now a list of quotation DOCUMENTS.
 *
 * What is worth pinning is that a document is readable WITHOUT opening it: the reference the
 * customer quotes back, what it is for, who it went to, whether it has been issued and what it
 * is worth - plus the footer that adds those values up, which is the number a sales manager
 * comes to this tab for. The price-floor guardrail stays on the toolbar, counted across the
 * project's scopes.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { QuotationDocument } from '../../_shared/services/quotationDocumentService';
import type { Project, ProjectQuotation } from '../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const listQuotationDocuments = vi.fn();
const createQuotationDocument = vi.fn();
const listQuotations = vi.fn();
const push = vi.fn();
const createPendingAction = vi.fn();

// The deferred Delete (F6/F7): the row menu and the bulk strip park a server-side action.
vi.mock('@/services/pendingActionService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/pendingActionService')>();
  return {
    ...actual,
    createPendingAction: (...args: unknown[]) => createPendingAction(...args),
    cancelPendingAction: vi.fn(),
    getCurrentPendingAction: vi.fn(async () => ({ pending: null, last_outcome: null })),
  };
});
vi.mock('@/components/common/deferredToast', () => ({
  deferredToast: (input: { id?: string }) => input.id ?? 'toast',
  dismissDeferredToast: vi.fn(),
}));
const toastCalls: unknown[][] = [];
vi.mock('@/lib/toast', () => {
  const record = (...args: unknown[]) => {
    toastCalls.push(args);
    return 'toast';
  };
  return { toast: { success: record, error: record, warning: record, custom: record, dismiss: vi.fn() } };
});
// The list gates Delete on the caller's grants; this suite is about the list, not the grants.
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => true,
  useHasAnyPermission: () => true,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => '/project-sales/p1',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  // Without this the grid never leaves its skeleton: the real hook fetches saved column
  // order and `isLoading` gates the body rows, and nothing answers that call under jsdom.
  useListingColumnPreferences: () => ({ resetToDefaults: async () => {}, isLoading: false }),
}));

vi.mock('../../_shared/services/quotationDocumentService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../_shared/services/quotationDocumentService')
  >();
  return {
    ...actual,
    listQuotationDocuments: (...args: unknown[]) => listQuotationDocuments(...args),
    createQuotationDocument: (...args: unknown[]) => createQuotationDocument(...args),
  };
});

vi.mock('../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../_shared/services/projectService')
  >();
  return {
    ...actual,
    listQuotations: (...args: unknown[]) => listQuotations(...args),
  };
});

import { QuotationsPanel } from './QuotationsPanel';

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

function scope(overrides: Partial<ProjectQuotation> = {}): ProjectQuotation {
  return {
    id: 'q1',
    project_id: 'p1',
    scope_label: 'House Units',
    outcome: 'open',
    version_count: 1,
    current_version_id: 'v1',
    current_version_no: 1,
    current_total: '12000.00',
    below_floor_count: 0,
    non_standard_count: 0,
    line_count: 0,
    ...overrides,
  };
}

function quotationDocument(overrides: Partial<QuotationDocument> = {}): QuotationDocument {
  return {
    id: 'd1',
    project_id: 'p1',
    document_no: 'SRT/Q/2026/0141',
    our_ref: 'SRT/Q/2026/0141 (R2)',
    your_ref: null,
    doc_date: '2026-02-26',
    recipient_party_id: null,
    recipient_name_snapshot: 'Nadi Cergas Sdn Bhd',
    recipient_address_snapshot: null,
    recipient_phone_snapshot: null,
    attn_name: 'Kelly',
    subject_title: 'CADANGAN MEMBINA PANGSAPURI RUMAH IDAM',
    cover_letter_html: null,
    terms_html: null,
    signatory_name: null,
    signatory_phone: null,
    scopes: [],
    grand_total: '696923.00',
    issue_count: 2,
    current_issue_no: 2,
    is_issued: true,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

function renderPanel(overrides: Partial<Project> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <QuotationsPanel project={project(overrides)} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  toastCalls.length = 0;
  listQuotationDocuments.mockResolvedValue([]);
  listQuotations.mockResolvedValue([]);
  createPendingAction.mockImplementation(async (input: { entityId: string }) => ({
    id: `pa-${input.entityId}`,
    action_key: 'quotation_document.delete',
    entity_type: 'quotation_document',
    entity_id: input.entityId,
    commit_at: new Date(Date.now() + 10_000).toISOString().replace(/\.\d+Z$/, ''),
    window_seconds: 10,
  }));
});

describe('QuotationsPanel', () => {
  it('offers the first quotation when nothing is quoted', async () => {
    renderPanel();

    expect(await screen.findByText(/Nothing quoted yet/i)).toBeInTheDocument();
    // ONE way in, in the toolbar (ADR 1d): a second button in the middle of an empty state is
    // another thing to read and decide between.
    expect(screen.getByRole('button', { name: /Add a quotation/i })).toBeInTheDocument();
  });

  it('reads a document without opening it: reference, subject, recipient, status, date', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);

    renderPanel();

    // The REFERENCE the customer quotes back, revision included - not the document number on
    // its own, which is a different string once anything has been issued.
    expect(await screen.findByText('SRT/Q/2026/0141 (R2)')).toBeInTheDocument();
    expect(screen.getByText('CADANGAN MEMBINA PANGSAPURI RUMAH IDAM')).toBeInTheDocument();
    expect(screen.getByText('Nadi Cergas Sdn Bhd')).toBeInTheDocument();
    expect(screen.getByText('Issued')).toBeInTheDocument();
    expect(screen.getByText('26/02/2026')).toBeInTheDocument();
    // No UUID reaches the screen.
    expect(screen.queryByText('d1')).not.toBeInTheDocument();
  });

  it('says Draft on a document nobody has issued', async () => {
    listQuotationDocuments.mockResolvedValue([
      quotationDocument({
        our_ref: 'SRT/Q/2026/0142',
        issue_count: 0,
        current_issue_no: null,
        is_issued: false,
      }),
    ]);

    renderPanel();

    expect(await screen.findByText('Draft')).toBeInTheDocument();
  });

  /**
   * S17 on the list. The client asked "when i request changes, how can i see it from the
   * system?", and a salesperson scanning this tab is the second place that question gets asked:
   * the row has to say which quotation is waiting on THEM without being opened.
   *
   * It rides the Status column rather than a new one, because "the customer asked for changes"
   * IS where the quotation stands - a second column would be mostly empty and would push the
   * money off a 375px screen.
   */
  it('says Changes requested on the row, so a scan finds the one waiting on you', async () => {
    listQuotationDocuments.mockResolvedValue([
      quotationDocument({
        customer_decision: 'changes_requested',
        changes_requested_at: '2026-08-06T02:15:00',
        changes_requested_note: 'can you provide me more discount',
        changes_requested_by_name: 'Kelly',
      }),
    ]);

    renderPanel();

    expect(await screen.findByText('Changes requested')).toBeInTheDocument();
    // It replaces Issued rather than sitting beside it: an issued quotation the customer has
    // answered is not still "Issued, awaiting", it is answered.
    expect(screen.queryByText('Issued')).toBeNull();
  });

  it('says Accepted, and acceptance wins over an older request', async () => {
    listQuotationDocuments.mockResolvedValue([
      quotationDocument({
        customer_decision: 'accepted',
        accepted_at: '2026-08-06T04:00:00',
        changes_requested_at: '2026-08-06T02:15:00',
        changes_requested_note: 'can you provide me more discount',
      }),
    ]);

    renderPanel();

    expect(await screen.findByText('Accepted')).toBeInTheDocument();
    expect(screen.queryByText('Changes requested')).toBeNull();
  });

  it('leaves a quotation nobody has answered reading Issued', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument({ customer_decision: null })]);

    renderPanel();

    expect(await screen.findByText('Issued')).toBeInTheDocument();
    expect(screen.queryByText('Changes requested')).toBeNull();
    expect(screen.queryByText('Accepted')).toBeNull();
  });

  it('adds up the values of the documents on the page', async () => {
    listQuotationDocuments.mockResolvedValue([
      quotationDocument({ id: 'd1', grand_total: '696923.00' }),
      quotationDocument({
        id: 'd2',
        our_ref: 'SRT/Q/2026/0142',
        document_no: 'SRT/Q/2026/0142',
        grand_total: '1250.50',
      }),
    ]);

    renderPanel();

    expect(await screen.findByText('RM 696,923.00')).toBeInTheDocument();
    expect(screen.getByText('RM 1,250.50')).toBeInTheDocument();
    // Summed to the cent off the strings, in the footer under the column it sums.
    expect(screen.getByText('RM 698,173.50')).toBeInTheDocument();
  });

  it('F1: renders no below-the-price-floor or non-standard chips in the tab', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);
    listQuotations.mockResolvedValue([
      scope({ id: 'q1', below_floor_count: 2, non_standard_count: 1 }),
      scope({ id: 'q2', below_floor_count: 1, non_standard_count: 3 }),
    ]);

    renderPanel();

    expect(await screen.findByText('SRT/Q/2026/0141 (R2)')).toBeInTheDocument();
    expect(screen.queryByText(/below the price floor/i)).toBeNull();
    expect(screen.queryByText(/non-standard/i)).toBeNull();
  });

  it('opens the document when the row is clicked', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);

    renderPanel();

    fireEvent.click(await screen.findByText('Nadi Cergas Sdn Bhd'));

    expect(push).toHaveBeenCalledWith('/project-sales/p1/quotation-documents/d1');
  });

  it('hides every write affordance on a project the user cannot edit', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);

    renderPanel({ can_edit: false });

    expect(await screen.findByText('SRT/Q/2026/0141 (R2)')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Add a quotation/i })).toBeNull();
  });

  it('AC-QF001: Add a quotation opens the form page and creates nothing', async () => {
    listQuotationDocuments.mockResolvedValue([]);

    renderPanel();

    fireEvent.click(await screen.findByRole('button', { name: /Add a quotation/i }));

    // The owner: "I should be able to add product straight away and save when I am
    // satisfied". The press is a navigation, never a write.
    expect(push).toHaveBeenCalledWith('/project-sales/p1/quotation-documents/new');
    expect(createQuotationDocument).not.toHaveBeenCalled();
  });
});


/** The row "..." menu. Radix opens on pointerdown, which jsdom does not synthesize from click. */
async function openRowMenu() {
  const trigger = await screen.findByRole('button', { name: /quotation actions/i });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: 'ArrowDown', code: 'ArrowDown' });
  return screen.findByRole('menu');
}

function draftDocument(overrides: Partial<QuotationDocument> = {}) {
  return quotationDocument({
    id: 'd-draft',
    our_ref: 'SRT/Q/2026/0150',
    document_no: 'SRT/Q/2026/0150',
    issue_count: 0,
    current_issue_no: null,
    is_issued: false,
    ...overrides,
  });
}

describe('QuotationsPanel: the standard list (QUOTE-APPROVAL-UX F2-F8)', () => {
  it('F2: the app list toolbar - search box first, then Columns, then Add a quotation on the right', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);
    const { container } = renderPanel();

    const search = await screen.findByRole('searchbox', { name: 'Search quotations' });
    const columns = screen.getByRole('button', { name: /Columns/i });
    const add = screen.getByRole('button', { name: /Add a quotation/i });

    expect(container.querySelector('[data-slot="data-grid-list-toolbar"]')).not.toBeNull();
    const follows = (a: Element, b: Element) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(follows(search, columns)).toBe(true);
    expect(follows(columns, add)).toBe(true);
  });

  it('F3: every row carries a tickbox; ticking shows "1 selected" with Delete and Export', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument(), draftDocument()]);
    renderPanel();

    const row = (await screen.findByText('SRT/Q/2026/0150')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('checkbox'));

    expect(await screen.findByText('1 selected')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Export' })).toBeEnabled();
  });

  it('F4: the row "..." menu carries the record gear action set', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);
    renderPanel();

    const menu = await openRowMenu();

    for (const name of [
      'Edit quotation',
      'Sign quotation',
      'Download PDF',
      'Download Excel',
      'Copy counter-sign link',
      'Delete quotation',
    ]) {
      expect(within(menu).getByRole('menuitem', { name: new RegExp(name) })).toBeInTheDocument();
    }
  });

  it('F4: items the record disables are disabled here too (a draft has nothing to download or copy)', async () => {
    listQuotationDocuments.mockResolvedValue([draftDocument()]);
    renderPanel();

    const menu = await openRowMenu();

    expect(within(menu).getByRole('menuitem', { name: /Download PDF/ })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(within(menu).getByRole('menuitem', { name: /Download Excel/ })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(
      within(menu).getByRole('menuitem', { name: /Copy counter-sign link/ }),
    ).toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('menuitem', { name: /Edit quotation/ })).not.toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('F5: the row body opens the quotation; the tickbox and the menu do not', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);
    renderPanel();

    const row = (await screen.findByText('Nadi Cergas Sdn Bhd')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('checkbox'));
    fireEvent.click(within(row).getByRole('button', { name: /quotation actions/i }));
    expect(push).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText('Nadi Cergas Sdn Bhd'));
    expect(push).toHaveBeenCalledWith('/project-sales/p1/quotation-documents/d1');
  });

  it('F7: row-menu Delete parks the deferred action and opens no confirmation dialog', async () => {
    listQuotationDocuments.mockResolvedValue([draftDocument()]);
    renderPanel();

    const menu = await openRowMenu();
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Delete quotation/ }));

    await waitFor(() => expect(createPendingAction).toHaveBeenCalledTimes(1));
    expect(createPendingAction).toHaveBeenCalledWith(
      expect.objectContaining({
        actionKey: 'quotation_document.delete',
        entityType: 'quotation_document',
        entityId: 'd-draft',
      }),
    );
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('F7: bulk-strip Delete parks one deferred action per ticked row, with no dialog', async () => {
    listQuotationDocuments.mockResolvedValue([
      draftDocument({ id: 'd-a', our_ref: 'SRT/Q/2026/0151', document_no: 'SRT/Q/2026/0151' }),
      draftDocument({ id: 'd-b', our_ref: 'SRT/Q/2026/0152', document_no: 'SRT/Q/2026/0152' }),
    ]);
    renderPanel();

    await screen.findByText('SRT/Q/2026/0151');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select all rows/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(createPendingAction).toHaveBeenCalledTimes(2));
    const ids = createPendingAction.mock.calls.map((call) => (call[0] as { entityId: string }).entityId);
    expect(ids.sort()).toEqual(['d-a', 'd-b']);
    expect(createPendingAction.mock.calls[0][0]).toMatchObject({
      actionKey: 'quotation_document.delete',
      entityType: 'quotation_document',
    });
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('F8: bulk Delete is disabled when every ticked row is issued', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);
    renderPanel();

    const row = (await screen.findByText('Nadi Cergas Sdn Bhd')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('checkbox'));

    expect(await screen.findByRole('button', { name: 'Delete' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(createPendingAction).not.toHaveBeenCalled();
  });

  it('F8: a mixed selection deletes only the draft and says one was skipped', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument(), draftDocument()]);
    renderPanel();

    await screen.findByText('SRT/Q/2026/0150');
    fireEvent.click(screen.getByRole('checkbox', { name: /Select all rows/i }));
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(createPendingAction).toHaveBeenCalledTimes(1));
    expect(createPendingAction).toHaveBeenCalledWith(
      expect.objectContaining({ entityId: 'd-draft' }),
    );
    await waitFor(() =>
      expect(JSON.stringify(toastCalls)).toMatch(/1[^"]*skipped|skipped[^"]*1/i),
    );
  });
});


describe('QuotationsPanel: fix round 2', () => {
  it('chip: the Status column renders the shared pill palette', async () => {
    listQuotationDocuments.mockResolvedValue([
      quotationDocument({ id: 'a', our_ref: 'REF-A', approval_status_key: 'pending_approval' }),
      quotationDocument({ id: 'b', our_ref: 'REF-B', approval_status_key: 'approved' }),
      quotationDocument({ id: 'c', our_ref: 'REF-C', approval_status_key: 'rejected' }),
      draftDocument({ id: 'd', our_ref: 'REF-D' }),
      quotationDocument({ id: 'e', our_ref: 'REF-E' }),
      quotationDocument({ id: 'f', our_ref: 'REF-F', customer_decision: 'changes_requested' }),
    ]);
    renderPanel();

    await screen.findByText('REF-A');
    const pill = (label: string) =>
      screen
        .getAllByText(label)
        .find((el) => el.classList.contains('rounded-full') && el.classList.contains('font-semibold'));
    const expectPill = (label: string, ...classes: string[]) => {
      const el = pill(label);
      expect(el, `pill for ${label}`).toBeDefined();
      for (const cls of classes) expect(el!.classList.contains(cls)).toBe(true);
    };
    expectPill('Pending approval', 'bg-amber-100', 'text-amber-800');
    expectPill('Approved', 'bg-blue-100', 'text-blue-800');
    expectPill('Rejected', 'bg-red-100', 'text-red-800');
    expectPill('Draft', 'bg-muted', 'text-muted-foreground');
    expectPill('Issued', 'bg-emerald-100', 'text-emerald-800');
    expectPill('Changes requested', 'bg-amber-100', 'text-amber-800');
  });

  it('4: the search box lives in the toolbar search slot - a ticked row swaps it for the bulk strip, unticking brings it back', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument(), draftDocument()]);
    renderPanel();

    const row = (await screen.findByText('SRT/Q/2026/0150')).closest('tr') as HTMLElement;
    expect(screen.getByRole('searchbox', { name: 'Search quotations' })).toBeInTheDocument();

    fireEvent.click(within(row).getByRole('checkbox'));
    expect(await screen.findByText('1 selected')).toBeInTheDocument();
    expect(screen.queryByRole('searchbox', { name: 'Search quotations' })).toBeNull();

    fireEvent.click(within(row).getByRole('checkbox'));
    expect(await screen.findByRole('searchbox', { name: 'Search quotations' })).toBeInTheDocument();
  });

  it('4: typing in the search box still filters the rows', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument(), draftDocument()]);
    renderPanel();

    await screen.findByText('SRT/Q/2026/0150');
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search quotations' }), {
      target: { value: '0150' },
    });

    expect(screen.getByText('SRT/Q/2026/0150')).toBeInTheDocument();
    expect(screen.queryByText('SRT/Q/2026/0141 (R2)')).toBeNull();
  });

  it('5: bulk Delete on issued rows only is disabled and carries a reason', async () => {
    listQuotationDocuments.mockResolvedValue([quotationDocument()]);
    renderPanel();

    const row = (await screen.findByText('Nadi Cergas Sdn Bhd')).closest('tr') as HTMLElement;
    fireEvent.click(within(row).getByRole('checkbox'));

    const del = await screen.findByRole('button', { name: 'Delete' });
    expect(del).toBeDisabled();
    // The toolbar shows a disabledReason as a tooltip on the wrapper (reachable by focus).
    const wrapper = del.closest('span[tabindex]') as HTMLElement | null;
    expect(wrapper, 'the disabled button sits in the toolbar tooltip wrapper').not.toBeNull();
    fireEvent.focus(wrapper!);
    expect((await screen.findAllByText(/cannot be deleted/i)).length).toBeGreaterThan(0);
  });
});
