'use client';

/**
 * DataGrid listing of tag templates.
 *
 * Columns: name, family (badge), print size (WxH mm), created_at, actions.
 * "New Template" button opens TagTemplateDialog.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from '@/lib/toast';
import { useRouter } from 'next/navigation';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  getCoreRowModel,
  getPaginationRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Pencil, Plus, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn, selectedRowIds } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import {
  useDeferredRowAction,
  useRowPending,
} from '@/hooks/useDeferredRowAction';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { familyLabel, type TagTemplate } from '@/lib/dealer-kit/tag-template-types';
import { listTemplates } from '../../services/tagTemplateService';
import { TagTemplateDialog } from './TagTemplateDialog';

export function TagTemplatesList() {
  const router = useRouter();

  const [templates, setTemplates] = useState<TagTemplate[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 50,
  });
  const [createOpen, setCreateOpen] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});

  // Whether rows are on screen, read by the load callback without re-creating it.
  const hasRowsRef = useRef(false);

  const fetchData = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await listTemplates();
      setTemplates(data);
      hasRowsRef.current = data.length > 0;
      setLoadError(null);
    } catch (e) {
      // Rows already on screen stay (a refresh after a delete or create failed); the grid
      // shows the failure only in place of no rows, so a refresh failure is said here.
      setLoadError(e);
      if (hasRowsRef.current) {
        toast.error(e instanceof Error ? e.message : 'Could not refresh the templates.');
      }
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Delete selected (D26, S11): the backend validates and deletes the whole
  // selection ATOMICALLY (a foreign/missing id refuses everything, nothing
  // partial), so this is ONE `useDeferredAction`, not `useDeferredBulkAction`
  // (that hook parks one independent action per row - the wrong shape when a
  // single id has to be able to sink the whole batch). `bulkDelete` freezes
  // the ids AND the noun read off them at the click: the selection is cleared
  // right after (so the grid does not keep dead rows looking tickable), and the
  // toast's copy is read again when the window lapses - anything derived live
  // from the selection would say "0 templates" by then.
  const [bulkDelete, setBulkDelete] = useState<{
    batchId: string;
    ids: string[];
    noun: string;
  } | null>(null);
  const bulkDeleteNoun = bulkDelete?.noun ?? '';

  const bulkDeletion = useDeferredAction({
    actionKey: 'tag_template.bulk_delete',
    entityType: 'tag_template',
    entityId: bulkDelete?.batchId ?? null,
    verb: 'Deleting',
    subject: bulkDeleteNoun,
    surface: 'toast',
    successMessage: `${bulkDeleteNoun} deleted`,
    payload: { template_ids: bulkDelete?.ids ?? [] },
    // Every row the reader picked dims for the length of the window, even though
    // the action is keyed on the batch token: the countdown names the count, and
    // the rows show which ones it means (D26).
    dimEntityIds: bulkDelete?.ids,
    onCommitted: fetchData,
  });

  // The per-row Delete asks nothing either (D7): the row dims and a toast counts
  // down with Cancel, exactly like the batch above - one template is just a batch
  // of one, and it goes through the single-record action so the countdown can name
  // the template by name.
  const deletion = useDeferredRowAction({
    actionKey: 'tag_template.delete',
    entityType: 'tag_template',
    successMessage: 'Template deleted',
    onCommitted: fetchData,
  });
  const rowPending = useRowPending<TagTemplate>('tag_template');

  // `start()` reads its OWN hook's current closure, which only carries the
  // batch above once a render has actually happened - calling it inline in
  // the click handler would still see last render's `entityId: null` and
  // no-op.
  useEffect(() => {
    if (bulkDelete) bulkDeletion.start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bulkDelete?.batchId]);

  const columns = useMemo<ColumnDef<TagTemplate>[]>(
    () => [
      buildSelectColumn<TagTemplate>({
        rowLabel: (row) => `Select ${row.original.name}`,
      }),
      {
        accessorKey: 'name',
        header: ({ column }) => (
          <DataGridColumnHeader column={column} title="Name" />
        ),
        size: 250,
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.name}>
            {row.original.name}
          </span>
        ),
      },
      {
        accessorKey: 'family',
        header: ({ column }) => (
          <DataGridColumnHeader column={column} title="Family" />
        ),
        size: 150,
        cell: ({ row }) => (
          <Badge variant="secondary" className="font-normal">
            {familyLabel(row.original.family)}
          </Badge>
        ),
      },
      {
        id: 'print_size',
        header: 'Print Size',
        size: 120,
        cell: ({ row }) => {
          const ps = row.original.print_size;
          return (
            <span className="text-muted-foreground">
              {ps.width_mm} x {ps.height_mm} mm
            </span>
          );
        },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => (
          <DataGridColumnHeader column={column} title="Created" />
        ),
        size: 160,
        cell: ({ row }) => (
          <span className="text-muted-foreground">
            {formatDateTimeInMalaysia(row.original.created_at)}
          </span>
        ),
      },
      {
        id: 'published',
        header: 'Status',
        size: 100,
        cell: ({ row }) =>
          row.original.published_version_id ? (
            <Badge variant="success" className="font-normal">
              Live v{row.original.published_version_no}
            </Badge>
          ) : (
            <Badge variant="outline" className="font-normal">
              Draft
            </Badge>
          ),
      },
      {
        id: 'actions',
        header: '',
        size: 100,
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              onClick={(e) => {
                e.stopPropagation();
                router.push(`/dealer-kit/tag-templates/${row.original.id}`);
              }}
              title="Edit template"
            >
              <Pencil className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0 text-destructive hover:text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                deletion.run({ id: row.original.id, subject: row.original.name });
              }}
              title="Delete template"
              aria-label="Delete template"
            >
              <Trash2 className="size-3.5" />
            </Button>
          </div>
        ),
      },
    ],
    [router, deletion],
  );

  const table = useReactTable({
    data: templates,
    columns,
    getRowId: (row) => row.id,
    state: { pagination, rowSelection },
    onPaginationChange: setPagination,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableRowSelection: true,
  });

  const handleBulkDelete = () => {
    const ids = selectedRowIds(table);
    if (ids.length === 0) return;
    // The noun is frozen HERE, off the ids this click captured: the selection is
    // cleared on the next line, and the toast's own copy is read again when the
    // window lapses - live state would have it saying "0 templates" by then.
    setBulkDelete({
      batchId: crypto.randomUUID(),
      ids,
      noun: `${ids.length} template${ids.length === 1 ? '' : 's'}`,
    });
    setRowSelection({});
  };

  const listPrimaryAction = (
    <Button size="sm" onClick={() => setCreateOpen(true)}>
      <Plus className="mr-1.5 size-3.5" />
      New Template
    </Button>
  );

  return (
    <>
      <Card>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            showColumns={false}
            exportConfig={false}
            primaryAction={listPrimaryAction}
            bulkActions={[
              {
                key: 'delete',
                label: 'Delete',
                icon: Trash2,
                destructive: true,
                onClick: handleBulkDelete,
              },
            ]}
          />
        </CardHeader>
        <CardTable>
          <DataGrid
            table={table}
            recordCount={templates.length}
            isLoading={isLoading}
            error={loadError}
            onRetry={() => void fetchData()}
            tableLayout={{ width: 'fixed', columnsResizable: true }}
            rowPending={rowPending}
            onRowClick={(row) =>
              router.push(`/dealer-kit/tag-templates/${row.id}`)
            }
          >
            <DataGridTable />
            <CardFooter className="justify-center">
              <DataGridPagination />
            </CardFooter>
          </DataGrid>
        </CardTable>
      </Card>

      <TagTemplateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={fetchData}
      />
    </>
  );
}
