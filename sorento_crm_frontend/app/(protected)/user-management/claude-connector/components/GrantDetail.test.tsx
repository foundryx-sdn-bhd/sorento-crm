/**
 * CLAUDE-CONNECTOR S3 red test (mockup sections 3 and 3b).
 *   file  app/(protected)/user-management/claude-connector/components/GrantDetail.tsx  (default export, prop { id })
 *   page  app/(protected)/user-management/claude-connector/[id]/page.tsx
 * Reads useGrantQuery(id) -> { data: GrantDetail, isLoading, isError }; revoke via useGrantRevocation().
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render as rtlRender, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// The recent-calls DataGrid reads column preferences through react-query, as on the real page.
const render = (ui: React.ReactElement) => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(ui, {
    wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>,
  });
};

const useGrantQuery = vi.fn();
const useGrantRevocation = vi.fn();
vi.mock('../hooks/useClaudeConnector', () => ({
  useGrantQuery: (...a: unknown[]) => useGrantQuery(...a),
  useGrantRevocation: () => useGrantRevocation(),
}));
vi.mock('@/components/common/ListPager', () => ({
  __esModule: true,
  default: () => <div data-testid="list-pager" />,
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/user-management/claude-connector/g1',
  useSearchParams: () => new URLSearchParams(),
}));

import GrantDetail from './GrantDetail';

const detail = (over: Record<string, unknown> = {}) => ({
  id: 'g1',
  user_id: 'u1',
  user_name: 'Sales Manager A',
  client_name: 'Claude',
  created_at: '2026-10-08T01:12:00',
  last_used_at: '2026-10-09T02:41:00',
  expires_at: '2027-01-06T01:12:00',
  status: 'Active',
  recent_calls: [
    { time: '2026-10-09T02:41:07', tool: 'chat_query', rows: 200, ms: 412, status: 'ok' },
    { time: '2026-10-09T02:31:44', tool: 'crm_low_stock_report', rows: 0, ms: 91, status: 'denied' },
  ],
  ...over,
});

let run: ReturnType<typeof vi.fn>;
beforeEach(() => {
  run = vi.fn();
  useGrantQuery.mockReset();
  useGrantRevocation.mockReset().mockReturnValue({ run, targetId: null, isPending: false, countdown: null });
});
afterEach(() => cleanup());

describe('GrantDetail', () => {
  it('shows the metadata, the line tab and the recent tool calls', () => {
    useGrantQuery.mockReturnValue({ data: detail(), isLoading: false, isError: false });
    render(<GrantDetail id="g1" />);
    expect(useGrantQuery).toHaveBeenCalledWith('g1');
    expect(screen.getByRole('heading', { name: /Sales Manager A/ })).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    for (const l of ['Client', 'Created', 'Last used', 'Expires']) {
      expect(screen.getByText(l)).toBeInTheDocument();
    }
    expect(screen.getByText('Claude')).toBeInTheDocument();
    expect(screen.getByText(/recent tool calls/i)).toBeInTheDocument();
    expect(screen.getByText('chat_query')).toBeInTheDocument();
    expect(screen.getByText('crm_low_stock_report')).toBeInTheDocument();
    expect(screen.getByText('412')).toBeInTheDocument();
    expect(screen.getByText('OK')).toBeInTheDocument();
    expect(screen.getByText('Denied')).toBeInTheDocument();
    expect(screen.getByTestId('list-pager')).toBeInTheDocument();
  });

  it('has Revoke as the one header action, parked without a dialog', () => {
    useGrantQuery.mockReturnValue({ data: detail(), isLoading: false, isError: false });
    render(<GrantDetail id="g1" />);
    const btn = screen.getByRole('button', { name: /^revoke$/i });
    fireEvent.click(btn);
    expect(run.mock.calls[0][0]).toMatchObject({ id: 'g1', subject: 'Sales Manager A' });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('shows the countdown in place of Revoke while parked', () => {
    useGrantRevocation.mockReturnValue({
      run, targetId: 'g1', isPending: true, countdown: <span>Revoking in 5s</span>,
    });
    useGrantQuery.mockReturnValue({ data: detail(), isLoading: false, isError: false });
    render(<GrantDetail id="g1" />);
    expect(screen.getByText('Revoking in 5s')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^revoke$/i })).not.toBeInTheDocument();
  });

  it('offers no Revoke on a grant that is already revoked', () => {
    useGrantQuery.mockReturnValue({ data: detail({ status: 'Revoked' }), isLoading: false, isError: false });
    render(<GrantDetail id="g1" />);
    expect(screen.getByText('Revoked')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^revoke$/i })).not.toBeInTheDocument();
  });

  it('renders an explicit empty state with a heading, a hint and no button', () => {
    useGrantQuery.mockReturnValue({
      data: detail({ recent_calls: [], last_used_at: null }),
      isLoading: false,
      isError: false,
    });
    render(<GrantDetail id="g1" />);
    const heading = screen.getByText('No tool calls yet');
    expect(screen.getByText('Calls made through this grant appear here.')).toBeInTheDocument();
    expect(heading.parentElement?.querySelector('button')).toBeNull();
    expect(screen.getAllByText('-').length).toBeGreaterThan(0); // Last used
  });

  it('has loading and error states', () => {
    useGrantQuery.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    const first = render(<GrantDetail id="g1" />);
    first.unmount();
    useGrantQuery.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<GrantDetail id="g1" />);
    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument();
  });
});
