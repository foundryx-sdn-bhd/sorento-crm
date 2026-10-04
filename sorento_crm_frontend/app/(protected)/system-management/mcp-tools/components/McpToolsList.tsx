'use client';

import * as React from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import { useReactTable, getCoreRowModel } from '@tanstack/react-table';
import { useMcpToolsCatalog } from '../hooks/useMcpAdmin';
import type { McpToolCatalogRow } from '../services/mcpAdminService';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';

export function McpToolsList() {
  const [includeInactive, setIncludeInactive] = React.useState(false);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
  } = useDebouncedSearch();
  const { data, isLoading, error, refetch } = useMcpToolsCatalog({ is_active: !includeInactive });
  const rows = (data ?? []).filter((r) => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (
      r.tool_name.toLowerCase().includes(q) ||
      r.module_key.toLowerCase().includes(q)
    );
  });

  const columns = React.useMemo<ColumnDef<McpToolCatalogRow>[]>(
    () => [
      {
        accessorKey: 'tool_name',
        header: ({ column }) => <DataGridColumnHeader title="Tool" column={column} />,
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.tool_name}</span>,
        size: 280,
        meta: { headerTitle: 'Tool' },
      },
      {
        accessorKey: 'module_key',
        header: ({ column }) => <DataGridColumnHeader title="Module" column={column} />,
        cell: ({ row }) => row.original.module_key || '-',
        size: 140,
        meta: { headerTitle: 'Module' },
      },
      {
        accessorKey: 'description',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.description ?? ''}>
            {row.original.description ?? '-'}
          </span>
        ),
        size: 400,
        meta: { headerTitle: 'Description' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>MCP Tools Catalog</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-4">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder="Search tool / module / owner..."
            className="max-w-sm"
          />
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={includeInactive} onCheckedChange={(v) => setIncludeInactive(v)} />
            Show deactivated
          </label>
          <span className="ml-auto text-sm text-muted-foreground">{rows.length} tools</span>
        </div>
        {isLoading ? (
          <SectionSkeleton rows={3} />
        ) : (
          <DataGrid
            table={table}
            recordCount={rows.length}
            error={error}
            onRetry={() => void refetch()}
            emptyMessage="No tools match."
            // Nav permission (`system.ai_assistant_settings.view`) is shared by three
            // sibling pages (Prompts/Usage/Wishlist) - `::mcp-tools` disambiguates the
            // saved column config from theirs, same convention as ModuleBundlesAdmin.
            listingKey="system.ai_assistant_settings.view::mcp-tools"
            tableLayout={{ width: 'fixed', columnsResizable: true }}
          >
            <DataGridTable />
          </DataGrid>
        )}
        <p className="text-xs text-muted-foreground">
          To assign a tool to an access agent, edit the agent under{' '}
          <code className="font-mono">User Management → Access Agents</code> and select tools in
          the &quot;MCP Tools&quot; card.
        </p>
      </CardContent>
    </Card>
  );
}
