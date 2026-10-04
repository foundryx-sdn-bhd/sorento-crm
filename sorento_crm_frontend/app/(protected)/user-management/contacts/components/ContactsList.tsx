'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { extractApiError } from '@/lib/api-client';
import { useHasPermission } from '@/hooks/usePermissions';

import {
  ColumnDef,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  getCoreRowModel,
} from '@tanstack/react-table';
import { Copy, Link2, MessageCircle, MessageCircleOff, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { PillOverflow, PillOverflowList } from '@/components/common/PillOverflow';
import { Skeleton } from '@/components/ui/skeleton';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import type { RespondContact } from '../types/contact.types';
import { formatDate } from '@/lib/helpers';
import { toast } from '@/lib/toast';
import BulkLinkCustomersDialog from './BulkLinkCustomersDialog';
import ContactCreateDialog from './ContactCreateDialog';
import ContactDeleteDialog from './ContactDeleteDialog';
import ContactBulkDeleteDialog from './ContactBulkDeleteDialog';
import BulkCopyAccessDialog from './BulkCopyAccessDialog';
import ContactAccessFilters from './ContactAccessFilters';
import PortalLinkButton from '@/components/contacts/PortalLinkButton';
import ContactOutboundCell from '@/components/contacts/ContactOutboundCell';
import ContactOutboundDisableDialog from '@/components/contacts/ContactOutboundDisableDialog';
import ContactOutboundSummary from '@/components/contacts/ContactOutboundSummary';
import { useRespondContactOutboundMutations } from '@/hooks/useRespondContactOutbound';


import { buildDetailSearch } from '@/lib/listNavQuery';
import {
  contactAccessFiltersFromUrl,
  contactsListFilters,
  contactsListQueryKey,
  fetchContactsPage,
  type ContactAccessFilters as AccessFilters,
} from '../lib/listQuery';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { contactActions } from '../actions';
import { ContactImpersonateDialog } from './ContactImpersonateDialog';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import UserAddDialog from '../../users/components/user-add-dialog';

const TIER_LABEL: Record<string, string> = { dealer: 'Dealer', office: 'Office', end_user: 'End user' };

/** A read-only on/off column (UAC A2.2): a light `Badge`, green when on. */
function yesNoColumn(
  id: string,
  title: string,
  isOn: (row: RespondContact) => boolean,
  onLabel: string,
  offLabel: string,
): ColumnDef<RespondContact> {
  return {
    id,
    accessorFn: (row) => (isOn(row) ? onLabel : offLabel),
    header: ({ column }) => <DataGridColumnHeader title={title} column={column} />,
    size: 110,
    enableSorting: false,
    cell: ({ row }) =>
      isOn(row.original) ? (
        <Badge variant="success" appearance="light">{onLabel}</Badge>
      ) : (
        <Badge variant="secondary" appearance="light">{offLabel}</Badge>
      ),
    meta: { headerTitle: title, skeleton: <Skeleton className="h-5 w-16" /> },
  };
}

export default function ContactsList() {
  const queryClient = useQueryClient();
  const canViewUsers = useHasPermission('user_management.users.view');
  const canAddUsers = useHasPermission('user_management.users.add');
  const canEditContacts = useHasPermission('user_management.contacts.edit');
  const [createUserContact, setCreateUserContact] = useState<RespondContact | null>(null);
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  // `chatbot_memory_level=own` (Settings > Chatbot > Memory's "N contacts" link,
  // review finding S13 / AC-MEM028) - the only filter this list reads from the URL
  // today, no filter UI of its own beyond the deep link that sets it.
  const [chatbotMemoryLevel, setChatbotMemoryLevel] = useState<string | null>(null);
  const [customersNone, setCustomersNone] = useState(false);
  // CONTACT-BULK-ACCESS (UAC A2.3): tier / cost / escalation / ... filters.
  const [accessFilters, setAccessFilters] = useState<AccessFilters>({});

  // Back hands the list its own query string back, and the pager keeps
  // rewriting it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl((state) => {
    setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
    setSorting(state.sorting);
    resetSearch(state.searchQuery);
    setChatbotMemoryLevel(state.filters.chatbot_memory_level ?? null);
    setCustomersNone(state.filters.customers === 'none');
    setAccessFilters(contactAccessFiltersFromUrl(state.filters));
  });

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  const accessFiltersKey = JSON.stringify(accessFilters);
  useResetPageOnFilterChange(setPagination, [
    searchQuery,
    chatbotMemoryLevel,
    customersNone,
    accessFiltersKey,
  ]);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [contactToDelete, setContactToDelete] = useState<RespondContact | null>(null);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [bulkDeleteDialogOpen, setBulkDeleteDialogOpen] = useState(false);
  const [bulkCopyDialogOpen, setBulkCopyDialogOpen] = useState(false);
  const [bulkLinkOpen, setBulkLinkOpen] = useState(false);
  const [impersonateTarget, setImpersonateTarget] = useState<RespondContact | null>(null);
  const [bulkDisableOutboundOpen, setBulkDisableOutboundOpen] = useState(false);

  // The list query, built through the shared key + fetch so the detail shell's
  // pager reads THIS cache entry instead of its own 100 newest contacts.
  const listParams = useMemo(
    () => ({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
      filters: contactsListFilters({ chatbotMemoryLevel, customersNone, access: accessFilters }),
    }),
    [pagination, sorting, searchQuery, chatbotMemoryLevel, customersNone, accessFilters],
  );

  // UAC A2.3: a filter change is written to the URL (same query string a row click
  // carries to the detail page), so a reload or a shared link keeps the filtered list.
  // Not on mount: the URL is the input then, `useListStateFromUrl` has just read it.
  const listFiltersKey = JSON.stringify(listParams.filters);
  const filtersWritten = useRef<string | null>(null);
  useEffect(() => {
    if (filtersWritten.current === null) {
      filtersWritten.current = listFiltersKey;
      return;
    }
    if (filtersWritten.current === listFiltersKey) return;
    filtersWritten.current = listFiltersKey;
    const search = buildDetailSearch(listParams, listParams.filters);
    window.history.replaceState(
      window.history.state,
      '',
      `${window.location.pathname}${search ? `?${search}` : ''}`,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [listFiltersKey]);

  const { data, isLoading, isPlaceholderData, refetch, isFetching, error } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: contactsListQueryKey(listParams),
    queryFn: () => fetchContactsPage(listParams),
    staleTime: Infinity,
    gcTime: 1000 * 60 * 60,
    retry: 1,
  });

  const pageContacts = useMemo(() => data?.data ?? [], [data]);
  const selectedContactIds = useMemo(() => Object.keys(rowSelection), [rowSelection]);
  const selectedContacts = useMemo(
    () => pageContacts.filter((c) => rowSelection[c.id]),
    [pageContacts, rowSelection],
  );

  // One row here is one contact, so the outbound switch is 1:1 with the row and
  // the selection needs no de-duplication (unlike the contact x agent grants grid).
  // Read through a ref so the column defs (and the row cells they mount) do not rebuild
  // whenever the mutation wrapper's identity changes.
  const { setOne: setOutboundOne, setBulk: setOutboundBulk } =
    useRespondContactOutboundMutations();
  const setOutboundOneRef = useRef(setOutboundOne);
  useEffect(() => {
    setOutboundOneRef.current = setOutboundOne;
  });
  // Only the BULK write blocks the switches. The per-row one is optimistic
  // (S7-01), so it has already moved the switch and can be flipped straight back.
  const outboundBusy = setOutboundBulk.isPending;
  const outboundCounts = useMemo(() => {
    let reachable = 0;
    let silenced = 0;
    for (const contact of pageContacts) {
      if (contact.outbound_enabled === false) silenced += 1;
      else if (contact.outbound_enabled === true) reachable += 1;
    }
    return { reachable, silenced };
  }, [pageContacts]);

  function contactSyncErrorMessage(response: Response): Promise<string> {
    return extractApiError(response, `Sync failed (${response.status})`);
  }

  const syncContactMutation = useMutation({
    mutationFn: async (contactId: string) => {
      const response = await apiFetch(`/api/user-management/contacts/${contactId}/sync`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(await contactSyncErrorMessage(response));
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
      toast.success('Contact synced successfully');
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Failed to sync contact');
    },
  });

  const bulkSyncMutation = useMutation({
    mutationFn: async (ids: string[]) => {
      const response = await apiFetch('/api/user-management/contacts/bulk-sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids }),
      });
      if (!response.ok) throw new Error(await contactSyncErrorMessage(response));
      return response.json() as Promise<{ succeeded: number; failed: number; errors: { id: string; message: string }[] }>;
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
      if (result.failed > 0) {
        toast.warning(`Synced ${result.succeeded}, ${result.failed} failed`);
      } else {
        toast.success(`Synced ${result.succeeded} contact(s) from Respond.io`);
      }
    },
    onError: (error: Error) => {
      toast.error(error.message || 'Bulk sync failed');
    },
  });

  const handleSync = (contactId: string) => {
    syncContactMutation.mutate(contactId);
  };

  const handleBulkSync = () => {
    if (selectedContactIds.length === 0) return;
    bulkSyncMutation.mutate(selectedContactIds);
  };

  // The whole row opens the record, carrying the list query the pager rebuilds
  // its key from.
  const rowHref = (contact: RespondContact) => {
    const search = buildDetailSearch(listParams, listParams.filters);
    return `/user-management/contacts/${contact.id}${search ? `?${search}` : ''}`;
  };


  const columns = useMemo<ColumnDef<RespondContact>[]>(
    () => [
      buildSelectColumn<RespondContact>(),
      {
        accessorKey: 'phone_number',
        header: ({ column }) => <DataGridColumnHeader title="Phone Number" column={column} />,
        size: 200,
        meta: { headerTitle: 'Phone Number', skeleton: <Skeleton className="h-4 w-32" /> },
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="font-medium font-mono">{row.original.phone_number}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                handleSync(row.original.id);
              }}
              disabled={bulkSyncMutation.isPending || syncContactMutation.isPending}
              title="Sync from Respond.io"
            >
              <RefreshCw
                className={`size-4 ${
                  syncContactMutation.isPending && syncContactMutation.variables === row.original.id
                    ? 'animate-spin'
                    : ''
                }`}
              />
            </Button>
          </div>
        ),
      },
      {
        accessorKey: 'first_name',
        header: ({ column }) => <DataGridColumnHeader title="First name" column={column} />,
        size: 160,
        cell: ({ row }) =>
          row.original.first_name || <span className="text-muted-foreground"> - </span>,
        meta: { headerTitle: 'First name', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        accessorKey: 'last_name',
        header: ({ column }) => <DataGridColumnHeader title="Last name" column={column} />,
        size: 160,
        cell: ({ row }) =>
          row.original.last_name || <span className="text-muted-foreground"> - </span>,
        meta: { headerTitle: 'Last name', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        id: 'customers',
        accessorFn: (row) => row.customer_codes?.join(', ') ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Customers" column={column} />,
        size: 200,
        enableSorting: false,
        cell: ({ row }) => {
          const label = (row.original.customer_codes ?? []).join(', ');
          return label ? (
            <span className="block truncate" title={label}>
              {label}
            </span>
          ) : (
            <span className="text-muted-foreground"> - </span>
          );
        },
        meta: { headerTitle: 'Customers', skeleton: <Skeleton className="h-4 w-28" /> },
      },
      {
        id: 'access_types',
        accessorFn: (row) => row.access_type_codes?.join(',') ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Access types" column={column} />,
        size: 220,
        enableSorting: false,
        cell: ({ row }) => {
          const types = row.original.access_types ?? [];
          if (types.length === 0) {
            return <span className="text-muted-foreground"> - </span>;
          }
          // One row line (owner rule, 29 Sep 2026): as many types as the column fits, the
          // rest behind "+N", every type in the popover (`PillOverflow`).
          return (
            <PillOverflow
              ariaLabel={`Access types of ${row.original.name || row.original.phone_number}`}
              testId={`contact-access-types-${row.original.id}`}
              items={types.map((t) => ({ key: t.code, label: t.name }))}
              renderPopover={(items) => <PillOverflowList items={items} />}
            />
          );
        },
        meta: { headerTitle: 'Access types', skeleton: <Skeleton className="h-4 w-32" /> },
      },
      // CONTACT-BULK-ACCESS (UAC A2.2): the access a bulk copy sets, readable per row.
      {
        id: 'chatbot_tier',
        accessorFn: (row) => row.chatbot_tier ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Tier" column={column} />,
        size: 100,
        enableSorting: false,
        cell: ({ row }) =>
          row.original.chatbot_tier ? (
            <span className="block truncate" title={TIER_LABEL[row.original.chatbot_tier] ?? row.original.chatbot_tier}>
              {TIER_LABEL[row.original.chatbot_tier] ?? row.original.chatbot_tier}
            </span>
          ) : (
            <span className="text-muted-foreground"> - </span>
          ),
        meta: { headerTitle: 'Tier', skeleton: <Skeleton className="h-4 w-16" /> },
      },
      yesNoColumn('cost_visible', 'Cost', (r) => r.cost_visible === true, 'Visible', 'Hidden'),
      yesNoColumn('escalation_allowed', 'Escalation', (r) => r.escalation_allowed !== false, 'Allowed', 'Blocked'),
      yesNoColumn('packing_list_allowed', 'Packing list', (r) => r.packing_list_allowed === true, 'Allowed', 'No'),
      yesNoColumn('chatbot_stock_allowed', 'Stock checks', (r) => r.chatbot_stock_allowed !== false, 'Allowed', 'Blocked'),
      // Identity S3 (AC-59): the linked user's name, a link; blank when none.
      // Hidden entirely without `users.view` - a caller who cannot open the
      // user has nothing to do with the name either.
      ...(canViewUsers
        ? [
            {
              id: 'linked_user',
              header: 'User',
              size: 180,
              enableSorting: false,
              cell: ({ row }: { row: { original: RespondContact } }) =>
                row.original.linked_user_id ? (
                  <Link
                    href={`/user-management/users/${row.original.linked_user_id}`}
                    className="text-primary hover:underline truncate block"
                    title={row.original.linked_user_name ?? ''}
                    onClick={(e) => e.stopPropagation()}
                  >
                    {row.original.linked_user_name}
                  </Link>
                ) : (
                  <span className="text-muted-foreground"> - </span>
                ),
              meta: { headerTitle: 'User', skeleton: <Skeleton className="h-4 w-28" /> },
            } satisfies ColumnDef<RespondContact>,
          ]
        : []),
      {
        accessorKey: 'outbound_enabled',
        header: ({ column }) => <DataGridColumnHeader title="Outbound" column={column} />,
        enableSorting: false,
        cell: ({ row }) => (
          <ContactOutboundCell
            enabled={row.original.outbound_enabled}
            contactLabel={row.original.name || row.original.phone_number}
            disabled={outboundBusy}
            onChange={(enabled) =>
              setOutboundOneRef.current.mutate({ contactId: row.original.id, enabled })
            }
          />
        ),
        size: 210,
        meta: { headerTitle: 'Outbound', skeleton: <Skeleton className="h-5 w-28" /> },
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Created At" column={column} />,
        cell: ({ row }) => formatDate(new Date(row.original.created_at)),
        size: 150,
        meta: { headerTitle: 'Created At' },
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Updated At" column={column} />,
        cell: ({ row }) => formatDate(new Date(row.original.updated_at)),
        size: 150,
        meta: { headerTitle: 'Updated At' },
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        cell: ({ row }) => (
          <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
            <PortalLinkButton
              contactId={row.original.id}
              contactLabel={row.original.name ?? row.original.phone_number ?? row.original.id}
              canSendViaRespondIo={!!row.original.respond_io_id}
              variant="icon"
            />
            {/* The record's own set, in the row's "..." (D15). */}
            <RowActionsMenu
              ariaLabel="contact"
              actions={contactActions(
                row.original,
                {
                  impersonate: () => setImpersonateTarget(row.original),
                  remove: () => {
                    setContactToDelete(row.original);
                    setDeleteDialogOpen(true);
                  },
                  createUser: () => setCreateUserContact(row.original),
                },
                // users.view too: without it the row carries no linked_user_id,
                // so every contact would look unlinked (fix round 2, N3).
                { canCreateUser: canAddUsers && canViewUsers },
              )}
            />
          </div>
        ),
        size: 90,
      },
    ],
    [
      syncContactMutation.isPending,
      syncContactMutation.variables,
      bulkSyncMutation.isPending,
      outboundBusy,
      canViewUsers,
      canAddUsers,
    ],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination?.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting, rowSelection },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableRowSelection: true,
  });

  const clearSelection = () => setRowSelection({});

  // Reset selection whenever the result set changes.
  useEffect(() => {
    setRowSelection({});
  }, [
    searchQuery,
    chatbotMemoryLevel,
    customersNone,
    accessFiltersKey,
    pagination.pageIndex,
    pagination.pageSize,
    sorting,
  ]);

  const runOutboundBulk = (enabled: boolean) =>
    setOutboundBulk.mutate(
      { enabled, contactIds: selectedContactIds },
      { onSuccess: clearSelection },
    );

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button onClick={() => setCreateDialogOpen(true)}>
      <Plus className="size-4 mr-2" />
      Create Contact
    </Button>
  );

  return (
    <DataGrid
      table={table}
      tableLayout={{ columnsVisibility: true }}
      recordCount={data?.pagination?.total || 0}
      isLoading={isLoading}
      error={error}
      onRetry={() => void refetch()}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
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
                placeholder="Search contacts..."
                className="w-64"
              />
            }
            exportConfig={{ filename: 'contacts_export.xlsx' }}
            onRefresh={() => void refetch()}
            isRefreshing={isFetching && !isLoading}
            primaryAction={listPrimaryAction}
            leftActions={
              <div className="flex w-full min-w-0 flex-wrap items-center gap-2">
                <Checkbox
                  id="contacts-no-customers"
                  checked={customersNone}
                  onCheckedChange={(v) => setCustomersNone(v === true)}
                />
                <Label htmlFor="contacts-no-customers" className="text-sm font-normal">
                  No customers linked
                </Label>
                <ContactAccessFilters value={accessFilters} onChange={setAccessFilters} />
              </div>
            }
            bulkActions={[
              ...(canEditContacts
                ? [
                    {
                      key: 'link-customers',
                      label: `Link customers (${selectedContactIds.length})`,
                      icon: Link2,
                      onClick: () => setBulkLinkOpen(true),
                    },
                  ]
                : []),
              {
                key: 'outbound-enable',
                label: `Enable messaging (${selectedContactIds.length})`,
                icon: MessageCircle,
                disabled: outboundBusy,
                onClick: () => runOutboundBulk(true),
              },
              {
                key: 'outbound-disable',
                label: `Disable messaging (${selectedContactIds.length})`,
                icon: MessageCircleOff,
                destructive: true,
                disabled: outboundBusy,
                onClick: () => setBulkDisableOutboundOpen(true),
              },
              {
                key: 'sync',
                label: `Sync from Respond (${selectedContactIds.length})`,
                icon: RefreshCw,
                disabled: bulkSyncMutation.isPending || syncContactMutation.isPending,
                onClick: handleBulkSync,
              },
              ...(canEditContacts
                ? [
                    {
                      key: 'copy-access',
                      label: `Copy access from contact (${selectedContactIds.length})`,
                      icon: Copy,
                      onClick: () => setBulkCopyDialogOpen(true),
                    },
                  ]
                : []),
              {
                key: 'delete',
                label: `Delete (${selectedContactIds.length})`,
                icon: Trash2,
                destructive: true,
                onClick: () => setBulkDeleteDialogOpen(true),
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

      <BulkLinkCustomersDialog
        open={bulkLinkOpen}
        onOpenChange={setBulkLinkOpen}
        contacts={selectedContacts}
        onLinked={(okIds) =>
          setRowSelection((prev) => {
            const next = { ...prev };
            okIds.forEach((id) => delete next[id]);
            return next;
          })
        }
      />

      <ContactCreateDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
      />

      <ContactDeleteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        contact={contactToDelete}
      />

      <ContactBulkDeleteDialog
        open={bulkDeleteDialogOpen}
        onOpenChange={setBulkDeleteDialogOpen}
        contactIds={selectedContactIds}
        onSuccess={clearSelection}
      />

      <ContactOutboundDisableDialog
        open={bulkDisableOutboundOpen}
        onOpenChange={setBulkDisableOutboundOpen}
        contactCount={selectedContactIds.length}
        busy={outboundBusy}
        onConfirm={() => {
          setBulkDisableOutboundOpen(false);
          runOutboundBulk(false);
        }}
      />

      <BulkCopyAccessDialog
        open={bulkCopyDialogOpen}
        onOpenChange={setBulkCopyDialogOpen}
        onApplied={clearSelection}
        targetContacts={selectedContacts}
        onCheckDiffers={(source) => setAccessFilters({ accessDiffersFrom: source.id })}
      />

      <ContactImpersonateDialog
        contact={impersonateTarget}
        onClose={() => setImpersonateTarget(null)}
      />

      <UserAddDialog
        open={!!createUserContact}
        closeDialog={() => setCreateUserContact(null)}
        contact={createUserContact ? { id: createUserContact.id } : undefined}
      />
    </DataGrid>
  );
}
