'use client';

import { useMemo } from 'react';
import { getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { ChevronDown } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import type { Registry, RegistryDomain } from '@/services/chatbotAccessService';

const humanize = (code?: string | null) =>
  code ? code.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase()) : '-';

/** Advanced: which team and agent a granted topic hands off to. Shared by the contact and role pages. */
export default function HandoffAdvanced({
  registry,
  domains,
}: {
  registry: Registry;
  domains: ReadonlySet<string> | readonly string[];
}) {
  const granted = useMemo(() => new Set(domains), [domains]);
  const rows = useMemo(
    () => registry.domains.filter((d) => granted.has(d.name) && d.escalation_agent_code),
    [registry, granted],
  );
  const columns = useMemo<ColumnDef<RegistryDomain>[]>(
    () => [
      {
        id: 'topic',
        accessorFn: (d) => d.access_label || d.label,
        header: ({ column }) => <DataGridColumnHeader title="Topic" column={column} />,
        cell: ({ row }) => {
          const label = row.original.access_label || row.original.label;
          return (
            <span className="truncate" title={label}>
              {label}
            </span>
          );
        },
        size: 240,
      },
      {
        id: 'team',
        accessorFn: (d) => d.escalation_tier1_team || '-',
        header: ({ column }) => <DataGridColumnHeader title="Hand-off team" column={column} />,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.escalation_tier1_team ?? ''}>
            {row.original.escalation_tier1_team || '-'}
          </span>
        ),
        size: 260,
      },
      {
        id: 'agent',
        accessorFn: (d) => d.escalation_agent_name || humanize(d.escalation_agent_code),
        header: ({ column }) => <DataGridColumnHeader title="Agent" column={column} />,
        cell: ({ row }) => {
          const name = row.original.escalation_agent_name || humanize(row.original.escalation_agent_code);
          return (
            <span className="truncate" title={name}>
              {name}
            </span>
          );
        },
        size: 240,
      },
    ],
    [],
  );
  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (d) => d.name,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <Collapsible className="rounded-lg border bg-card">
      <CollapsibleTrigger className="flex w-full items-center justify-between px-5 py-3 font-medium">
        <span>
          Advanced <span className="text-xs font-normal text-muted-foreground">hand-off teams</span>
        </span>
        <ChevronDown className="size-4" />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-5 pb-4">
          {rows.length === 0 ? (
            <div className="py-4 text-center">
              <p className="text-sm font-medium">No hand-off teams</p>
              <p className="text-sm text-muted-foreground">
                None of the granted topics hands off to an agent.
              </p>
            </div>
          ) : (
            <DataGrid
              table={table}
              recordCount={rows.length}
              listingKey={null}
              tableLayout={{ width: 'fixed', columnsResizable: true }}
            >
              <Card>
                <CardContent className="overflow-x-auto p-0">
                  <DataGridTable />
                </CardContent>
              </Card>
            </DataGrid>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}
