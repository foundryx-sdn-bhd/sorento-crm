'use client';

import { useMemo, useState } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { useReactTable, getCoreRowModel } from '@tanstack/react-table';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  useEmailEventConfigs,
  useUpdateEmailEventConfig,
} from '../hooks/useEmailEventConfigs';
import type { EmailEventConfig } from '../types/emailEventConfig.types';

interface OverrideDraft {
  rate_per_window_override: string;
  window_seconds_override: string;
  coalesce_window_seconds_override: string;
}

function _toIntOrNull(value: string): number | null | undefined {
  if (value === '') return null;
  const n = Number(value);
  if (Number.isNaN(n)) return undefined;
  return n;
}

export default function EmailEventConfigsTable() {
  const { data, isLoading, error, refetch } = useEmailEventConfigs();
  const updateMut = useUpdateEmailEventConfig();
  const [drafts, setDrafts] = useState<Record<string, OverrideDraft>>({});

  function getDraft(row: EmailEventConfig): OverrideDraft {
    return (
      drafts[row.event_key] ?? {
        rate_per_window_override: row.rate_per_window_override?.toString() ?? '',
        window_seconds_override: row.window_seconds_override?.toString() ?? '',
        coalesce_window_seconds_override:
          row.coalesce_window_seconds_override?.toString() ?? '',
      }
    );
  }

  function setDraftField(event_key: string, field: keyof OverrideDraft, value: string) {
    setDrafts((d) => ({
      ...d,
      [event_key]: { ...getDraft({ event_key } as EmailEventConfig), ...d[event_key], [field]: value },
    }));
  }

  const columns = useMemo<ColumnDef<EmailEventConfig>[]>(
    () => [
      {
        id: 'event',
        accessorFn: (row) => row.display_name,
        header: ({ column }) => <DataGridColumnHeader title="Event" column={column} />,
        cell: ({ row }) => (
          <div>
            <div className="font-medium truncate" title={row.original.display_name}>
              {row.original.display_name}
            </div>
            <div
              className="font-mono text-xs text-muted-foreground truncate"
              title={row.original.event_key}
            >
              {row.original.event_key}
            </div>
            {row.original.description && (
              <div className="text-xs text-muted-foreground mt-1 max-w-[480px]">
                {row.original.description}
              </div>
            )}
          </div>
        ),
        size: 260,
        meta: { headerTitle: 'Event' },
      },
      {
        id: 'enabled',
        accessorFn: (row) => row.enabled,
        header: ({ column }) => <DataGridColumnHeader title="Enabled" column={column} />,
        cell: ({ row }) => (
          <Switch
            checked={row.original.enabled}
            onCheckedChange={(checked) =>
              updateMut.mutate({
                event_key: row.original.event_key,
                payload: { enabled: checked },
              })
            }
          />
        ),
        size: 110,
        meta: { headerTitle: 'Enabled' },
      },
      {
        id: 'rate_per_window_override',
        header: ({ column }) => (
          <DataGridColumnHeader title="Rate / window override" column={column} />
        ),
        cell: ({ row }) => {
          const draft = getDraft(row.original);
          return (
            <Input
              placeholder="default"
              value={draft.rate_per_window_override}
              onChange={(e) =>
                setDraftField(row.original.event_key, 'rate_per_window_override', e.target.value)
              }
            />
          );
        },
        size: 150,
        meta: { headerTitle: 'Rate / window override' },
      },
      {
        id: 'window_seconds_override',
        header: ({ column }) => (
          <DataGridColumnHeader title="Window seconds override" column={column} />
        ),
        cell: ({ row }) => {
          const draft = getDraft(row.original);
          return (
            <Input
              placeholder="default"
              value={draft.window_seconds_override}
              onChange={(e) =>
                setDraftField(row.original.event_key, 'window_seconds_override', e.target.value)
              }
            />
          );
        },
        size: 150,
        meta: { headerTitle: 'Window seconds override' },
      },
      {
        id: 'coalesce_window_seconds_override',
        header: ({ column }) => (
          <DataGridColumnHeader title="Coalesce seconds override" column={column} />
        ),
        cell: ({ row }) => {
          const draft = getDraft(row.original);
          return (
            <Input
              placeholder="default"
              value={draft.coalesce_window_seconds_override}
              onChange={(e) =>
                setDraftField(
                  row.original.event_key,
                  'coalesce_window_seconds_override',
                  e.target.value,
                )
              }
            />
          );
        },
        size: 170,
        meta: { headerTitle: 'Coalesce seconds override' },
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => {
          const draft = getDraft(row.original);
          return (
            <Button
              size="sm"
              variant="outline"
              disabled={updateMut.isPending}
              onClick={() => {
                const r = _toIntOrNull(draft.rate_per_window_override);
                const w = _toIntOrNull(draft.window_seconds_override);
                const c = _toIntOrNull(draft.coalesce_window_seconds_override);
                updateMut.mutate({
                  event_key: row.original.event_key,
                  payload: {
                    rate_per_window_override: r ?? null,
                    window_seconds_override: w ?? null,
                    coalesce_window_seconds_override: c ?? null,
                  },
                });
              }}
            >
              Save overrides
            </Button>
          );
        },
        size: 130,
        enableResizing: false,
        meta: { headerTitle: 'Actions' },
      },
    ],
    // getDraft/setDraftField close over `drafts` and are redefined every render,
    // same as the raw table's per-render `.map()` this replaces - listing `drafts`
    // is what actually drives a recompute.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [drafts, updateMut],
  );

  const table = useReactTable({
    columns,
    data: data ?? [],
    getRowId: (row) => row.event_key,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Email event kill switches</CardTitle>
        <CardDescription>
          One row per email event. Disable an event to silence it without redeploy. Override rate
          caps or coalesce windows per event when defaults are too tight or too loose.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <DataGrid
            table={table}
            recordCount={(data ?? []).length}
            error={error}
            onRetry={() => void refetch()}
            listingKey="system.email_event_configs.view"
            emptyMessage="No email events configured."
            tableLayout={{ width: 'fixed', columnsResizable: true }}
          >
            <DataGridTable />
          </DataGrid>
        )}
      </CardContent>
    </Card>
  );
}
