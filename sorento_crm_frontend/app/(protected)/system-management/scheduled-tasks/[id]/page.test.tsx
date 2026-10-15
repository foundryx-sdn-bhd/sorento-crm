/**
 * ROUTE-PERM-GATES: the scheduled task detail page (Run now, the config form) is
 * for admins, like the list page that links to it.
 *
 * Its two writes (PATCH and run-now) now take the admin role on the server. The
 * list page was already `<RequireAccess superadmin>`; the detail page was not,
 * so a deep link put Run now in front of anyone signed in.
 */
import React, { Suspense } from 'react';
import { act, cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

let isSuperadmin: boolean | null = false;
vi.mock('@/hooks/useEffectiveIsSuperadmin', () => ({
  useEffectiveIsSuperadmin: () => isSuperadmin,
}));

vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('next/navigation', () => ({
  usePathname: () => '/system-management/scheduled-tasks/task-1',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const noopMutation = { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false };
const useScheduledTask = vi.fn((id: string) => ({
  data: {
    id,
    key: 'respond_contacts_sync',
    name: 'Respond contacts sync',
    description: null,
    enabled: true,
    last_status: 'success',
    last_run_at: null,
    next_run_at: null,
    last_error: null,
  },
  isLoading: false,
}));
vi.mock('../hooks/useScheduledTasks', () => ({
  useScheduledTask: (id: string) => useScheduledTask(id),
  useUpdateScheduledTask: () => noopMutation,
  useRunScheduledTaskNow: () => noopMutation,
}));
vi.mock('../components/ScheduledTaskForm', () => ({
  ScheduledTaskForm: () => <div>task-form</div>,
}));
vi.mock('../components/RunLogsTable', () => ({
  RunLogsTable: () => <div>run-logs</div>,
}));

import ScheduledTaskDetailPage from './page';

async function renderPage() {
  // `act` lets the page's `use(params)` settle before the assertions run.
  await act(async () => {
    render(
      <Suspense fallback={<div>suspended</div>}>
        <ScheduledTaskDetailPage params={Promise.resolve({ id: 'task-1' })} />
      </Suspense>,
    );
  });
}

afterEach(() => {
  cleanup();
  useScheduledTask.mockClear();
});

describe('Scheduled task detail page', () => {
  it('answers no access to a non-admin and never loads the task', async () => {
    isSuperadmin = false;
    await renderPage();

    expect(screen.getByText("You don't have access to this page")).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Run now/ })).not.toBeInTheDocument();
    expect(screen.queryByText('task-form')).not.toBeInTheDocument();
    expect(useScheduledTask).not.toHaveBeenCalled();
  });

  it('renders Run now and the form for an admin', async () => {
    isSuperadmin = true;
    await renderPage();

    expect(screen.getByRole('button', { name: /Run now/ })).toBeInTheDocument();
    expect(screen.getByText('task-form')).toBeInTheDocument();
  });
});
