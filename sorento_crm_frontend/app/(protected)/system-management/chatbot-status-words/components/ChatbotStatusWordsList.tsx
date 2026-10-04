'use client';

import { useMemo, useState } from 'react';
import { getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Card, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { useChatbotDomainsQuery } from '@/app/(protected)/system-management/chatbot-domains/hooks/useChatbotDomains';
import { useChatbotStatusWordsQuery } from '../hooks/useChatbotStatusWords';
import type { ChatbotStatusWord } from '../types/chatbotStatusWord.types';
import ChatbotStatusWordModal from './ChatbotStatusWordModal';

export default function ChatbotStatusWordsList() {
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
  } = useDebouncedSearch();
  const [domainFilter, setDomainFilter] = useState('');
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const canManage = useHasPermission('system.chatbot_config.manage');
  const { data, isLoading, isError } = useChatbotStatusWordsQuery();
  const { data: domains } = useChatbotDomainsQuery();
  const rows = useMemo(() => data ?? [], [data]);
  const domainOptions = useMemo(
    () => (domains ?? []).map((d) => ({ value: d.name, label: `${d.label} (${d.name})` })),
    [domains],
  );

  const filtered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return rows.filter((r) => {
      const matchesSearch =
        !q ||
        r.value.toLowerCase().includes(q) ||
        r.label.toLowerCase().includes(q) ||
        r.trigger_words.some((w) => w.toLowerCase().includes(q));
      return matchesSearch && (!domainFilter || r.domain === domainFilter);
    });
  }, [rows, searchQuery, domainFilter]);

  const columns = useMemo<ColumnDef<ChatbotStatusWord>[]>(
    () => [
      {
        accessorKey: 'value',
        header: ({ column }) => <DataGridColumnHeader title="Status" column={column} />,
        cell: ({ row }) => <span className="font-medium">{row.original.value}</span>,
        size: 170,
        meta: { headerTitle: 'Status' },
      },
      {
        accessorKey: 'domain',
        header: ({ column }) => <DataGridColumnHeader title="Domain" column={column} />,
        cell: ({ row }) => (
          <Badge variant="secondary" appearance="light" size="sm">
            {row.original.domain}
          </Badge>
        ),
        size: 110,
        meta: { headerTitle: 'Domain' },
      },
      {
        accessorKey: 'label',
        header: ({ column }) => <DataGridColumnHeader title="Meaning" column={column} />,
        cell: ({ row }) => (
          <span className="truncate block" title={row.original.label}>
            {row.original.label}
          </span>
        ),
        size: 240,
        meta: { headerTitle: 'Meaning' },
      },
      {
        id: 'trigger_words',
        header: ({ column }) => <DataGridColumnHeader title="Customer words" column={column} />,
        cell: ({ row }) => {
          const text = row.original.trigger_words.join(', ') || '(none)';
          return (
            <span className="truncate block text-muted-foreground" title={text}>
              {text}
            </span>
          );
        },
        size: 320,
        meta: { headerTitle: 'Customer words' },
      },
      {
        accessorKey: 'updated_at',
        header: ({ column }) => <DataGridColumnHeader title="Updated" column={column} />,
        cell: ({ row }) => formatDateTimeInMalaysia(row.original.updated_at),
        size: 140,
        meta: { headerTitle: 'Updated' },
      },
    ],
    [],
  );

  const table = useReactTable({
    data: filtered,
    columns,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
  });

  const openCreate = () => {
    setEditingId(null);
    setModalOpen(true);
  };

  if (isError) {
    return (
      <p className="text-sm text-destructive">
        Status words could not be loaded. Reload the page to try again.
      </p>
    );
  }

  return (
    <>
      <DataGrid
        table={table}
        recordCount={filtered.length}
        isLoading={isLoading}
        onRowClick={(row) => {
          setEditingId(row.id);
          setModalOpen(true);
        }}
        tableLayout={{ width: 'fixed', columnsResizable: true, columnsVisibility: true }}
      >
        <Card>
          <CardHeader className="block">
            <DataGridListToolbar
              table={table}
              searchSlot={
                <ListSearchInput
                  value={searchInput}
                  onChange={setSearchInput}
                  placeholder="Search statuses and words"
                  className="w-72"
                />
              }
              filters={{
                kind: 'custom',
                active: domainFilter !== '',
                activeCount: domainFilter ? 1 : 0,
                content: (
                  <div className="space-y-4">
                    <div>
                      <Label>Domain</Label>
                      <SearchableSelect
                        value={domainFilter}
                        onChange={setDomainFilter}
                        clearable
                        placeholder="All domains"
                        triggerClassName="mt-1"
                        options={domainOptions}
                      />
                    </div>
                  </div>
                ),
              }}
              primaryAction={
                canManage ? (
                  <Button onClick={openCreate}>
                    <Plus className="size-4" />
                    Add status word
                  </Button>
                ) : undefined
              }
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </Card>
      </DataGrid>

      <ChatbotStatusWordModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        statusId={editingId}
        rows={filtered}
        domainOptions={domainOptions}
        onNavigate={(id) => setEditingId(id)}
        canManage={canManage}
      />
    </>
  );
}
