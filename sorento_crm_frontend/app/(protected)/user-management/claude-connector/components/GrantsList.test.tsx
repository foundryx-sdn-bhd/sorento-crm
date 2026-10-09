/**
 * CLAUDE-CONNECTOR S3 red test (mockup section 2).
 *   file  app/(protected)/user-management/claude-connector/components/GrantsList.tsx  (default export)
 *   page  app/(protected)/user-management/claude-connector/page.tsx  (PageHeader "Claude connector access")
 * Reads useGrantsQuery -> { data: { data: GrantRow[], pagination }, isLoading, isError }.
 * Revoke uses useGrantRevocation() ({ run, targetId, countdown }); no dialog of any kind.
 */
import React from 'react';
import fs from 'node:fs';
import path from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

const useGrantsQuery = vi.fn();
const useGrantRevocation = vi.fn();
vi.mock('../hooks/useClaudeConnector', () => ({
  useGrantsQuery: (...a: unknown[]) => useGrantsQuery(...a),
  useGrantRevocation: () => useGrantRevocation(),
}));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/user-management/claude-connector',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import GrantsList from './GrantsList';

const row = (over: Record<string, unknown>) => ({
  id: 'g1',
  user_id: 'u1',
  user_name: 'Sales Manager A',
  created_at: '2026-10-08T01:12:00',
  last_used_at: '2026-10-09T02:41:00',
  calls_30d: 214,
  status: 'Active',
  ...over,
});
const page = (rows: unknown[]) => ({
  data: { data: rows, pagination: { total: rows.length, page: 1, limit: 25 } },
  isLoading: false,
  isError: false,
});

let run: ReturnType<typeof vi.fn>;
beforeEach(() => {
  run = vi.fn();
  useGrantsQuery.mockReset();
  useGrantRevocation.mockReset().mockReturnValue({ run, targetId: null, isPending: false, countdown: null });
});
afterEach(() => cleanup());

describe('GrantsList', () => {
  it('renders the mock columns and a Badge per status', () => {
    useGrantsQuery.mockReturnValue(
      page([
        row({}),
        row({ id: 'g2', user_name: 'Procurement Officer D', status: 'Revoked', calls_30d: 12 }),
        row({ id: 'g3', user_name: 'Sales Executive E', status: 'Expired', calls_30d: 0, last_used_at: null }),
      ]),
    );
    render(<GrantsList />);
    for (const h of ['User', 'Created', 'Last used', 'Calls (30 d)', 'Status']) {
      expect(screen.getAllByText(h).length).toBeGreaterThan(0);
    }
    expect(screen.getByText('Sales Manager A')).toBeInTheDocument();
    expect(screen.getByText('214')).toBeInTheDocument();
    expect(screen.getByText('Active')).toBeInTheDocument();
    expect(screen.getByText('Revoked')).toBeInTheDocument();
    expect(screen.getByText('Expired')).toBeInTheDocument();
  });

  it('shows Revoke only on Active rows and parks it with the grant id and the user name as subject', () => {
    useGrantsQuery.mockReturnValue(
      page([row({}), row({ id: 'g2', user_name: 'Procurement Officer D', status: 'Revoked' })]),
    );
    render(<GrantsList />);
    const buttons = screen.getAllByRole('button', { name: /^revoke$/i });
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][0]).toMatchObject({ id: 'g1', subject: 'Sales Manager A' });
    // no confirmation dialog opens
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('replaces Revoke with the countdown on the row being revoked', () => {
    useGrantRevocation.mockReturnValue({
      run,
      targetId: 'g2',
      isPending: true,
      countdown: <span>Revoking in 5s</span>,
    });
    useGrantsQuery.mockReturnValue(
      page([row({}), row({ id: 'g2', user_name: 'Ops Analyst B' })]),
    );
    render(<GrantsList />);
    expect(screen.getByText('Revoking in 5s')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /^revoke$/i })).toHaveLength(1);
  });

  it('has loading, empty and error states', () => {
    useGrantsQuery.mockReturnValue({ data: undefined, isLoading: true, isError: false });
    const { unmount } = render(<GrantsList />);
    unmount();
    useGrantsQuery.mockReturnValue(page([]));
    const empty = render(<GrantsList />);
    expect(screen.getByText(/no claude connector grants/i)).toBeInTheDocument();
    empty.unmount();
    useGrantsQuery.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<GrantsList />);
    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument();
  });
});

describe('GrantsList source standards', () => {
  const src = fs.readFileSync(path.join(__dirname, 'GrantsList.tsx'), 'utf8');
  it('uses a fixed resizable DataGrid layout and no confirm dialogs', () => {
    expect(src).toMatch(/width:\s*'fixed'/);
    expect(src).toMatch(/columnsResizable:\s*true/);
    expect(src).toMatch(/columnResizeMode:\s*'onChange'/);
    expect(src).not.toMatch(/ConfirmDeleteDialog|AlertDialog|confirm\(/);
    expect(src).not.toMatch(/new URLSearchParams/);
    expect(src).toMatch(/Badge/);
  });
});
