'use client';

import { useMemo, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  ColumnDef,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Plus, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { IGNORE_FIELD } from '@/components/common/ImportColumnMapper';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { pendingEntityKey, usePendingEntityKeys } from '@/lib/pending-entity-store';
import {
  importFieldAliasListQueryKey,
  useImportFieldAliases,
} from '../hooks/useImportFieldAliases';
import { IMPORT_FIELD_ALIAS_DOC_TYPES } from '../types/importFieldAlias.types';
import type { ImportFieldAliasDocType, ImportFieldAliasGroup } from '../types/importFieldAlias.types';
import { ImportFieldAliasFormDialog } from './ImportFieldAliasFormDialog';

/** One frozen empty array while the query is in flight: TanStack reads `data` by identity
 *  and a fresh `[]` per render is a render loop with a clean console
 *  (`data-grid.stable-data.inventory.test.ts`, and this page's own measured 100% CPU on a
 *  doc-type switch). */
const NO_GROUPS: ImportFieldAliasGroup[] = [];

/**
 * Import column mappings (S5, AC-E3): per document type, every system field and the
 * headers on file that resolve to it. A chip's × parks a deferred `import_field_alias
 * .forget` on the SERVER (`useDeferredRowAction`), which commits when the window lapses
 * even if this tab is closed; a mapping is a small, frequently-corrected fact, so the
 * countdown lives in a toast the same way a list row's delete does, not a confirmation
 * dialog.
 */
/** The Supplier codes tab links here with `?doc_type=` (AC-F5, D6) - so a person picking
 *  "Stock list words" from that tab lands on the word list, not the page's own default. An
 *  unrecognised or absent value falls back to the default rather than an empty grid. */
function initialDocType(param: string | null): ImportFieldAliasDocType {
  const known = IMPORT_FIELD_ALIAS_DOC_TYPES.some((d) => d.value === param);
  return known ? (param as ImportFieldAliasDocType) : 'proforma_invoice';
}

export default function ImportFieldAliasesList() {
  const searchParams = useSearchParams();
  const [docType, setDocType] = useState<ImportFieldAliasDocType>(() =>
    initialDocType(searchParams.get('doc_type')),
  );
  const { data: groups, isLoading, error, refetch } = useImportFieldAliases(docType);
  const [addOpen, setAddOpen] = useState(false);
  const removal = useDeferredRowAction({
    actionKey: 'import_field_alias.forget',
    entityType: 'import_field_alias',
    verb: 'Removing',
    successMessage: 'Mapping removed',
    invalidateKeys: [importFieldAliasListQueryKey(docType)],
  });
  // Which chips are counting down - read from the tab's pending store rather than a local
  // set, so a cancelled window puts the chip back with nothing here to undo.
  const pendingKeys = usePendingEntityKeys();

  const columns = useMemo<ColumnDef<ImportFieldAliasGroup>[]>(
    () => [
      {
        accessorKey: 'label',
        header: ({ column }) => <DataGridColumnHeader title="System field" column={column} />,
        cell: ({ row }) =>
          // The mapper's own reserved field (F4, G2/AC-M7) surfaces here as a synthetic
          // group so an ignored column can still be found and deleted - "Ignored" reads as
          // a decision, where the field's own title-cased name ("Ignore") would not.
          row.original.field === IGNORE_FIELD ? (
            <span className="font-medium text-muted-foreground">Ignored</span>
          ) : (
            <span className="font-medium">{row.original.label}</span>
          ),
        size: 200,
      },
      {
        id: 'aliases',
        header: () => 'Headers on file',
        cell: ({ row }) => {
          const visible = row.original.aliases.filter(
            (a) => !pendingKeys.has(pendingEntityKey('import_field_alias', a.id)),
          );
          if (!visible.length) {
            return <span className="text-xs text-muted-foreground">No header maps here yet.</span>;
          }
          return (
            <div className="flex flex-wrap gap-1.5 py-1">
              {visible.map((a) => (
                <span key={a.id} className="inline-flex items-center gap-1">
                  <Badge variant="secondary" appearance="light" size="sm" className="gap-1">
                    {a.alias}
                    {a.locale ? <span className="text-muted-foreground">({a.locale})</span> : null}
                    <button
                      type="button"
                      aria-label={`Remove ${a.alias}`}
                      className="ms-0.5 rounded-full hover:bg-muted"
                      onClick={() =>
                        removal.run({
                          id: a.id,
                          subject: `"${a.alias}" from ${row.original.label}`,
                        })
                      }
                    >
                      <X className="size-3" />
                    </button>
                  </Badge>
                  {/* Per-supplier word row (S4, D6) - the name, never the id (no UUID in the
                      UI), a second badge beside the alias rather than text folded into it
                      (review round 1, item 9); a shared row (supplier_id NULL) carries
                      none. */}
                  {a.supplier_name ? (
                    <Badge variant="outline" appearance="light" size="sm">
                      {a.supplier_name}
                    </Badge>
                  ) : null}
                </span>
              ))}
            </div>
          );
        },
        size: 480,
        enableSorting: false,
      },
    ],
    [pendingKeys, removal],
  );

  const table = useReactTable({
    columns,
    data: groups ?? NO_GROUPS,
    getRowId: (row) => row.field,
    getCoreRowModel: getCoreRowModel(),
    // `tableLayout.columnsResizable` below draws the handles; without these the drag does
    // nothing, which reads as a broken control rather than a column that cannot move.
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  const addButton = (
    <Button onClick={() => setAddOpen(true)}>
      <Plus className="size-4" />
      Add mapping
    </Button>
  );

  return (
    <>
      <div className="mb-4 max-w-xs">
        <Label htmlFor="import-field-alias-doc-type" className="mb-1 block text-xs">
          Document type
        </Label>
        <SearchableSelect
          id="import-field-alias-doc-type"
          value={docType}
          onChange={(v: string) => setDocType(v as ImportFieldAliasDocType)}
          options={IMPORT_FIELD_ALIAS_DOC_TYPES}
        />
      </div>
      <DataGrid
        table={table}
        recordCount={groups?.length ?? 0}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar table={table} primaryAction={addButton} />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </Card>
      </DataGrid>
      <ImportFieldAliasFormDialog open={addOpen} onOpenChange={setAddOpen} docType={docType} />
    </>
  );
}
