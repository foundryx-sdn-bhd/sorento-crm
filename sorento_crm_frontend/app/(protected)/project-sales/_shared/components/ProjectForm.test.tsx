/**
 * ProjectForm, the one component both /project-sales/new and /project-sales/<id>/edit
 * render (PLAN-project-form-28sep.md, UAC project-form-28sep-acceptance-criteria.md).
 *
 * Red before the coder: this component does not exist yet. Every test here is written
 * against the contract in the plan and the captain's test list, not against any
 * implementation.
 *
 * SearchableSelect, SearchableMultiSelect and DateRangePicker are mocked to plain form
 * controls so the tests exercise ProjectForm's own wiring (which prop goes to which
 * field, what gets submitted) rather than Radix/cmdk popover mechanics, which those
 * components' own test files already cover.
 */
import React from 'react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ClashCandidate, Project } from '../types/project.types';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  usePathname: () => '/project-sales/new',
  useRouter: () => ({ push, replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));

let sessionUser: { id: string; name: string } | null = { id: 'u-me', name: 'Me' };
vi.mock('next-auth/react', () => ({
  useSession: () =>
    sessionUser
      ? { data: { user: sessionUser }, status: 'authenticated' }
      : { data: null, status: 'unauthenticated' },
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

// Same absolute module `../hooks/useProjects.ts` imports as `../services/projectService`
// (both files sit one level under `_shared/`), so this mock reaches every hook ProjectForm
// will use without ProjectForm having to know the service is mocked.
vi.mock('../services/projectService', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../services/projectService')>();
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

type Option = { value: string; label: string };

vi.mock('@/components/common/SearchableSelect', () => {
  function StaticSelect({
    id,
    value,
    onChange,
    onOptionChange,
    options,
    disabled,
    clearable,
  }: {
    id?: string;
    value: string;
    onChange: (next: string) => void;
    onOptionChange?: (option: Option | null) => void;
    options?: Option[];
    disabled?: boolean;
    clearable?: boolean;
  }) {
    return (
      <span>
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
        {clearable && value && (
          <button
            type="button"
            onClick={() => {
              onChange('');
              onOptionChange?.(null);
            }}
          >
            Clear
          </button>
        )}
      </span>
    );
  }

  // Async mode: the Lead field. Fetches on every keystroke (no debounce, this is a test
  // double) and shows the caller-supplied `selectedOption` when nothing has been typed.
  function AsyncSelect({
    id,
    value,
    onChange,
    onOptionChange,
    fetchOptions,
    selectedOption,
    clearable,
  }: {
    id?: string;
    value: string;
    onChange: (next: string) => void;
    onOptionChange?: (option: Option | null) => void;
    fetchOptions?: (query: string, pageIndex: number) => Promise<Option[]>;
    selectedOption?: Option;
    clearable?: boolean;
  }) {
    const [query, setQuery] = React.useState('');
    const [results, setResults] = React.useState<Option[]>([]);
    const displayValue =
      query === '' && selectedOption && selectedOption.value === value
        ? selectedOption.label
        : query;
    return (
      <span>
        <input
          id={id}
          value={displayValue}
          onChange={(event) => {
            setQuery(event.target.value);
            void fetchOptions?.(event.target.value, 0).then(setResults);
          }}
          placeholder="Search"
        />
        <div>
          {results.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => {
                onChange(option.value);
                onOptionChange?.(option);
                setResults([]);
                setQuery('');
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
        {clearable && value && (
          <button
            type="button"
            aria-label="Clear lead"
            onClick={() => {
              onChange('');
              onOptionChange?.(null);
              setQuery('');
            }}
          >
            Clear lead
          </button>
        )}
      </span>
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
        const selected = Array.from(event.target.selectedOptions).map((option) => option.value);
        onChange(selected);
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

import { ProjectForm } from './ProjectForm';

const DEVELOPER = { id: 'd1', party_type: 'developer', name: 'SP Setia', is_active: true };
const ARCHITECT = { id: 'a1', party_type: 'architect', name: 'Archico', is_active: true };
const CONTRACTOR = {
  id: 'c1',
  party_type: 'main_contractor',
  name: 'BuildIt',
  is_active: true,
};
const TYPE_LAUNCH = {
  id: 'ty1',
  name: 'Property',
  code: 'PROP',
  derives_delivery_from_launch: true,
  sort_order: 1,
  is_active: true,
};
const TYPE_NO_LAUNCH = {
  id: 'ty2',
  name: 'Hotel',
  code: 'HOTEL',
  derives_delivery_from_launch: false,
  sort_order: 2,
  is_active: true,
};
const BRAND_A = {
  id: 'b1',
  brand_code: 'BR1',
  brand_name: 'BrandOne',
  is_active: true,
  flows_to_purchasing: true,
} as never;

const LEAD_OPEN = {
  id: 'lead-1',
  lead_code: 'LEAD-000001',
  title: 'Menara Lead',
  outcome: 'open',
  customer_id: 'cust-1',
  project_count: 0,
  possible_duplicates: [],
  can_edit: true,
};

const BLOCKING: ClashCandidate = {
  project_id: 'p9',
  project_code: 'PRJ-000009',
  title: 'Setia Alam Phase 3B',
  outcome: 'open',
  owner_name: 'Aina',
  brands: [],
  similarity: 1,
  blocks: true,
};

function baseProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    project_code: 'PRJ-000001',
    title: 'Setia Alam Phase 3B',
    outcome: 'open',
    is_critical: false,
    developer_party_id: 'd1',
    developer_name: 'SP Setia',
    type_id: 'ty2',
    type_name: 'Hotel',
    template_id: null,
    template_name: null,
    owner_user_id: 'u-2',
    owner_name: 'Aina',
    registered_company_name: 'Setia SPV Sdn Bhd',
    location: 'Setia Alam, Selangor',
    address: '123 Jalan Setia',
    admin_ref: 'PS26-0001',
    estimated_sales_value: '1000000',
    launch_date: null,
    expected_delivery_from: '2027-01-01',
    expected_delivery_to: '2027-06-01',
    brands: ['BrandOne'],
    brand_ids: ['b1'],
    architect_party_id: 'a1',
    architect_name: 'Archico',
    main_contractor_party_id: 'c1',
    main_contractor_name: 'BuildIt',
    lead_id: 'lead-9',
    lead_code: 'LEAD-000009',
    next_action_overdue: false,
    stale_level: 0,
    is_unattended: false,
    open_task_count: 0,
    can_edit: true,
    ...overrides,
  } as Project;
}

function renderForm(props: { mode: 'create' } | { mode: 'edit'; project: Project }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProjectForm {...props} />
    </QueryClientProvider>,
  );
}

async function fillWho() {
  await waitFor(() =>
    expect(within(screen.getByLabelText('Developer')).getByText('SP Setia')).toBeInTheDocument(),
  );
  fireEvent.change(screen.getByLabelText('Developer'), { target: { value: 'd1' } });
  await waitFor(() =>
    expect(within(screen.getByLabelText('Project type')).getByText('Hotel')).toBeInTheDocument(),
  );
  fireEvent.change(screen.getByLabelText('Project type'), { target: { value: 'ty2' } });
  fireEvent.change(screen.getByLabelText(/project title/i), {
    target: { value: 'Setia Alam Phase 3B' },
  });
  fireEvent.blur(screen.getByLabelText(/project title/i));
  // Details opens by itself once the template list for the type has answered.
  await waitFor(() =>
    expect(screen.getByRole('button', { name: /^Details/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    ),
  );
}

/** Details starts folded in create mode (AC-PF033); open it by hand for its fields. */
function openDetails() {
  const header = screen.getByRole('button', { name: /^Details/ });
  if (header.getAttribute('aria-expanded') !== 'true') fireEvent.click(header);
}

async function pickType(typeId: string, typeName: string) {
  await waitFor(() =>
    expect(within(screen.getByLabelText('Project type')).getByText(typeName)).toBeInTheDocument(),
  );
  fireEvent.change(screen.getByLabelText('Project type'), { target: { value: typeId } });
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionUser = { id: 'u-me', name: 'Me' };
  hasManagePermission.mockReturnValue(true);
  listParties.mockImplementation(async (params: { party_type?: string }) => {
    const byType: Record<string, unknown[]> = {
      developer: [DEVELOPER],
      architect: [ARCHITECT],
      main_contractor: [CONTRACTOR],
    };
    return {
      data: byType[params.party_type ?? ''] ?? [],
      pagination: { total: 1, page: 1, limit: 200 },
    };
  });
  listProjectTypes.mockResolvedValue([TYPE_LAUNCH, TYPE_NO_LAUNCH]);
  listProjectTemplates.mockResolvedValue([]);
  previewClashes.mockResolvedValue({ candidates: [], would_block: false });
  registerProject.mockResolvedValue({ id: 'new1', project_code: 'PRJ-000099' });
  updateProject.mockResolvedValue(baseProject());
  listLeads.mockResolvedValue({ data: [LEAD_OPEN], pagination: { total: 1, page: 1, limit: 20 } });
  getUserLookup.mockResolvedValue([
    { id: 'u-me', name: 'Me' },
    { id: 'u-2', name: 'Aina' },
  ]);
  useBrandSelectQuery.mockReturnValue({ data: [BRAND_A], isLoading: false });
});

describe('ProjectForm: sections (AC-PF033, AC-PF034)', () => {
  it('create mode opens Who and what and Salesperson and lead, collapses Details', () => {
    renderForm({ mode: 'create' });
    expect(screen.getByRole('button', { name: /Who and what/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(screen.getByRole('button', { name: /^Details/ })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
    expect(screen.getByRole('button', { name: /Salesperson and lead/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('edit mode opens every section', () => {
    renderForm({ mode: 'edit', project: baseProject() });
    for (const name of [/Who and what/, /^Details/, /Salesperson and lead/]) {
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-expanded', 'true');
    }
  });

  it('opens Details by itself once Who and what is filled, then never reopens it by hand', async () => {
    renderForm({ mode: 'create' });
    await fillWho();
    await waitFor(() => expect(screen.getByLabelText('Location')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /^Details/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );

    fireEvent.click(screen.getByRole('button', { name: /^Details/ }));
    expect(screen.queryByLabelText('Location')).toBeNull();

    fireEvent.change(screen.getByLabelText(/project title/i), {
      target: { value: 'Setia Alam Phase 3C' },
    });
    fireEvent.blur(screen.getByLabelText(/project title/i));
    expect(screen.queryByLabelText('Location')).toBeNull();
  });
});

describe('ProjectForm: primary CTA (AC-PF007)', () => {
  it('renders exactly one submit button', () => {
    const { container } = renderForm({ mode: 'create' });
    expect(container.querySelectorAll('button[type="submit"]').length).toBe(1);
  });

  it('labels the CTA Register project on create and Save changes on edit', () => {
    renderForm({ mode: 'create' });
    expect(screen.getByRole('button', { name: 'Register project' })).toBeInTheDocument();

    renderForm({ mode: 'edit', project: baseProject() });
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument();
  });

  it('disables submit while the title is empty (AC-PF010)', () => {
    renderForm({ mode: 'create' });
    expect(screen.getByRole('button', { name: 'Register project' })).toBeDisabled();
  });
});

describe('ProjectForm: Cancel (AC-PF007)', () => {
  it('returns to the pipeline on create', () => {
    renderForm({ mode: 'create' });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(push).toHaveBeenCalledWith('/project-sales/pipeline');
  });

  it('returns to the project on edit', () => {
    renderForm({ mode: 'edit', project: baseProject() });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(push).toHaveBeenCalledWith('/project-sales/p1');
  });
});

describe('ProjectForm: Check button (AC-PF030, AC-PF031)', () => {
  it('is disabled below 4 characters', () => {
    renderForm({ mode: 'create' });
    const check = screen.getByRole('button', { name: 'Check' });
    expect(check).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/project title/i), { target: { value: 'Set' } });
    expect(check).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/project title/i), { target: { value: 'Seti' } });
    expect(check).toBeEnabled();
  });

  it('runs no duplicate check while typing', async () => {
    renderForm({ mode: 'create' });
    for (const value of ['Seti', 'Setia', 'Setia Alam Phase 3B']) {
      fireEvent.change(screen.getByLabelText(/project title/i), { target: { value } });
    }
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    expect(previewClashes).not.toHaveBeenCalled();
  });

  it('Check runs the check exactly once', async () => {
    renderForm({ mode: 'create' });
    fireEvent.change(screen.getByLabelText(/project title/i), {
      target: { value: 'Setia Alam Phase 3B' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    await waitFor(() => expect(previewClashes).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: 'Register project' })).toBeEnabled();
  });

  it('a blocking match renames and disables the CTA', async () => {
    previewClashes.mockResolvedValue({ candidates: [BLOCKING], would_block: true });
    renderForm({ mode: 'create' });
    fireEvent.change(screen.getByLabelText(/project title/i), {
      target: { value: 'Setia Alam Phase 3B' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Check' }));
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Blocked by an existing project' }),
      ).toBeDisabled(),
    );
  });

  it('submit re-runs the check as a guard even when Check was never pressed (AC-PF031)', async () => {
    renderForm({ mode: 'create' });
    await fillWho();
    fireEvent.click(screen.getByRole('button', { name: 'Register project' }));
    await waitFor(() => expect(registerProject).toHaveBeenCalledTimes(1));
    expect(previewClashes).toHaveBeenCalledTimes(1);
  });

  it('a submit-time blocking match stops the register call', async () => {
    previewClashes.mockResolvedValue({ candidates: [BLOCKING], would_block: true });
    renderForm({ mode: 'create' });
    await fillWho();
    fireEvent.click(screen.getByRole('button', { name: 'Register project' }));
    await waitFor(() => expect(previewClashes).toHaveBeenCalledTimes(1));
    expect(registerProject).not.toHaveBeenCalled();
  });
});

describe('ProjectForm: type drives Template and delivery fields (AC-PF015, PF016, PF019, PF020)', () => {
  it('shows Template only once a type is chosen', () => {
    renderForm({ mode: 'create' });
    expect(screen.queryByLabelText('Template')).toBeNull();
  });

  it('clears Template when the type changes', async () => {
    listProjectTemplates.mockResolvedValue([
      { id: 'tp1', name: 'Standard', has_forked_status_graph: false },
    ]);
    renderForm({ mode: 'create' });
    await pickType('ty2', 'Hotel');
    await waitFor(() =>
      expect(within(screen.getByLabelText('Template')).getByText('Standard')).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText('Template'), { target: { value: 'tp1' } });
    fireEvent.change(screen.getByLabelText('Project type'), { target: { value: 'ty1' } });
    await waitFor(() =>
      expect((screen.getByLabelText('Template') as HTMLSelectElement).value).toBe(''),
    );
  });

  it('shows Project launch date for a type that derives delivery from launch', async () => {
    renderForm({ mode: 'create' });
    await pickType('ty1', TYPE_LAUNCH.name);
    openDetails();
    await waitFor(() => expect(screen.getByLabelText('Project launch date')).toBeInTheDocument());
    expect(screen.queryByLabelText('Expected delivery')).toBeNull();
  });

  it('shows Expected delivery for a type that does not derive delivery from launch', async () => {
    renderForm({ mode: 'create' });
    await pickType('ty2', 'Hotel');
    openDetails();
    await waitFor(() => expect(screen.getByLabelText('Expected delivery')).toBeInTheDocument());
    expect(screen.queryByLabelText('Project launch date')).toBeNull();
  });
});

describe('ProjectForm: clearable selects (AC-PF011, PF015, PF022, PF023)', () => {
  it('Developer clears back to empty', async () => {
    renderForm({ mode: 'create' });
    await waitFor(() =>
      expect(within(screen.getByLabelText('Developer')).getByText('SP Setia')).toBeInTheDocument(),
    );
    fireEvent.change(screen.getByLabelText('Developer'), { target: { value: 'd1' } });
    const wrapper = screen.getByLabelText('Developer').parentElement as HTMLElement;
    fireEvent.click(within(wrapper).getByRole('button', { name: 'Clear' }));
    expect((screen.getByLabelText('Developer') as HTMLSelectElement).value).toBe('');
  });
});

describe('ProjectForm: text and number fields (AC-PF012-014, PF017, PF018)', () => {
  it('limits Filing reference to 64 characters', () => {
    renderForm({ mode: 'create' });
    openDetails();
    expect(screen.getByLabelText('Filing reference')).toHaveAttribute('maxLength', '64');
  });

  it('Estimated sales value is a non-negative number field', () => {
    renderForm({ mode: 'create' });
    openDetails();
    const input = screen.getByLabelText('Estimated sales value (RM)');
    expect(input).toHaveAttribute('type', 'number');
    expect(input).toHaveAttribute('min', '0');
  });

  it('renders Address as a text field', async () => {
    renderForm({ mode: 'create' });
    await fillWho();
    await waitFor(() => expect(screen.getByLabelText('Address')).toBeInTheDocument());
  });
});

describe('ProjectForm: Brands (AC-PF021)', () => {
  it('preselects the project brands in edit mode', () => {
    renderForm({ mode: 'edit', project: baseProject({ brand_ids: ['b1'] }) });
    const select = screen.getByLabelText('Brands') as HTMLSelectElement;
    expect(Array.from(select.selectedOptions).map((option) => option.value)).toEqual(['b1']);
  });

  it('sends brand_ids on create', async () => {
    renderForm({ mode: 'create' });
    await fillWho();
    const select = screen.getByLabelText('Brands') as HTMLSelectElement;
    await userEvent.selectOptions(select, ['b1']);
    fireEvent.click(screen.getByRole('button', { name: 'Register project' }));
    await waitFor(() =>
      expect(registerProject).toHaveBeenCalledWith(
        expect.objectContaining({ brand_ids: ['b1'] }),
      ),
    );
  });
});

describe('ProjectForm: Salesperson (AC-PF040-043)', () => {
  it('defaults to the current session user on create', async () => {
    renderForm({ mode: 'create' });
    await waitFor(() => expect(getUserLookup).toHaveBeenCalled());
    await waitFor(() =>
      expect((screen.getByLabelText('Salesperson') as HTMLSelectElement).value).toBe('u-me'),
    );
  });

  it('defaults to the current owner on edit', async () => {
    renderForm({ mode: 'edit', project: baseProject({ owner_user_id: 'u-2' }) });
    await waitFor(() =>
      expect((screen.getByLabelText('Salesperson') as HTMLSelectElement).value).toBe('u-2'),
    );
  });

  it('is disabled without manage permission', async () => {
    hasManagePermission.mockReturnValue(false);
    renderForm({ mode: 'create' });
    await waitFor(() => expect(screen.getByLabelText('Salesperson')).toBeDisabled());
  });

  it('lets a manage holder pick another salesperson on create, sent as owner_user_id', async () => {
    renderForm({ mode: 'create' });
    await fillWho();
    await waitFor(() => expect(screen.getByLabelText('Salesperson')).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText('Salesperson'), { target: { value: 'u-2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Register project' }));
    await waitFor(() =>
      expect(registerProject).toHaveBeenCalledWith(
        expect.objectContaining({ owner_user_id: 'u-2' }),
      ),
    );
  });
});

describe('ProjectForm: Lead link (AC-PF050-058)', () => {
  it('searches open leads with the typed text, capped at 20', async () => {
    renderForm({ mode: 'create' });
    fireEvent.change(screen.getByLabelText('Lead'), { target: { value: 'Menara' } });
    await waitFor(() =>
      expect(listLeads).toHaveBeenCalledWith({ query: 'Menara', outcome: ['open'], limit: 20 }),
    );
  });

  it('shows the current lead on the trigger in edit mode though listLeads never returns it', () => {
    renderForm({
      mode: 'edit',
      project: baseProject({ lead_id: 'lead-9', lead_code: 'LEAD-000009' }),
    });
    expect(screen.getByDisplayValue(/LEAD-000009/)).toBeInTheDocument();
    expect(listLeads).not.toHaveBeenCalled();
  });

  it('sends lead_id on create when a lead is picked', async () => {
    renderForm({ mode: 'create' });
    await fillWho();
    fireEvent.change(screen.getByLabelText('Lead'), { target: { value: 'Menara' } });
    fireEvent.click(await screen.findByText(/LEAD-000001/));
    fireEvent.click(screen.getByRole('button', { name: 'Register project' }));
    await waitFor(() =>
      expect(registerProject).toHaveBeenCalledWith(
        expect.objectContaining({ lead_id: 'lead-1' }),
      ),
    );
  });

  it('sends lead_id null on edit when the lead is cleared', async () => {
    renderForm({
      mode: 'edit',
      project: baseProject({ lead_id: 'lead-9', lead_code: 'LEAD-000009' }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Clear lead' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ lead_id: null }),
      ),
    );
  });

  it('leaves lead_id alone on edit when the lead is not touched', async () => {
    renderForm({
      mode: 'edit',
      project: baseProject({ lead_id: 'lead-9', lead_code: 'LEAD-000009' }),
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(updateProject).toHaveBeenCalled());
    const body = updateProject.mock.calls[0][1] as { lead_id?: string | null };
    expect(body.lead_id === undefined || body.lead_id === 'lead-9').toBe(true);
  });
});

describe('ProjectForm: create submit (AC-PF024)', () => {
  it('registers with the filled fields and navigates to the new project', async () => {
    renderForm({ mode: 'create' });
    await fillWho();
    fireEvent.change(screen.getByLabelText('Registered company / SPV'), {
      target: { value: 'Setia SPV' },
    });
    fireEvent.change(screen.getByLabelText('Location'), { target: { value: 'Setia Alam' } });
    fireEvent.change(screen.getByLabelText('Address'), {
      target: { value: '123 Jalan Setia' },
    });
    fireEvent.change(screen.getByLabelText('Filing reference'), {
      target: { value: 'PS26-0099' },
    });
    fireEvent.change(screen.getByLabelText('Estimated sales value (RM)'), {
      target: { value: '500000' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Register project' }));

    await waitFor(() =>
      expect(registerProject).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Setia Alam Phase 3B',
          developer_party_id: 'd1',
          type_id: 'ty2',
          registered_company_name: 'Setia SPV',
          location: 'Setia Alam',
          address: '123 Jalan Setia',
          admin_ref: 'PS26-0099',
          estimated_sales_value: '500000',
        }),
      ),
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith('/project-sales/new1'));
  });
});

describe('ProjectForm: edit submit (AC-PF025)', () => {
  it('saves the edited fields and navigates back to the project', async () => {
    renderForm({ mode: 'edit', project: baseProject() });
    fireEvent.change(screen.getByLabelText('Location'), { target: { value: 'New Location' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ location: 'New Location' }),
      ),
    );
    await waitFor(() => expect(push).toHaveBeenCalledWith('/project-sales/p1'));
  });

  it('sends null for an optional text field that was cleared', async () => {
    renderForm({
      mode: 'edit',
      project: baseProject({ registered_company_name: 'Setia SPV' }),
    });
    fireEvent.change(screen.getByLabelText('Registered company / SPV'), {
      target: { value: '' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ registered_company_name: null }),
      ),
    );
  });
});

describe('ProjectForm: edit guards (review round 1, PR #1345)', () => {
  const blockingMatch = (projectId: string): ClashCandidate => ({
    project_id: projectId,
    project_code: `PRJ-${projectId}`,
    title: 'Setia Alam Phase 3B',
    outcome: 'open',
    brands: [],
    similarity: 1,
    blocks: true,
  });

  it('keeps the project template when the edit form opens under StrictMode (AC-PF015, PF025)', async () => {
    listProjectTemplates.mockResolvedValue([
      { id: 'tp1', name: 'Standard', has_forked_status_graph: false },
    ]);
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <React.StrictMode>
        <QueryClientProvider client={client}>
          <ProjectForm mode="edit" project={baseProject({ template_id: 'tp1' })} />
        </QueryClientProvider>
      </React.StrictMode>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ template_id: 'tp1' }),
      ),
    );
  });

  it('saves an unrelated field without running the clash guard (AC-PF032)', async () => {
    previewClashes.mockResolvedValue({ candidates: [blockingMatch('p9')], would_block: true });
    renderForm({ mode: 'edit', project: baseProject() });
    fireEvent.change(screen.getByLabelText('Location'), { target: { value: 'Puchong' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ location: 'Puchong' }),
      ),
    );
    expect(previewClashes).not.toHaveBeenCalled();
  });

  it('a rename matching only the project itself is not blocked (AC-PF032)', async () => {
    previewClashes.mockResolvedValue({ candidates: [blockingMatch('p1')], would_block: true });
    renderForm({ mode: 'edit', project: baseProject() });
    fireEvent.change(screen.getByLabelText(/project title/i), {
      target: { value: 'Setia Alam Phase 3B Tower' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(updateProject).toHaveBeenCalledWith(
        'p1',
        expect.objectContaining({ title: 'Setia Alam Phase 3B Tower' }),
      ),
    );
    expect(previewClashes).toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Blocked by an existing project' })).toBeNull();
  });

  it('a rename onto another project is still blocked (AC-PF032)', async () => {
    previewClashes.mockResolvedValue({ candidates: [blockingMatch('p9')], would_block: true });
    renderForm({ mode: 'edit', project: baseProject() });
    fireEvent.change(screen.getByLabelText(/project title/i), {
      target: { value: 'Setia Alam Phase 3B Tower' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: 'Blocked by an existing project' }),
      ).toBeDisabled(),
    );
    expect(updateProject).not.toHaveBeenCalled();
  });
});
