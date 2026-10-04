'use client';

import { useMemo, useState } from 'react';
import {
  ColumnDef,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Download, Settings2 } from 'lucide-react';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { FileDropzone } from '@/components/common/FileDropzone';
import { toast } from '@/lib/toast';
import { generateExcelFile, parseExcelFile, type ColumnOption } from '@/lib/excel-utils';
import { useCompareMappings, useComparePull } from '../hooks/useAutocountPull';
import { CompareMappingDialog } from './CompareMappingDialog';
import { isCompareFullMatch } from '../types/compareMatch';
import { buildCompareRows, type CompareRow } from './compareRows';
import {
  isDocumentEntity,
  type AutocountComparePullResult,
  type AutocountPullCompareSource,
  type AutocountPullCompareSummary,
  type AutocountPullEntity,
  type CompareMappingKind,
} from '../types/autocountPull.types';

export interface PullCompareTabProps {
  jobId: string;
  entity: AutocountPullEntity;
  /** Delivery orders: the DocDate window the compare cuts the files to (from the pull). */
  window?: { fromDay: string | null; toDay: string | null } | null;
}

/** D4 (small-fix track): same reasoning as `PullExcelViewTab`'s own constant - a stable
 *  key per entity, never the pathname (which embeds the job id) the shared `DataGrid`
 *  falls back to without one. */
const COMPARE_LISTING_KEY: Record<AutocountPullEntity, string> = {
  products: 'master_data.products.autocount_pull::compare',
  stock_balances: 'inventory.stock.autocount_pull::compare',
  delivery_orders: 'order_management.orders.autocount_pull::compare',
  goods_receive_notes: 'procurement.grn.autocount_pull::compare',
};

interface CompareSourceEntry {
  source: AutocountPullCompareSource;
  title: string;
  ariaLabel: string;
  unit: string;
  /** The saved mapping this file is read with. */
  kind: CompareMappingKind;
}

/** The two macro files a delivery-orders pull is compared with (owner decision 30 Sep, mock
 *  section 2). `Overall Tracking` is not compared (owner Q6): AutoCount has none of it. */
const DO_SOURCES: CompareSourceEntry[] = [
  {
    source: 'lines',
    title: 'Order Listing (macro)',
    ariaLabel: 'Order Listing sheet to compare',
    unit: 'lines',
    kind: 'order_listing',
  },
  {
    source: 'headers',
    title: 'Order Tracking (macro)',
    ariaLabel: 'Order Tracking sheet to compare',
    unit: 'documents',
    kind: 'order_tracking',
  },
];

/** The two files a GRN pull is compared with (owner Q5 a, 2 Oct): the "DETAIL LISTING"
 *  export (lines) and the "GRN Listing" macro, read from its `Master` sheet by name. */
const GRN_SOURCES: CompareSourceEntry[] = [
  {
    source: 'lines',
    title: 'Detail Listing',
    ariaLabel: 'GRN Detail Listing sheet to compare',
    unit: 'lines',
    kind: 'grn_detail_listing',
  },
  {
    source: 'headers',
    title: 'GRN Listing (macro)',
    ariaLabel: 'GRN Listing sheet to compare',
    unit: 'documents',
    kind: 'grn_listing',
  },
];

const NO_SOURCES: CompareSourceEntry[] = [];

const SOURCES_BY_ENTITY: Record<AutocountPullEntity, CompareSourceEntry[]> = {
  products: NO_SOURCES,
  stock_balances: NO_SOURCES,
  delivery_orders: DO_SOURCES,
  goods_receive_notes: GRN_SOURCES,
};

function noun(entity: AutocountPullEntity): string {
  if (entity === 'delivery_orders') return 'delivery order lines and documents';
  if (entity === 'goods_receive_notes') return 'GRN lines and documents';
  return 'items';
}

function summaryHeadline(
  summary: AutocountPullCompareSummary,
  differencesCount: number,
  entity: AutocountPullEntity,
): { title: string; body: string; ok: boolean } {
  const ok = isCompareFullMatch(summary);
  if (ok) {
    return {
      title: '100% match.',
      body: `${summary.matched} of ${summary.total} ${noun(entity)} agree with ${summary.filename}.`,
      ok: true,
    };
  }
  // CT-2: `summary.different` counts ITEMS with at least one differing field; `differences`
  // holds one entry PER FIELD, so an item that differs on two fields makes the two numbers
  // diverge. Name both when they do; a bare "N differ" would be ambiguous about which count
  // it is. Dropped entirely when there are no per-field differences at all (only-in-only
  // result) - the only-in clauses below still say what changed.
  const itemsDiffer = summary.different;
  const parts: string[] = [];
  if (itemsDiffer > 0) {
    const itemWord = itemsDiffer === 1 ? 'item' : 'items';
    const verb = itemsDiffer === 1 ? 'differs' : 'differ';
    parts.push(
      itemsDiffer === differencesCount
        ? `${itemsDiffer} ${itemWord} ${verb}`
        : `${itemsDiffer} ${itemWord} ${verb} (${differencesCount} differences)`,
    );
  }
  if (summary.only_in_excel) parts.push(`${summary.only_in_excel} only in your Excel`);
  if (summary.only_in_pull) parts.push(`${summary.only_in_pull} only in AutoCount`);
  const advisory =
    isDocumentEntity(entity)
      ? ' Confirm applies the AutoCount pull as it is; the differences are for you to check.'
      : '';
  return {
    title: `${summary.matched} of ${summary.total} match.`,
    body: `${parts.join(', ')}.${advisory}`,
    ok: false,
  };
}

/** dd/MM/yyyy for a `YYYY-MM-DD` day (the same rule the review header's scope line uses). */
function formatDay(day: string | null | undefined): string {
  if (!day) return '';
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : day;
}

/** Only the columns the mapping reads travel to the server (a full macro sheet body is over
 *  12 MB); a key matches a mapped header trimmed and case-insensitive, its spelling kept. */
function projectRows(rows: unknown[], headers: Set<string> | null): Record<string, unknown>[] {
  if (!headers) return rows as Record<string, unknown>[];
  return (rows as Record<string, unknown>[]).map((row) =>
    Object.fromEntries(Object.entries(row).filter(([key]) => headers.has(key.trim().toLowerCase()))),
  );
}

type SourceResults = Partial<Record<AutocountPullCompareSource, AutocountComparePullResult>>;

/**
 * Drops the same file the checker would have uploaded by hand and lines it up against the
 * pull server-side. Advisory only (P11) - the result is shown, never used to gate Confirm
 * (a delivery-orders pull may be held by the owner's Q4 switch, which the header's Confirm
 * reads off the pull itself). Delivery orders take the two macro files, one dropzone each,
 * cut to the pulled DocDate window, one headline and one grid grouped by a Source column.
 */
export function PullCompareTab({ jobId, entity, window }: PullCompareTabProps) {
  // Delivery orders and goods receive notes: two files, one dropzone each.
  const isDocument = isDocumentEntity(entity);
  const sources = SOURCES_BY_ENTITY[entity];
  const [files, setFiles] = useState<Partial<Record<AutocountPullCompareSource | 'single', File[]>>>({});
  const [single, setSingle] = useState<AutocountComparePullResult | null>(null);
  const [results, setResults] = useState<SourceResults>({});
  const compareMutation = useComparePull(jobId);
  const mappings = useCompareMappings(isDocument);
  const [mappingOpen, setMappingOpen] = useState(false);
  const hintFor = (kind: CompareMappingKind): string => {
    const headers = mappings.data?.items.find((m) => m.kind === kind)?.columns.map((c) => c.excel_header);
    return headers?.length
      ? `Columns read: ${headers.join(', ')}. Drop the file here, or click to browse.`
      : 'Drop the file here, or click to browse.';
  };
  const mappedHeaders = (kind: CompareMappingKind): Set<string> | null => {
    const items = mappings.data?.items.find((m) => m.kind === kind)?.columns;
    return items ? new Set(items.map((c) => c.excel_header.trim().toLowerCase())) : null;
  };
  const sheetFor = (kind: CompareMappingKind): string =>
    mappings.data?.items.find((m) => m.kind === kind)?.sheet_name ?? 'Master';
  const accept = entity === 'products' ? '.xlsx,.xls' : '.xlsx,.xls,.xlsm';

  const handleFilesChange = async (next: File[], source?: AutocountPullCompareSource) => {
    setFiles((prev) => ({ ...prev, [source ?? 'single']: next }));
    const clearResult = () => {
      if (source) setResults((prev) => ({ ...prev, [source]: undefined }));
      else setSingle(null);
    };
    const file = next[0];
    if (!file) {
      clearResult();
      return;
    }
    if (isDocument && mappings.isLoading) {
      clearResult();
      toast.error('The mapping is still loading. Try again in a moment.');
      return;
    }
    try {
      const entry = sources.find((d) => d.source === source);
      const rows = entry
        ? await parseExcelFile(file, { sheetName: sheetFor(entry.kind) })
        : await parseExcelFile(file);
      if (rows.length === 0) {
        clearResult();
        toast.error('That file has no rows.');
        return;
      }
      const postRows = entry
        ? projectRows(rows, mappedHeaders(entry.kind))
        : (rows as Record<string, unknown>[]);
      compareMutation.mutate(
        { filename: file.name, rows: postRows, source },
        {
          onSuccess: (data) => {
            if (source) setResults((prev) => ({ ...prev, [source]: data }));
            else setSingle(data);
          },
        },
      );
    } catch (error) {
      clearResult();
      toast.error(error instanceof Error ? error.message : 'Could not read that file.');
    }
  };

  const columns = useMemo<ColumnDef<CompareRow>[]>(() => {
    const base: ColumnDef<CompareRow>[] = [];
    if (isDocument) {
      base.push({
        accessorKey: 'doc_no',
        header: ({ column }) => <DataGridColumnHeader title="Doc No" column={column} />,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.doc_no}>
            {row.original.doc_no || '-'}
          </span>
        ),
        size: 140,
      });
    }
    base.push({
      accessorKey: 'item_code',
      header: ({ column }) => <DataGridColumnHeader title="Item Code" column={column} />,
      cell: ({ row }) => (
        <span className="truncate" title={row.original.item_code}>
          {row.original.item_code || '-'}
        </span>
      ),
      size: 140,
    });
    if (entity === 'stock_balances' || isDocument) {
      base.push({
        accessorKey: 'location',
        header: ({ column }) => <DataGridColumnHeader title="Location" column={column} />,
        cell: ({ row }) => <span className="truncate">{row.original.location || '-'}</span>,
        size: 120,
      });
    }
    base.push(
      {
        accessorKey: 'field',
        header: ({ column }) => <DataGridColumnHeader title="Difference" column={column} />,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.field}>
            {row.original.field}
          </span>
        ),
        size: 180,
      },
      {
        accessorKey: 'excel',
        header: ({ column }) => <DataGridColumnHeader title="Your Excel" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.excel}>
            {row.original.excel}
          </span>
        ),
        size: 220,
      },
      {
        accessorKey: 'pull',
        header: ({ column }) => <DataGridColumnHeader title="AutoCount pull" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate" title={row.original.pull}>
            {row.original.pull}
          </span>
        ),
        size: 220,
      },
    );
    if (isDocument) {
      base.push({
        accessorKey: 'source',
        header: ({ column }) => <DataGridColumnHeader title="Source" column={column} />,
        cell: ({ row }) => <span>{row.original.source || '-'}</span>,
        size: 100,
      });
    }
    return base;
  }, [entity, isDocument]);

  // Differences + only_in_excel + only_in_pull, formatted and labelled - the SAME array the
  // grid's recordCount and the download both use (CT-4). Delivery orders: the lines file's
  // rows first, then the headers file's, each labelled by its Source.
  const rows = useMemo<CompareRow[]>(() => {
    if (!isDocument) return single ? buildCompareRows(single, entity) : [];
    return sources.flatMap(({ source }) => {
      const result = results[source];
      return result ? buildCompareRows(result, entity, source) : [];
    });
  }, [single, results, entity, isDocument, sources]);

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row, index) =>
      `${row.source ?? ''}-${row.doc_no ?? ''}-${row.item_code}-${row.location ?? ''}-${row.field}-${index}`,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  const handleDownloadDifferences = async () => {
    if (rows.length === 0) return;
    const cols: ColumnOption[] = [
      ...(isDocument ? [{ key: 'doc_no', label: 'Doc No', selected: true } satisfies ColumnOption] : []),
      { key: 'item_code', label: 'Item Code', selected: true },
      ...(entity === 'stock_balances' || isDocument
        ? [{ key: 'location', label: 'Location', selected: true } satisfies ColumnOption]
        : []),
      { key: 'field', label: 'Difference', selected: true },
      { key: 'excel', label: 'Your Excel', selected: true },
      { key: 'pull', label: 'AutoCount pull', selected: true },
      ...(isDocument ? [{ key: 'source', label: 'Source', selected: true } satisfies ColumnOption] : []),
    ];
    // No UUID in the filename the user sees (cursor rule) - the entity, not the job id.
    await generateExcelFile(rows, cols, `autocount-${entity}-differences.xlsx`);
  };

  // Delivery orders: one headline over the files on screen - each result's own
  // `source_summary` added up (the server's combined `summary` still counts a file that was
  // since removed or failed to parse). A result without one falls back to its `summary`.
  const onScreen = isDocument
    ? sources.map(({ source }) => results[source]).filter(
        (r): r is AutocountComparePullResult => Boolean(r),
      )
    : [];
  const latest: { summary: AutocountPullCompareSummary } | null = isDocument
    ? onScreen.length
      ? {
          summary: onScreen
            .map((r) => r.source_summary ?? r.summary)
            .reduce((acc, cur) => ({
              ...acc,
              total: acc.total + cur.total,
              matched: acc.matched + cur.matched,
              different: acc.different + cur.different,
              only_in_excel: acc.only_in_excel + cur.only_in_excel,
              only_in_pull: acc.only_in_pull + cur.only_in_pull,
              filename: `${acc.filename} and ${cur.filename}`,
            })),
        }
      : null
    : single;
  const differencesCount = isDocument
    ? sources.reduce((n, { source }) => n + (results[source]?.differences.length ?? 0), 0)
    : single?.differences.length ?? 0;
  const headline = latest ? summaryHeadline(latest.summary, differencesCount, entity) : null;
  const windowLine =
    isDocument && (window?.fromDay || window?.toDay)
      ? `Compared inside the pulled window only, ${formatDay(window?.fromDay) || 'start'} to ${formatDay(window?.toDay) || 'today'}, by document number and line. Rows outside the window are ignored.`
      : isDocument
        ? 'Compared by document number and line.'
        : null;

  const renderDropzone = (
    id: string,
    label: string,
    title: string,
    hint: string,
    current: File[],
    source?: AutocountPullCompareSource,
  ) => (
    <FileDropzone
      id={id}
      accept={accept}
      files={current}
      onFilesChange={(next) => void handleFilesChange(next, source)}
      onReject={() => toast.error(`Invalid file type. Please use: ${accept}`)}
      title={title}
      hint={hint}
      disabled={compareMutation.isPending}
      aria-label={label}
    />
  );

  return (
    <div className="space-y-4">
      {isDocument && (
        <div className="flex justify-end">
          <Button variant="outline" size="sm" onClick={() => setMappingOpen(true)}>
            <Settings2 className="size-4" />
            Mapping
          </Button>
        </div>
      )}
      {isDocument ? (
        <div className="grid gap-4 sm:grid-cols-2">
          {sources.map((entry) => {
            const result = results[entry.source];
            return (
              <div key={entry.source} className="min-w-0 space-y-2">
                {renderDropzone(
                  `autocount-compare-${jobId}-${entry.source}`,
                  entry.ariaLabel,
                  `${entry.title}, sheet ${sheetFor(entry.kind)}`,
                  hintFor(entry.kind),
                  files[entry.source] ?? [],
                  entry.source,
                )}
                {result && (
                  <p className="text-xs text-muted-foreground">
                    {result.rows_in_window?.toLocaleString() ?? result.summary.total} {entry.unit} in the window
                    {result.ignored_outside_window
                      ? `, ${result.ignored_outside_window.toLocaleString()} outside it ignored`
                      : ''}
                    .
                  </p>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2">
          {renderDropzone(
            `autocount-compare-${jobId}`,
            'Excel file to compare',
            'Drop the Excel file here, or click to browse',
            `Accepted formats: ${accept}`,
            files.single ?? [],
          )}
          <div className="space-y-2">
            {compareMutation.isPending && <p className="text-sm text-muted-foreground">Comparing…</p>}
            {headline && (
              <Alert
                className={
                  headline.ok
                    ? 'border-green-200 bg-green-50 text-green-900'
                    : 'border-amber-200 bg-amber-50 text-amber-900'
                }
              >
                <AlertTitle>{headline.title}</AlertTitle>
                <AlertDescription>{headline.body}</AlertDescription>
              </Alert>
            )}
          </div>
        </div>
      )}

      {isDocument && (
        <>
          {windowLine && <p className="text-xs text-muted-foreground">{windowLine}</p>}
          {compareMutation.isPending && <p className="text-sm text-muted-foreground">Comparing…</p>}
          {headline && (
            <Alert
              className={
                headline.ok
                  ? 'border-green-200 bg-green-50 text-green-900'
                  : 'border-amber-200 bg-amber-50 text-amber-900'
              }
            >
              <AlertTitle>{headline.title}</AlertTitle>
              <AlertDescription>{headline.body}</AlertDescription>
            </Alert>
          )}
        </>
      )}

      {rows.length > 0 && (
        <DataGrid
          table={table}
          recordCount={rows.length}
          isLoading={false}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          listingKey={COMPARE_LISTING_KEY[entity]}
        >
          <Card>
            <div className="flex items-center justify-end p-3">
              <Button variant="outline" size="sm" onClick={handleDownloadDifferences}>
                <Download className="size-4" />
                Download differences
              </Button>
            </div>
            <CardTable>
              <DataGridTable />
            </CardTable>
          </Card>
        </DataGrid>
      )}
      {isDocument && mappingOpen && (
        <CompareMappingDialog
          open={mappingOpen}
          onOpenChange={setMappingOpen}
          entity={entity === 'goods_receive_notes' ? 'goods_receive_notes' : 'delivery_orders'}
        />
      )}
    </div>
  );
}

export default PullCompareTab;
