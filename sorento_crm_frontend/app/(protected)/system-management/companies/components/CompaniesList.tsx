'use client';

import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';
import {
  RowSelectionState,
  getCoreRowModel,
  useReactTable,
} from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { Card, CardHeader, CardTable } from '@/components/ui/card';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { isSuperadminUser } from '@/lib/is-superadmin';
import { useCompanies } from '../hooks/useCompanies';
import { buildCompanyColumns } from './CompanyTable';
import CompanyFormDialog from './CompanyFormDialog';
import CompanyDeleteDialog from './CompanyDeleteDialog';
import CompanyAccessDialog from './CompanyAccessDialog';
import type { Company } from '../types/company.types';

export default function CompaniesList() {
  // Companies admin is superadmin/admin-only (backend enforces via role slug;
  // this is the UX guard for direct navigation). TODO(AC-A1/A4): swap for a
  // `system.companies.manage` permission slug once the backend defines one.
  const { data: session, status } = useSession();
  const denied = status === 'authenticated' && !isSuperadminUser(session?.user);

  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: searchQuery,
  } = useDebouncedSearch();
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [formOpen, setFormOpen] = useState(false);
  const [editingCompanyId, setEditingCompanyId] = useState<string | undefined>(undefined);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [companyToDelete, setCompanyToDelete] = useState<Company | null>(null);
  const [accessCompany, setAccessCompany] = useState<Company | null>(null);

  const { data, isLoading, isPlaceholderData, error, refetch } = useCompanies({
    pageIndex: 0,
    pageSize: 100,
    sorting: [{ id: 'name', desc: false }],
    searchQuery: '',
  });

  const handleEdit = (company: Company) => {
    setEditingCompanyId(company.id);
    setFormOpen(true);
  };

  const handleManageAccess = (company: Company) => {
    setAccessCompany(company);
  };

  const handleDelete = (company: Company) => {
    setCompanyToDelete(company);
    setDeleteDialogOpen(true);
  };

  const handleFormClose = (open: boolean) => {
    setFormOpen(open);
    if (!open) setEditingCompanyId(undefined);
  };

  // Search + status are filtered client-side (the list loads up to 100 companies).
  const filteredCompanies = useMemo(() => {
    const companies = data?.data ?? [];
    const q = searchQuery.toLowerCase();
    return companies.filter((c) => {
      const matchesSearch =
        !q || c.name?.toLowerCase().includes(q) || c.code?.toLowerCase().includes(q);
      const matchesStatus =
        statusFilter === 'all' || (statusFilter === 'active' ? c.is_active : !c.is_active);
      return matchesSearch && matchesStatus;
    });
  }, [data, searchQuery, statusFilter]);

  const columns = useMemo(
    () =>
      buildCompanyColumns({
        onEdit: handleEdit,
        onManageAccess: handleManageAccess,
        onDelete: handleDelete,
      }),
    [],
  );

  const table = useReactTable({
    columns,
    data: filteredCompanies,
    getRowId: (row) => row.id,
    state: { rowSelection },
    enableRowSelection: true,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
  });

  const statusActive = statusFilter !== 'all';

  if (denied) {
    return (
      <div className="rounded-md border p-6 text-sm text-muted-foreground">
        Forbidden - you don&apos;t have permission to manage companies.
      </div>
    );
  }

  // The one offer this listing makes, in both places it belongs: the
  // toolbar, and the empty state's next step (S5-06).
  const listPrimaryAction = (
    <Button
      onClick={() => {
        setEditingCompanyId(undefined);
        setFormOpen(true);
      }}
    >
      <Plus className="size-4" />
      Create Company
    </Button>
  );

  return (
    <>
      <DataGrid
        table={table}
        recordCount={filteredCompanies.length}
        isLoading={isLoading}
        error={error}
        onRetry={() => void refetch()}
        isPlaceholderData={isPlaceholderData}
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
                  placeholder="Search companies..."
                  className="w-64"
                />
              }
              filters={{
                kind: 'custom',
                active: statusActive,
                activeCount: statusActive ? 1 : 0,
                content: (
                  <div className="space-y-4">
                    <div>
                      <Label>Status</Label>
                      <SearchableSelect
                        value={statusFilter}
                        onChange={(v) => setStatusFilter(v as 'all' | 'active' | 'inactive')}
                        placeholder="All statuses"
                        triggerClassName="mt-1"
                        options={[
                          { value: 'all', label: 'All statuses' },
                          { value: 'active', label: 'Active' },
                          { value: 'inactive', label: 'Inactive' },
                        ]}
                      />
                    </div>
                    {statusActive && (
                      <div className="flex justify-end">
                        <Button variant="ghost" size="sm" onClick={() => setStatusFilter('all')}>
                          Clear filters
                        </Button>
                      </div>
                    )}
                  </div>
                ),
              }}
              exportConfig={{ filename: 'companies_export.xlsx' }}
              primaryAction={listPrimaryAction}
            />
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </Card>
      </DataGrid>

      <CompanyFormDialog
        open={formOpen}
        onOpenChange={handleFormClose}
        companyId={editingCompanyId}
      />

      {accessCompany && (
        <CompanyAccessDialog
          open={!!accessCompany}
          onOpenChange={(open) => {
            if (!open) setAccessCompany(null);
          }}
          company={accessCompany}
        />
      )}

      {companyToDelete && (
        <CompanyDeleteDialog
          open={deleteDialogOpen}
          closeDialog={() => {
            setDeleteDialogOpen(false);
            setCompanyToDelete(null);
          }}
          company={companyToDelete}
        />
      )}
    </>
  );
}
