import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

import ConversationSLATrackingList from './ConversationSLATrackingList';

/**
 * AC-M2: the listing honours the history deep links by passing them to the
 * SERVER query - a client-side slice would page and count wrong.
 */

const useConversationSLATracking = vi.fn();
const useSyncAssigneeFromRespond = vi.fn();

vi.mock('../hooks/useConversationSLATracking', () => ({
  useConversationSLATracking: (...a: unknown[]) => useConversationSLATracking(...a),
  useSyncAssigneeFromRespond: (...a: unknown[]) => useSyncAssigneeFromRespond(...a),
}));

vi.mock('@tanstack/react-query', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('@tanstack/react-query');
  return {
    ...actual,
    useQuery: () => ({ data: [] }),
    useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  };
});

const replace = vi.fn();
let params: Record<string, string | null> = {};
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace }),
  useSearchParams: () => ({ get: (key: string) => params[key] ?? null }),
}));

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn() }));

// The grid itself is covered elsewhere; this file is about which query runs.
vi.mock('@/components/ui/data-grid', () => ({
  DataGrid: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/ui/data-grid-table', () => ({ DataGridTable: () => <table /> }));
vi.mock('@/components/ui/data-grid-pagination', () => ({ DataGridPagination: () => <div /> }));
vi.mock('@/components/ui/data-grid-column-header', () => ({
  DataGridColumnHeader: ({ title }: { title?: string }) => <span>{title}</span>,
}));
vi.mock('@/components/ui/data-grid-select-column', () => ({
  buildSelectColumn: () => ({ id: 'select' }),
}));
vi.mock('@/components/ui/data-grid-list-toolbar', () => ({
  DataGridListToolbar: ({ searchSlot }: { searchSlot?: React.ReactNode }) => (
    <div>{searchSlot}</div>
  ),
}));

function rowsResponse(rows: Array<Record<string, unknown>> = []) {
  return {
    data: { data: rows, pagination: { total: rows.length, page: 1, limit: 50 } },
    isLoading: false,
    isFetching: false,
  };
}

beforeEach(() => {
  params = {};
  replace.mockReset();
  useConversationSLATracking.mockReset();
  useSyncAssigneeFromRespond.mockReturnValue({ mutate: vi.fn(), isPending: false });
  useConversationSLATracking.mockReturnValue(rowsResponse());
});

describe('ConversationSLATrackingList history deep links (AC-M2)', () => {
  it('passes ?contact= straight into the server list query', async () => {
    params = { contact: '10025531' };
    render(<ConversationSLATrackingList />);

    await waitFor(() =>
      expect(useConversationSLATracking).toHaveBeenCalledWith(
        expect.objectContaining({ contact: '10025531', is_resolved: undefined }),
      ),
    );
  });

  it('turns ?is_resolved=true&resolved_by=me into the resolved-by-me query, sorted by resolved_at', async () => {
    params = { is_resolved: 'true', resolved_by: 'me', sort: 'resolved_at', dir: 'desc' };
    render(<ConversationSLATrackingList />);

    await waitFor(() =>
      expect(useConversationSLATracking).toHaveBeenCalledWith(
        expect.objectContaining({
          is_resolved: true,
          resolved_by: 'me',
          sorting: [{ id: 'resolved_at', desc: true }],
        }),
      ),
    );
  });

  it('says out loud that it is showing a subset, and offers a way out', async () => {
    params = { contact: '10025531' };
    useConversationSLATracking.mockReturnValue(
      rowsResponse([
        {
          id: 'r1',
          contact_name: 'Aisyah Rahman',
          contact_phone: '+60120000553',
          current_tier: 1,
          is_resolved: false,
          initiated_at: '2026-08-15T02:00:00',
          due_at: '2026-08-15T06:00:00',
        },
      ]),
    );
    render(<ConversationSLATrackingList />);

    const summary = await screen.findByTestId('history-filter-summary');
    expect(summary).toHaveTextContent('Contact: Aisyah Rahman');
    screen.getByRole('button', { name: /Show all/i }).click();
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith('/sla-management/conversation-sla-tracking', {
        scroll: false,
      }),
    );
  });

  it('renders no subset banner without deep-link params', async () => {
    render(<ConversationSLATrackingList />);
    await waitFor(() => expect(useConversationSLATracking).toHaveBeenCalled());
    expect(screen.queryByTestId('history-filter-summary')).not.toBeInTheDocument();
  });
});
