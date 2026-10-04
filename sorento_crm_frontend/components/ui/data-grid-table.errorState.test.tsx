/**
 * L2 (NEVER-STUCK-UI S3, "Lists"): a list whose read failed never says "No data".
 *
 * `DataGrid` takes the list query's `error` and an `onRetry`. With no rows and an error:
 * - a refusal (403 shapes) renders the inline no-access state, never the raw
 *   `Permission required: x.y` string and never a Retry (retrying a refusal cannot help);
 * - any other failure renders the message and a Retry that calls `onRetry`.
 * Rows already on screen (a failed background refetch) stay on screen.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useReactTable, getCoreRowModel, type ColumnDef } from '@tanstack/react-table';

import { DataGrid } from './data-grid';
import { DataGridTable } from './data-grid-table';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;

vi.mock('next/navigation', () => ({
  usePathname: () => '/some-listing',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

type Row = { id: string; name: string };
const COLUMNS: ColumnDef<Row>[] = [{ accessorKey: 'name', header: 'Name', size: 200 }];

function Harness({
  rows = [],
  error,
  onRetry,
  isLoading = false,
}: {
  rows?: Row[];
  error?: unknown;
  onRetry?: () => void;
  isLoading?: boolean;
}) {
  const table = useReactTable({
    data: rows,
    columns: COLUMNS,
    getRowId: (r) => r.id,
    getCoreRowModel: getCoreRowModel(),
  });
  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={isLoading}
      error={error}
      onRetry={onRetry}
      emptyMessage="No tickets match"
      tableLayout={{ width: 'fixed', columnsResizable: true }}
    >
      <DataGridTable />
    </DataGrid>
  );
}

describe('DataGridTable error state', () => {
  it('no error: the empty state is unchanged', () => {
    render(<Harness />);
    expect(screen.getByText('No tickets match')).toBeInTheDocument();
  });

  it('a 5xx renders the message and a Retry that refetches, not "No data"', () => {
    const onRetry = vi.fn();
    render(<Harness error={new Error('Server error. Try again or contact support.')} onRetry={onRetry} />);
    expect(screen.queryByText('No tickets match')).not.toBeInTheDocument();
    expect(screen.queryByText('No data available')).not.toBeInTheDocument();
    expect(screen.getByText('Server error. Try again or contact support.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it.each([
    'Permission required: tickets.tickets.view',
    'One of these permissions required (module may be disabled): a.b',
    'Module not enabled: tickets',
  ])('a refusal (%s) renders no access in place, without the raw slug or a Retry', (m) => {
    render(<Harness error={new Error(m)} onRetry={vi.fn()} />);
    expect(screen.getByTestId('data-grid-no-access')).toBeInTheDocument();
    expect(screen.getByText(/don.t have access/i)).toBeInTheDocument();
    expect(screen.queryByText(m)).not.toBeInTheDocument();
    expect(screen.queryByText('No tickets match')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
  });

  it('a timeout renders the timeout message with Retry', () => {
    const onRetry = vi.fn();
    render(<Harness error={new Error('The server took too long to answer.')} onRetry={onRetry} />);
    expect(screen.getByText('The server took too long to answer.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('rows on screen stay on screen when a background refetch fails', () => {
    render(<Harness rows={[{ id: '1', name: 'Alpha' }]} error={new Error('Server error.')} />);
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.queryByTestId('data-grid-error')).not.toBeInTheDocument();
  });

  it('while loading, the skeleton wins over a stale error', () => {
    render(<Harness isLoading error={new Error('Server error.')} />);
    expect(screen.queryByTestId('data-grid-error')).not.toBeInTheDocument();
  });

  it('an error with no onRetry still never reads as empty', () => {
    render(<Harness error={new Error('Server error.')} />);
    expect(screen.getByTestId('data-grid-error')).toBeInTheDocument();
    expect(screen.queryByText('No tickets match')).not.toBeInTheDocument();
  });
});
