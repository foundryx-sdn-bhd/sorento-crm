'use client';

import { useMemo, useState } from 'react';
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query';
import { Boxes, Plus, Pencil, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { StockVisibilitySection } from '@/components/stock-visibility/StockVisibilitySection';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
} from '@tanstack/react-table';
import { toast } from '@/lib/toast';
import {
  getAllContactAccessTypes,
  createContactAccessType,
  updateContactAccessType,
  deleteContactAccessType,
  CONTACT_ACCESS_TYPE_PERMS,
  type ContactAccessTypeAdmin,
} from '../services/contactAccessTypeService';
import { useHasPermission } from '@/hooks/usePermissions';

export default function ContactAccessTypesAdmin() {
  const queryClient = useQueryClient();

  const { data: types = [], isLoading: typesLoading, error, refetch } = useQuery({
    queryKey: ['contact-access-types-admin'],
    queryFn: getAllContactAccessTypes,
  });

  const createTypeMutation = useMutation({
    mutationFn: createContactAccessType,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contact-access-types-admin'] });
      queryClient.invalidateQueries({ queryKey: ['contact-access-types'] });
      setTypeDialogOpen(false);
      resetTypeForm();
      toast.success('Access type created');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const updateTypeMutation = useMutation({
    mutationFn: ({ code, body }: { code: string; body: Parameters<typeof updateContactAccessType>[1] }) =>
      updateContactAccessType(code, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contact-access-types-admin'] });
      queryClient.invalidateQueries({ queryKey: ['contact-access-types'] });
      setTypeDialogOpen(false);
      setEditingType(null);
      resetTypeForm();
      toast.success('Access type updated');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const deleteTypeMutation = useMutation({
    mutationFn: deleteContactAccessType,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contact-access-types-admin'] });
      queryClient.invalidateQueries({ queryKey: ['contact-access-types'] });
      setDeleteTypeCode(null);
      toast.success('Access type deleted');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  // Writes need `reference_data.manage`; the screen itself is reachable with
  // `access_agents.view`, so a reader sees the list without buttons that would 403.
  const canManage = useHasPermission(CONTACT_ACCESS_TYPE_PERMS.manage);
  const [typeDialogOpen, setTypeDialogOpen] = useState(false);
  const [editingType, setEditingType] = useState<ContactAccessTypeAdmin | null>(null);
  const [deleteTypeCode, setDeleteTypeCode] = useState<string | null>(null);
  // Stock visibility is one row per access type, so it is edited from the row it
  // belongs to rather than as a column every type would have to carry.
  const [policyType, setPolicyType] = useState<ContactAccessTypeAdmin | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [typeForm, setTypeForm] = useState({
    code: '',
    name: '',
    description: '',
    is_active: true,
    sort_order: '' as string | number,
    keywords: '',
  });

  function resetTypeForm() {
    setTypeForm({
      code: '',
      name: '',
      description: '',
      is_active: true,
      sort_order: '',
      keywords: '',
    });
    setEditingType(null);
  }

  function openCreateType() {
    resetTypeForm();
    setTypeDialogOpen(true);
  }

  function openEditType(row: ContactAccessTypeAdmin) {
    setEditingType(row);
    setTypeForm({
      code: row.code,
      name: row.name,
      description: row.description ?? '',
      is_active: row.is_active,
      sort_order: row.sort_order ?? '',
      keywords: (row.keywords ?? []).join(', '),
    });
    setTypeDialogOpen(true);
  }

  function parseKeywords(raw: string): string[] {
    return Array.from(
      new Set(
        raw
          .split(',')
          .map((s) => s.trim())
          .filter((s) => s.length > 0),
      ),
    );
  }

  function saveType() {
    const keywords = parseKeywords(typeForm.keywords);
    if (editingType) {
      const sort = typeForm.sort_order === '' ? undefined : Number(typeForm.sort_order);
      if (Number.isNaN(sort)) return;
      updateTypeMutation.mutate({
        code: editingType.code,
        body: {
          name: typeForm.name,
          description: typeForm.description || null,
          is_active: typeForm.is_active,
          sort_order: sort,
          keywords,
        },
      });
    } else {
      const sort = typeForm.sort_order === '' ? undefined : Number(typeForm.sort_order);
      if (!typeForm.code.trim() || !typeForm.name.trim()) {
        toast.error('Code and name are required');
        return;
      }
      if (typeForm.sort_order !== '' && Number.isNaN(Number(typeForm.sort_order))) return;
      createTypeMutation.mutate({
        code: typeForm.code.trim(),
        name: typeForm.name.trim(),
        description: typeForm.description.trim() || null,
        is_active: typeForm.is_active,
        sort_order: sort ?? null,
        keywords,
      });
    }
  }

  const typeColumns = useMemo<ColumnDef<ContactAccessTypeAdmin>[]>(
    () => [
      buildSelectColumn<ContactAccessTypeAdmin>(),
      {
        id: 'code',
        accessorFn: (row) => row.code,
        header: ({ column }) => <DataGridColumnHeader title="Code" column={column} />,
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Code', skeleton: <Skeleton className="h-4 w-20 font-mono" /> },
        cell: ({ row }) => <span className="font-mono">{row.original.code}</span>,
      },
      {
        id: 'name',
        accessorFn: (row) => row.name,
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        size: 200,
        enableSorting: false,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-28" /> },
        cell: ({ row }) => <span>{row.original.name}</span>,
      },
      {
        id: 'description',
        accessorFn: (row) => row.description,
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        size: 260,
        enableSorting: false,
        meta: { headerTitle: 'Description', skeleton: <Skeleton className="h-4 w-44" /> },
        cell: ({ row }) => (
          <span className="max-w-[200px] truncate">{row.original.description ?? '-'}</span>
        ),
      },
      {
        id: 'keywords',
        accessorFn: (row) => (row.keywords ?? []).join(', '),
        header: ({ column }) => <DataGridColumnHeader title="Keywords" column={column} />,
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Keywords', skeleton: <Skeleton className="h-4 w-32" /> },
        cell: ({ row }) => {
          const kw = row.original.keywords ?? [];
          const joined = kw.join(', ');
          return (
            <span className="max-w-[200px] truncate" title={joined || undefined}>
              {kw.length ? joined : '-'}
            </span>
          );
        },
      },
      {
        id: 'sort_order',
        accessorFn: (row) => row.sort_order ?? null,
        header: ({ column }) => <DataGridColumnHeader title="Sort order" column={column} />,
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Sort order', skeleton: <Skeleton className="h-4 w-20" /> },
        cell: ({ row }) => <span>{row.original.sort_order ?? '-'}</span>,
      },
      {
        id: 'is_active',
        accessorFn: (row) => row.is_active,
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Status', skeleton: <Skeleton className="h-6 w-20" /> },
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge variant="primary">Active</Badge>
          ) : (
            <Badge variant="secondary">Inactive</Badge>
          ),
      },
      {
        id: 'actions',
        header: '',
        size: 180,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
        cell: ({ row }) => (
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setPolicyType(row.original)}
              aria-label="Stock visibility"
              title="Stock visibility"
            >
              <Boxes className="size-4" />
            </Button>
            {canManage && (
              <>
                <Button variant="ghost" size="icon" onClick={() => openEditType(row.original)} aria-label="Edit">
                  <Pencil className="size-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setDeleteTypeCode(row.original.code)}
                  aria-label="Delete"
                >
                  <Trash2 className="size-4 text-destructive" />
                </Button>
              </>
            )}
          </div>
        ),
      },
    ],
    [canManage],
  );

  const typeTable = useReactTable({
    columns: typeColumns,
    data: types,
    getRowId: (row) => row.code,
    state: {
      pagination: { pageIndex: 0, pageSize: 10 },
      rowSelection,
    },
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
    getCoreRowModel: getCoreRowModel(),
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = canManage ? (
    <Button onClick={openCreateType}>
      <Plus className="size-4 mr-2" />
      Add type
    </Button>
  ) : undefined;

  return (
    <div className="space-y-6">
      <DataGrid
        table={typeTable}
        recordCount={types.length}
        isLoading={typesLoading}
        error={error}
        onRetry={() => void refetch()}
        emptyMessage="No access types. Add one to get started."
        tableLayout={{ width: 'fixed', columnsVisibility: true }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={typeTable}
              exportConfig={{ filename: 'contact_access_types_export.xlsx' }}
              primaryAction={listPrimaryAction}
            />
          </CardHeader>
          <CardContent>
            <DataGridTable />
          </CardContent>
        </Card>
      </DataGrid>

      {/* Type create/edit dialog */}
      <Dialog open={typeDialogOpen} onOpenChange={setTypeDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editingType ? 'Edit access type' : 'Add access type'}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="type-code">Code</Label>
              <Input
                id="type-code"
                value={typeForm.code}
                onChange={(e) => setTypeForm((f) => ({ ...f, code: e.target.value }))}
                placeholder="e.g. dealer"
                disabled={!!editingType}
              />
              {editingType && (
                <p className="text-xs text-muted-foreground">Code cannot be changed after creation.</p>
              )}
            </div>
            <div className="grid gap-2">
              <Label htmlFor="type-name">Name</Label>
              <Input
                id="type-name"
                value={typeForm.name}
                onChange={(e) => setTypeForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Dealer"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="type-desc">Description (optional)</Label>
              <Input
                id="type-desc"
                value={typeForm.description}
                onChange={(e) => setTypeForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Optional description"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="type-keywords">Keywords (optional)</Label>
              <Input
                id="type-keywords"
                value={typeForm.keywords}
                onChange={(e) => setTypeForm((f) => ({ ...f, keywords: e.target.value }))}
                placeholder="customer, homeowner, b2c"
              />
              <p className="text-xs text-muted-foreground">
                Comma-separated synonyms used by the AI matcher to resolve free-text phrasing
                (e.g. &quot;customer&quot; → this access level). Case-insensitive.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="type-sort">Sort order (optional)</Label>
              <Input
                id="type-sort"
                type="number"
                value={typeForm.sort_order}
                onChange={(e) =>
                  setTypeForm((f) => ({
                    ...f,
                    sort_order: e.target.value === '' ? '' : Number(e.target.value),
                  }))
                }
                placeholder="0"
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="type-active"
                checked={typeForm.is_active}
                onCheckedChange={(v) => setTypeForm((f) => ({ ...f, is_active: v === true }))}
              />
              <Label htmlFor="type-active">Active</Label>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTypeDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={saveType}
              disabled={
                createTypeMutation.isPending ||
                updateTypeMutation.isPending ||
                !typeForm.name.trim() ||
                (!editingType && !typeForm.code.trim())
              }
            >
              {editingType ? 'Update' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Stock visibility policy for one access type */}
      <Dialog open={!!policyType} onOpenChange={(open) => !open && setPolicyType(null)}>
        <DialogContent className="max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Stock visibility - {policyType?.name}</DialogTitle>
          </DialogHeader>
          {policyType ? (
            <StockVisibilitySection
              heading={null}
              scope={{ kind: 'access_type', accessTypeCode: policyType.code }}
            />
          ) : null}
        </DialogContent>
      </Dialog>

      {/* Delete type confirmation */}
      <Dialog open={!!deleteTypeCode} onOpenChange={(open) => !open && setDeleteTypeCode(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete access type</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            Are you sure you want to delete the access type &quot;{deleteTypeCode}&quot;? This may
            affect contacts and content visibility.
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTypeCode(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteTypeCode && deleteTypeMutation.mutate(deleteTypeCode)}
              disabled={deleteTypeMutation.isPending}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
