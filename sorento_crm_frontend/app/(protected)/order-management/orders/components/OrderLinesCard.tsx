'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import type { ColumnDef, RowSelectionState } from '@tanstack/react-table';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { FileDropzone } from '@/components/common/FileDropzone';
import { Trash2, Plus, Upload } from 'lucide-react';
import { useBulkDeleteOrderLines, useCreateOrderLine } from '../hooks/useOrders';
import { importDeliveryOrderDetail } from '../services/orderService';
import type { OrderLine } from '../types/order.types';
import { toast } from '@/lib/toast';
import OrderLineDeleteDialog from './OrderLineDeleteDialog';
import { codeWithName } from '@/lib/product-display';

interface OrderLinesCardProps {
  orderId: string;
  lines: OrderLine[];
  /** AutoCount-owned DO: its lines come only from AutoCount, so no add / delete. */
  readOnly?: boolean;
}

/** An optional money figure: blank is "not given", but a typed one must be a readable number of 0 or more. */
function optionalFigureError(raw: string): string | undefined {
  return numberFieldError(raw, { min: 0, optional: true });
}

export default function OrderLinesCard({ orderId, lines, readOnly = false }: OrderLinesCardProps) {
  const queryClient = useQueryClient();
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [importDialogOpen, setImportDialogOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);
  const [deleteLine, setDeleteLine] = useState<OrderLine | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const createLineMutation = useCreateOrderLine();
  const bulkDeleteLinesMutation = useBulkDeleteOrderLines();

  const [formProductId, setFormProductId] = useState('');
  const [formWarehouseId, setFormWarehouseId] = useState('');
  const [formQuantity, setFormQuantity] = useState('');
  const [showErrors, setShowErrors] = useState(false);
  const [formUnitPrice, setFormUnitPrice] = useState('');
  const [formDiscount, setFormDiscount] = useState('');
  const [formTotal, setFormTotal] = useState('');
  const [formTax, setFormTax] = useState('');
  const [formTotalExcl, setFormTotalExcl] = useState('');
  const [formTotalIncl, setFormTotalIncl] = useState('');

  const quantityError = numberFieldError(formQuantity, { min: 0 });

  const handleAddLine = async () => {
    if (!formProductId.trim() || !formWarehouseId.trim()) {
      toast.error('Product and Warehouse are required');
      return;
    }
    // A line without a quantity is not a line: checked here, never dropped silently.
    if (quantityError || [formUnitPrice, formDiscount, formTotal, formTax, formTotalExcl, formTotalIncl].some(optionalFigureError)) {
      setShowErrors(true);
      return;
    }
    try {
      await createLineMutation.mutateAsync({
        orderId,
        data: {
          product_id: formProductId.trim(),
          warehouse_id: formWarehouseId.trim(),
          quantity: Number(formQuantity),
          unit_price: formUnitPrice ? Number(formUnitPrice) : undefined,
          discount: formDiscount ? Number(formDiscount) : undefined,
          total: formTotal ? Number(formTotal) : undefined,
          tax: formTax ? Number(formTax) : undefined,
          total_excluding_tax: formTotalExcl ? Number(formTotalExcl) : undefined,
          total_including_tax: formTotalIncl ? Number(formTotalIncl) : undefined,
        },
      });
      setAddDialogOpen(false);
      setFormProductId('');
      setFormWarehouseId('');
      setFormQuantity('');
      setShowErrors(false);
      setFormUnitPrice('');
      setFormDiscount('');
      setFormTotal('');
      setFormTax('');
      setFormTotalExcl('');
      setFormTotalIncl('');
    } catch {
      // toast from mutation
    }
  };

  const handleImport = async () => {
    if (!importFile) {
      toast.error('Select a file');
      return;
    }
    setImporting(true);
    try {
      await importDeliveryOrderDetail(importFile);
      queryClient.invalidateQueries({ queryKey: ['order', orderId] });
      queryClient.invalidateQueries({ queryKey: ['orders'] });
      toast.success('Import queued. Check System → Import jobs for status.');
      setImportDialogOpen(false);
      setImportFile(null);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Import failed');
    } finally {
      setImporting(false);
    }
  };

  const formatNum = (v: number | string | null | undefined) =>
    v != null && v !== '' ? new Intl.NumberFormat('en-MY', { minimumFractionDigits: 2 }).format(Number(v)) : '-';

  const productLineLabel = (line: OrderLine) => {
    if (line.product) {
      const code = line.product.product_code ?? '';
      const name = line.product.product_name ?? '';
      if (code) return codeWithName(code, name);
      return (name || line.product_id).trim();
    }
    return line.product_id;
  };

  const selectedCount = Object.keys(rowSelection).length;

  const columns = useMemo<ColumnDef<OrderLine>[]>(
    () => [
      ...(readOnly
        ? []
        : [
            buildSelectColumn<OrderLine>({
              rowLabel: (row) => `Select line ${row.original.id}`,
            }),
          ]),
      {
        id: 'product',
        accessorFn: (row) => productLineLabel(row),
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        cell: ({ row }) => {
          const productId = row.original.product?.id ?? row.original.product_id;
          return productId ? (
            <Link
              href={`/master-data-management/products/${productId}`}
              className="text-primary font-medium hover:underline underline-offset-2"
              onClick={(e) => e.stopPropagation()}
            >
              {productLineLabel(row.original)}
            </Link>
          ) : (
            <span className="text-muted-foreground"> - </span>
          );
        },
        size: 220,
        meta: { headerTitle: 'Product' },
      },
      {
        id: 'warehouse',
        accessorFn: (row) =>
          row.warehouse
            ? `${row.warehouse.warehouse_code ?? ''} - ${row.warehouse.warehouse_name ?? ''}`
            : row.warehouse_id,
        header: ({ column }) => <DataGridColumnHeader title="Warehouse" column={column} />,
        cell: ({ row }) =>
          row.original.warehouse
            ? `${row.original.warehouse.warehouse_code ?? ''} - ${row.original.warehouse.warehouse_name ?? ''}`
            : row.original.warehouse_id,
        size: 160,
        meta: { headerTitle: 'Warehouse' },
      },
      {
        id: 'quantity',
        accessorFn: (row) => row.quantity ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Qty" column={column} className="justify-end" />
        ),
        cell: ({ row }) => <span className="block text-right">{formatNum(row.original.quantity)}</span>,
        size: 90,
        meta: { headerTitle: 'Qty' },
      },
      {
        id: 'unit_price',
        accessorFn: (row) => row.unit_price ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Unit price" column={column} className="justify-end" />
        ),
        cell: ({ row }) => (
          <span className="block text-right">{formatNum(row.original.unit_price)}</span>
        ),
        size: 110,
        meta: { headerTitle: 'Unit price' },
      },
      {
        id: 'discount',
        accessorFn: (row) => row.discount ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Discount" column={column} className="justify-end" />
        ),
        cell: ({ row }) => (
          <span className="block text-right">{formatNum(row.original.discount)}</span>
        ),
        size: 100,
        meta: { headerTitle: 'Discount' },
      },
      {
        id: 'total',
        accessorFn: (row) => row.total ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Total" column={column} className="justify-end" />
        ),
        cell: ({ row }) => <span className="block text-right">{formatNum(row.original.total)}</span>,
        size: 110,
        meta: { headerTitle: 'Total' },
      },
      {
        id: 'tax',
        accessorFn: (row) => row.tax ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Tax" column={column} className="justify-end" />
        ),
        cell: ({ row }) => <span className="block text-right">{formatNum(row.original.tax)}</span>,
        size: 90,
        meta: { headerTitle: 'Tax' },
      },
      {
        id: 'total_excluding_tax',
        accessorFn: (row) => row.total_excluding_tax ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Total (excl)" column={column} className="justify-end" />
        ),
        cell: ({ row }) => (
          <span className="block text-right">{formatNum(row.original.total_excluding_tax)}</span>
        ),
        size: 120,
        meta: { headerTitle: 'Total (excl)' },
      },
      {
        id: 'total_including_tax',
        accessorFn: (row) => row.total_including_tax ?? '',
        header: ({ column }) => (
          <DataGridColumnHeader title="Total (incl)" column={column} className="justify-end" />
        ),
        cell: ({ row }) => (
          <span className="block text-right">{formatNum(row.original.total_including_tax)}</span>
        ),
        size: 120,
        meta: { headerTitle: 'Total (incl)' },
      },
      ...(readOnly ? [] : [{
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="icon"
            className="text-destructive hover:text-destructive"
            onClick={(e) => {
              e.stopPropagation();
              setDeleteLine(row.original);
            }}
            aria-label="Delete line"
          >
            <Trash2 className="size-4" />
          </Button>
        ),
        size: 60,
        enableResizing: false,
        meta: { headerTitle: 'Actions' },
      } satisfies ColumnDef<OrderLine>]),
    ],
    [readOnly],
  );

  return (
    <>
      <PanelDataGrid<OrderLine>
        title="Delivery Order Lines"
        toolbar={
          readOnly ? (
            <span className="text-sm text-muted-foreground">From AutoCount</span>
          ) : (
          <>
            <Button
              variant="destructive"
              size="sm"
              disabled={selectedCount === 0 || bulkDeleteLinesMutation.isPending}
              onClick={() => setBulkDeleteDialogOpen(true)}
            >
              <Trash2 className="size-4 mr-1" />
              Delete selected ({selectedCount})
            </Button>
            <Button variant="outline" size="sm" onClick={() => setImportDialogOpen(true)}>
              <Upload className="size-4 mr-1" />
              Import
            </Button>
            <Button variant="primary" size="sm" onClick={() => setAddDialogOpen(true)} disabled={createLineMutation.isPending}>
              <Plus className="size-4 mr-1" />
              Add line
            </Button>
          </>
          )
        }
        columns={columns}
        rows={lines}
        getRowId={(row) => row.id}
        listingKey="order_management.orders.view::lines"
        rowSelection={readOnly ? undefined : rowSelection}
        onRowSelectionChange={readOnly ? undefined : setRowSelection}
        emptyTitle="No delivery order lines."
        emptyBody={readOnly ? undefined : 'Import from Excel or add manually.'}
        // SF-8 (M5 run 3 review): a document's own line table renders every
        // row - a page-2 would hide lines the reader expects in one scroll.
        paginate={false}
      />

      <Dialog
        open={addDialogOpen}
        onOpenChange={(open) => {
          setAddDialogOpen(open);
          if (!open) setShowErrors(false);
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Add delivery order line</DialogTitle>
            <DialogDescription>Product ID and Warehouse ID are required (UUIDs from Products and Warehouses).</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid gap-2">
              <Label>Product ID</Label>
              <Input value={formProductId} onChange={(e) => setFormProductId(e.target.value)} placeholder="UUID" />
            </div>
            <div className="grid gap-2">
              <Label>Warehouse ID</Label>
              <Input value={formWarehouseId} onChange={(e) => setFormWarehouseId(e.target.value)} placeholder="UUID" />
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div className="grid gap-2">
                <Label>Quantity</Label>
                <NumberInput
                  value={formQuantity}
                  error={showErrors ? quantityError : undefined}
                  onChange={(e) => setFormQuantity(e.target.value)}
                />
              </div>
              <div className="grid gap-2">
                <Label>Unit price</Label>
                <NumberInput value={formUnitPrice} error={showErrors ? optionalFigureError(formUnitPrice) : undefined} onChange={(e) => setFormUnitPrice(e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div className="grid gap-2">
                <Label>Discount</Label>
                <NumberInput value={formDiscount} error={showErrors ? optionalFigureError(formDiscount) : undefined} onChange={(e) => setFormDiscount(e.target.value)} />
              </div>
              <div className="grid gap-2">
                <Label>Total</Label>
                <NumberInput value={formTotal} error={showErrors ? optionalFigureError(formTotal) : undefined} onChange={(e) => setFormTotal(e.target.value)} />
              </div>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-2">
              <div className="grid gap-2">
                <Label>Tax</Label>
                <NumberInput value={formTax} error={showErrors ? optionalFigureError(formTax) : undefined} onChange={(e) => setFormTax(e.target.value)} />
              </div>
              <div className="grid gap-2">
                <Label>Total (excl)</Label>
                <NumberInput value={formTotalExcl} error={showErrors ? optionalFigureError(formTotalExcl) : undefined} onChange={(e) => setFormTotalExcl(e.target.value)} />
              </div>
              <div className="grid gap-2">
                <Label>Total (incl)</Label>
                <NumberInput value={formTotalIncl} error={showErrors ? optionalFigureError(formTotalIncl) : undefined} onChange={(e) => setFormTotalIncl(e.target.value)} />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => { setAddDialogOpen(false); setShowErrors(false); }}>Cancel</Button>
            <Button onClick={handleAddLine} disabled={createLineMutation.isPending}>Add</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={importDialogOpen} onOpenChange={setImportDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Import delivery order lines</DialogTitle>
            <DialogDescription>Upload Excel with columns: Doc No, Item Code, Location, Qty, Unit Price, Discount, Total, Tax, Total Excluding Tax, Total Including Tax.</DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <FileDropzone
              accept=".xlsx,.xls"
              files={importFile ? [importFile] : []}
              onFilesChange={(files) => setImportFile(files[0] ?? null)}
              onReject={() => toast.error('Only .xlsx and .xls files are allowed.')}
              title="Drop the Excel file here, or click to browse"
              hint=".xlsx or .xls only"
              aria-label="Delivery order lines workbook"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setImportDialogOpen(false)}>Cancel</Button>
            <Button onClick={handleImport} disabled={!importFile || importing}>{importing ? 'Queuing…' : 'Import'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {deleteLine && (
        <OrderLineDeleteDialog
          open={!!deleteLine}
          onOpenChange={(open: boolean) => !open && setDeleteLine(null)}
          orderId={orderId}
          line={deleteLine}
          onSuccess={() => setDeleteLine(null)}
        />
      )}

      <Dialog open={bulkDeleteDialogOpen} onOpenChange={setBulkDeleteDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete selected delivery order lines?</DialogTitle>
            <DialogDescription>
              This will permanently delete {selectedCount} selected line(s).
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setBulkDeleteDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={selectedCount === 0 || bulkDeleteLinesMutation.isPending}
              onClick={async () => {
                try {
                  await bulkDeleteLinesMutation.mutateAsync({
                    orderId,
                    ids: Object.keys(rowSelection),
                  });
                  setRowSelection({});
                  setBulkDeleteDialogOpen(false);
                } catch {
                  // toast from mutation
                }
              }}
            >
              {bulkDeleteLinesMutation.isPending ? 'Deleting…' : 'Delete selected'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
