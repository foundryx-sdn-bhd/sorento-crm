/**
 * S2c - QualifyLeadDialog (AC-O4, AC-O5).
 *
 * Qualify is the one place a lead meets the registration lock, so what matters is that
 * a block stops the submit BEFORE the request, using the same clash panel the register
 * form uses, and that a context-only match does not.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ClashCandidate, ProjectLead } from '../../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const previewQualify = vi.fn();
const qualifyLead = vi.fn();

vi.mock('../../../_shared/services/projectService', () => ({
  previewQualify: (...args: unknown[]) => previewQualify(...args),
  qualifyLead: (...args: unknown[]) => qualifyLead(...args),
  listParties: vi.fn(async () => ({
    data: [],
    pagination: { total: 0, page: 1, limit: 50 },
  })),
  listProjectTypes: vi.fn(async () => []),
  listProjectTemplates: vi.fn(async () => []),
  listLeads: vi.fn(),
  getLead: vi.fn(),
  createLead: vi.fn(),
  updateLead: vi.fn(),
  changeLeadStatus: vi.fn(),
  disqualifyLead: vi.fn(),
  reopenLead: vi.fn(),
  deleteLead: vi.fn(),
  listDisqualifyReasons: vi.fn(async () => []),
  getLeadMetrics: vi.fn(),
  getCustomerPortfolio: vi.fn(),
  listProjectTasks: vi.fn(),
  createProjectTask: vi.fn(),
  updateProjectTask: vi.fn(),
  changeTaskStatus: vi.fn(),
  deleteProjectTask: vi.fn(),
  getTaskHistory: vi.fn(),
  listMyTasks: vi.fn(),
  listTemplateTasks: vi.fn(),
  createTemplateTask: vi.fn(),
  updateTemplateTask: vi.fn(),
  deleteTemplateTask: vi.fn(),
  listProjects: vi.fn(),
  getProject: vi.fn(),
  listStakeholders: vi.fn(),
  listCollaborators: vi.fn(),
  listTakeoverRequests: vi.fn(),
  previewClashes: vi.fn(),
  registerProject: vi.fn(),
  updateProject: vi.fn(),
  deleteProject: vi.fn(),
  changeProjectStatus: vi.fn(),
  addStakeholder: vi.fn(),
  updateStakeholder: vi.fn(),
  removeStakeholder: vi.fn(),
  createParty: vi.fn(),
  updateParty: vi.fn(),
  deleteParty: vi.fn(),
  createTakeoverRequest: vi.fn(),
  decideTakeoverRequest: vi.fn(),
  createProjectType: vi.fn(),
  updateProjectType: vi.fn(),
  deleteProjectType: vi.fn(),
  createProjectTemplate: vi.fn(),
  updateProjectTemplate: vi.fn(),
  deleteProjectTemplate: vi.fn(),
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

import { QualifyLeadDialog } from './QualifyLeadDialog';

function lead(overrides: Partial<ProjectLead> = {}): ProjectLead {
  return {
    id: 'l1',
    lead_code: 'LEAD-000001',
    title: 'Setia Alam Phase 9',
    customer_id: 'c1',
    outcome: 'open',
    project_count: 0,
    possible_duplicates: [],
    can_edit: true,
    ...overrides,
  };
}

function candidate(overrides: Partial<ClashCandidate> = {}): ClashCandidate {
  return {
    project_id: 'p9',
    project_code: 'PRJ-000009',
    title: 'Setia Alam Phase 9',
    outcome: 'open',
    owner_name: 'Siti',
    brands: [],
    similarity: 0.94,
    blocks: true,
    ...overrides,
  };
}

function renderDialog() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <QualifyLeadDialog lead={lead()} onDone={vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  previewQualify.mockResolvedValue({ candidates: [], would_block: false });
});

describe('QualifyLeadDialog', () => {
  it('carries no explanatory prose (owner, 13 Oct)', async () => {
    renderDialog();
    await screen.findByLabelText(/Project title/);
    expect(screen.queryByText(/locks it to its owner/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Split a masterplan here/)).not.toBeInTheDocument();
    expect(screen.queryByText(/sharpens the duplicate/)).not.toBeInTheDocument();
    expect(screen.queryByText(/stakeholder roles/)).not.toBeInTheDocument();
  });

  it('checks for clashes using the lead title it was opened with', async () => {
    renderDialog();

    await waitFor(() =>
      expect(previewQualify).toHaveBeenCalledWith('l1', {
        title: 'Setia Alam Phase 9',
        developer_party_id: null,
      }),
    );
  });

  it('blocks the submit when somebody already holds the development', async () => {
    previewQualify.mockResolvedValue({
      candidates: [candidate()],
      would_block: true,
    });

    renderDialog();

    const submit = await screen.findByRole('button', {
      name: /Blocked by an existing project/i,
    });
    expect(submit).toBeDisabled();
    // The incumbent is named, so the user knows who to talk to.
    expect(screen.getByText(/PRJ-000009/)).toBeInTheDocument();
    fireEvent.click(submit);
    expect(qualifyLead).not.toHaveBeenCalled();
  });

  it('still allows qualifying past a context-only match', async () => {
    previewQualify.mockResolvedValue({
      candidates: [
        candidate({ blocks: false, outcome: 'lost', project_code: 'PRJ-000004' }),
      ],
      would_block: false,
    });
    qualifyLead.mockResolvedValue({ id: 'p1', project_code: 'PRJ-000010' });

    renderDialog();

    const submit = await screen.findByRole('button', { name: /Qualify and register/i });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    await waitFor(() =>
      expect(qualifyLead).toHaveBeenCalledWith('l1', {
        title: 'Setia Alam Phase 9',
        developer_party_id: null,
        type_id: null,
        template_id: null,
      }),
    );
  });

  it('lets the title be changed, so a masterplan can be split per phase', async () => {
    qualifyLead.mockResolvedValue({ id: 'p1', project_code: 'PRJ-000011' });

    renderDialog();

    fireEvent.change(await screen.findByLabelText(/Project title/), {
      target: { value: 'Setia Alam Phase 9 Block B' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Qualify and register/i }));

    await waitFor(() =>
      expect(qualifyLead).toHaveBeenCalledWith(
        'l1',
        expect.objectContaining({ title: 'Setia Alam Phase 9 Block B' }),
      ),
    );
  });
});
