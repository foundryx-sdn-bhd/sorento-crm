'use client';

import * as React from 'react';
import type { PaginationState, SortingState } from '@tanstack/react-table';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { buildDetailSearch } from '@/lib/listNavQuery';
import { useLeadMutations, useLeads } from '../../_shared/hooks/useProjects';
import { useLeadAcceptanceMutations } from '../../_shared/hooks/useLeadAcceptance';
import type { LeadWithAcceptance } from '../../_shared/types/leadAcceptance.types';
import { AssignLeadDialog } from './AssignLeadDialog';
import { LeadsGrid } from './LeadsGrid';
import { LeadWizardDialog } from './LeadWizardDialog';
import { PageHeader } from '@/components/common/PageHeader';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';

const OUTCOME_OPTIONS = [
  { value: 'open', label: 'Open' },
  { value: 'qualified', label: 'Qualified' },
  { value: 'disqualified', label: 'Disqualified' },
];

const SOURCE_OPTIONS = [
  { value: 'site_visit', label: 'Site visit' },
  { value: 'architect', label: 'Architect' },
  { value: 'contractor', label: 'Contractor' },
  { value: 'dealer', label: 'Dealer' },
  { value: 'inbound', label: 'Inbound enquiry' },
  { value: 'other', label: 'Other' },
];

/**
 * Leads: what we have heard about, before anybody owns it.
 *
 * Defaults to OPEN leads because the list is a worklist, not an archive: a qualified
 * lead's real life continues on its project, and a disqualified one is history. Both
 * stay one filter click away rather than being hidden.
 *
 * The rows are the shared DataGrid, the same one every other list in the product uses.
 * Search, the two filters and Export all live on that grid's ONE toolbar row rather than
 * in a bespoke bar above it, so this screen is read exactly like the users list, the
 * pipeline and the acceptance worklist.
 *
 * There are no summary tiles above the grid. Conversion is a reporting question and it
 * is answered on the reports screen; on a worklist it pushed the rows below the fold to
 * say something nobody came here to read.
 */
export function LeadsClient() {
  const [wizardOpen, setWizardOpen] = React.useState(false);
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling: debouncedSettling,
    reset: resetSearch,
  } = useDebouncedSearch();
  const [outcome, setOutcome] = React.useState('open');
  const [source, setSource] = React.useState('');
  const [pagination, setPagination] = React.useState<PaginationState>({
    pageIndex: 0,
    pageSize: 25,
  });
  const [sorting, setSorting] = React.useState<SortingState>([
    { id: 'created_at', desc: true },
  ]);

  // Back hands the list its own query string back, and the pager keeps rewriting
  // it, so the list reads it (S3-01). Below the state it writes: the hook applies
  // during the render, and a const read above its own line throws.
  useListStateFromUrl((urlState) => {
    setPagination({ pageIndex: urlState.pageIndex, pageSize: urlState.pageSize });
    setSorting(urlState.sorting);
    resetSearch(urlState.searchQuery);
    setOutcome(urlState.filters.outcome ?? '');
    setSource(urlState.filters.source ?? '');
  });

  const [assignTarget, setAssignTarget] = React.useState<LeadWithAcceptance | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<LeadWithAcceptance | null>(null);

  const { assign } = useLeadAcceptanceMutations();
  const { remove } = useLeadMutations();

  // Narrowing the set changes which rows exist, so page 3 of the old set is a page of
  // nothing in the new one. Only on a CHANGE - the mount run used to stamp page 1 over
  // the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [debounced, outcome, source]);

  const leads = useLeads({
    query: debounced || undefined,
    outcome: outcome ? [outcome] : undefined,
    source: source ? [source] : undefined,
    page: pagination.pageIndex + 1,
    limit: pagination.pageSize,
    sort: sorting[0]?.id ?? 'created_at',
    dir: (sorting[0]?.desc ?? true) ? 'desc' : 'asc',
  });
  // The lead list already serves the P1 informant and acceptance fields; phase 1's
  // ProjectLead type predates them, so the rows are read through the wider type.
  const rows: LeadWithAcceptance[] = leads.data?.data ?? [];
  const total = leads.data?.pagination.total ?? 0;

  /**
   * Carried into the record URL so the lead page's pager walks the SAME searched,
   * sorted, filtered page the reader was on. The two filters ride along as well as
   * the sort: a pager rebuilt from page and sort alone would step through every
   * outcome, including the disqualified ones this list hides by default.
   */
  const detailSearch = React.useMemo(
    () =>
      buildDetailSearch(
        {
          pageIndex: pagination.pageIndex,
          pageSize: pagination.pageSize,
          sorting,
          searchQuery: debounced,
        },
        { outcome, source },
      ),
    [debounced, outcome, pagination, sorting, source],
  );

  const filtered = Boolean(debounced || source || outcome !== 'open');
  const activeFilterCount = (outcome !== 'open' ? 1 : 0) + (source ? 1 : 0);

  const searchSlot = (
    <ListSearchInput
      value={search}
      onChange={setSearch}
      isSettling={isSearchInFlight(debouncedSettling, leads.isFetching, debounced)}
      placeholder="Search title or lead code…"
      aria-label="Search leads"
      className="w-full max-w-xs"
    />
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Leads"
        actions={
          <Button type="button" onClick={() => setWizardOpen(true)}>
            <Plus className="size-4" aria-hidden />
            Create lead
          </Button>
        }
      />

      {leads.isError ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center">
          <h2 className="text-sm font-semibold text-destructive">
            Leads could not be loaded
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            {leads.error instanceof Error ? leads.error.message : 'Try again shortly.'}
          </p>
          <Button
            type="button"
            variant="outline"
            className="mt-4"
            onClick={() => void leads.refetch()}
          >
            Try again
          </Button>
        </div>
      ) : (
        <LeadsGrid
          leads={rows}
          total={total}
          isLoading={leads.isLoading}
          isFetching={leads.isFetching}
          isPlaceholderData={leads.isPlaceholderData}
          pagination={pagination}
          onPaginationChange={setPagination}
          sorting={sorting}
          onSortingChange={setSorting}
          onRefresh={() => void leads.refetch()}
          searchSlot={searchSlot}
          onAssign={setAssignTarget}
          onDelete={setDeleteTarget}
          rowHref={(lead) =>
            `/project-sales/leads/${lead.id}${detailSearch ? `?${detailSearch}` : ''}`
          }
          filters={{
            kind: 'custom',
            active: activeFilterCount > 0,
            activeCount: activeFilterCount,
            content: (
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Outcome</Label>
                  <SearchableSelect
                    value={outcome}
                    onChange={setOutcome}
                    clearable
                    options={OUTCOME_OPTIONS}
                    placeholder="All outcomes"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Source</Label>
                  <SearchableSelect
                    value={source}
                    onChange={setSource}
                    clearable
                    options={SOURCE_OPTIONS}
                    placeholder="All sources"
                  />
                </div>
                {activeFilterCount > 0 && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="w-full"
                    onClick={() => {
                      setOutcome('open');
                      setSource('');
                    }}
                  >
                    Back to open leads
                  </Button>
                )}
              </div>
            ),
          }}
          emptyState={
            <div className="px-6 py-10 text-center">
              <p className="text-sm font-semibold">
                {filtered ? 'No leads match these filters' : 'No open leads'}
              </p>
              <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
                Record what you heard on site, even when it is vague. A lead costs
                nothing and claims nothing: it becomes somebody&apos;s only when they
                accept it.
              </p>
            </div>
          }
        />
      )}

      {wizardOpen && <LeadWizardDialog onDone={() => setWizardOpen(false)} />}

      {assignTarget && (
        <AssignLeadDialog
          leadCode={assignTarget.lead_code}
          currentOwnerName={assignTarget.owner_name}
          submitting={assign.isPending}
          onDone={() => setAssignTarget(null)}
          onConfirm={async (ownerUserId, note) => {
            await assign.mutateAsync({ id: assignTarget.id, ownerUserId, note });
          }}
        />
      )}

      <ConfirmDeleteDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(next) => !next && setDeleteTarget(null)}
        title="Confirm delete"
        description={
          deleteTarget
            ? `Delete ${deleteTarget.lead_code} "${deleteTarget.title}"? This action cannot be undone. Any project already registered from it is kept.`
            : ''
        }
        onDelete={async () => {
          if (deleteTarget) await remove.mutateAsync(deleteTarget.id);
        }}
        successMessage="Lead deleted"
      />
    </div>
  );
}
