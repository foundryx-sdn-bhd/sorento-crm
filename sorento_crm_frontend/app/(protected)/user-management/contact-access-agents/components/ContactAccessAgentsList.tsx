'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
} from '@tanstack/react-table';
import { MessageCircle, MessageCircleOff } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Skeleton } from '@/components/ui/skeleton';
import ContactOutboundCell from '@/components/contacts/ContactOutboundCell';
import ContactOutboundDisableDialog from '@/components/contacts/ContactOutboundDisableDialog';
import ContactOutboundSummary from '@/components/contacts/ContactOutboundSummary';
import { useRespondContactOutboundMutations } from '@/hooks/useRespondContactOutbound';
import { useContactAccessAgents } from '../hooks/useContactAccessAgents';
import type { ContactAccessAgent } from '../types/contactAccessAgent.types';
import { formatDate } from '@/lib/helpers';

/** One contact, however many grant rows it owns on this page. */
interface OutboundContact {
  contactId: string;
  label: string;
  enabled: boolean;
}

/**
 * The distinct CONTACTS behind a set of grant rows.
 *
 * A row here is a contact x agent grant, so the same person appears once per
 * agent, while the outbound switch is per contact. Every count and every bulk
 * payload is built from this, never from the row count - otherwise a
 * confirmation would claim to silence three people when it silences one.
 * Rows with no linked `respond_contacts` row are left out: there is nothing to
 * flip.
 */
function distinctContacts(rows: ContactAccessAgent[]): OutboundContact[] {
  const byContact = new Map<string, OutboundContact>();
  for (const row of rows) {
    const contactId = row.respond_contact_id;
    if (!contactId || row.outbound_enabled === null || row.outbound_enabled === undefined) {
      continue;
    }
    if (byContact.has(contactId)) continue;
    byContact.set(contactId, {
      contactId,
      label: row.respond_contact_name || row.respond_contact_phone,
      enabled: row.outbound_enabled,
    });
  }
  return Array.from(byContact.values());
}

export default function ContactAccessAgentsList() {
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDisableOpen, setBulkDisableOpen] = useState(false);

  // A search brings the reader back to page 0 to see the matches.
  const searchMounted = useRef(false);
  useEffect(() => {
    if (!searchMounted.current) {
      searchMounted.current = true;
      return;
    }
    setPagination((p) => ({ ...p, pageIndex: 0 }));
  }, [searchQuery]);

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useContactAccessAgents({
    pageIndex: pagination.pageIndex,
    pageSize: pagination.pageSize,
    sorting,
    searchQuery,
  });

  const rows = useMemo(() => data?.data ?? [], [data]);

  const { setOne: setOutboundOne, setBulk: setOutboundBulk } =
    useRespondContactOutboundMutations();
  // Only the BULK write blocks the switches. The per-row one is optimistic
  // (S7-01), so it has already moved the switch and can be flipped straight back.
  const outboundBusy = setOutboundBulk.isPending;

  const pageContacts = useMemo(() => distinctContacts(rows), [rows]);
  const outboundCounts = useMemo(
    () => ({
      reachable: pageContacts.filter((c) => c.enabled).length,
      silenced: pageContacts.filter((c) => !c.enabled).length,
    }),
    [pageContacts],
  );

  const selectedContacts = useMemo(
    () => distinctContacts(rows.filter((row) => rowSelection[row.id])),
    [rows, rowSelection],
  );
  const selectedContactIds = useMemo(
    () => selectedContacts.map((c) => c.contactId),
    [selectedContacts],
  );
  const contactCountLabel = `${selectedContacts.length} contact${
    selectedContacts.length === 1 ? '' : 's'
  }`;

  const clearSelection = () => setRowSelection({});
  const runOutboundBulk = (enabled: boolean) =>
    setOutboundBulk.mutate(
      { enabled, contactIds: selectedContactIds },
      { onSuccess: clearSelection },
    );

  const columns = useMemo<ColumnDef<ContactAccessAgent>[]>(
    () => [
      buildSelectColumn<ContactAccessAgent>(),
      {
        accessorKey: 'respond_contact_phone',
        header: ({ column }) => <DataGridColumnHeader title="Respond Contact Phone" column={column} />,
        size: 200,
        meta: { headerTitle: 'Respond Contact Phone', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'respond_contact_name',
        header: ({ column }) => <DataGridColumnHeader title="Respond Contact Name" column={column} />,
        size: 220,
        meta: { headerTitle: 'Respond Contact Name', skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        // The switch belongs to the CONTACT, so it repeats on each of that
        // contact's grant rows and flipping any one of them moves all of them.
        accessorKey: 'outbound_enabled',
        header: ({ column }) => <DataGridColumnHeader title="Outbound" column={column} />,
        enableSorting: false,
        cell: ({ row }) => (
          <ContactOutboundCell
            enabled={row.original.outbound_enabled}
            contactLabel={row.original.respond_contact_name || row.original.respond_contact_phone}
            disabled={outboundBusy}
            onChange={(enabled) => {
              const contactId = row.original.respond_contact_id;
              if (!contactId) return;
              setOutboundOne.mutate({ contactId, enabled });
            }}
          />
        ),
        size: 210,
        meta: { headerTitle: 'Outbound', skeleton: <Skeleton className="h-5 w-28" /> },
      },
      {
        accessorKey: 'agent_code',
        header: ({ column }) => <DataGridColumnHeader title="Agent Code" column={column} />,
        size: 150,
        meta: { headerTitle: 'Agent Code', skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'agent_name',
        header: ({ column }) => <DataGridColumnHeader title="Agent Name" column={column} />,
        size: 250,
        meta: { headerTitle: 'Agent Name', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'is_allowed',
        header: ({ column }) => <DataGridColumnHeader title="Allowed" column={column} />,
        cell: ({ row }) => (
          <Badge variant={row.original.is_allowed ? 'success' : 'secondary'}>
            {row.original.is_allowed ? 'Yes' : 'No'}
          </Badge>
        ),
        size: 100,
        meta: { headerTitle: 'Allowed' },
      },
      {
        accessorKey: 'valid_from',
        header: ({ column }) => <DataGridColumnHeader title="Valid From" column={column} />,
        cell: ({ row }) => row.original.valid_from ? formatDate(new Date(row.original.valid_from)) : '-',
        size: 150,
        meta: { headerTitle: 'Valid From' },
      },
      {
        accessorKey: 'valid_to',
        header: ({ column }) => <DataGridColumnHeader title="Valid To" column={column} />,
        cell: ({ row }) => row.original.valid_to ? formatDate(new Date(row.original.valid_to)) : '-',
        size: 150,
        meta: { headerTitle: 'Valid To' },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Created At" column={column} />,
        cell: ({ row }) => formatDate(new Date(row.original.created_at)),
        size: 150,
        meta: { headerTitle: 'Created At' },
      },
    ],
    [outboundBusy, setOutboundOne],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableRowSelection: true,
  });

  return (
    <DataGrid table={table} recordCount={data?.pagination.total || 0} isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      tableLayout={{ columnsVisibility: true }}
    >
      <Card>
        <CardHeader className="block">
          <ContactOutboundSummary
            reachable={outboundCounts.reachable}
            silenced={outboundCounts.silenced}
          />
        </CardHeader>
        <CardHeader className="block">
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                placeholder="Search contact access agents..."
                className="w-64"
              />
            }
            exportConfig={{ filename: 'contact_access_agents_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            // Counted in contacts, not rows: several selected rows can be the
            // same person, and the write is per contact.
            bulkActions={[
              {
                key: 'outbound-enable',
                label: `Enable messaging (${contactCountLabel})`,
                icon: MessageCircle,
                disabled: outboundBusy || selectedContacts.length === 0,
                onClick: () => runOutboundBulk(true),
              },
              {
                key: 'outbound-disable',
                label: `Disable messaging (${contactCountLabel})`,
                icon: MessageCircleOff,
                destructive: true,
                disabled: outboundBusy || selectedContacts.length === 0,
                onClick: () => setBulkDisableOpen(true),
              },
            ]}
          />
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>

      <ContactOutboundDisableDialog
        open={bulkDisableOpen}
        onOpenChange={setBulkDisableOpen}
        contactCount={selectedContacts.length}
        busy={outboundBusy}
        onConfirm={() => {
          setBulkDisableOpen(false);
          runOutboundBulk(false);
        }}
      />
    </DataGrid>
  );
}
