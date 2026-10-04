'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useCompareMappings, useSaveCompareMapping } from '../hooks/useAutocountPull';
import type {
  CompareMappingBody,
  CompareMappingColumn,
  CompareMappingKind,
} from '../types/autocountPull.types';

const KIND_TABS: Record<'delivery_orders' | 'goods_receive_notes', Array<{ kind: CompareMappingKind; label: string }>> = {
  delivery_orders: [
    { kind: 'order_listing', label: 'Order Listing' },
    { kind: 'order_tracking', label: 'Order Tracking' },
  ],
  goods_receive_notes: [
    { kind: 'grn_detail_listing', label: 'Detail Listing' },
    { kind: 'grn_listing', label: 'GRN Listing' },
  ],
};

const TRANSFORM_OPTIONS = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'money', label: 'Money' },
  { value: 'date', label: 'Date' },
  { value: 'percent_text', label: 'Percent text' },
  { value: 'percent_fraction', label: 'Percent fraction' },
  { value: 'cancel_flag', label: 'Cancel flag' },
];

const FIELD_OPTIONS: Record<CompareMappingKind, Array<{ value: string; label: string }>> = {
  order_listing: [
    { value: 'doc_no', label: 'Doc No' },
    { value: 'doc_date', label: 'Doc Date' },
    { value: 'item_code', label: 'Item Code' },
    { value: 'location', label: 'Location' },
    { value: 'qty', label: 'Qty' },
    { value: 'unit_price', label: 'Unit Price' },
    { value: 'discount', label: 'Discount' },
    { value: 'total_ex', label: 'Total (Ex)' },
  ],
  order_tracking: [
    { value: 'doc_no', label: 'Doc No' },
    { value: 'doc_date', label: 'Doc Date' },
    { value: 'debtor_code', label: 'Debtor Code' },
    { value: 'cancel', label: 'Cancel' },
  ],
  grn_detail_listing: [
    { value: 'doc_no', label: 'Doc No' },
    { value: 'doc_date', label: 'Doc Date' },
    { value: 'item_code', label: 'Item Code' },
    { value: 'location', label: 'Location' },
    { value: 'qty', label: 'Qty' },
    { value: 'source_doc', label: 'Source PO / SPO' },
  ],
  grn_listing: [
    { value: 'doc_no', label: 'Doc No' },
    { value: 'doc_date', label: 'Doc Date' },
    { value: 'creditor_code', label: 'Creditor Code' },
    { value: 'source_doc', label: 'Source PO / SPO' },
    { value: 'cancel', label: 'Cancelled' },
  ],
};

/** Mirrors the backend `TRANSFORMS_BY_FIELD`: the transforms that can read each field. */
const TRANSFORMS_BY_FIELD: Record<string, string[]> = {
  doc_no: ['text'], item_code: ['text'], location: ['text'], debtor_code: ['text'],
  doc_date: ['date'], qty: ['number'], unit_price: ['money'], total_ex: ['money'],
  discount: ['percent_text', 'percent_fraction'], cancel: ['cancel_flag'],
  creditor_code: ['text'], source_doc: ['text'],
};

function transformOptionsFor(field: string) {
  const allowed = TRANSFORMS_BY_FIELD[field];
  return allowed ? TRANSFORM_OPTIONS.filter((o) => allowed.includes(o.value)) : TRANSFORM_OPTIONS;
}

type Drafts = Partial<Record<CompareMappingKind, CompareMappingBody>>;

export interface CompareMappingDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Whose two workbooks the tabs edit; delivery orders when absent. */
  entity?: 'delivery_orders' | 'goods_receive_notes';
}

/** "Mapping": the sheet and the Excel column -> transform -> Sorento field rows the delivery
 *  orders compare reads each macro workbook with. One line tab per workbook kind, edited in
 *  place, one Save for the tab on screen. */
export function CompareMappingDialog({
  open,
  onOpenChange,
  entity = 'delivery_orders',
}: CompareMappingDialogProps) {
  const { data } = useCompareMappings(open);
  const save = useSaveCompareMapping();
  const tabs = KIND_TABS[entity];
  const [kind, setKind] = useState<CompareMappingKind>(tabs[0].kind);
  const [drafts, setDrafts] = useState<Drafts | null>(null);

  // Seed the drafts once per open; later refetches (after a save) must not wipe unsaved
  // edits on the other tab.
  useEffect(() => {
    if (!open) {
      setDrafts(null);
      return;
    }
    if (data && drafts === null) {
      const next: Drafts = {};
      for (const item of data.items) {
        next[item.kind] = { sheet_name: item.sheet_name, columns: item.columns.map((c) => ({ ...c })) };
      }
      setDrafts(next);
    }
  }, [open, data, drafts]);

  const draft = drafts?.[kind];

  const updateDraft = useCallback(
    (patch: (current: CompareMappingBody) => CompareMappingBody) =>
      setDrafts((prev) => {
        const current = prev?.[kind];
        return prev && current ? { ...prev, [kind]: patch(current) } : prev;
      }),
    [kind],
  );

  const updateColumn = useCallback(
    (index: number, patch: Partial<CompareMappingColumn>) =>
      updateDraft((current) => ({
        ...current,
        columns: current.columns.map((c, i) => (i === index ? { ...c, ...patch } : c)),
      })),
    [updateDraft],
  );

  const columns = useMemo<ColumnDef<CompareMappingColumn>[]>(
    () => [
      {
        id: 'excel_header',
        header: ({ column }) => <DataGridColumnHeader title="Excel column" column={column} />,
        cell: ({ row }) => (
          <Input
            aria-label="Excel column"
            value={row.original.excel_header}
            title={row.original.excel_header}
            onChange={(e) => updateColumn(row.index, { excel_header: e.target.value })}
          />
        ),
        size: 240,
        enableSorting: false,
      },
      {
        id: 'transform',
        header: ({ column }) => <DataGridColumnHeader title="Transform" column={column} />,
        cell: ({ row }) => (
          <SearchableSelect
            aria-label="Transform"
            value={row.original.transform}
            options={transformOptionsFor(row.original.field)}
            onChange={(value) => updateColumn(row.index, { transform: value })}
            truncateTriggerLabel
          />
        ),
        size: 180,
        enableSorting: false,
      },
      {
        id: 'field',
        header: ({ column }) => <DataGridColumnHeader title="Sorento field" column={column} />,
        cell: ({ row }) => (
          <SearchableSelect
            aria-label="Sorento field"
            value={row.original.field}
            options={FIELD_OPTIONS[kind]}
            onChange={(value) => {
              const allowed = TRANSFORMS_BY_FIELD[value];
              updateColumn(
                row.index,
                allowed && !allowed.includes(row.original.transform)
                  ? { field: value, transform: allowed[0] }
                  : { field: value },
              );
            }}
            truncateTriggerLabel
          />
        ),
        size: 180,
        enableSorting: false,
      },
      {
        id: 'remove',
        header: () => <span className="sr-only">Remove</span>,
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Remove row"
            onClick={() =>
              updateDraft((current) => ({
                ...current,
                columns: current.columns.filter((_, i) => i !== row.index),
              }))
            }
          >
            <Trash2 className="size-4" />
          </Button>
        ),
        size: 56,
        enableSorting: false,
      },
    ],
    [kind, updateColumn, updateDraft],
  );

  const table = useReactTable({
    columns,
    data: draft?.columns ?? [],
    getRowId: (_row, index) => String(index),
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  const handleSave = () => {
    if (!draft) return;
    save.mutate({ kind, body: draft });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Mapping</DialogTitle>
          <DialogDescription className="sr-only">Compare mapping</DialogDescription>
        </DialogHeader>
        <Tabs value={kind} onValueChange={(value) => setKind(value as CompareMappingKind)}>
          <TabsList variant="line">
            {tabs.map((tab) => (
              <TabsTrigger key={tab.kind} value={tab.kind}>
                {tab.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        {draft && (
          <div className="space-y-4">
            <div className="max-w-xs space-y-1.5">
              <Label htmlFor="compare-mapping-sheet">Sheet name</Label>
              <Input
                id="compare-mapping-sheet"
                value={draft.sheet_name}
                onChange={(e) => updateDraft((current) => ({ ...current, sheet_name: e.target.value }))}
              />
            </div>
            <DataGrid
              table={table}
              recordCount={draft.columns.length}
              isLoading={false}
              tableLayout={{ width: 'fixed', columnsResizable: true }}
              listingKey={null}
            >
              <Card>
                <CardTable className="max-h-[50dvh] overflow-auto">
                  <DataGridTable />
                </CardTable>
              </Card>
            </DataGrid>
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                updateDraft((current) => {
                  const used = new Set(current.columns.map((c) => c.field));
                  const field = FIELD_OPTIONS[kind].find((o) => !used.has(o.value))?.value ?? '';
                  const transform = TRANSFORMS_BY_FIELD[field]?.[0] ?? 'text';
                  return { ...current, columns: [...current.columns, { excel_header: '', transform, field }] };
                })
              }
            >
              <Plus className="size-4" />
              Add row
            </Button>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button onClick={handleSave} disabled={!draft || save.isPending}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default CompareMappingDialog;
