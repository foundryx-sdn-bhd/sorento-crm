/**
 * S12 - the document read as four tabs instead of one long scroll.
 *
 * The client's words: "the cover letter, terms and conditions, signatures should be their own tab,
 * so I don't need to scroll down to see". Three things are worth pinning, because each is one
 * careless edit from coming back:
 *
 * 1. Every tab is a ROUTE, so the terms can be linked to and Back walks through them.
 * 2. The letterhead - ref, recipient, total, the CTA - is on screen on EVERY tab. It is the
 *    identity of the record, not a section of it.
 * 3. An empty tab still renders, with its empty state. A tab that hid itself when it had nothing
 *    in it would read as a missing feature.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { QuotationDocument } from '../../../../_shared/services/quotationDocumentService';
import type { Project } from '../../../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const BASE = '/project-sales/p1/quotation-documents/d1';

const getQuotationDocument = vi.fn();
const listQuotationIssues = vi.fn();
const getProject = vi.fn();
const listQuotations = vi.fn();

// The tab strip reads the pathname to decide what is open, so each test sets the route it is
// making a claim about.
let pathname = BASE;

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => pathname,
  useSearchParams: () => new URLSearchParams(),
}));

// The shell renders the price-floor block, which reads the caller's grants. Answered outright:
// the real hook reaches NextAuth, which throws outside a SessionProvider, and none of the tab
// specs here are about the approval gate (it has its own spec).
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: () => false,
  useHasAnyPermission: () => false,
  usePermissions: () => ({ permissions: [], permissionSet: new Set(), isLoading: false }),
}));

vi.mock('../../../../_shared/hooks/useQuotationDocuments', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../../_shared/hooks/useQuotationDocuments')
  >();
  return {
    ...actual,
    useQuotationApprovalGraph: () => ({ data: null, isLoading: false }),
  };
});

vi.mock('../../../../_shared/services/quotationDocumentService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../../_shared/services/quotationDocumentService')
  >();
  return {
    ...actual,
    getQuotationDocument: (...args: unknown[]) => getQuotationDocument(...args),
    listQuotationIssues: (...args: unknown[]) => listQuotationIssues(...args),
  };
});

vi.mock('../../../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../../_shared/services/projectService')
  >();
  return {
    ...actual,
    getProject: (...args: unknown[]) => getProject(...args),
    listQuotations: (...args: unknown[]) => listQuotations(...args),
  };
});

// Stubbed for the same reason the sibling spec stubs it: this file tests where things render, not
// the line editor, which owns its own suite.
vi.mock('../../../components/QuotationVersionEditor', () => ({
  QuotationVersionEditor: () => <div data-testid="line-editor" />,
}));

import { QuotationDocumentClient } from './QuotationDocumentClient';
import { QuotationScopesTab } from './QuotationScopesTab';
import {
  QuotationCoverLetterTab,
  QuotationHeaderTab,
  QuotationSignaturesTab,
  QuotationTermsTab,
} from './QuotationDocumentTabPanels';

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

function quotationDocument(overrides: Partial<QuotationDocument> = {}): QuotationDocument {
  return {
    id: 'd1',
    project_id: 'p1',
    document_no: 'SRT/Q/2026/0141',
    our_ref: 'SRT/Q/2026/0141',
    your_ref: null,
    doc_date: '2026-02-26',
    recipient_party_id: null,
    recipient_name_snapshot: 'Nadi Cergas Sdn Bhd',
    recipient_address_snapshot: null,
    recipient_phone_snapshot: null,
    attn_name: 'Kelly',
    subject_title: 'CADANGAN MEMBINA PANGSAPURI',
    cover_letter_html: null,
    terms_html: null,
    signatory_name: 'Ahmad Faizal',
    signatory_phone: null,
    scopes: [],
    grand_total: '235000.00',
    issue_count: 0,
    current_issue_no: null,
    is_issued: false,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

function renderTab(route: string, tab: React.ReactNode) {
  pathname = route;
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <QuotationDocumentClient projectId="p1" documentId="d1">
        {tab}
      </QuotationDocumentClient>
    </QueryClientProvider>,
  );
}

/**
 * What stays above the tabs on every tab (#1341, owner: "i need this to be under 'Header' tab"):
 * the title, the status pill, the project title and developer lines, and the header actions.
 * The letterhead card itself (To, Our Ref, Your Ref, Date, Total) lives in the Header tab only.
 */
async function expectIdentityOnScreen() {
  expect(
    await screen.findByRole('heading', { name: 'CADANGAN MEMBINA PANGSAPURI' }),
  ).toBeInTheDocument();
  expect(screen.getAllByText('Nadi Cergas Sdn Bhd').length).toBeGreaterThan(0);
  expect(screen.getByText('Draft')).toBeInTheDocument();
  // E4: an unsigned, unblocked draft leads with Sign and offers no Send to Customer.
  expect(screen.getByRole('button', { name: 'Sign' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^Send to Customer/ })).toBeNull();
  expect(screen.getByRole('button', { name: 'Quotation actions' })).toBeInTheDocument();
}

/**
 * A panel's own heading, told apart from the tab of the same name: the tab is a link in the strip,
 * this is the card the tab leads to.
 */
function panelTitle(title: string) {
  return screen.getByText(title, { selector: '[data-slot="card-title"]' });
}

beforeEach(() => {
  vi.clearAllMocks();
  pathname = BASE;
  getProject.mockResolvedValue(project());
  listQuotations.mockResolvedValue([]);
  listQuotationIssues.mockResolvedValue([]);
  getQuotationDocument.mockResolvedValue(quotationDocument());
});

describe('the quotation document tab strip', () => {
  it('AC-QF050/053: reads Header, Lines, Cover letter, Terms, Signatures, each its own route', async () => {
    renderTab(BASE, <QuotationHeaderTab />);

    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Header',
      'Lines',
      'Cover letter',
      'Terms',
      'Signatures',
    ]);
    expect(screen.getByRole('tab', { name: 'Header' })).toHaveAttribute('href', BASE);
    expect(screen.getByRole('tab', { name: 'Lines' })).toHaveAttribute('href', `${BASE}/lines`);
    expect(screen.getByRole('tab', { name: 'Cover letter' })).toHaveAttribute(
      'href',
      `${BASE}/cover-letter`,
    );
    expect(screen.getByRole('tab', { name: 'Terms' })).toHaveAttribute('href', `${BASE}/terms`);
    expect(screen.getByRole('tab', { name: 'Signatures' })).toHaveAttribute(
      'href',
      `${BASE}/signatures`,
    );
    // The owner struck "Scopes" out and wrote "Lines" over it.
    expect(screen.queryByRole('tab', { name: 'Scopes' })).toBeNull();
  });

  it('takes the open tab from the URL, so a link can point straight at the terms', async () => {
    renderTab(`${BASE}/terms`, <QuotationTermsTab />);

    expect(await screen.findByRole('tab', { name: 'Terms' })).toHaveAttribute(
      'data-state',
      'active',
    );
    expect(screen.getByRole('tab', { name: 'Header' })).toHaveAttribute('data-state', 'inactive');
  });

  it('AC-QF050: opens on Header, the first tab, on the index route', async () => {
    renderTab(BASE, <QuotationHeaderTab />);

    expect(await screen.findByRole('tab', { name: 'Header' })).toHaveAttribute(
      'data-state',
      'active',
    );
  });

  it('marks Lines open on its own route', async () => {
    renderTab(`${BASE}/lines`, <QuotationScopesTab />);

    expect(await screen.findByRole('tab', { name: 'Lines' })).toHaveAttribute(
      'data-state',
      'active',
    );
  });
});

describe('what each quotation document tab renders', () => {
  it('AC-QF051: the Header tab holds the letterhead card, below the identity lines', async () => {
    getQuotationDocument.mockResolvedValue(
      quotationDocument({ your_ref: 'NC/18', recipient_address_snapshot: 'Level 8\nMenara' }),
    );
    renderTab(BASE, <QuotationHeaderTab />);

    const card = await screen.findByTestId('quotation-header-card');
    expect(card).toHaveTextContent('To');
    expect(card).toHaveTextContent('Level 8');
    expect(card).toHaveTextContent('Attn:');
    expect(card).toHaveTextContent('Our Ref');
    expect(card).toHaveTextContent('SRT/Q/2026/0141');
    expect(card).toHaveTextContent('NC/18');
    expect(card).toHaveTextContent('Date');
    expect(card).toHaveTextContent('RM 235,000.00');
    await expectIdentityOnScreen();
  });

  it('AC-QF051: the letterhead card is not above the tabs, so Lines does not repeat it', async () => {
    renderTab(`${BASE}/lines`, <QuotationScopesTab />);

    expect(await screen.findByText('No lines on this quotation yet')).toBeInTheDocument();
    expect(screen.queryByTestId('quotation-header-card')).toBeNull();
    expect(screen.queryByText('Our Ref')).toBeNull();
    expect(screen.queryByText('RM 235,000.00')).toBeNull();
    await expectIdentityOnScreen();
  });

  it('renders the cover letter tab, and the identity above it', async () => {
    getQuotationDocument.mockResolvedValue(
      quotationDocument({ cover_letter_html: '<p>Dear Kelly</p>' }),
    );
    renderTab(`${BASE}/cover-letter`, <QuotationCoverLetterTab />);

    expect(await screen.findByText('Dear Kelly')).toBeInTheDocument();
    expect(panelTitle('Cover letter')).toBeInTheDocument();
    await expectIdentityOnScreen();
  });

  it('renders the terms tab, and the identity above it', async () => {
    getQuotationDocument.mockResolvedValue(quotationDocument({ terms_html: '<p>30 days</p>' }));
    renderTab(`${BASE}/terms`, <QuotationTermsTab />);

    expect(await screen.findByText('30 days')).toBeInTheDocument();
    expect(panelTitle('Terms and conditions')).toBeInTheDocument();
    await expectIdentityOnScreen();
  });

  it('renders the signatures tab, and the identity above it', async () => {
    renderTab(`${BASE}/signatures`, <QuotationSignaturesTab />);

    // Both halves render even with no ink on either, which is AC-H8's whole point.
    expect(
      await screen.findByText(/No signature captured on this quotation yet/i),
    ).toBeInTheDocument();
    expect(panelTitle('Signatures')).toBeInTheDocument();
    expect(
      screen.getByText(/Send this quotation to the customer to give them a link/i),
    ).toBeInTheDocument();
    await expectIdentityOnScreen();
  });

  it('still renders an empty cover letter and an empty terms tab, with their empty states', async () => {
    renderTab(`${BASE}/cover-letter`, <QuotationCoverLetterTab />);
    // Exact: no explanation of when the template is applied, and no "issued" wording (AC-QF057).
    expect(await screen.findByText('No cover letter on this quotation yet.')).toBeInTheDocument();

    renderTab(`${BASE}/terms`, <QuotationTermsTab />);
    expect(await screen.findByText('No terms on this quotation yet.')).toBeInTheDocument();
  });
});
