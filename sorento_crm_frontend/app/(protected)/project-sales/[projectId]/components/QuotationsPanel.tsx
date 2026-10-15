'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import type { ColumnDef, RowSelectionState, Table } from '@tanstack/react-table';
import { Plus, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { Button } from '@/components/ui/button';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { useDeferredBulkAction } from '@/hooks/useDeferredBulkAction';
import { formatDateInMalaysia } from '@/lib/helpers';
import { quotationStanding } from '../../_shared/lib/quotationDecision';
import {
  quotationDocumentsKey,
  useQuotationDocuments,
} from '../../_shared/hooks/useQuotationDocuments';
import { useQuotationDocumentActions } from '../quotation-documents/actions';
import type { QuotationDocument } from '../../_shared/services/quotationDocumentService';
import type { Project } from '../../_shared/types/project.types';
import { formatMyrExact, sumMoney } from '../../_shared/lib/money';

/**
 * The row "..." cell: the record's own action set, in the same order as its gear.
 *
 * A component of its own because the set is a hook (it owns the sign dialog and the deferred
 * delete). The wrapper stops clicks, including those bubbling out of the dialogs, so nothing
 * inside it opens the row.
 */
function QuotationRowActions({
  projectId,
  document,
  canEdit,
}: {
  projectId: string;
  document: QuotationDocument;
  canEdit: boolean;
}) {
  const { actions, dialogs } = useQuotationDocumentActions({ projectId, document, canEdit });
  return (
    <div onClick={(event) => event.stopPropagation()}>
      <RowActionsMenu actions={actions} ariaLabel="quotation" />
      {dialogs}
    </div>
  );
}

/**
 * Every quotation DOCUMENT on this project: one row per letterhead the customer receives.
 *
 * The list used to hold one row per scope revision, which is the wrong grain now that a
 * document carries several scopes and is issued as a whole: a customer holds "SRT/Q/2026/0141
 * (R2)", not "House Units v2". So the row is the document, the scopes are a count, and the
 * revision history lives on the document's own page.
 *
 * It is the standard list: search, Columns and Add in the app toolbar, a tickbox column with
 * Delete in the bulk strip, and the record's own action set in the row "..." menu.
 */
export function QuotationsPanel({ project }: { project: Project }) {
  const router = useRouter();
  const documents = useQuotationDocuments(project.id);
  const [search, setSearch] = React.useState('');
  // Client-side on purpose: a project's quotations are all in memory and unpaged.
  const rows = React.useMemo(() => {
    const needle = search.trim().toLowerCase();
    const all = documents.data ?? [];
    if (!needle) return all;
    return all.filter((row) =>
      [row.our_ref, row.document_no, row.subject_title, row.recipient_name_snapshot]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(needle),
    );
  }, [documents.data, search]);
  const [rowSelection, setRowSelection] = React.useState<RowSelectionState>({});

  // One countdown over the ticked drafts. An issued quotation is refused by the server, so it is
  // left out and counted in a closing sentence rather than swallowed.
  const bulkDelete = useDeferredBulkAction({
    actionKey: 'quotation_document.delete',
    entityType: 'quotation_document',
    describe: (count) => `${count} quotation${count === 1 ? '' : 's'}`,
    invalidateKeys: [quotationDocumentsKey(project.id)],
    onStarted: () => setRowSelection({}),
  });

  const deleteTicked = () => {
    const ticked = rows.filter((row) => rowSelection[row.id]);
    const deletable = ticked.filter((row) => !row.is_issued);
    const skipped = ticked.length - deletable.length;
    if (skipped > 0) {
      toast.warning(`${skipped} issued quotation${skipped === 1 ? '' : 's'} skipped`);
    }
    bulkDelete.run(deletable.map((row) => ({ id: row.id })));
  };

  const columns = React.useMemo<ColumnDef<QuotationDocument>[]>(
    () => [
      buildSelectColumn<QuotationDocument>({
        rowLabel: (row) => `Select ${row.original.our_ref ?? row.original.document_no}`,
      }),
      {
        id: 'our_ref',
        accessorFn: (row) => row.our_ref ?? row.document_no,
        header: ({ column }) => <DataGridColumnHeader title="Reference" column={column} />,
        cell: ({ row }) => {
          const reference = row.original.our_ref ?? row.original.document_no;
          return (
            <span className="truncate text-sm font-medium" title={reference}>
              {reference}
            </span>
          );
        },
        size: 190,
        meta: { headerTitle: 'Reference' },
      },
      {
        id: 'subject_title',
        accessorFn: (row) => row.subject_title ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Subject" column={column} />,
        cell: ({ row }) =>
          row.original.subject_title ? (
            <span className="truncate text-sm" title={row.original.subject_title}>
              {row.original.subject_title}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 260,
        meta: { headerTitle: 'Subject' },
      },
      {
        id: 'recipient_name_snapshot',
        accessorFn: (row) => row.recipient_name_snapshot ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Recipient" column={column} />,
        cell: ({ row }) =>
          row.original.recipient_name_snapshot ? (
            <span className="truncate text-sm" title={row.original.recipient_name_snapshot}>
              {row.original.recipient_name_snapshot}
            </span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
        size: 200,
        meta: { headerTitle: 'Recipient' },
      },
      {
        id: 'scope_count',
        accessorFn: (row) => row.scopes.length,
        header: ({ column }) => <DataGridColumnHeader title="Scopes" column={column} />,
        cell: ({ row }) => <span className="text-sm">{row.original.scopes.length}</span>,
        size: 100,
        meta: { headerTitle: 'Scopes' },
      },
      {
        // Draft / Issued, and - once the customer has answered - what they answered.
        //
        // The client asked "when i request changes, how can i see it from the system?", and a
        // salesperson scanning this tab is the second place that question gets asked. It rides
        // this column rather than a new one because the customer's answer IS where the quotation
        // stands: a second, mostly-empty column would push the money off a 375px screen.
        id: 'status',
        accessorFn: (row) => quotationStanding(row).label,
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => {
          const standing = quotationStanding(row.original);
          return (
            <span className={`${STATUS_PILL_BASE} ${statusPillClass(standing.label)}`}>
              {standing.label}
            </span>
          );
        },
        size: 150,
        meta: { headerTitle: 'Status' },
      },
      {
        id: 'grand_total',
        accessorFn: (row) => row.grand_total,
        header: ({ column }) => <DataGridColumnHeader title="Value" column={column} />,
        cell: ({ row }) => (
          <span className="block truncate text-end text-sm font-medium tabular-nums">
            {formatMyrExact(row.original.grand_total)}
          </span>
        ),
        // Summed off the STRINGS, to the cent: a float sum of a page of quotations drifts, and
        // a footer that disagrees with the documents above it by a cent is worse than no footer.
        footer: ({ table }: { table: Table<QuotationDocument> }) => {
          const total = sumMoney(
            table.getCoreRowModel().rows.map((row) => row.original.grand_total),
          );
          return (
            <span className="block text-end tabular-nums">
              {total === null ? '-' : formatMyrExact(total)}
            </span>
          );
        },
        size: 160,
        meta: { headerTitle: 'Value' },
      },
      {
        id: 'doc_date',
        accessorFn: (row) => row.doc_date ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Dated" column={column} />,
        cell: ({ row }) => {
          const dated = row.original.doc_date
            ? formatDateInMalaysia(row.original.doc_date)
            : '';
          return dated ? (
            <span className="truncate text-sm">{dated}</span>
          ) : (
            <span className="text-muted-foreground">-</span>
          );
        },
        size: 130,
        meta: { headerTitle: 'Dated' },
      },
      {
        id: 'actions',
        header: '',
        size: 56,
        enableSorting: false,
        enableHiding: false,
        cell: ({ row }) => (
          <QuotationRowActions
            projectId={project.id}
            document={row.original}
            canEdit={project.can_edit}
          />
        ),
      },
    ],
    [project.id, project.can_edit],
  );

  const ticked = rows.filter((row) => rowSelection[row.id]);
  const deletableTicked = ticked.filter((row) => !row.is_issued);

  return (
    <PanelDataGrid
      title="Quotations"
      toolbar={({ table }) => (
        <DataGridListToolbar
          table={table}
          exportConfig={{ filename: 'quotations' }}
          searchSlot={
            <ListSearchInput
              type="search"
              inputClassName="[&::-webkit-search-cancel-button]:appearance-none"
              value={search}
              onChange={setSearch}
              placeholder="Search quotations"
              aria-label="Search quotations"
              className="w-full sm:w-64"
            />
          }
          bulkActions={
            project.can_edit
              ? [
                  {
                    key: 'delete',
                    label: 'Delete',
                    icon: Trash2,
                    destructive: true,
                    disabled: deletableTicked.length === 0 || bulkDelete.isStarting,
                    disabledReason:
                      ticked.length > 0 && deletableTicked.length === 0
                        ? 'Sent to the customer, so it cannot be deleted'
                        : undefined,
                    onClick: deleteTicked,
                  },
                ]
              : []
          }
          primaryAction={
            project.can_edit ? (
              <Button
                type="button"
                size="sm"
                // A navigation, never a write (#1341). The owner: "I should be able to add
                // product straight away and save when I am satisfied". The form page holds the
                // letterhead and the scopes with their lines, and nothing exists until its Save.
                onClick={() =>
                  router.push(`/project-sales/${project.id}/quotation-documents/new`)
                }
              >
                <Plus className="size-4" aria-hidden />
                Add a quotation
              </Button>
            ) : undefined
          }
        />
      )}
      rowSelection={rowSelection}
      onRowSelectionChange={setRowSelection}
      columns={columns}
      rows={rows}
      getRowId={(row) => row.id}
      // Suffixed on purpose: the column SET changed when the list went from one row per scope
      // revision to one row per document, and a saved per-user order for the old set appends
      // the new columns to the end, which reads as scrambled.
      listingKey="projects.projects.view::project-quotation-documents"
      isLoading={documents.isLoading}
      error={documents.isError ? documents.error : undefined}
      emptyTitle={
        search.trim() && (documents.data ?? []).length > 0
          ? 'Nothing matches that search'
          : 'Nothing quoted yet'
      }
      pageResetKey={search}
      pageSize={15}
      // The row is the way in, and it goes to the document's own page: a list answers "what do
      // we have", a document answers "what is in this one", and stacking them cramps both.
      onRowClick={(row) =>
        router.push(`/project-sales/${project.id}/quotation-documents/${row.id}`)
      }
    />
  );
}

export function formatMyr(value: string): string {
  // Delegates to the ONE money renderer. This used to be its own `Number()` +
  // `toLocaleString` implementation - the float path every module docstring in
  // `_shared/lib/money` exists to forbid - and the third spelling of "RM x,xxx.xx"
  // in one directory. The name survives because six screens import it.
  return formatMyrExact(value);
}
