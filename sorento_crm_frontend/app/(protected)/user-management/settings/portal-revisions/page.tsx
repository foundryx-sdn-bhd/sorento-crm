'use client';

import { useMemo, useState } from 'react';
import {
  type ColumnDef,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Pencil } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTable,
  CardTitle,
} from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';

import { useSettings } from '../components/settings-context';
import { PortalRevisionConfigDialog } from './components/PortalRevisionConfigDialog';
import {
  usePortalRevisionConfigs,
  useSavePortalRevisionGlobals,
  useUpdatePortalRevisionConfig,
} from './hooks/usePortalRevisionConfigs';
import type { PortalRevisionConfig } from './services/portalRevisionConfigService';
import {
  portalRevisionStatusLabel,
  portalRevisionTypeLabel,
} from './lib/portal-revision-options';

/**
 * Settings -> Portal Revisions (UAC A6).
 *
 * Two globals from `system_settings` (the kill switch and the fallback cap),
 * plus one row per portal submission type. Editing a row is a modal, per the
 * CRUD UX standard. The office never creates or deletes a config row: all four
 * types are seeded, so turning one on is a toggle (UAC A5).
 */
export default function PortalRevisionsSettingsPage() {
  const { settings } = useSettings();
  const [enabled, setEnabled] = useState<boolean>(settings.portalRevisionsEnabled ?? true);
  const [maxRevisions, setMaxRevisions] = useState<string>(
    String(settings.portalMaxRevisions ?? 2),
  );
  const [editing, setEditing] = useState<PortalRevisionConfig | null>(null);

  const configsQuery = usePortalRevisionConfigs();
  const saveGlobals = useSavePortalRevisionGlobals();
  const updateConfig = useUpdatePortalRevisionConfig();

  const maxInvalid =
    !/^\d+$/.test(maxRevisions.trim()) || Number(maxRevisions.trim()) > 50;

  const columns = useMemo<ColumnDef<PortalRevisionConfig>[]>(
    () => [
      {
        accessorKey: 'source_entity_type',
        header: ({ column }) => <DataGridColumnHeader title="Form type" column={column} />,
        size: 180,
        minSize: 140,
        enableSorting: false,
        cell: ({ row }) => {
          const label = portalRevisionTypeLabel(row.original.source_entity_type);
          return (
            <span className="truncate" title={label}>
              {label}
            </span>
          );
        },
        meta: { headerTitle: 'Form type', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'is_enabled',
        header: ({ column }) => <DataGridColumnHeader title="Enabled" column={column} />,
        size: 110,
        minSize: 90,
        enableSorting: false,
        cell: ({ row }) => (
          <Badge variant={row.original.is_enabled ? 'success' : 'secondary'}>
            {row.original.is_enabled ? 'On' : 'Off'}
          </Badge>
        ),
        meta: { headerTitle: 'Enabled', skeleton: <Skeleton className="h-4 w-10" /> },
      },
      {
        accessorKey: 'max_revisions',
        header: ({ column }) => <DataGridColumnHeader title="Max" column={column} />,
        size: 110,
        minSize: 80,
        enableSorting: false,
        cell: ({ row }) =>
          row.original.max_revisions == null ? (
            <span className="text-muted-foreground">Global</span>
          ) : (
            <span className="tabular-nums">{row.original.max_revisions}</span>
          ),
        meta: { headerTitle: 'Max', skeleton: <Skeleton className="h-4 w-8" /> },
      },
      {
        accessorKey: 'allowed_statuses',
        header: ({ column }) => (
          <DataGridColumnHeader title="Allowed statuses" column={column} />
        ),
        size: 280,
        minSize: 160,
        enableSorting: false,
        cell: ({ row }) => {
          const text = (row.original.allowed_statuses ?? [])
            .map((status) =>
              portalRevisionStatusLabel(row.original.source_entity_type, status),
            )
            .join(', ');
          if (!text) return <span className="text-muted-foreground">None</span>;
          return (
            <span className="truncate" title={text}>
              {text}
            </span>
          );
        },
        meta: {
          headerTitle: 'Allowed statuses',
          skeleton: <Skeleton className="h-4 w-40" />,
        },
      },
      {
        accessorKey: 'restart_stage_code',
        header: ({ column }) => <DataGridColumnHeader title="Restart stage" column={column} />,
        size: 180,
        minSize: 120,
        enableSorting: false,
        cell: ({ row }) => {
          const code = row.original.restart_stage_code;
          if (!code) return <span className="text-muted-foreground">First stage</span>;
          const label = code.replace(/[_-]+/g, ' ');
          return (
            <span className="truncate" title={label}>
              {label}
            </span>
          );
        },
        meta: { headerTitle: 'Restart stage', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        id: 'actions',
        header: '',
        size: 80,
        minSize: 60,
        enableSorting: false,
        enableHiding: false,
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="sm"
            aria-label={`Edit ${portalRevisionTypeLabel(row.original.source_entity_type)}`}
            onClick={() => setEditing(row.original)}
          >
            <Pencil className="size-4" />
            Edit
          </Button>
        ),
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: configsQuery.data ?? [],
    getRowId: (row) => row.source_entity_type,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="border-b border-border">
          <CardTitle>Portal Revisions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5 py-5">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor="portal-revisions-enabled">Enabled</Label>
            <Switch
              id="portal-revisions-enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="portal-max-revisions">Max revisions</Label>
            <Input
              id="portal-max-revisions"
              inputMode="numeric"
              value={maxRevisions}
              onChange={(e) => setMaxRevisions(e.target.value)}
              aria-invalid={maxInvalid}
              className="max-w-40"
            />
          </div>
        </CardContent>
        <CardFooter className="flex justify-end gap-4 py-5">
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setEnabled(settings.portalRevisionsEnabled ?? true);
              setMaxRevisions(String(settings.portalMaxRevisions ?? 2));
            }}
          >
            Reset
          </Button>
          <Button
            type="button"
            disabled={saveGlobals.isPending || maxInvalid}
            onClick={() =>
              saveGlobals.mutate({
                portal_revisions_enabled: enabled,
                portal_max_revisions: Number(maxRevisions.trim()),
              })
            }
          >
            {saveGlobals.isPending ? 'Saving…' : 'Save Settings'}
          </Button>
        </CardFooter>
      </Card>

      <DataGrid
        table={table}
        recordCount={configsQuery.data?.length ?? 0}
        isLoading={configsQuery.isLoading}
        error={configsQuery.error}
        onRetry={() => void configsQuery.refetch()}
        standardToolbar={false}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
      >
        <Card>
          <CardHeader className="border-b border-border">
            <CardTitle>Form Types</CardTitle>
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </Card>
      </DataGrid>

      <PortalRevisionConfigDialog
        open={editing !== null}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        config={editing}
        isSaving={updateConfig.isPending}
        onSave={async (input) => {
          if (!editing) return;
          await updateConfig.mutateAsync({
            sourceEntityType: editing.source_entity_type,
            input,
          });
          setEditing(null);
        }}
      />
    </div>
  );
}
