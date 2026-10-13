/**
 * #1341 - the quotation form page, create and edit (AC-QF010 to AC-QF024, AC-QF040/041,
 * AC-QF070 to AC-QF073).
 *
 * The owner: "I should be able to add product straight away and save when I am satisfied, if I
 * want to edit I can click on the gear button to edit". So create is a page that writes nothing
 * until Save, and Save is ONE request; edit is the same page, filled, and its Save is ONE PATCH.
 *
 * Round 3, the owner: "we shouldn't revamp the Lines tab, it was good, we should reuse that". So
 * the Lines tab is the quotation page's lines editing from origin/main: `QuotationVersionEditor`
 * (the `InlineLineTable` cells, Add a line, Add a section, strike-through Remove) driven by the
 * same `useQuotationEditSession` handlers, with the same scope strip and scope dialogs.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { QuotationDocument } from '../../../_shared/services/quotationDocumentService';
import type {
  Project,
  ProjectQuotation,
  QuotationLine,
  QuotationVersion,
} from '../../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

// Removing a saved scope is a hard delete, gated on projects.projects.delete like the scope
// DELETE route. Granted by default; a spec below takes it away.
let granted = new Set(['projects.projects.view', 'projects.projects.edit', 'projects.projects.delete']);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => granted.has(slug),
  useHasAnyPermission: (slugs: string[]) => slugs.some((slug) => granted.has(slug)),
  usePermissions: () => ({ permissions: [...granted], permissionSet: granted, isLoading: false }),
}));

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => '/project-sales/p1/quotation-documents/new',
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({
    resetToDefaults: async () => {},
    isLoading: false,
  }),
}));

const createQuotationDocument = vi.fn();
const updateQuotationDocument = vi.fn();
const getQuotationDocument = vi.fn();
const getQuotationLetterTemplates = vi.fn();
vi.mock(
  '../../../_shared/services/quotationDocumentService',
  async (importOriginal) => {
    const actual =
      await importOriginal<
        typeof import('../../../_shared/services/quotationDocumentService')
      >();
    return {
      ...actual,
      createQuotationDocument: (...args: unknown[]) =>
        createQuotationDocument(...args),
      updateQuotationDocument: (...args: unknown[]) =>
        updateQuotationDocument(...args),
      getQuotationDocument: (...args: unknown[]) =>
        getQuotationDocument(...args),
      getQuotationLetterTemplates: (...args: unknown[]) =>
        getQuotationLetterTemplates(...args),
    };
  },
);

const getProject = vi.fn();
const listQuotations = vi.fn();
const listQuotationVersions = vi.fn();
const listQuotationLines = vi.fn();
const listSeries = vi.fn();
const replaceQuotationLines = vi.fn();
vi.mock('../../../_shared/services/projectService', async (importOriginal) => {
  const actual =
    await importOriginal<
      typeof import('../../../_shared/services/projectService')
    >();
  return {
    ...actual,
    getProject: (...args: unknown[]) => getProject(...args),
    listQuotations: (...args: unknown[]) => listQuotations(...args),
    listQuotationVersions: (...args: unknown[]) =>
      listQuotationVersions(...args),
    listQuotationLines: (...args: unknown[]) => listQuotationLines(...args),
    listSeries: (...args: unknown[]) => listSeries(...args),
    replaceQuotationLines: (...args: unknown[]) =>
      replaceQuotationLines(...args),
    judgeQuotationLine: vi.fn(async () => ({
      is_non_standard: false,
      is_below_floor: false,
      floor_value: null,
      floor_level: null,
    })),
  };
});

vi.mock(
  '@/app/(protected)/master-data-management/products/services/productService',
  () => ({
    getProductsForLineSelect: vi.fn(async () => [
      {
        id: 'p9',
        product_code: 'SRT-BASIN-02',
        product_name: 'Counter basin',
        description: 'Vitreous china counter basin',
        brand_id: 'b1',
        base_uom_id: 'u1',
        list_price: '560.00',
      },
    ]),
    getProductsForVariantSelect: vi.fn(async () => []),
  }),
);
vi.mock(
  '@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query',
  () => ({
    useBrandSelectQuery: () => ({
      data: [{ id: 'b1', brand_name: 'SORENTO' }],
    }),
  }),
);
vi.mock(
  '@/app/(protected)/master-data-management/shared/hooks/use-uom-select-query',
  () => ({
    useUOMSelectQuery: () => ({
      data: [{ id: 'u1', uom_code: 'PCS', uom_name: 'Pieces' }],
    }),
  }),
);

// The repo's rich-text editor needs layout APIs jsdom lacks; a textarea stands in so the value
// the tab holds can be read and typed.
vi.mock('@/components/ui/rich-text-editor', () => ({
  RichTextEditor: ({
    value,
    onChange,
    placeholder,
  }: {
    value: string;
    onChange: (html: string) => void;
    placeholder?: string;
  }) => (
    <textarea
      aria-label={placeholder}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}));

/**
 * The quotation page's own editor and edit session, spied rather than replaced: the real ones
 * render, and the specs can prove the form's lines go through them and nowhere else.
 */
const editorProps = vi.fn();
vi.mock('../../components/QuotationVersionEditor', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../../components/QuotationVersionEditor')>();
  const Real = actual.QuotationVersionEditor;
  return {
    ...actual,
    QuotationVersionEditor: (props: Parameters<typeof Real>[0]) => {
      editorProps(props);
      return <Real {...props} />;
    },
  };
});

const stageScopeSpy = vi.fn();
const toggleRemovedSpy = vi.fn();
vi.mock('../[documentId]/components/useQuotationEditSession', async (importOriginal) => {
  const actual =
    await importOriginal<typeof import('../[documentId]/components/useQuotationEditSession')>();
  const ReactModule = await import('react');
  return {
    ...actual,
    useQuotationEditSession: () => {
      const session = actual.useQuotationEditSession();
      const { stageScope, toggleRemoved } = session;
      const stage = ReactModule.useCallback(
        (...args: Parameters<typeof stageScope>) => {
          stageScopeSpy(...args);
          stageScope(...args);
        },
        [stageScope],
      );
      const toggle = ReactModule.useCallback(
        (...args: Parameters<typeof toggleRemoved>) => {
          toggleRemovedSpy(...args);
          toggleRemoved(...args);
        },
        [toggleRemoved],
      );
      return { ...session, stageScope: stage, toggleRemoved: toggle };
    },
  };
});

import { QuotationFormClient } from './QuotationFormClient';

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
    developer_name: 'Nadi Cergas Sdn Bhd',
    ...overrides,
  };
}

function document(
  overrides: Partial<QuotationDocument> = {},
): QuotationDocument {
  return {
    id: 'd1',
    project_id: 'p1',
    document_no: 'PRJQ-2026-0002',
    our_ref: 'PRJQ-2026-0002',
    your_ref: 'NC/18',
    doc_date: '2026-09-28',
    recipient_party_id: null,
    recipient_name_snapshot: 'Nadi Cergas Sdn Bhd',
    recipient_address_snapshot: 'Level 8',
    recipient_phone_snapshot: null,
    attn_name: 'Kelly',
    subject_title: 'Menara Test',
    cover_letter_html: '<p>Dear Sir</p>',
    terms_html: '<p>Terms</p>',
    signatory_name: null,
    signatory_phone: null,
    scopes: [
      {
        id: 'q1',
        scope_label: 'Townhouse',
        sort_order: 0,
        outcome: 'open',
        current_version_id: 'v1',
        current_version_no: 1,
        line_count: 1,
        scope_total: '9000.00',
      },
    ],
    grand_total: '9000.00',
    issue_count: 0,
    current_issue_no: null,
    is_issued: false,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

const SCOPE: ProjectQuotation = {
  id: 'q1',
  project_id: 'p1',
  scope_label: 'Townhouse',
  outcome: 'open',
  version_count: 1,
  current_version_id: 'v1',
  current_version_no: 1,
  current_total: '9000.00',
  below_floor_count: 0,
  non_standard_count: 0,
  line_count: 1,
  series_id: null,
};

function version(overrides: Partial<QuotationVersion> = {}): QuotationVersion {
  return {
    id: 'v1',
    quotation_id: 'q1',
    version_no: 1,
    is_current: true,
    is_editable: true,
    total_amount: '9000.00',
    ...overrides,
  };
}

const LINE: QuotationLine = {
  id: 'l1',
  version_id: 'v1',
  product_id: 'p1',
  product_code: 'SRT-WC-01',
  description: 'Wall-hung WC',
  unit_price: '900.00',
  quantity: '10.00',
  line_total: '9000.00',
  is_non_standard: false,
  is_below_floor: false,
  sort_order: 0,
};

function renderForm(documentId?: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <QuotationFormClient projectId="p1" documentId={documentId} />
    </QueryClientProvider>,
  );
}

/** The form's own tabs (#1341, owner: "header stays in header tab"). */
async function openTab(name: 'Header' | 'Lines' | 'Cover letter' | 'Terms') {
  fireEvent.mouseDown(await screen.findByRole('tab', { name }), { button: 0 });
  await waitFor(() =>
    expect(screen.getByRole('tab', { name })).toHaveAttribute('data-state', 'active'),
  );
}

/** The open scope's card, by the name its strip tab carries. */
function scopeCard(name: string) {
  return screen.getByRole('region', { name });
}

/** The Lines tab, open, with the scope's line table drawn and ready to type into. */
async function openLines(scope = 'Scope 1') {
  await openTab('Lines');
  const card = await waitFor(() => scopeCard(scope));
  await within(card).findByRole('button', { name: 'Add a line' });
  return card;
}

/** The scope's name, typed straight into its card header (owner, 13 Oct), staged by the form. */
async function nameScope(card: HTMLElement, label: string) {
  fireEvent.change(within(card).getByRole('textbox', { name: 'Scope name' }), {
    target: { value: label },
  });
  await waitFor(() => expect(screen.getByRole('region', { name: label })).toBeInTheDocument());
}

/** The quotation page's Add a scope: the + at the end of the scope strip, then its name dialog. */
async function addScope(label: string) {
  fireEvent.click(screen.getByRole('button', { name: 'Add a scope' }));
  const dialog = await screen.findByRole('dialog');
  fireEvent.change(within(dialog).getByLabelText('Scope name'), { target: { value: label } });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Add' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  const card = await waitFor(() => scopeCard(label));
  await within(card).findByRole('button', { name: 'Add a line' });
  return card;
}

/** Add a line on the open scope and wait for its cells. */
async function addLine(card: HTMLElement, lineNo: number) {
  fireEvent.click(within(card).getByRole('button', { name: 'Add a line' }));
  return within(card).findByRole('textbox', { name: `Description on line ${lineNo}` });
}

beforeEach(() => {
  vi.clearAllMocks();
  granted = new Set(['projects.projects.view', 'projects.projects.edit', 'projects.projects.delete']);
  getProject.mockResolvedValue(project());
  listSeries.mockResolvedValue([
    {
      id: 's1',
      name: 'Premium Series',
      is_active: true,
      category_ids: [],
      category_names: [],
    },
  ]);
  listQuotations.mockResolvedValue([SCOPE]);
  listQuotationVersions.mockResolvedValue([version()]);
  listQuotationLines.mockResolvedValue([LINE]);
  getQuotationDocument.mockResolvedValue(document());
  createQuotationDocument.mockResolvedValue(document({ id: 'd-new' }));
  updateQuotationDocument.mockResolvedValue(document());
  getQuotationLetterTemplates.mockResolvedValue({
    cover_letter_html: '<p>Dear {{attn_name}}</p>',
    terms_html: '<p>Valid 30 days</p>',
  });
});

describe('QuotationFormClient create', () => {
  it('defaults Name, Phone and Attn from the project customer (owner, 13 Oct)', async () => {
    getProject.mockResolvedValue(
      project({
        customer_id: 'cust-1',
        customer_name: 'Gamuda Builders Sdn Bhd',
        customer_phone: '03-7777 1234',
        customer_attn: 'Lim Wei Ming',
      }),
    );
    renderForm();

    await waitFor(() =>
      expect(screen.getByLabelText('Name')).toHaveValue('Gamuda Builders Sdn Bhd'),
    );
    expect(screen.getByLabelText('Phone')).toHaveValue('03-7777 1234');
    expect(screen.getByLabelText('Attn')).toHaveValue('Lim Wei Ming');
  });

  it('falls back to the developer when the project has no customer', async () => {
    renderForm();
    await waitFor(() =>
      expect(screen.getByLabelText('Name')).toHaveValue('Nadi Cergas Sdn Bhd'),
    );
    expect(screen.getByLabelText('Attn')).toHaveValue('');
  });

  it('AC-QF011/012: opens with the letterhead and one empty scope ready for products', async () => {
    renderForm();

    expect(await screen.findByLabelText('Your Ref')).toBeInTheDocument();
    expect(screen.getByLabelText('Attn')).toBeInTheDocument();
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    expect(screen.getByLabelText('Date')).toHaveValue(today);
    // The subject arrives as the project's title, the same default the server would use.
    await waitFor(() =>
      expect(screen.getByLabelText('Subject')).toHaveValue('Menara Test'),
    );
    const card = await openLines();
    // Owner, 13 Oct: the name is edited in place, no Edit scope dialog in between.
    expect(within(card).getByRole('textbox', { name: 'Scope name' })).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Edit scope' })).toBeNull();
    expect(within(card).getByRole('button', { name: 'Add a section' })).toBeInTheDocument();
  });

  it('edits the scope name, series and notes in place and saves them', async () => {
    renderForm();
    const card = await openLines();
    await nameScope(card, 'Guard House');
    const named = screen.getByRole('region', { name: 'Guard House' });
    fireEvent.change(within(named).getByRole('textbox', { name: 'Scope notes' }), {
      target: { value: 'Excludes the gate' },
    });
    await addLine(named, 1).then((cell) =>
      fireEvent.change(cell, { target: { value: 'Gate lock' } }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));
    await waitFor(() => expect(createQuotationDocument).toHaveBeenCalled());
    const body = createQuotationDocument.mock.calls[0][1] as {
      scopes: { scope_label: string; notes?: string | null }[];
    };
    expect(body.scopes[0]).toEqual(
      expect.objectContaining({ scope_label: 'Guard House', notes: 'Excludes the gate' }),
    );
  });

  it('AC-QF070: the Lines tab is the quotation page\'s own lines editor, not a new one', async () => {
    renderForm();
    const card = await openLines();

    // The same component the quotation page renders, in its staged edit mode.
    expect(editorProps).toHaveBeenCalled();
    const props = editorProps.mock.calls.at(-1)![0];
    expect(props.edit).toEqual(
      expect.objectContaining({
        seed: expect.any(Function),
        stage: expect.any(Function),
        toggleRemoved: expect.any(Function),
      }),
    );
    // A scope added here has no saved record yet.
    expect(props.quotation).toBeNull();

    // Its line table, whose lines ARE rows of cells, and the scope strip with Add a scope at its end.
    expect(within(card).getByRole('table')).toBeInTheDocument();
    expect(screen.getByTestId('quotation-scope-strip')).toBeInTheDocument();
    await addLine(card, 1);
    expect(within(card).getByRole('textbox', { name: 'Unit price on line 1' })).toBeInTheDocument();

    // And not the round 2 inline editor: no per-line panel under the grid, no Edit line, no Done.
    expect(screen.queryByRole('group', { name: 'Line 1' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Edit line 1' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Done' })).toBeNull();
  });

  it('AC-QF071: Add a line, Add a section and Remove go through the quotation page\'s session handlers', async () => {
    renderForm();
    const card = await openLines();

    const description = await addLine(card, 1);
    fireEvent.change(description, { target: { value: 'Grab bar' } });
    await waitFor(() =>
      expect(stageScopeSpy).toHaveBeenCalledWith(
        expect.stringMatching(/^new-scope:/),
        [expect.objectContaining({ id: null, draft: expect.objectContaining({ description: 'Grab bar' }) })],
      ),
    );

    fireEvent.click(within(card).getByRole('button', { name: 'Add a section' }));
    const heading = await within(card).findByRole('textbox', { name: 'Section heading on line 2' });
    fireEvent.change(heading, { target: { value: 'OPTIONAL ITEMS' } });
    await waitFor(() =>
      expect(stageScopeSpy).toHaveBeenLastCalledWith(
        expect.stringMatching(/^new-scope:/),
        [
          expect.anything(),
          expect.objectContaining({ draft: expect.objectContaining({ band_label: 'OPTIONAL ITEMS' }) }),
        ],
      ),
    );

    fireEvent.click(within(card).getByRole('button', { name: 'Remove line 1' }));
    expect(toggleRemovedSpy).toHaveBeenCalledWith(
      expect.stringMatching(/^new-scope:/),
      expect.any(String),
    );
    expect(await within(card).findByText('Removed on save')).toBeInTheDocument();
    expect(createQuotationDocument).not.toHaveBeenCalled();
    expect(replaceQuotationLines).not.toHaveBeenCalled();
  });

  it('AC-QF010: writes nothing while the form is being filled', async () => {
    renderForm();
    const card = await openLines();

    await nameScope(card, 'Townhouse');
    await addLine(scopeCard('Townhouse'), 1);
    await addScope('Guard House');

    expect(createQuotationDocument).not.toHaveBeenCalled();
    expect(updateQuotationDocument).not.toHaveBeenCalled();
    expect(replaceQuotationLines).not.toHaveBeenCalled();
  });

  it('AC-QF014: Save sends ONE request with the header, every scope and every line', async () => {
    renderForm();
    fireEvent.change(await screen.findByLabelText('Your Ref'), {
      target: { value: 'NC/19' },
    });
    await nameScope(await openLines(), 'Townhouse');
    const first = scopeCard('Townhouse');

    fireEvent.change(await addLine(first, 1), { target: { value: 'Bespoke vanity top' } });
    fireEvent.change(within(first).getByRole('textbox', { name: 'Unit price on line 1' }), {
      target: { value: '120.00' },
    });

    await addScope('Guard House');

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));

    await waitFor(() =>
      expect(createQuotationDocument).toHaveBeenCalledTimes(1),
    );
    const [projectId, body] = createQuotationDocument.mock.calls[0];
    expect(projectId).toBe('p1');
    expect(body).toMatchObject({
      your_ref: 'NC/19',
      subject_title: 'Menara Test',
      scopes: [
        {
          scope_label: 'Townhouse',
          lines: [
            expect.objectContaining({
              description_snapshot: 'Bespoke vanity top',
              unit_price: '120.00',
              quantity: '1',
            }),
          ],
        },
        { scope_label: 'Guard House', lines: [] },
      ],
    });
    // Lands on the quotation the server just created.
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        '/project-sales/p1/quotation-documents/d-new',
      ),
    );
  });

  it('AC-QF018: refuses a scope with no name and a line with nothing on it, before any request', async () => {
    renderForm();
    const card = await openLines();
    await addLine(card, 1);

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));

    expect(
      await screen.findByText(/Every scope needs a name/i),
    ).toBeInTheDocument();
    expect(createQuotationDocument).not.toHaveBeenCalled();

    await nameScope(card, 'Townhouse');
    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));

    expect(
      await screen.findByText(
        /One line still needs a product or a description/i,
      ),
    ).toBeInTheDocument();
    expect(createQuotationDocument).not.toHaveBeenCalled();
  });

  it('AC-QF013: a scope added on the form can be removed before Save', async () => {
    renderForm();
    await openLines();

    const added = await addScope('Guard House');
    // Owner, 13 Oct: a red destructive button with the trash icon, not an outline one.
    const remove = within(added).getByRole('button', { name: 'Remove scope' });
    expect(remove.className).toMatch(/bg-destructive/);
    expect(remove.querySelector('svg')).not.toBeNull();
    fireEvent.click(remove);

    await waitFor(() => expect(screen.queryByRole('region', { name: 'Guard House' })).toBeNull());
    expect(screen.getByRole('region', { name: 'Scope 1' })).toBeInTheDocument();
  });

  it('AC-QF016: Cancel goes back to the Quotations tab and writes nothing', async () => {
    renderForm();
    await openLines();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(push).toHaveBeenCalledWith('/project-sales/p1?tab=quotations');
    expect(createQuotationDocument).not.toHaveBeenCalled();
  });

  it('AC-QF040: one primary CTA and no subtitle under the title', async () => {
    renderForm();
    await openLines();

    expect(
      screen.getByRole('heading', { level: 1, name: 'New quotation' }),
    ).toBeInTheDocument();
    // Cancel is an outline button; Save is the only filled one.
    const save = screen.getByRole('button', { name: 'Save quotation' });
    const cancel = screen.getByRole('button', { name: 'Cancel' });
    expect(save.className).toMatch(/bg-primary/);
    expect(cancel.className).not.toMatch(/bg-primary/);
    expect(
      screen
        .getAllByRole('button')
        .filter((b) => /bg-primary /.test(b.className)),
    ).toHaveLength(1);
  });
});

describe('QuotationFormClient edit', () => {
  /** A saved scope's lines, seeded into the session and drawn as cells. */
  async function openSavedLines() {
    const card = await openLines('Townhouse');
    await within(card).findByRole('textbox', { name: 'Qty on SRT-WC-01' });
    return card;
  }

  it('AC-QF020: opens filled from the saved quotation, with the same sections plus the letter', async () => {
    renderForm('d1');

    await waitFor(() =>
      expect(screen.getByLabelText('Your Ref')).toHaveValue('NC/18'),
    );
    expect(screen.getByLabelText('Attn')).toHaveValue('Kelly');
    const card = await openSavedLines();
    expect(within(card).getByRole('textbox', { name: 'Description on SRT-WC-01' })).toHaveValue(
      'Wall-hung WC',
    );
    // The saved scope's own record reaches the editor, as on the quotation page.
    expect(editorProps.mock.calls.at(-1)![0].quotation).toMatchObject({ id: 'q1' });
    expect(
      screen.getByRole('heading', { level: 1, name: 'PRJQ-2026-0002' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Cover letter' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Terms' })).toBeInTheDocument();
  });

  it('AC-QF021: Save sends ONE PATCH with the header and every scope, lines included', async () => {
    renderForm('d1');
    await waitFor(() => expect(screen.getByLabelText('Your Ref')).toHaveValue('NC/18'));
    fireEvent.change(screen.getByLabelText('Your Ref'), {
      target: { value: 'NC/20' },
    });
    const card = await openSavedLines();
    fireEvent.change(within(card).getByRole('textbox', { name: 'Qty on SRT-WC-01' }), {
      target: { value: '12' },
    });
    await nameScope(card, 'Townhouse Block A');

    await addScope('Reception');

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));

    await waitFor(() =>
      expect(updateQuotationDocument).toHaveBeenCalledTimes(1),
    );
    const [projectId, documentId, body] = updateQuotationDocument.mock.calls[0];
    expect([projectId, documentId]).toEqual(['p1', 'd1']);
    expect(body).toMatchObject({
      your_ref: 'NC/20',
      scopes: [
        {
          id: 'q1',
          scope_label: 'Townhouse Block A',
          lines: [expect.objectContaining({ id: 'l1', quantity: '12' })],
        },
        { scope_label: 'Reception', lines: [] },
      ],
    });
    expect(replaceQuotationLines).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith(
        '/project-sales/p1/quotation-documents/d1',
      ),
    );
  });

  it('AC-QF071: a line removed on the form is struck through and left out of the PATCH', async () => {
    renderForm('d1');
    const card = await openSavedLines();

    fireEvent.click(within(card).getByRole('button', { name: 'Remove SRT-WC-01' }));
    expect(toggleRemovedSpy).toHaveBeenCalledWith('q1', 'l1');
    expect(await within(card).findByText('Removed on save')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));
    await waitFor(() => expect(updateQuotationDocument).toHaveBeenCalledTimes(1));
    expect(updateQuotationDocument.mock.calls[0][2].scopes[0]).toMatchObject({
      id: 'q1',
      lines: [],
    });
  });

  it('AC-QF022: Cancel goes back to the quotation page and writes nothing', async () => {
    renderForm('d1');
    await waitFor(() =>
      expect(screen.getByLabelText('Your Ref')).toHaveValue('NC/18'),
    );

    fireEvent.change(screen.getByLabelText('Your Ref'), {
      target: { value: 'changed' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(push).toHaveBeenCalledWith(
      '/project-sales/p1/quotation-documents/d1',
    );
    expect(updateQuotationDocument).not.toHaveBeenCalled();
  });

  it('AC-QF023: a scope the customer holds shows its lines read-only and never sends them', async () => {
    listQuotationVersions.mockResolvedValue([version({ is_editable: false })]);
    renderForm('d1');
    await openTab('Lines');
    const card = await waitFor(() => scopeCard('Townhouse'));
    await within(card).findByText('Wall-hung WC');

    expect(within(card).queryByRole('button', { name: 'Add a line' })).toBeNull();
    expect(within(card).queryByRole('textbox', { name: 'Qty on SRT-WC-01' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));
    await waitFor(() =>
      expect(updateQuotationDocument).toHaveBeenCalledTimes(1),
    );
    const body = updateQuotationDocument.mock.calls[0][2];
    expect(body.scopes[0]).not.toHaveProperty('lines');
  });

  it('AC-QF072: a saved scope nobody opened on the Lines tab is saved without its lines', async () => {
    renderForm('d1');
    await waitFor(() => expect(screen.getByLabelText('Your Ref')).toHaveValue('NC/18'));

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));
    await waitFor(() => expect(updateQuotationDocument).toHaveBeenCalledTimes(1));
    const body = updateQuotationDocument.mock.calls[0][2];
    // The write replaces the WHOLE set, so a scope nobody touched is never re-sent.
    expect(body.scopes).toEqual([{ id: 'q1', scope_label: 'Townhouse', series_id: null }]);
  });

  it('AC-QF058: a saved scope nothing in which was sent can be removed, in the ONE PATCH', async () => {
    renderForm('d1');
    const card = await openSavedLines();

    await waitFor(() =>
      expect(within(card).getByRole('button', { name: 'Remove scope' })).toBeInTheDocument(),
    );
    fireEvent.click(within(card).getByRole('button', { name: 'Remove scope' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Townhouse' })).toBeNull());
    expect(updateQuotationDocument).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));
    await waitFor(() => expect(updateQuotationDocument).toHaveBeenCalledTimes(1));
    const body = updateQuotationDocument.mock.calls[0][2];
    expect(body.remove_scope_ids).toEqual(['q1']);
    expect(body.scopes).toEqual([]);
  });

  it('AC-QF058: a scope any version of which was sent to the customer offers no Remove', async () => {
    listQuotationVersions.mockResolvedValue([
      version({ id: 'v2', version_no: 2, is_current: true, is_editable: true }),
      version({ id: 'v1', version_no: 1, is_current: false, is_editable: false, is_issued: true }),
    ]);
    renderForm('d1');
    const card = await openSavedLines();

    expect(within(card).queryByRole('button', { name: 'Remove scope' })).toBeNull();
  });

  it('AC-QF060: a header-only edit saves the letterhead with no scope change', async () => {
    renderForm('d1');
    await waitFor(() => expect(screen.getByLabelText('Your Ref')).toHaveValue('NC/18'));
    fireEvent.change(screen.getByLabelText('Attn'), { target: { value: 'Mr Tan' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));
    await waitFor(() => expect(updateQuotationDocument).toHaveBeenCalledTimes(1));
    expect(updateQuotationDocument.mock.calls[0][2]).toMatchObject({ attn_name: 'Mr Tan' });
    expect(updateQuotationDocument.mock.calls[0][2]).not.toHaveProperty('remove_scope_ids');
  });
});

describe('QuotationFormClient guards', () => {
  it('refuses a quantity that is not a number before any request', async () => {
    renderForm();
    const card = await openLines();
    await nameScope(card, 'Townhouse');
    const named = scopeCard('Townhouse');
    fireEvent.change(await addLine(named, 1), { target: { value: 'Grab bar' } });
    fireEvent.change(within(named).getByRole('textbox', { name: 'Qty on line 1' }), {
      target: { value: 'two' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));

    expect(
      await screen.findByText(
        /One line has a quantity or unit price that is not a number/i,
      ),
    ).toBeInTheDocument();
    expect(createQuotationDocument).not.toHaveBeenCalled();
  });

  it('warns before the browser throws typed work away, and not before anything is typed', async () => {
    renderForm();
    await openLines();

    const untouched = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(untouched);
    expect(untouched.defaultPrevented).toBe(false);

    await openTab('Header');
    fireEvent.change(screen.getByLabelText('Your Ref'), {
      target: { value: 'NC/21' },
    });
    const touched = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(touched);
    expect(touched.defaultPrevented).toBe(true);
  });
});

/**
 * Fix round 2 (#1341, owner alignment notes 11:33Z): "header stays in header tab", "show them on
 * create as well", "yes can, header only is fine".
 */
describe('QuotationFormClient tabs (AC-QF055)', () => {
  it('create reads Header, Lines, Cover letter, Terms as tabs, not stacked sections', async () => {
    renderForm();

    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      'Header',
      'Lines',
      'Cover letter',
      'Terms',
    ]);
    expect(screen.getByRole('tab', { name: 'Header' })).toHaveAttribute('data-state', 'active');
    // Header is open: its fields are on screen and the scopes are not.
    expect(await screen.findByRole('textbox', { name: 'Your Ref' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Scope 1' })).toBeNull();

    await openLines();
    expect(scopeCard('Scope 1')).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Your Ref' })).toBeNull();
  });

  it('holds recipient, attention, your ref, date, subject and address in the Header tab', async () => {
    renderForm();

    for (const label of ['Name', 'Address', 'Attn', 'Your Ref', 'Date', 'Subject']) {
      expect(await screen.findByLabelText(label)).toBeInTheDocument();
    }
  });

  it('edit uses the same four tabs in the same order', async () => {
    renderForm('d1');

    await waitFor(() => expect(screen.getByLabelText('Your Ref')).toHaveValue('NC/18'));
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'Header',
      'Lines',
      'Cover letter',
      'Terms',
    ]);
  });
});

describe('QuotationFormClient letter on create (AC-QF056)', () => {
  it('prefills Cover letter and Terms from the company templates, editable before Save', async () => {
    renderForm();

    await openTab('Cover letter');
    const letter = await screen.findByRole('textbox', {
      name: 'The letter the customer reads before the prices',
    });
    expect(letter).toHaveValue('<p>Dear {{attn_name}}</p>');
    fireEvent.change(letter, { target: { value: '<p>Dear {{attn_name}}, edited</p>' } });

    await openTab('Terms');
    expect(
      await screen.findByRole('textbox', { name: 'The clauses the customer holds us to' }),
    ).toHaveValue('<p>Valid 30 days</p>');

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));
    await waitFor(() => expect(createQuotationDocument).toHaveBeenCalledTimes(1));
    expect(createQuotationDocument.mock.calls[0][1]).toMatchObject({
      cover_letter_html: '<p>Dear {{attn_name}}, edited</p>',
      terms_html: '<p>Valid 30 days</p>',
    });
  });

  it('opens with empty letter tabs when the company has no template, and still saves', async () => {
    getQuotationLetterTemplates.mockResolvedValue({ cover_letter_html: null, terms_html: null });
    renderForm();

    await openTab('Cover letter');
    expect(
      await screen.findByRole('textbox', {
        name: 'The letter the customer reads before the prices',
      }),
    ).toHaveValue('');
    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));
    await waitFor(() => expect(createQuotationDocument).toHaveBeenCalledTimes(1));
    expect(createQuotationDocument.mock.calls[0][1]).not.toHaveProperty('cover_letter_html');
  });
});

describe('QuotationFormClient header-only save (AC-QF060)', () => {
  it('saves a create that touched only the Header, sending no empty scope', async () => {
    renderForm();
    fireEvent.change(await screen.findByLabelText('Your Ref'), { target: { value: 'NC/30' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));

    await waitFor(() => expect(createQuotationDocument).toHaveBeenCalledTimes(1));
    const body = createQuotationDocument.mock.calls[0][1];
    expect(body).toMatchObject({ your_ref: 'NC/30', scopes: [] });
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith('/project-sales/p1/quotation-documents/d-new'),
    );
  });

  it('still refuses a scope that has lines but no name, and opens Lines to show it', async () => {
    renderForm();
    const card = await openLines();
    fireEvent.change(await addLine(card, 1), { target: { value: 'Grab bar' } });
    await openTab('Header');

    fireEvent.click(screen.getByRole('button', { name: 'Save quotation' }));

    expect(await screen.findByText(/Every scope needs a name/i)).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Lines' })).toHaveAttribute('data-state', 'active');
    expect(createQuotationDocument).not.toHaveBeenCalled();
  });
});

describe('QuotationFormClient from the Overview (AC-QF061)', () => {
  it('opens cleanly for a project with no quotation, and Cancel returns to the Quotations tab', async () => {
    listQuotations.mockResolvedValue([]);
    renderForm();

    expect(await screen.findByRole('heading', { level: 1, name: 'New quotation' })).toBeInTheDocument();
    expect(await screen.findByLabelText('Your Ref')).toHaveValue('');
    expect(getQuotationDocument).not.toHaveBeenCalled();
    expect(screen.queryByText(/could not be loaded/i)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(push).toHaveBeenCalledWith('/project-sales/p1?tab=quotations');
    expect(createQuotationDocument).not.toHaveBeenCalled();
  });
});

describe('QuotationFormClient review round (fix round 2)', () => {
  it('AC-QF058: without the delete grant a saved scope offers no Remove; a new one still does', async () => {
    granted = new Set(['projects.projects.view', 'projects.projects.edit']);
    renderForm('d1');
    const card = await openLines('Townhouse');
    await within(card).findByRole('textbox', { name: 'Qty on SRT-WC-01' });

    expect(within(card).queryByRole('button', { name: 'Remove scope' })).toBeNull();
    const added = await addScope('Reception');
    expect(within(added).getByRole('button', { name: 'Remove scope' })).toBeInTheDocument();
  });

  it('AC-QF052: a scope removed on the form leaves the Header details too', async () => {
    listQuotationVersions.mockResolvedValue([version({ issued_by_name: 'Baser Ramli' })]);
    renderForm('d1');
    await openTab('Header');
    expect(await screen.findByText('Baser Ramli')).toBeInTheDocument();

    const card = await openLines('Townhouse');
    const remove = await within(card).findByRole('button', { name: 'Remove scope' });
    fireEvent.click(remove);
    await openTab('Header');
    expect(screen.queryByText('Baser Ramli')).toBeNull();
  });

  it('AC-QF056: keeps Save off on create until the letter templates have answered', async () => {
    let answer: (value: { cover_letter_html: string | null; terms_html: string | null }) => void =
      () => {};
    getQuotationLetterTemplates.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    renderForm();
    await screen.findByLabelText('Your Ref');

    expect(screen.getByRole('button', { name: 'Save quotation' })).toBeDisabled();
    answer({ cover_letter_html: '<p>Hi</p>', terms_html: null });
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Save quotation' })).toBeEnabled(),
    );
  });
});
