/**
 * S2b - TasksPanel (AC-N3, AC-N4, AC-N6).
 *
 * What is worth pinning here is the SHAPE of the tab, because the shape is the design
 * decision: work-stream sections rather than status columns, a phase default that
 * follows the project's outcome, and a status move that stops for context when the
 * target rung demands it.
 *
 * SearchableSelect is mocked to a native <select> per repo convention: the real one is
 * a Radix popover plus cmdk list, which jsdom drives unreliably.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Project, ProjectTask } from '../../_shared/types/project.types';

if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const listProjectTasks = vi.fn();
const changeTaskStatus = vi.fn();

vi.mock('../../_shared/services/projectService', () => ({
  listProjectTasks: (...args: unknown[]) => listProjectTasks(...args),
  createProjectTask: vi.fn(),
  updateProjectTask: vi.fn(),
  changeTaskStatus: (...args: unknown[]) => changeTaskStatus(...args),
  deleteProjectTask: vi.fn(),
  getTaskHistory: vi.fn(async () => []),
  listMyTasks: vi.fn(),
  listTemplateTasks: vi.fn(),
  createTemplateTask: vi.fn(),
  updateTemplateTask: vi.fn(),
  deleteTemplateTask: vi.fn(),
  listProjects: vi.fn(),
  getProject: vi.fn(),
  listProjectTypes: vi.fn(),
  listProjectTemplates: vi.fn(),
  listParties: vi.fn(),
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
    'aria-label': ariaLabel,
  }: {
    value: string;
    onChange: (next: string) => void;
    options?: { value: string; label: string }[];
    placeholder?: string;
    'aria-label'?: string;
  }) => (
    <select
      aria-label={ariaLabel ?? placeholder ?? 'select'}
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

const STATUSES = [
  { id: 's-not-started', key: 'not_started', label: 'Not Started', sort_order: 0 },
  { id: 's-in-progress', key: 'in_progress', label: 'In Progress', sort_order: 1 },
  { id: 's-escalate', key: 'escalate', label: 'Escalate', sort_order: 2 },
  { id: 's-stuck', key: 'stuck', label: 'Stuck', sort_order: 3 },
  { id: 's-done', key: 'done', label: 'Done', sort_order: 4 },
].map((status) => ({
  ...status,
  entity_type: 'project_task',
  scope_id: null,
  category: null,
  color_hex: null,
  description: null,
  is_initial: status.key === 'not_started',
  is_terminal: status.key === 'done',
  is_active: true,
  is_archived: false,
  is_default: status.key === 'not_started',
  is_system: false,
}));

vi.mock('@/app/(protected)/project-sales/_shared/hooks/useProjectStatusGraph', () => ({
  useProjectStatusGraph: () => ({ data: { statuses: STATUSES, transitions: [] } }),
}));

vi.mock('@/services/userSelectService', () => ({
  getUserLookup: vi.fn(async () => [
    { id: 'u-siti', name: 'Siti' },
  ]),
}));

import { TasksPanel } from './TasksPanel';

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p1',
    project_code: 'PRJ-000001',
    title: 'Menara Test',
    outcome: 'open',
    template_id: 'tpl-1',
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

function task(overrides: Partial<ProjectTask> = {}): ProjectTask {
  return {
    id: 't1',
    project_id: 'p1',
    name: 'Task',
    task_phase: 'pursuit',
    status_id: 's-not-started',
    status_key: 'not_started',
    status_label: 'Not Started',
    is_open: true,
    is_overdue: false,
    sort_order: 0,
    can_edit: true,
    ...overrides,
  };
}

function renderPanel(overrides: Partial<Project> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TasksPanel project={project(overrides)} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  listProjectTasks.mockResolvedValue([]);
});

describe('TasksPanel', () => {
  it('asks for the pursuit phase on an open project', async () => {
    renderPanel({ outcome: 'open' });

    await screen.findByText(/No pursuit tasks on this project/i);
    expect(listProjectTasks).toHaveBeenCalledWith('p1', 'pursuit');
  });

  it('asks for the delivery phase once the project is won', async () => {
    renderPanel({ outcome: 'won' });

    await screen.findByText(/No delivery tasks on this project/i);
    expect(listProjectTasks).toHaveBeenCalledWith('p1', 'delivery');
  });

  it('groups into work-stream sections, not status columns', async () => {
    listProjectTasks.mockResolvedValue([
      task({ id: 'a', name: 'Get spec in', category: 'Spec-in' }),
      task({ id: 'b', name: 'Send samples', category: 'Sampling' }),
      task({
        id: 'c',
        name: 'Agree price',
        category: 'Commercial',
        status_id: 's-in-progress',
        status_key: 'in_progress',
        status_label: 'In Progress',
      }),
    ]);

    renderPanel();

    // Sections are the work-streams. A status-column board would have shown
    // "Not Started" and "In Progress" as the headings instead.
    expect(await screen.findByRole('button', { name: /Spec-in/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sampling/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Commercial/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Not Started/ })).not.toBeInTheDocument();
  });

  it('collapses a section with nothing open and keeps the open one expanded', async () => {
    listProjectTasks.mockResolvedValue([
      task({ id: 'a', name: 'Still to do', category: 'Live stream', is_open: true }),
      task({
        id: 'b',
        name: 'Already finished',
        category: 'Finished stream',
        is_open: false,
        status_id: 's-done',
        status_key: 'done',
        status_label: 'Done',
      }),
    ]);

    renderPanel();

    expect(await screen.findByText('Still to do')).toBeInTheDocument();
    expect(screen.queryByText('Already finished')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Finished stream/ }));
    expect(screen.getByText('Already finished')).toBeInTheDocument();
  });

  it('surfaces overdue work in the header count and on the row', async () => {
    listProjectTasks.mockResolvedValue([
      task({
        id: 'a',
        name: 'Late one',
        category: 'Spec-in',
        due_date: '2026-07-01',
        is_overdue: true,
      }),
    ]);

    renderPanel();

    // Twice on purpose: the tab-wide summary and the work-stream section header.
    expect(await screen.findAllByText('1 overdue')).toHaveLength(2);
    expect(screen.getByText(/01 Jul 2026 \(overdue\)/)).toBeInTheDocument();
  });

  it('says a task has no due date rather than leaving the row silent', async () => {
    listProjectTasks.mockResolvedValue([task({ id: 'a', name: 'Undated', category: 'Spec-in' })]);

    renderPanel();

    expect(await screen.findByText('No due date')).toBeInTheDocument();
  });

  it('moves an ordinary rung in one request, with no dialog in the way', async () => {
    listProjectTasks.mockResolvedValue([
      task({ id: 'a', name: 'Get spec in', category: 'Spec-in' }),
    ]);
    changeTaskStatus.mockResolvedValue(
      task({ id: 'a', status_id: 's-in-progress', status_label: 'In Progress' }),
    );

    renderPanel();

    fireEvent.change(await screen.findByLabelText('Status of Get spec in'), {
      target: { value: 's-in-progress' },
    });

    await waitFor(() =>
      expect(changeTaskStatus).toHaveBeenCalledWith('p1', 'a', {
        to_status_id: 's-in-progress',
      }),
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('refuses to escalate until an escalation target is chosen, then sends both together', async () => {
    listProjectTasks.mockResolvedValue([
      task({ id: 'a', name: 'Get spec in', category: 'Spec-in' }),
    ]);
    changeTaskStatus.mockResolvedValue(task({ id: 'a' }));

    renderPanel();

    fireEvent.change(await screen.findByLabelText('Status of Get spec in'), {
      target: { value: 's-escalate' },
    });

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Escalate' })).toBeDisabled();
    expect(changeTaskStatus).not.toHaveBeenCalled();

    const picker = await within(dialog).findByLabelText('Select a person');
    fireEvent.change(picker, { target: { value: 'u-siti' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Escalate' }));

    await waitFor(() =>
      expect(changeTaskStatus).toHaveBeenCalledWith('p1', 'a', {
        to_status_id: 's-escalate',
        escalated_to_user_id: 'u-siti',
        stuck_reason: undefined,
      }),
    );
  });

  it('refuses to flag stuck until a reason is given, then sends both together', async () => {
    listProjectTasks.mockResolvedValue([
      task({ id: 'a', name: 'Get spec in', category: 'Spec-in' }),
    ]);
    changeTaskStatus.mockResolvedValue(task({ id: 'a' }));

    renderPanel();

    fireEvent.change(await screen.findByLabelText('Status of Get spec in'), {
      target: { value: 's-stuck' },
    });

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Flag as stuck' })).toBeDisabled();

    fireEvent.change(within(dialog).getByLabelText(/What is blocking it/), {
      target: { value: 'Waiting on the architect' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Flag as stuck' }));

    await waitFor(() =>
      expect(changeTaskStatus).toHaveBeenCalledWith('p1', 'a', {
        to_status_id: 's-stuck',
        escalated_to_user_id: undefined,
        stuck_reason: 'Waiting on the architect',
      }),
    );
  });

  it('switches to a read-only timeline of dated work (AC-N7)', async () => {
    listProjectTasks.mockResolvedValue([
      task({
        id: 'a',
        name: 'Dated one',
        category: 'Spec-in',
        start_date: '2026-07-01',
        due_date: '2026-07-20',
      }),
      task({ id: 'b', name: 'Undated one', category: 'Spec-in' }),
    ]);

    renderPanel();

    fireEvent.click(await screen.findByRole('tab', { name: 'Timeline' }));

    expect(screen.getByText('Jul 2026')).toBeInTheDocument();
    // Undated work is reported, not silently dropped off the chart.
    expect(screen.getByText('1 task with no dates')).toBeInTheDocument();
    // Timeline is a read-only view: no per-row status control.
    expect(screen.queryByLabelText('Status of Dated one')).not.toBeInTheDocument();
  });

  it('explains an empty timeline instead of rendering a blank chart', async () => {
    listProjectTasks.mockResolvedValue([task({ id: 'a', name: 'No dates', category: 'Spec-in' })]);

    renderPanel();

    fireEvent.click(await screen.findByRole('tab', { name: 'Timeline' }));

    expect(screen.getByText(/Nothing to plot yet/i)).toBeInTheDocument();
  });

  it('hides every write control on a project the user may not edit', async () => {
    listProjectTasks.mockResolvedValue([
      task({ id: 'a', name: 'Get spec in', category: 'Spec-in', can_edit: false }),
    ]);

    renderPanel({ can_edit: false });

    await screen.findByText('Get spec in');
    expect(screen.queryByRole('button', { name: 'Add task' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Status of Get spec in')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Delete Get spec in')).not.toBeInTheDocument();
    // History stays available: read-only users still need to see what happened.
    expect(screen.getByLabelText('History of Get spec in')).toBeInTheDocument();
  });

  it('tells a template-less project why it has no checklist', async () => {
    renderPanel({ template_id: null });

    expect(
      await screen.findByText(/Set a project type and template so the checklist copies in/i),
    ).toBeInTheDocument();
  });
});
