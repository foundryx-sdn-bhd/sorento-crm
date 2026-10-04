/**
 * PipelineClient - the filters moved into the toolbar.
 *
 * Developer, Type and "Critical only" used to sit in a bordered card ABOVE the grid
 * card, which is a second layout nobody else in the product uses. They are now the
 * grid toolbar's Filters popover with an active count, exactly as the users list does
 * it. Board view has no grid toolbar to host them, so it carries the same search box
 * and the same Filters button in one row instead.
 *
 * The view toggle and Start deliberately stay in the page header: they change what the
 * whole screen IS, which is not a list-toolbar job.
 *
 * Start replaces the old standalone "Register project" button (S2, R21): one page-level
 * button, top right, opening Register a project / Upload PO / Upload delivery schedule.
 * Rows carry no Start of their own.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Project } from '../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const listProjects = vi.fn();
const listParties = vi.fn();
const listProjectTypes = vi.fn();
const listingKeys: (string | null | undefined)[] = [];
const push = vi.fn();

vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/pipeline',
  useRouter: () => ({ push, replace: vi.fn() }),
  // The list restores its page, sort and filters from the query string Back hands
  // it (S3-01), so it reads the URL on every render.
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: ({ listingKey }: { listingKey?: string | null }) => {
    listingKeys.push(listingKey);
    return { resetToDefaults: async () => {}, isLoading: false };
  },
}));

vi.mock('@/app/(protected)/project-sales/_shared/hooks/useProjectStatusGraph', () => ({
  useProjectStatusGraph: () => ({ data: { statuses: [] }, isLoading: false }),
}));

vi.mock('../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../_shared/services/projectService')
  >();
  return {
    ...actual,
    listProjects: (...args: unknown[]) => listProjects(...args),
    listParties: (...args: unknown[]) => listParties(...args),
    listProjectTypes: (...args: unknown[]) => listProjectTypes(...args),
    listProjectTemplates: vi.fn(async () => []),
    previewClashes: vi.fn(async () => ({ candidates: [] })),
    registerProject: vi.fn(),
    changeProjectStatus: vi.fn(),
  };
});

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), custom: vi.fn() },
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string;
    onChange: (next: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      aria-label={placeholder ?? 'select'}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      <option value="">{placeholder ?? ''}</option>
      {(options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

import { PipelineClient } from './PipelineClient';

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    project_code: 'PRJ-000001',
    title: 'Setia Alam Phase 3B',
    outcome: 'open',
    is_critical: false,
    brands: [],
    brand_ids: [],
    can_edit: true,
    open_task_count: 0,
    ...overrides,
  } as Project;
}

function renderClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PipelineClient />
    </QueryClientProvider>,
  );
}

/** Radix opens its popovers on pointerdown, which fireEvent.click does not send. */
function openFilters() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /filters/i }), {
    button: 0,
    ctrlKey: false,
  });
}

function openStart() {
  fireEvent.pointerDown(screen.getByRole('button', { name: /^start/i }), {
    button: 0,
    ctrlKey: false,
  });
}

function switchToGrid() {
  fireEvent.click(screen.getByRole('button', { name: /grid/i }));
}

beforeEach(() => {
  vi.clearAllMocks();
  listingKeys.length = 0;
  window.localStorage.clear();
  listProjects.mockResolvedValue({
    data: [],
    pagination: { total: 0, page: 1, limit: 50 },
  });
  listParties.mockResolvedValue({
    data: [{ id: 'd1', party_type: 'developer', name: 'SP Setia', is_active: true }],
    pagination: { total: 1, page: 1, limit: 200 },
  });
  listProjectTypes.mockResolvedValue([
    { id: 'ty1', name: 'Hotel', code: 'HOTEL', derives_delivery_from_launch: false, sort_order: 1, is_active: true },
  ]);
});

describe('PipelineClient', () => {
  it('keeps the view toggle and Start in the page header', async () => {
    renderClient();

    // The page header's action group (S5-01): the hand-rolled <header> that used
    // to carry the banner role is now PageHeader's own toolbar row.
    const header = document.querySelector(
      '[data-slot="toolbar-actions"]',
    ) as HTMLElement;
    expect(within(header).getByRole('button', { name: /board/i })).toBeInTheDocument();
    expect(within(header).getByRole('button', { name: /grid/i })).toBeInTheDocument();
    expect(within(header).getByRole('button', { name: /^start/i })).toBeInTheDocument();
    // The old standalone button is gone, folded into the Start menu (R21).
    expect(
      within(header).queryByRole('button', { name: /^register project$/i }),
    ).toBeNull();

    await waitFor(() => expect(listProjects).toHaveBeenCalled());
  });

  // #1335: no subtitle under the title, and the empty board offers no second
  // CTA beside the header's Start menu.
  it('carries no subtitle, and the empty board repeats no header CTA', async () => {
    renderClient();

    const empty = (await screen.findByText('No projects registered yet'))
      .parentElement as HTMLElement;
    expect(within(empty).queryByRole('button')).toBeNull();
    expect(within(empty).queryByRole('link')).toBeNull();
    expect(
      screen.queryByText(/so nobody works a development twice/i),
    ).not.toBeInTheDocument();
  });

  it('carries no separate filter card above the grid', async () => {
    renderClient();
    switchToGrid();

    // The filters are reachable only through the toolbar's Filters button now.
    const toolbar = (await screen.findByPlaceholderText(/Search title or code/i)).closest(
      '[data-slot="card-header"]',
    ) as HTMLElement;
    expect(toolbar).not.toBeNull();
    expect(within(toolbar).getByRole('button', { name: /filters/i })).toBeInTheDocument();
    expect(within(toolbar).getByRole('button', { name: /columns/i })).toBeInTheDocument();
    expect(within(toolbar).getByRole('button', { name: /^export$/i })).toBeInTheDocument();
    // Nothing renders the filter controls outside the popover.
    expect(screen.queryByLabelText('All developers')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('All types')).not.toBeInTheDocument();
  });

  it('filters from the toolbar popover and asks the server for it', async () => {
    renderClient();
    switchToGrid();
    await screen.findByPlaceholderText(/Search title or code/i);

    openFilters();
    fireEvent.change(await screen.findByLabelText('All developers'), {
      target: { value: 'd1' },
    });

    await waitFor(() =>
      expect(listProjects).toHaveBeenCalledWith(
        expect.objectContaining({ developer_party_id: ['d1'] }),
      ),
    );
  });

  it('counts the active filters on the Filters button', async () => {
    renderClient();
    switchToGrid();
    await screen.findByPlaceholderText(/Search title or code/i);

    openFilters();
    fireEvent.change(await screen.findByLabelText('All types'), {
      target: { value: 'ty1' },
    });

    await waitFor(() =>
      expect(listProjects).toHaveBeenCalledWith(
        expect.objectContaining({ type_id: ['ty1'] }),
      ),
    );

    // Radix hides the rest of the page from the a11y tree while its menu is open, so
    // the trigger is only readable again once the popover is closed.
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });

    await waitFor(() =>
      expect(
        within(screen.getByRole('button', { name: /filters/i })).getByText('1'),
      ).toBeInTheDocument(),
    );
  });

  it('keeps the toolbar on screen when the grid is empty, so a filter can be undone', async () => {
    renderClient();
    switchToGrid();

    expect(await screen.findByText('No projects registered yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /filters/i })).toBeInTheDocument();
  });

  it('pins the pipeline listing key rather than falling back to the pathname', async () => {
    renderClient();
    switchToGrid();

    await waitFor(() => expect(listingKeys.length).toBeGreaterThan(0));
    expect(listingKeys).toContain('projects.projects.view::pipeline');
    expect(listingKeys).not.toContain('/project-sales/pipeline');
  });

  it('renders the rows the server returned', async () => {
    listProjects.mockResolvedValue({
      data: [project({ title: 'Setia Alam Phase 3B', developer_name: 'SP Setia' })],
      pagination: { total: 1, page: 1, limit: 50 },
    });

    renderClient();
    switchToGrid();

    expect(await screen.findByText('Setia Alam Phase 3B')).toBeInTheDocument();
    expect(screen.getByText('SP Setia')).toBeInTheDocument();
  });

  it('gives board view the same search box and Filters button, in one row', async () => {
    renderClient();

    expect(
      await screen.findByPlaceholderText(/Search title or code/i),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /filters/i })).toBeInTheDocument();
  });
});

/**
 * S2 - one Start button, top right, replacing the per-project entry points (R21). The
 * menu order is Register a project, then Upload PO, then Upload delivery schedule.
 */
describe('PipelineClient, Start menu (S2)', () => {
  it('opens a menu with the three items in order (S2-2)', async () => {
    renderClient();
    openStart();

    const items = await screen.findAllByRole('menuitem');
    expect(items.map((item) => item.textContent)).toEqual([
      'Register a project',
      'Upload PO',
      'Upload delivery schedule',
    ]);
  });

  it('Register a project navigates to the create page, opening no dialog (AC-PF003)', async () => {
    renderClient();
    openStart();

    fireEvent.click(await screen.findByRole('menuitem', { name: 'Register a project' }));

    expect(push).toHaveBeenCalledWith('/project-sales/new');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('Upload PO opens the PO upload dialog with a required project field first (S2-4)', async () => {
    renderClient();
    openStart();

    fireEvent.click(await screen.findByRole('menuitem', { name: 'Upload PO' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Upload a customer PO')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Search a project')).toBeInTheDocument();
    // Nothing chosen yet, so the upload is refused.
    expect(within(dialog).getByRole('button', { name: /^Upload$/ })).toBeDisabled();
  });

  it('Upload delivery schedule opens the schedule upload dialog with the project field first (S2-5)', async () => {
    renderClient();
    openStart();

    fireEvent.click(
      await screen.findByRole('menuitem', { name: 'Upload delivery schedule' }),
    );

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Upload a delivery schedule')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Search a project')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: /^Upload$/ })).toBeDisabled();
  });

  it('shows no Start action on a project row', async () => {
    listProjects.mockResolvedValue({
      data: [project({ title: 'Setia Alam Phase 3B' })],
      pagination: { total: 1, page: 1, limit: 50 },
    });
    renderClient();
    switchToGrid();

    const row = (await screen.findByText('Setia Alam Phase 3B')).closest('tr') as HTMLElement;
    expect(within(row).queryByRole('button', { name: /^start/i })).toBeNull();
  });
});
