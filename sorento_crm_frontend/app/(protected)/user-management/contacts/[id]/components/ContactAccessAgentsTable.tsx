'use client';

import { useMemo, useState } from 'react';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  useReactTable,
  getCoreRowModel,
} from '@tanstack/react-table';
import { Plus, Edit, Trash2, Copy, SlidersHorizontal } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CardContent } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import { formatDate } from '@/lib/helpers';
import ContactAgentAccessDialog from '../../../access-agents/components/ContactAgentAccessDialog';
import ContactAgentAccessDeleteDialog from '../../../access-agents/components/contact-agent-access-delete-dialog';
import CopyAccessAgentsFromContactDialog from './CopyAccessAgentsFromContactDialog';
import ContactFieldAccessDialog from '../../../access-agents/components/ContactFieldAccessDialog';
import type { ContactAgentAccess } from '../../../access-agents/types/accessAgent.types';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

interface ContactAccessAgentsTableProps {
  contactId: string;
}

export default function ContactAccessAgentsTable({ contactId }: ContactAccessAgentsTableProps) {
  const queryClient = useQueryClient();
  const [pagination, setPagination] = useState<PaginationState>({ pageIndex: 0, pageSize: 50 });
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingContactAccess, setEditingContactAccess] = useState<ContactAgentAccess | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [contactAccessToDelete, setContactAccessToDelete] = useState<ContactAgentAccess | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string>('');
  const [copyFromDialogOpen, setCopyFromDialogOpen] = useState(false);
  // Which agent's per-contact field exceptions are being edited.
  const [fieldAccessFor, setFieldAccessFor] = useState<ContactAgentAccess | null>(null);

  const { data, isLoading, isPlaceholderData, error, refetch } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: ['contact-access-agents', contactId, pagination, sorting],
    queryFn: async () => {
      const sortField = sorting?.[0]?.id || 'created_at';
      const sortDirection = sorting?.[0]?.desc ? 'desc' : 'asc';
      const params = new URLSearchParams({
        page: String(pagination.pageIndex + 1),
        limit: String(pagination.pageSize),
        sort: sortField,
        dir: sortDirection,
      });
      const response = await apiFetch(`/api/user-management/contacts/${contactId}/access-agents?${params.toString()}`);
      if (!response.ok) throw new Error('Failed to fetch contact access agents');
      return response.json();
    },
    enabled: !!contactId,
  });

  // Fetch contact details for phone number
  const { data: contact } = useQuery({
    queryKey: ['respond-contact', contactId],
    queryFn: async () => {
      const response = await apiFetch(`/api/user-management/contacts/${contactId}`);
      if (!response.ok) throw new Error('Failed to fetch contact');
      return response.json();
    },
    enabled: !!contactId,
  });

  // Fetch access agents for selection
  const { data: accessAgentsData } = useQuery({
    queryKey: ['access-agents-select'],
    queryFn: async () => {
      const response = await apiFetch('/api/user-management/access-agents?page=1&limit=1000');
      if (!response.ok) throw new Error('Failed to fetch access agents');
      const data = await response.json();
      return data.data || [];
    },
  });

  const handleAdd = () => {
    setEditingContactAccess(null);
    setSelectedAgentId('');
    setDialogOpen(true);
  };

  const handleEdit = (contactAccess: ContactAgentAccess) => {
    setEditingContactAccess(contactAccess);
    setSelectedAgentId(contactAccess.agent_id);
    setDialogOpen(true);
  };

  const handleDelete = (contactAccess: ContactAgentAccess) => {
    setContactAccessToDelete(contactAccess);
    setDeleteDialogOpen(true);
  };

  const handleDialogClose = () => {
    setDialogOpen(false);
    setEditingContactAccess(null);
    setSelectedAgentId('');
    queryClient.invalidateQueries({ queryKey: ['contact-access-agents', contactId] });
  };

  const columns = useMemo<ColumnDef<ContactAgentAccess>[]>(
    () => [
      {
        accessorKey: 'agent_code',
        header: ({ column }) => <DataGridColumnHeader title="Agent Code" column={column} />,
        size: 150,
        meta: { skeleton: <Skeleton className="h-4 w-24" /> },
      },
      {
        accessorKey: 'agent_name',
        header: ({ column }) => <DataGridColumnHeader title="Agent Name" column={column} />,
        size: 250,
        meta: { skeleton: <Skeleton className="h-4 w-32" /> },
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
      },
      {
        accessorKey: 'valid_from',
        header: ({ column }) => <DataGridColumnHeader title="Valid From" column={column} />,
        cell: ({ row }) => row.original.valid_from ? formatDate(new Date(row.original.valid_from)) : '-',
        size: 150,
      },
      {
        accessorKey: 'valid_to',
        header: ({ column }) => <DataGridColumnHeader title="Valid To" column={column} />,
        cell: ({ row }) => row.original.valid_to ? formatDate(new Date(row.original.valid_to)) : '-',
        size: 150,
      },
      {
        accessorKey: 'created_at',
        header: ({ column }) => <DataGridColumnHeader title="Created At" column={column} />,
        cell: ({ row }) => formatDate(new Date(row.original.created_at)),
        size: 150,
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex gap-2">
            <Button
              variant="ghost"
              size="sm"
              title="Fields this contact may be told"
              onClick={(e) => {
                e.stopPropagation();
                setFieldAccessFor(row.original);
              }}
            >
              <SlidersHorizontal className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                handleEdit(row.original);
              }}
            >
              <Edit className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                handleDelete(row.original);
              }}
            >
              <Trash2 className="size-4 text-destructive" />
            </Button>
          </div>
        ),
        size: 140,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row) => row.id,
    state: { pagination, sorting },
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualPagination: true,
    manualSorting: true,
  });

  return (
    <>
      <CardContent>
        <div className="flex flex-wrap items-center justify-between gap-2 mb-4">
          <h3 className="text-lg font-semibold">Access Agents</h3>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={() => setCopyFromDialogOpen(true)} size="sm">
              <Copy className="size-4" />
              Copy from another user
            </Button>
            <Button onClick={handleAdd} size="sm">
              <Plus className="size-4" />
              Add Access Agent
            </Button>
          </div>
        </div>
        {isLoading ? (
          <div className="text-center py-8 text-muted-foreground">Loading access agents...</div>
        ) : (
          // A grant is edited in a lightbox and has no page of its own, so the
          // row opens that lightbox (D3, second clause).
          <DataGrid
            table={table}
            recordCount={data?.pagination.total || 0}
            isLoading={isLoading}
            error={error}
            onRetry={() => void refetch()}
            isPlaceholderData={isPlaceholderData}
            onRowClick={handleEdit}
          >
            <DataGridTable />
          </DataGrid>
        )}
      </CardContent>

      {/* Simplified Add/Edit Dialog - combines agent selection with allowed/valid_from/valid_to */}
      {contact && (
        <ContactAgentAccessDialog
          open={dialogOpen}
          onOpenChange={(open) => {
            if (!open) {
              handleDialogClose();
            }
          }}
          accessAgentId={editingContactAccess?.agent_id || selectedAgentId || ''}
          contactAccess={editingContactAccess}
          defaultContactPhone={contact.phone_number}
          defaultContactName={contact.name || undefined}
          contactId={contactId}
          accessAgents={accessAgentsData || []}
          showAgentSelection={!editingContactAccess}
        />
      )}

      {/* Delete Dialog */}
      {contactAccessToDelete && (
        <ContactAgentAccessDeleteDialog
          open={deleteDialogOpen}
          closeDialog={() => {
            setDeleteDialogOpen(false);
            setContactAccessToDelete(null);
            queryClient.invalidateQueries({ queryKey: ['contact-access-agents', contactId] });
          }}
          accessAgentId={contactAccessToDelete.agent_id}
          contactAccess={contactAccessToDelete}
        />
      )}

      {/* Copy from another user */}
      {contact && (
        <CopyAccessAgentsFromContactDialog
          open={copyFromDialogOpen}
          onOpenChange={setCopyFromDialogOpen}
          currentContactId={contactId}
          currentContactPhone={contact.phone_number}
          currentContactName={contact.name ?? undefined}
          onSuccess={() => queryClient.invalidateQueries({ queryKey: ['contact-access-agents', contactId] })}
        />
      )}

      {fieldAccessFor && (
        <ContactFieldAccessDialog
          open
          onOpenChange={(open) => !open && setFieldAccessFor(null)}
          agentId={fieldAccessFor.agent_id}
          agentName={fieldAccessFor.agent_name || fieldAccessFor.agent_code || 'this agent'}
          contactId={contactId}
          contactName={fieldAccessFor.respond_contact_name}
        />
      )}
    </>
  );
}

