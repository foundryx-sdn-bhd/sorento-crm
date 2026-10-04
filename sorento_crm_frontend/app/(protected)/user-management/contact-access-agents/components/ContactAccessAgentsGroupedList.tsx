'use client';

import { useMemo, useRef, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  useReactTable,
  getCoreRowModel,
  getExpandedRowModel,
} from '@tanstack/react-table';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import { useContactAccessAgents } from '../hooks/useContactAccessAgents';
import type { ContactAccessAgent } from '../types/contactAccessAgent.types';
import { formatDate } from '@/lib/helpers';
import ContactAgentAccessDialog from '../../access-agents/components/ContactAgentAccessDialog';
import React, { useEffect } from 'react';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { useAccessAgents } from '../../access-agents/hooks/useAccessAgents';
import { useQueryClient } from '@tanstack/react-query';
import ContactOutboundCell from '@/components/contacts/ContactOutboundCell';
import ContactOutboundSummary from '@/components/contacts/ContactOutboundSummary';
import { useRespondContactOutboundMutations } from '@/hooks/useRespondContactOutbound';

// Grouped row type
interface ContactGroup {
  id: string;
  respond_contact_phone: string;
  respond_contact_name?: string | null;
  /** The contact behind the group. Null on legacy grants keyed by phone only. */
  respond_contact_id?: string | null;
  /** The contact's outbound kill switch. Null when no contact row is linked. */
  outbound_enabled?: boolean | null;
  accessAgents: ContactAccessAgent[];
  isExpanded?: boolean;
}

export default function ContactAccessAgentsGroupedList() {
  const queryClient = useQueryClient();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'respond_contact_phone', desc: false }]);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [dialogOpen, setDialogOpen] = useState(false);
  const [agentSelectDialogOpen, setAgentSelectDialogOpen] = useState(false);
  const [selectedContactPhone, setSelectedContactPhone] = useState<string | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string>('');
  const [editingContactAccess, setEditingContactAccess] = useState<ContactAccessAgent | null>(null);
  
  // Fetch access agents for selection
  const { data: accessAgentsData } = useAccessAgents({
    pageIndex: 0,
    pageSize: 1000,
    sorting: [],
    searchQuery: '',
  });

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
    pageIndex: 0,
    pageSize: 10000, // Get all for grouping
    sorting: [],
    searchQuery,
  });

  // Refresh data when dialog closes
  useEffect(() => {
    if (!dialogOpen && !agentSelectDialogOpen) {
      queryClient.invalidateQueries({ queryKey: ['contact-access-agents'] });
    }
  }, [dialogOpen, agentSelectDialogOpen, queryClient]);

  // Group data by contact phone
  const groupedData = useMemo(() => {
    if (!data?.data) return [];

    const groupsMap = new Map<string, ContactGroup>();

    data.data.forEach((item) => {
      const phone = item.respond_contact_phone;
      if (!groupsMap.has(phone)) {
        groupsMap.set(phone, {
          id: `group-${phone}`,
          respond_contact_phone: phone,
          respond_contact_name: item.respond_contact_name,
          // One group is one contact, so the contact's outbound switch is 1:1
          // with the group row. Every grant in the group reports the same value.
          respond_contact_id: item.respond_contact_id ?? null,
          outbound_enabled: item.outbound_enabled ?? null,
          accessAgents: [],
          isExpanded: expandedGroups.has(phone),
        });
      }
      groupsMap.get(phone)!.accessAgents.push(item);
    });

    return Array.from(groupsMap.values()).sort((a, b) => {
      if (sorting[0]?.id === 'respond_contact_phone') {
        return sorting[0]?.desc
          ? b.respond_contact_phone.localeCompare(a.respond_contact_phone)
          : a.respond_contact_phone.localeCompare(b.respond_contact_phone);
      }
      return 0;
    });
  }, [data?.data, expandedGroups, sorting]);

  const { setOne: setOutboundOne, setBulk: setOutboundBulk } =
    useRespondContactOutboundMutations();
  // Only the BULK write blocks the switches. The per-row one is optimistic
  // (S7-01), so it has already moved the switch and can be flipped straight back.
  const outboundBusy = setOutboundBulk.isPending;

  // One group is one contact, so counting groups already counts contacts.
  const outboundCounts = useMemo(
    () => ({
      reachable: groupedData.filter((g) => g.outbound_enabled === true).length,
      silenced: groupedData.filter((g) => g.outbound_enabled === false).length,
    }),
    [groupedData],
  );

  const toggleGroup = (phone: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(phone)) {
        next.delete(phone);
      } else {
        next.add(phone);
      }
      return next;
    });
  };

  const handleAddAccessAgent = (contactPhone: string) => {
    setSelectedContactPhone(contactPhone);
    setEditingContactAccess(null);
    setSelectedAgentId('');
    setAgentSelectDialogOpen(true);
  };

  const handleAgentSelected = () => {
    if (selectedAgentId) {
      setAgentSelectDialogOpen(false);
      setDialogOpen(true);
    }
  };

  const handleEditAccessAgent = (contactAccess: ContactAccessAgent) => {
    setSelectedContactPhone(contactAccess.respond_contact_phone);
    setEditingContactAccess(contactAccess);
    setDialogOpen(true);
  };

  const handleDialogClose = () => {
    setDialogOpen(false);
    setSelectedContactPhone(null);
    setEditingContactAccess(null);
    setSelectedAgentId('');
    // Invalidate queries to refresh the list
    queryClient.invalidateQueries({ queryKey: ['contact-access-agents'] });
  };

  const columns = useMemo<ColumnDef<ContactGroup>[]>(
    () => [
      {
        id: 'expand',
        header: '',
        cell: ({ row }) => (
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={() => toggleGroup(row.original.respond_contact_phone)}
          >
            {expandedGroups.has(row.original.respond_contact_phone) ? (
              <ChevronDown className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
          </Button>
        ),
        size: 50,
      },
      {
        accessorKey: 'respond_contact_phone',
        header: ({ column }) => <DataGridColumnHeader title="Respond Contact Phone" column={column} />,
        size: 200,
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <span className="font-medium">{row.original.respond_contact_phone}</span>
            <Badge variant="secondary" size="sm">
              {row.original.accessAgents.length} agent{row.original.accessAgents.length !== 1 ? 's' : ''}
            </Badge>
          </div>
        ),
        meta: { skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'respond_contact_name',
        header: ({ column }) => <DataGridColumnHeader title="Respond Contact Name" column={column} />,
        size: 220,
        cell: ({ row }) => row.original.respond_contact_name || '-',
        meta: { skeleton: <Skeleton className="h-4 w-40" /> },
      },
      {
        id: 'outbound',
        header: ({ column }) => <DataGridColumnHeader title="Outbound" column={column} />,
        size: 210,
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
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <Button
            variant="outline"
            size="sm"
            onClick={() => handleAddAccessAgent(row.original.respond_contact_phone)}
          >
            <Plus className="size-4 mr-1" />
            Add Agent
          </Button>
        ),
        size: 120,
      },
    ],
    [expandedGroups, outboundBusy, setOutboundOne],
  );

  const table = useReactTable({
    columns,
    data: groupedData,
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    manualPagination: true,
    manualSorting: true,
  });

  return (
    <>
      <DataGrid
        table={table}
        tableLayout={{ columnsVisibility: true }}
        recordCount={groupedData.length}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
        onRefresh={() => void refetch()}
        isRefreshing={isFetching && !isLoading}
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
              showColumns={false}
              exportConfig={false}
              onRefresh={() => void refetch()}
              isRefreshing={isFetching && !isLoading}
            />
          </CardHeader>
          <CardTable>
            <ScrollArea>
              <table className="w-auto min-w-full border-collapse">
                <thead>
                  <tr className="border-b">
                    {table.getHeaderGroups()[0]?.headers.map((header) => (
                      <th
                        key={header.id}
                        className="text-left p-4 font-medium text-sm text-muted-foreground"
                        style={{ width: header.getSize() }}
                      >
                        {header.isPlaceholder
                          ? null
                          : header.column.columnDef.header
                            ? typeof header.column.columnDef.header === 'function'
                              ? header.column.columnDef.header(header.getContext())
                              : header.column.columnDef.header
                            : null}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {isLoading ? (
                    <tr>
                      <td colSpan={columns.length} className="p-4">
                        <SectionSkeleton rows={4} />
                      </td>
                    </tr>
                  ) : groupedData.length === 0 ? (
                    <tr>
                      <td colSpan={columns.length} className="p-4">
                        <div className="text-center text-muted-foreground">No contact access agents found</div>
                      </td>
                    </tr>
                  ) : (
                    groupedData.map((group) => (
                      <React.Fragment key={group.id}>
                        <tr className="border-b hover:bg-accent/50">
                          {table.getHeaderGroups()[0]?.headers.map((header) => {
                            const cell = table.getRow(group.id)?.getVisibleCells().find((c) => c.column.id === header.id);
                            return (
                              <td key={header.id} className="p-4" style={{ width: header.getSize() }}>
                                {cell ? (
                                  typeof cell.column.columnDef.cell === 'function'
                                    ? cell.column.columnDef.cell(cell.getContext())
                                    : cell.column.columnDef.cell
                                ) : null}
                              </td>
                            );
                          })}
                        </tr>
                        {expandedGroups.has(group.respond_contact_phone) && (
                          <tr>
                            <td colSpan={columns.length} className="p-0 bg-muted/30">
                              <div className="p-4">
                                <div className="mb-3 font-medium text-sm">Access Agents for {group.respond_contact_phone}</div>
                                <ScrollArea>
                                  <table className="w-auto min-w-full border-collapse">
                                    <thead>
                                      <tr className="border-b">
                                        <th className="text-left p-2 text-xs font-medium text-muted-foreground">Agent Code</th>
                                        <th className="text-left p-2 text-xs font-medium text-muted-foreground">Agent Name</th>
                                        <th className="text-left p-2 text-xs font-medium text-muted-foreground">Allowed</th>
                                        <th className="text-left p-2 text-xs font-medium text-muted-foreground">Valid From</th>
                                        <th className="text-left p-2 text-xs font-medium text-muted-foreground">Valid To</th>
                                        <th className="text-left p-2 text-xs font-medium text-muted-foreground">Created At</th>
                                        <th className="text-left p-2 text-xs font-medium text-muted-foreground">Actions</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {group.accessAgents.map((agent) => (
                                        <tr key={agent.id} className="border-b hover:bg-background">
                                          <td className="p-2 text-sm">{agent.agent_code || '-'}</td>
                                          <td className="p-2 text-sm">{agent.agent_name || '-'}</td>
                                          <td className="p-2">
                                            <Badge variant={agent.is_allowed ? 'success' : 'secondary'} size="sm">
                                              {agent.is_allowed ? 'Yes' : 'No'}
                                            </Badge>
                                          </td>
                                          <td className="p-2 text-sm">{agent.valid_from ? formatDate(new Date(agent.valid_from)) : '-'}</td>
                                          <td className="p-2 text-sm">{agent.valid_to ? formatDate(new Date(agent.valid_to)) : '-'}</td>
                                          <td className="p-2 text-sm">{formatDate(new Date(agent.created_at))}</td>
                                          <td className="p-2">
                                            <Button
                                              variant="ghost"
                                              size="sm"
                                              onClick={() => handleEditAccessAgent(agent)}
                                            >
                                              Edit
                                            </Button>
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                  <ScrollBar orientation="horizontal" />
                                </ScrollArea>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    ))
                  )}
                </tbody>
              </table>
              <ScrollBar orientation="horizontal" />
            </ScrollArea>
          </CardTable>
          <CardFooter>
            <div className="text-sm text-muted-foreground">
              Showing {groupedData.length} contact{groupedData.length !== 1 ? 's' : ''}
            </div>
          </CardFooter>
        </Card>
      </DataGrid>

      {/* Agent Selection Dialog */}
      <Dialog open={agentSelectDialogOpen} onOpenChange={setAgentSelectDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Select Access Agent</DialogTitle>
            <DialogDescription>
              Choose an access agent to add for contact {selectedContactPhone}
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <SearchableSelect
              value={selectedAgentId}
              onChange={setSelectedAgentId}
              placeholder="Select access agent"
              options={(accessAgentsData?.data || [])
                .filter((agent) => agent.is_active)
                .map((agent) => ({
                  value: agent.id,
                  label: `${agent.name} (${agent.code})`,
                }))}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAgentSelectDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleAgentSelected} disabled={!selectedAgentId}>
              Continue
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Add/Edit Dialog */}
      {selectedContactPhone && selectedAgentId && (
        <ContactAgentAccessDialog
          open={dialogOpen}
          onOpenChange={(open) => {
            if (!open) {
              handleDialogClose();
            } else {
              setDialogOpen(open);
            }
          }}
          accessAgentId={editingContactAccess?.agent_id || selectedAgentId}
          contactAccess={editingContactAccess}
          defaultContactPhone={selectedContactPhone}
        />
      )}
    </>
  );
}
