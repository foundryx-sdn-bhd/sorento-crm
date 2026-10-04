/**
 * EditProjectClient: loads the project, then renders ProjectForm in edit mode, or a
 * read-only refusal when the viewer cannot edit (AC-PF002, AC-PF008).
 *
 * Red before the coder: neither this component nor `ProjectForm` exist yet. The
 * SearchableSelect / SearchableMultiSelect / DateRangePicker mocks mirror
 * `ProjectForm.test.tsx` exactly, because the real `ProjectForm` renders under this
 * component and the "prefilled fields" assertions need real labelled inputs to read.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Project } from '../../../_shared/types/project.types';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/p1/edit',
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));

vi.mock('next-auth/react', () => ({
  useSession: () => ({ data: { user: { id: 'u-me', name: 'Me' } }, status: 'authenticated' }),
}));

const hasManagePermission = vi.fn(() => true);
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => hasManagePermission(slug),
}));

const getUserLookup = vi.fn();
vi.mock('@/services/userSelectService', () => ({
  getUserLookup: (...args: unknown[]) => getUserLookup(...args),
}));

const useBrandSelectQuery = vi.fn();
vi.mock(
  '@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query',
  () => ({
    useBrandSelectQuery: () => useBrandSelectQuery(),
  }),
);

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), custom: vi.fn() },
}));

const listParties = vi.fn();
const listProjectTypes = vi.fn();
const listProjectTemplates = vi.fn();
const previewClashes = vi.fn();
const registerProject = vi.fn();
const updateProject = vi.fn();
const listLeads = vi.fn();

vi.mock('../../../_shared/services/projectService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../_shared/services/projectService')>();
  return {
    ...actual,
    listParties: (...args: unknown[]) => listParties(...args),
    listProjectTypes: (...args: unknown[]) => listProjectTypes(...args),
    listProjectTemplates: (...args: unknown[]) => listProjectTemplates(...args),
    previewClashes: (...args: unknown[]) => previewClashes(...args),
    registerProject: (...args: unknown[]) => registerProject(...args),
    updateProject: (...args: unknown[]) => updateProject(...args),
    listLeads: (...args: unknown[]) => listLeads(...args),
  };
});

// Everything else from the shared hooks module stays real (useRegisterProject,
// useUpdateProject, useProjectParties, ...) - only `useProject` is swapped for the
// fixture this file drives, so ProjectForm's own hooks keep working underneath it.
let projectFixture: Project | undefined;
let projectLoading = false;
vi.mock('../../../_shared/hooks/useProjects', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../_shared/hooks/useProjects')>();
  return {
    ...actual,
    useProject: () => ({ data: projectFixture, isLoading: projectLoading, isError: false }),
  };
});

type Option = { value: string; label: string };

vi.mock('@/components/common/SearchableSelect', () => {
  function StaticSelect({
    id,
    value,
    onChange,
    onOptionChange,
    options,
    disabled,
  }: {
    id?: string;
    value: string;
    onChange: (next: string) => void;
    onOptionChange?: (option: Option | null) => void;
    options?: Option[];
    disabled?: boolean;
  }) {
    return (
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(event) => {
          const next = event.target.value;
          onChange(next);
          onOptionChange?.((options ?? []).find((option) => option.value === next) ?? null);
        }}
      >
        <option value="" />
        {(options ?? []).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  function AsyncSelect({
    id,
    value,
    fetchOptions,
    selectedOption,
  }: {
    id?: string;
    value: string;
    fetchOptions?: (query: string, pageIndex: number) => Promise<Option[]>;
    selectedOption?: Option;
  }) {
    void fetchOptions;
    return (
      <input
        id={id}
        readOnly
        value={selectedOption && selectedOption.value === value ? selectedOption.label : ''}
      />
    );
  }

  return {
    SearchableSelect: (props: Record<string, unknown>) =>
      props.fetchOptions ? (
        <AsyncSelect {...(props as never)} />
      ) : (
        <StaticSelect {...(props as never)} />
      ),
  };
});

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: ({
    id,
    value,
    onChange,
    options,
  }: {
    id?: string;
    value: string[];
    onChange: (next: string[]) => void;
    options?: Option[];
  }) => (
    <select
      id={id}
      multiple
      value={value}
      onChange={(event) => {
        onChange(Array.from(event.target.selectedOptions).map((option) => option.value));
      }}
    >
      {(options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

vi.mock('@/components/ui/date-range-picker', () => ({
  DateRangePicker: ({
    id,
    from,
    to,
    onChange,
  }: {
    id?: string;
    from: string | null;
    to: string | null;
    onChange: (next: { from: string | null; to: string | null }) => void;
  }) => (
    <input
      id={id}
      value={[from ?? '', to ?? ''].join(',')}
      onChange={(event) => {
        const [nextFrom, nextTo] = event.target.value.split(',');
        onChange({ from: nextFrom || null, to: nextTo || null });
      }}
    />
  ),
}));

import { EditProjectClient } from './EditProjectClient';

function fixture(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    project_code: 'PRJ-000001',
    title: 'Setia Alam Phase 3B',
    outcome: 'open',
    is_critical: false,
    location: 'Setia Alam, Selangor',
    admin_ref: 'PS26-0001',
    brands: [],
    brand_ids: [],
    next_action_overdue: false,
    stale_level: 0,
    is_unattended: false,
    open_task_count: 0,
    can_edit: true,
    ...overrides,
  } as Project;
}

function renderClient() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <EditProjectClient projectId="p1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  hasManagePermission.mockReturnValue(true);
  projectLoading = false;
  projectFixture = fixture();
  listParties.mockResolvedValue({ data: [], pagination: { total: 0, page: 1, limit: 200 } });
  listProjectTypes.mockResolvedValue([]);
  listProjectTemplates.mockResolvedValue([]);
  previewClashes.mockResolvedValue({ candidates: [], would_block: false });
  updateProject.mockResolvedValue(fixture());
  listLeads.mockResolvedValue({ data: [], pagination: { total: 0, page: 1, limit: 20 } });
  getUserLookup.mockResolvedValue([{ id: 'u-me', name: 'Me' }]);
  useBrandSelectQuery.mockReturnValue({ data: [], isLoading: false });
});

describe('EditProjectClient (AC-PF002, AC-PF008)', () => {
  it('renders neither the form nor the refusal while loading', () => {
    projectLoading = true;
    projectFixture = undefined;
    renderClient();
    expect(screen.queryByLabelText(/project title/i)).toBeNull();
    expect(screen.queryByText('You cannot edit this project')).toBeNull();
  });

  it('shows a read-only refusal and no form when the viewer cannot edit', () => {
    projectFixture = fixture({ can_edit: false });
    renderClient();
    expect(screen.getByText('You cannot edit this project')).toBeInTheDocument();
    expect(screen.queryByLabelText(/project title/i)).toBeNull();
  });

  it('prefills the form from the project when the viewer can edit', () => {
    projectFixture = fixture({
      title: 'Menara Test',
      location: 'Cyberjaya',
      admin_ref: 'PS26-0042',
      can_edit: true,
    });
    renderClient();
    expect((screen.getByLabelText(/project title/i) as HTMLInputElement).value).toBe(
      'Menara Test',
    );
    expect((screen.getByLabelText('Location') as HTMLInputElement).value).toBe('Cyberjaya');
    expect((screen.getByLabelText('Filing reference') as HTMLInputElement).value).toBe(
      'PS26-0042',
    );
  });
});
