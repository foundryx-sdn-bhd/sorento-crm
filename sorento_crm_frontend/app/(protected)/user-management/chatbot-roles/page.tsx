'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Container } from '@/components/common/container';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { PageHeader } from '@/components/common/PageHeader';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useChatbotRoles, useCreateChatbotRole } from '@/hooks/useChatbotAccess';
import { TIER_OPTIONS, tierLabel } from '@/lib/chatbot-access/labels';
import type { AudienceTier, ChatbotRole } from '@/services/chatbotAccessService';

export default function ChatbotRolesPage() {
  const router = useRouter();
  const { data: roles = [], isLoading, isError } = useChatbotRoles();
  const createRole = useCreateChatbotRole();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [tier, setTier] = useState('');
  const [search, setSearch] = useState('');
  const shown = useMemo(
    () => roles.filter((r) => r.name.toLowerCase().includes(search.trim().toLowerCase())),
    [roles, search],
  );

  const columns = useMemo<ColumnDef<ChatbotRole>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Role" column={column} />,
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.name}>
            {row.original.name}
          </span>
        ),
        size: 260,
      },
      {
        id: 'tier',
        accessorFn: (r) => tierLabel(r.audience_tier),
        header: ({ column }) => <DataGridColumnHeader title="Tier" column={column} />,
        cell: ({ row }) => tierLabel(row.original.audience_tier) || '-',
        size: 140,
      },
      {
        id: 'customers',
        accessorFn: (r) => (r.sees_all_customers ? 'All' : 'Linked only'),
        header: ({ column }) => <DataGridColumnHeader title="Customers" column={column} />,
        cell: ({ row }) => (row.original.sees_all_customers ? 'All' : 'Linked only'),
        size: 160,
      },
      {
        accessorKey: 'contact_count',
        header: ({ column }) => <DataGridColumnHeader title="Contacts" column={column} />,
        cell: ({ row }) => String(row.original.contact_count),
        size: 120,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: shown,
    getRowId: (r) => r.id,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
    getCoreRowModel: getCoreRowModel(),
  });

  const submit = () => {
    if (!name.trim()) return;
    createRole.mutate(
      {
        name: name.trim(),
        sees_all_customers: false,
        audience_tier: (tier || null) as AudienceTier | null,
      },
      {
        onSuccess: (role) => {
          setOpen(false);
          setName('');
          setTier('');
          if (role?.id) router.push(`/user-management/chatbot-roles/${role.id}`);
        },
      },
    );
  };

  return (
    <>
      <Container>
        <PageHeader
          title="Chatbot Roles"
          actions={
            <Button onClick={() => setOpen(true)}>
              <Plus className="size-4 mr-2" />
              Add role
            </Button>
          }
        />
      </Container>
      <Container>
        <div className="mb-3 max-w-sm">
          <ListSearchInput value={search} onChange={setSearch} placeholder="Search roles" />
        </div>
        <DataGrid
          table={table}
          recordCount={shown.length}
          isLoading={isLoading}
          error={isError ? new Error('Failed to load roles') : undefined}
          emptyMessage="No roles yet."
          listingKey="user_management.access_agents.view::chatbot_roles"
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          rowHref={(r: ChatbotRole) => `/user-management/chatbot-roles/${r.id}`}
        >
          <Card>
            <CardHeader className="hidden" />
            <CardContent className="overflow-x-auto">
              <DataGridTable />
            </CardContent>
          </Card>
        </DataGrid>
      </Container>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add role</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="role-name">Name</Label>
              <Input id="role-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>Tier</Label>
              <SearchableSelect value={tier} onChange={setTier} options={TIER_OPTIONS} clearable />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={submit} disabled={!name.trim() || createRole.isPending}>
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
