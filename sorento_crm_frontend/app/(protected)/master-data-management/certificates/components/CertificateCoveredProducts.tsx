'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  ColumnDef,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { getCertificateProductOptions } from '../services/certificateService';
import { SOURCE_LABELS } from '../lib/certificateDisplay';
import { useAddCertificateProduct, useRemoveCertificateProduct } from '../hooks/useCertificates';
import type { CertificateProduct } from '../types/certificate.types';
import { distinctName } from '@/lib/product-display';

/**
 * Coverage as a standard DataGrid, not bespoke chips: fixed layout, resizable
 * columns, explicit sizes and `truncate` + `title` on long text, the same as
 * every other listing in the system. `source` is a shared status pill from
 * lib/status-pill rather than a per-feature colour scheme.
 */
export default function CertificateCoveredProducts({
  certificateId,
  products,
}: {
  certificateId: string;
  products: CertificateProduct[];
}) {
  const [productToAdd, setProductToAdd] = useState('');
  const [pendingUnlink, setPendingUnlink] = useState<CertificateProduct | null>(null);

  const addMutation = useAddCertificateProduct();
  const removeMutation = useRemoveCertificateProduct();

  const [allOptions, setAllOptions] = useState<{ value: string; label: string }[]>([]);

  // Loaded once per mount. The select is static-mode, so the full list has to be
  // present before filtering; the endpoint is the shared products select.
  useEffect(() => {
    let cancelled = false;
    getCertificateProductOptions()
      .then((rows) => {
        if (!cancelled) setAllOptions(rows);
      })
      .catch(() => {
        if (!cancelled) setAllOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const alreadyCovered = new Set(products.map((p) => p.product_id));
  const options = allOptions.filter((o) => !alreadyCovered.has(o.value));

  const handleAdd = async () => {
    if (!productToAdd) return;
    try {
      await addMutation.mutateAsync({ id: certificateId, productId: productToAdd });
      setProductToAdd('');
    } catch {
      // Error surfaced by the mutation's onError toast.
    }
  };

  const columns = useMemo<ColumnDef<CertificateProduct>[]>(
    () => [
      {
        accessorKey: 'product_code',
        header: ({ column }) => <DataGridColumnHeader title="Product Code" column={column} />,
        cell: ({ row }) => (
          <div className="truncate font-medium" title={row.original.product_code}>
            {row.original.product_code}
          </div>
        ),
        size: 180,
        minSize: 120,
        meta: { headerTitle: 'Product Code' },
      },
      {
        accessorKey: 'product_name',
        header: ({ column }) => <DataGridColumnHeader title="Product Name" column={column} />,
        cell: ({ row }) => (
          <div
            className="truncate"
            title={distinctName(row.original.product_code, row.original.product_name) ?? undefined}
          >
            {distinctName(row.original.product_code, row.original.product_name)}
          </div>
        ),
        size: 320,
        minSize: 160,
        meta: { headerTitle: 'Product Name' },
      },
      {
        accessorKey: 'company_name',
        header: ({ column }) => <DataGridColumnHeader title="Company" column={column} />,
        cell: ({ row }) => (
          <div className="truncate" title={row.original.company_name ?? undefined}>
            {row.original.company_name ?? '-'}
          </div>
        ),
        size: 140,
        minSize: 100,
        meta: { headerTitle: 'Company' },
      },
      {
        accessorKey: 'source',
        header: ({ column }) => <DataGridColumnHeader title="Added By" column={column} />,
        cell: ({ row }) => (
          <span className={`${STATUS_PILL_BASE} ${statusPillClass(row.original.source)}`}>
            {SOURCE_LABELS[row.original.source]}
          </span>
        ),
        size: 150,
        minSize: 120,
        enableSorting: false,
        meta: { headerTitle: 'Added By' },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <Button
            mode="icon"
            variant="dim"
            aria-label={`Remove ${row.original.product_code} from coverage`}
            onClick={(e) => {
              e.stopPropagation();
              setPendingUnlink(row.original);
            }}
          >
            <Trash2 className="size-4" />
          </Button>
        ),
        size: 60,
        minSize: 60,
        enableSorting: false,
        enableResizing: false,
        meta: { headerTitle: 'Actions' },
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: products,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <SearchableSelect
          value={productToAdd}
          onChange={setProductToAdd}
          options={options}
          placeholder="Add a product"
          emptyMessage="No product left to add."
          clearable
          size="sm"
          triggerClassName="w-64"
        />
        <Button
          size="sm"
          onClick={() => void handleAdd()}
          disabled={!productToAdd || addMutation.isPending}
        >
          {addMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus />}
          Add
        </Button>
      </div>

      <DataGrid
        table={table}
        recordCount={products.length}
        emptyMessage="No product is covered yet. Use the product picker above to add the first one."
        tableLayout={{ width: 'fixed', columnsResizable: true }}
      >
        <Card>
          <CardTable>
            <DataGridTable />
          </CardTable>
          {/* A certificate can cover 90 products. Without a pager the grid
              silently shows the first page and hides the rest. */}
          <CardFooter>
            <DataGridPagination />
          </CardFooter>
        </Card>
      </DataGrid>

      <AlertDialog
        open={pendingUnlink !== null}
        onOpenChange={(open) => !open && setPendingUnlink(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm remove</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingUnlink
                ? `${pendingUnlink.product_code} will stop being covered by this certificate, and the certificate will no longer be served on that product page. This action cannot be undone.`
                : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={removeMutation.isPending}
              onClick={(e) => {
                e.preventDefault();
                if (!pendingUnlink) return;
                removeMutation.mutate(
                  { id: certificateId, coverageId: pendingUnlink.id },
                  { onSettled: () => setPendingUnlink(null) },
                );
              }}
            >
              {removeMutation.isPending && <Loader2 className="me-2 size-4 animate-spin" />}
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
