/** AC-SN-43: `secondaryActionsAsMenu` keeps even ONE secondary action inside "Actions". */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useReactTable, getCoreRowModel, type ColumnDef } from '@tanstack/react-table';
import { DataGridListToolbar } from './data-grid-list-toolbar';
import { TooltipProvider } from './tooltip';

type Row = { id: string; name: string };
const COLUMNS: ColumnDef<Row>[] = [{ accessorKey: 'name', header: 'Name', meta: { headerTitle: 'Name' } }];
const DATA: Row[] = [{ id: '1', name: 'Alpha' }];

function Harness({ extra }: { extra: Record<string, unknown> }) {
  const table = useReactTable({ data: DATA, columns: COLUMNS, getRowId: (r) => r.id, getCoreRowModel: getCoreRowModel() });
  return (
    <TooltipProvider>
      <DataGridListToolbar table={table} exportConfig={false} {...(extra as object)} />
    </TooltipProvider>
  );
}

describe('secondaryActionsAsMenu', () => {
  it('puts the single action inside the Actions dropdown, not a standalone button', async () => {
    const onClick = vi.fn();
    render(
      <Harness
        extra={{
          secondaryActionsAsMenu: true,
          secondaryActions: [{ key: 'send', label: 'Send all parked requests to CS (3)', onClick }],
        }}
      />,
    );
    expect(screen.queryByRole('button', { name: /send all parked requests/i })).not.toBeInTheDocument();
    const trigger = screen.getByRole('button', { name: /actions/i });
    fireEvent.pointerDown(trigger, { button: 0, ctrlKey: false });
    fireEvent.click(trigger);
    const item = await screen.findByRole('menuitem', { name: /send all parked requests to cs \(3\)/i });
    fireEvent.click(item);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('without the prop a single action stays a standalone button and no Actions menu exists', () => {
    render(
      <Harness
        extra={{ secondaryActions: [{ key: 'send', label: 'Send all', onClick: () => {} }] }}
      />,
    );
    expect(screen.getByRole('button', { name: 'Send all' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^actions$/i })).not.toBeInTheDocument();
  });
});
