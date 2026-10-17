'use client';

import { useMemo, useState } from 'react';
import { Plus, Pencil, Ruler, Trash2 } from 'lucide-react';
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type RowSelectionState,
} from '@tanstack/react-table';
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
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Checkbox } from '@/components/ui/checkbox';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { SpecVisibilitySection } from '@/components/spec-visibility/SpecVisibilitySection';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { ADDITIONAL_LANDING_KINDS, portalFormKindLabel } from '@/lib/portal-form-kinds';
import { toast } from '@/lib/toast';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { useMarketSegments, useMarketSegmentMutations } from '../hooks/useMarketSegments';
import { type MarketSegment } from '../services/marketSegmentService';

/** The kinds a segment may grant beyond the base four every contact already
 *  has (PLAN-portal-forms-market-segment D3/D4; today: Price Tag Request). */
const ADDITIONAL_PORTAL_FORM_OPTIONS = ADDITIONAL_LANDING_KINDS.map((kind) => ({
  value: kind,
  label: portalFormKindLabel(kind),
}));

export default function MarketSegmentsAdmin() {
  const { data: segments = [], isLoading, isError } = useMarketSegments();
  const { create, update } = useMarketSegmentMutations();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<MarketSegment | null>(null);
  // Spec visibility is one row per segment, so it is edited from the row it
  // belongs to rather than as a column every segment would have to carry
  // (same convention as `ContactAccessTypesAdmin`'s stock visibility dialog).
  const [specSegment, setSpecSegment] = useState<MarketSegment | null>(null);
  // Delete asks nothing (D7): a toast counts down with Cancel. A segment still
  // assigned to a contact or a team member is refused by the server, and that
  // refusal now arrives as the toast's error rather than as a warning in a
  // dialog nobody could act on.
  const deletion = useDeferredRowAction({
    actionKey: 'market_segment.delete',
    entityType: 'market_segment',
    successMessage: 'Market segment deleted',
    invalidateKeys: [['market-segments']],
  });
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [form, setForm] = useState({
    code: '',
    name: '',
    description: '',
    is_active: true,
    sort_order: '' as string | number,
    is_requestor_selectable: false,
    portal_form_types: [] as string[],
  });

  // Set by a Save that found the sort order wrong; the sentence then follows the typing.
  const [sortChecked, setSortChecked] = useState(false);
  const sortError = numberFieldError(form.sort_order, { integer: true, optional: true });

  function resetForm() {
    setSortChecked(false);
    setForm({
      code: '',
      name: '',
      description: '',
      is_active: true,
      sort_order: '',
      is_requestor_selectable: false,
      portal_form_types: [],
    });
    setEditing(null);
  }

  function openCreate() {
    resetForm();
    setDialogOpen(true);
  }

  function openEdit(row: MarketSegment) {
    setEditing(row);
    setSortChecked(false);
    setForm({
      code: row.code,
      name: row.name,
      description: row.description ?? '',
      is_active: row.is_active,
      sort_order: row.sort_order ?? '',
      is_requestor_selectable: row.is_requestor_selectable,
      portal_form_types: row.portal_form_types ?? [],
    });
    setDialogOpen(true);
  }

  function save() {
    const sort = form.sort_order === '' ? undefined : Number(form.sort_order);
    if (sortError) {
      setSortChecked(true);
      return;
    }
    if (editing) {
      update.mutate(
        {
          code: editing.code,
          body: {
            name: form.name.trim(),
            description: form.description.trim() || null,
            is_active: form.is_active,
            sort_order: sort,
            is_requestor_selectable: form.is_requestor_selectable,
            portal_form_types: form.portal_form_types,
          },
        },
        {
          onSuccess: () => {
            setDialogOpen(false);
            resetForm();
          },
        },
      );
    } else {
      if (!form.code.trim() || !form.name.trim()) {
        toast.error('Code and name are required');
        return;
      }
      create.mutate(
        {
          code: form.code.trim(),
          name: form.name.trim(),
          description: form.description.trim() || null,
          is_active: form.is_active,
          sort_order: sort ?? null,
          is_requestor_selectable: form.is_requestor_selectable,
          portal_form_types: form.portal_form_types,
        },
        {
          onSuccess: () => {
            setDialogOpen(false);
            resetForm();
          },
        },
      );
    }
  }

  const columns = useMemo<ColumnDef<MarketSegment>[]>(
    () => [
      buildSelectColumn<MarketSegment>(),
      {
        id: 'code',
        accessorFn: (row) => row.code,
        header: ({ column }) => <DataGridColumnHeader title="Code" column={column} />,
        size: 160,
        enableSorting: false,
        meta: { headerTitle: 'Code', skeleton: <Skeleton className="h-4 w-20 font-mono" /> },
        cell: ({ row }) => (
          <span className="font-mono max-w-[140px] truncate block" title={row.original.code}>
            {row.original.code}
          </span>
        ),
      },
      {
        id: 'name',
        accessorFn: (row) => row.name,
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Name', skeleton: <Skeleton className="h-4 w-28" /> },
        cell: ({ row }) => (
          <span className="max-w-[200px] truncate block" title={row.original.name}>
            {row.original.name}
          </span>
        ),
      },
      {
        id: 'description',
        accessorFn: (row) => row.description,
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        size: 280,
        enableSorting: false,
        meta: { headerTitle: 'Description', skeleton: <Skeleton className="h-4 w-44" /> },
        cell: ({ row }) => (
          <span className="max-w-[240px] truncate block" title={row.original.description ?? undefined}>
            {row.original.description ?? '-'}
          </span>
        ),
      },
      {
        id: 'sort_order',
        accessorFn: (row) => row.sort_order ?? null,
        header: ({ column }) => <DataGridColumnHeader title="Sort order" column={column} />,
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Sort order', skeleton: <Skeleton className="h-4 w-16" /> },
        cell: ({ row }) => <span>{row.original.sort_order ?? '-'}</span>,
      },
      {
        id: 'is_active',
        accessorFn: (row) => row.is_active,
        header: ({ column }) => <DataGridColumnHeader title="Active" column={column} />,
        size: 140,
        enableSorting: false,
        meta: { headerTitle: 'Active', skeleton: <Skeleton className="h-6 w-20" /> },
        cell: ({ row }) =>
          row.original.is_active ? (
            <Badge variant="primary">Active</Badge>
          ) : (
            <Badge variant="secondary">Inactive</Badge>
          ),
      },
      {
        id: 'is_requestor_selectable',
        accessorFn: (row) => row.is_requestor_selectable,
        header: ({ column }) => (
          <DataGridColumnHeader title="Requestor picker" column={column} />
        ),
        size: 170,
        enableSorting: false,
        meta: { headerTitle: 'Requestor picker', skeleton: <Skeleton className="h-6 w-24" /> },
        cell: ({ row }) =>
          row.original.is_requestor_selectable ? (
            <Badge variant="primary" appearance="light">
              Included
            </Badge>
          ) : (
            <Badge variant="secondary" appearance="light">
              Excluded
            </Badge>
          ),
      },
      {
        id: 'portal_form_types',
        accessorFn: (row) => (row.portal_form_types ?? []).join(', '),
        header: ({ column }) => (
          <DataGridColumnHeader title="Additional portal forms" column={column} />
        ),
        size: 220,
        enableSorting: false,
        meta: { headerTitle: 'Additional portal forms', skeleton: <Skeleton className="h-6 w-32" /> },
        cell: ({ row }) => {
          const kinds = row.original.portal_form_types ?? [];
          if (!kinds.length) return <span className="text-muted-foreground">-</span>;
          return (
            <div
              className="flex flex-wrap gap-1"
              title={kinds.map(portalFormKindLabel).join(', ')}
            >
              {kinds.map((kind) => (
                <Badge key={kind} variant="secondary" size="sm">
                  {portalFormKindLabel(kind)}
                </Badge>
              ))}
            </div>
          );
        },
      },
      {
        id: 'actions',
        header: '',
        size: 160,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
        cell: ({ row }) => (
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setSpecSegment(row.original)}
              aria-label="Spec visibility"
              title="Spec visibility"
            >
              <Ruler className="size-4" />
            </Button>
            <Button variant="ghost" size="icon" onClick={() => openEdit(row.original)} aria-label="Edit">
              <Pencil className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() =>
                deletion.run({
                  id: row.original.code,
                  subject: row.original.name || row.original.code,
                })
              }
              aria-label="Delete"
            >
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        ),
      },
    ],
    [deletion],
  );

  const table = useReactTable({
    columns,
    data: segments,
    getRowId: (row) => row.code,
    state: {
      pagination: { pageIndex: 0, pageSize: 10 },
      rowSelection,
    },
    onRowSelectionChange: setRowSelection,
    enableRowSelection: true,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={openCreate}>
      <Plus className="size-4 mr-2" />
      Add segment
    </Button>
  );

  return (
    <div className="space-y-6">
      {isError ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-destructive">
            Failed to load market segments. Please try again.
          </CardContent>
        </Card>
      ) : (
        <DataGrid
          table={table}
          recordCount={segments.length}
          isLoading={isLoading}
          emptyMessage="No market segments yet. Add one to get started."
          tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
        >
          <Card>
            <CardHeader className="block">
              <DataGridListToolbar
                table={table}
                exportConfig={{ filename: 'market_segments_export.xlsx' }}
                primaryAction={listPrimaryAction}
              />
            </CardHeader>
            <CardContent>
              <DataGridTable />
            </CardContent>
          </Card>
        </DataGrid>
      )}

      {/* Create / edit dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? 'Edit market segment' : 'Add market segment'}</DialogTitle>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label htmlFor="segment-code">Code</Label>
              <Input
                id="segment-code"
                value={form.code}
                onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                placeholder="e.g. retail"
                disabled={!!editing}
              />
              {editing && (
                <p className="text-xs text-muted-foreground">Code cannot be changed after creation.</p>
              )}
            </div>
            <div className="grid gap-2">
              <Label htmlFor="segment-name">Name</Label>
              <Input
                id="segment-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                placeholder="e.g. Retail"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="segment-desc">Description (optional)</Label>
              <Input
                id="segment-desc"
                value={form.description}
                onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                placeholder="Optional description"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="segment-sort">Sort order (optional)</Label>
              <NumberInput
                id="segment-sort"
                value={form.sort_order}
                onChange={(e) => setForm((f) => ({ ...f, sort_order: e.target.value }))}
                error={sortChecked ? sortError : undefined}
                placeholder="0"
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="segment-active"
                checked={form.is_active}
                onCheckedChange={(v) => setForm((f) => ({ ...f, is_active: v === true }))}
              />
              <Label htmlFor="segment-active">Active</Label>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="segment-portal-forms">Additional portal forms</Label>
              <SearchableMultiSelect
                id="segment-portal-forms"
                value={form.portal_form_types}
                onChange={(v) => setForm((f) => ({ ...f, portal_form_types: v }))}
                options={ADDITIONAL_PORTAL_FORM_OPTIONS}
                placeholder="No additional portal forms"
                emptyMessage="No additional portal forms"
              />
            </div>
            <div className="flex items-start gap-2">
              <Checkbox
                id="segment-requestor-selectable"
                className="mt-0.5"
                checked={form.is_requestor_selectable}
                onCheckedChange={(v) =>
                  setForm((f) => ({ ...f, is_requestor_selectable: v === true }))
                }
              />
              <div className="grid gap-1">
                <Label htmlFor="segment-requestor-selectable">Include in requestor picker</Label>
                <p className="text-xs text-muted-foreground">
                  Contacts in this segment can be picked as the requester on a purchase
                  request, sponsorship form or stock inquiry.
                </p>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={save}
              disabled={
                create.isPending ||
                update.isPending ||
                !form.name.trim() ||
                (!editing && !form.code.trim())
              }
            >
              {editing ? 'Update' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Spec visibility policy for one market segment */}
      <Dialog open={!!specSegment} onOpenChange={(open) => !open && setSpecSegment(null)}>
        <DialogContent className="max-h-[85dvh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Spec visibility - {specSegment?.name}</DialogTitle>
          </DialogHeader>
          {specSegment ? (
            <SpecVisibilitySection
              heading={null}
              scope={{ kind: 'segment', segmentCode: specSegment.code }}
            />
          ) : null}
        </DialogContent>
      </Dialog>

    </div>
  );
}
