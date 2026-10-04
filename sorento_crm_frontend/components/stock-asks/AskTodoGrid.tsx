'use client';

import { useMemo } from 'react';
import { getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { ArrowDown, ArrowUp } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { AskDoneByCell } from '@/components/stock-asks/AskEditCells';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import type { StockAsk } from '@/lib/stock-asks';
import {
  askAnswerText,
  askProductText,
  bucketTodo,
  type AskTodoPayload,
} from '@/lib/stock-asks-todo';
import type { LandingSort } from '@/app/(auth)/portal/lib/landing-fields';

type GroupKey = 'open' | 'done_today';
interface GridRow {
  ask: StockAsk;
  group: GroupKey;
}
const GROUP_LABEL: Record<GroupKey, string> = {
  open: 'Open',
  done_today: 'Done today',
};

export interface AskTodoGridProps {
  payload: AskTodoPayload;
  sort: LandingSort;
  showAgent: boolean;
  /** The CRM only: who cleared each ask. */
  showDoneBy?: boolean;
  /** `sales.customer_asks.view::todo` on the CRM; null on the portal (no user row to key on). */
  listingKey: string | null;
  onOpen: (ask: StockAsk) => void;
  onDone: (askId: string) => void;
  onReopen: (askId: string) => void;
  pendingAskId?: string | null;
  /** A header click sorts inside every group, the same state the toolbar's Sort writes. */
  onSortChange?: (sort: LandingSort) => void;
}

function SortHeader({
  fieldKey,
  label,
  sort,
  onSortChange,
}: {
  fieldKey: string;
  label: string;
  sort: LandingSort;
  onSortChange?: (sort: LandingSort) => void;
}) {
  if (!onSortChange) return <span>{label}</span>;
  const active = sort.key === fieldKey;
  return (
    <button
      type="button"
      className="flex items-center gap-1 text-left font-medium"
      onClick={(e) => {
        e.stopPropagation();
        onSortChange({
          key: fieldKey,
          dir: active && sort.dir === 'asc' ? 'desc' : 'asc',
        });
      }}
    >
      <span>{label}</span>
      {active && (sort.dir === 'asc' ? <ArrowUp className="size-3.5" /> : <ArrowDown className="size-3.5" />)}
    </button>
  );
}

/**
 * The list view of the to-do: the system DataGrid. The three groups are full-width section rows
 * (`renderGroupHeader`) so the date-first order survives; a header click sorts inside a group;
 * the action column is the last one; a row opens the conversation, its button does not.
 */
export function AskTodoGrid({
  payload,
  sort,
  showAgent,
  showDoneBy = false,
  listingKey,
  onOpen,
  onDone,
  onReopen,
  pendingAskId = null,
  onSortChange,
}: AskTodoGridProps) {
  const rows = useMemo<GridRow[]>(() => {
    const { sections, done } = bucketTodo(payload, sort);
    return [
      ...sections.flatMap((s) => s.days.flatMap((d) => d.asks.map((ask) => ({ ask, group: s.key as GroupKey })))),
      ...done.map((ask) => ({ ask, group: 'done_today' as GroupKey })),
    ];
  }, [payload, sort]);

  const columns = useMemo<ColumnDef<GridRow>[]>(() => {
    const header = (fieldKey: string, label: string) => {
      function AskHeader() {
        return <SortHeader fieldKey={fieldKey} label={label} sort={sort} onSortChange={onSortChange} />;
      }
      return AskHeader;
    };
    const text = (value: string | null | undefined) => (
      <span className="block truncate" title={value ?? undefined}>
        {value || '-'}
      </span>
    );
    return [
      {
        id: 'created_at',
        header: header('created_at', 'Asked at'),
        size: 170,
        cell: ({ row }) => text(formatDateTimeInMalaysia(row.original.ask.created_at)),
      },
      ...(showAgent
        ? [
            {
              id: 'agent_code',
              header: 'Agent',
              size: 100,
              cell: ({ row }) => text(row.original.ask.agent_code),
            } satisfies ColumnDef<GridRow>,
          ]
        : []),
      {
        id: 'customer_name',
        header: header('customer_name', 'Customer'),
        size: 180,
        cell: ({ row }) => text(row.original.ask.customer_name),
      },
      { id: 'contact_name', header: 'Contact', size: 130, cell: ({ row }) => text(row.original.ask.contact_name) },
      {
        id: 'asked',
        header: header('title', 'Asked'),
        size: 130,
        cell: ({ row }) => text(askProductText(row.original.ask)),
      },
      {
        id: 'answer',
        header: header('answer', 'Answered'),
        size: 300,
        cell: ({ row }) => text(askAnswerText(row.original.ask)),
      },
      ...(showDoneBy
        ? [
            {
              id: 'done_by',
              header: 'Done by',
              size: 220,
              cell: ({ row }) => <AskDoneByCell ask={row.original.ask} />,
            } satisfies ColumnDef<GridRow>,
          ]
        : []),
      {
        id: 'actions',
        header: '',
        size: 110,
        cell: ({ row }) => {
          const { ask } = row.original;
          const pending = pendingAskId === ask.id;
          return ask.state === 'done' ? (
            <Button size="sm" variant="outline" disabled={pending} onClick={() => onReopen(ask.id)}>
              Reopen
            </Button>
          ) : (
            <Button size="sm" variant="primary" disabled={pending} onClick={() => onDone(ask.id)}>
              Done
            </Button>
          );
        },
      },
    ];
  }, [showAgent, showDoneBy, sort, onSortChange, pendingAskId, onDone, onReopen]);

  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (r) => r.ask.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      listingKey={listingKey}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
      onRowClick={(r: GridRow) => onOpen(r.ask)}
      renderGroupHeader={(row: GridRow, previous: GridRow | null) =>
        previous && previous.group === row.group ? null : <span>{GROUP_LABEL[row.group]}</span>
      }
    >
      <DataGridTable />
    </DataGrid>
  );
}
