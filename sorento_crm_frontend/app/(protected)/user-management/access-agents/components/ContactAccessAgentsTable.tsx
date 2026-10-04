'use client';

import { useState, useMemo } from 'react';
import {
  ColumnDef,
  useReactTable,
  getCoreRowModel,
} from '@tanstack/react-table';
import { Plus, Edit, Trash2 } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { CardContent } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Skeleton } from '@/components/ui/skeleton';
import { useContactAccessAgents, useDeleteContactAgentAccess } from '../hooks/useAccessAgents';
import { formatDate } from '@/lib/helpers';
import ContactAgentAccessDialog from './ContactAgentAccessDialog';
import ContactAgentAccessDeleteDialog from './contact-agent-access-delete-dialog';
import type { ContactAgentAccess } from '../types/accessAgent.types';

interface ContactAccessAgentsTableProps {
  accessAgentId: string;
}

export default function ContactAccessAgentsTable({ accessAgentId }: ContactAccessAgentsTableProps) {
  const { data: contactAccesses, isLoading, isPlaceholderData, error, refetch } = useContactAccessAgents(accessAgentId);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingContactAccess, setEditingContactAccess] = useState<ContactAgentAccess | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [contactAccessToDelete, setContactAccessToDelete] = useState<ContactAgentAccess | null>(null);

  const handleAdd = () => {
    setEditingContactAccess(null);
    setDialogOpen(true);
  };

  const handleEdit = (contactAccess: ContactAgentAccess) => {
    setEditingContactAccess(contactAccess);
    setDialogOpen(true);
  };

  const handleDelete = (contactAccess: ContactAgentAccess) => {
    setContactAccessToDelete(contactAccess);
    setDeleteDialogOpen(true);
  };

  const handleDialogClose = () => {
    setDialogOpen(false);
    setEditingContactAccess(null);
  };

  const columns = useMemo<ColumnDef<ContactAgentAccess>[]>(
    () => [
      {
        accessorKey: 'respond_contact_phone',
        header: ({ column }) => <DataGridColumnHeader title="Respond Contact Phone" column={column} />,
        size: 200,
        meta: { skeleton: <Skeleton className="h-4 w-32" /> },
      },
      {
        accessorKey: 'respond_contact_name',
        header: ({ column }) => <DataGridColumnHeader title="Respond Contact Name" column={column} />,
        size: 220,
        meta: { skeleton: <Skeleton className="h-4 w-40" /> },
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
        size: 100,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: contactAccesses || [],
    getCoreRowModel: getCoreRowModel(),
  });

  return (
    <>
      <CardContent>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">Contact Access Agents</h3>
          <Button onClick={handleAdd} size="sm">
            <Plus className="size-4" />
            Add Contact Access
          </Button>
        </div>
        {isLoading ? (
          <div className="text-center py-8 text-muted-foreground">Loading contact access agents...</div>
        ) : (
          // A grant is edited in a lightbox and has no page of its own, so the
          // row opens that lightbox (D3, second clause).
          <DataGrid
            table={table}
            recordCount={contactAccesses?.length || 0}
            isLoading={isLoading}
            error={error}
            onRetry={() => void refetch()}
            isPlaceholderData={isPlaceholderData}
            onRowClick={handleEdit}
            emptyMessage={
              <div className="text-center py-8 text-muted-foreground">
                <p>No contact access agents yet.</p>
                <p className="text-sm mt-1">Add contact access to control which contacts can use this agent.</p>
                <Button variant="outline" size="sm" className="mt-3" onClick={handleAdd}>
                  <Plus className="size-4" />
                  Add Contact Access
                </Button>
              </div>
            }
          >
            <DataGridTable />
          </DataGrid>
        )}
      </CardContent>

      {/* Add/Edit Dialog */}
      <ContactAgentAccessDialog
        open={dialogOpen}
        onOpenChange={handleDialogClose}
        accessAgentId={accessAgentId}
        contactAccess={editingContactAccess}
      />

      {/* Delete Dialog */}
      {contactAccessToDelete && (
        <ContactAgentAccessDeleteDialog
          open={deleteDialogOpen}
          closeDialog={() => {
            setDeleteDialogOpen(false);
            setContactAccessToDelete(null);
          }}
          accessAgentId={accessAgentId}
          contactAccess={contactAccessToDelete}
        />
      )}
    </>
  );
}
