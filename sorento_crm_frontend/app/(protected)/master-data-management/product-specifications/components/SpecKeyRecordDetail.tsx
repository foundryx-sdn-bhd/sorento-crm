'use client';

import { useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { Pencil } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import AuditTrail from '@/components/audit/AuditTrail';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import BackToList from '@/components/common/BackToList';
import RecordNavigation from '@/components/common/RecordNavigation';
import { buildDetailSearch, parseDetailSearch } from '@/lib/listNavQuery';
import { getProductClassLabels } from '../../product-categories/services/categoryService';
import { useSpecKeyActions } from '../actions';
import { useSpecCoverageQuery } from '../hooks/useSpecCoverageQuery';
import { useSpecKeyRecord } from '../hooks/useSpecKeyRecord';
import { selectSpecKey, useSpecRegistryQuery } from '../hooks/useSpecRegistryQuery';
import { filterSpecKeys } from '../lib/specRegistryFilter';
import { HeaderTab } from './record/HeaderTab';
import { RulesTab } from './record/RulesTab';
import { SeenInProductsTab } from './record/SeenInProductsTab';
import { SpecKeyRecordCard } from './record/SpecKeyRecordCard';
import { ValuesAndWordsTab } from './record/ValuesAndWordsTab';

const LIST_PATH = '/master-data-management/product-specifications';

/**
 * The specification record page (B.1-B.8).
 *
 * View and edit share this one layout: the same five tabs in the same order (D15b:
 * Header first), the same record card, only the controls each field renders change.
 * `useSpecKeyRecord` owns the draft and the one PATCH Save sends; this component is
 * the wiring - the pager, the tabs, the not-found state, and the unsaved-changes
 * guard.
 */
export function SpecKeyRecordDetail({ specKey }: { specKey: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [tab, setTab] = useState('details');

  const { data: keys, isLoading, isError } = useSpecRegistryQuery();
  const row = selectSpecKey(keys, specKey);
  const record = useSpecKeyRecord(row);
  const { actions, dialogs, pending } = useSpecKeyActions(row, {
    onDeleted: () => router.push(LIST_PATH),
  });
  // The header's products count and last read time (fix round 5). A failed read
  // leaves both as "-" rather than blanking the card.
  const { data: coverage } = useSpecCoverageQuery();
  // Product class counts its choices from the category master, the same read and
  // cache key as the list's Choices column (AC-S3.3, D5).
  const { data: classLabels } = useQuery({
    queryKey: ['product-class-labels'],
    queryFn: () => getProductClassLabels(),
    staleTime: 5 * 60 * 1000,
    enabled: specKey === 'class',
  });

  // The pager walks the SAME filtered+sorted list the reader came from (B.1, D9):
  // the whole registry is one page in the React Query cache already, so this reads
  // it rather than paying for a query of its own (`ListPager`'s `pagerNode` slot,
  // design-language D4 - see `DetailActions`).
  const searchQuery = parseDetailSearch(searchParams).searchQuery;
  const filtered = useMemo(
    () => filterSpecKeys(keys ?? [], searchQuery),
    [keys, searchQuery],
  );
  const index = row ? filtered.findIndex((key) => key.spec_key === row.spec_key) : -1;

  const stepTo = (nextIndex: number) => {
    const target = filtered[nextIndex];
    if (!target) return;
    const search = buildDetailSearch({
      pageIndex: 0,
      pageSize: Math.max(filtered.length, 1),
      sorting: [{ id: 'label', desc: false }],
      searchQuery,
    });
    router.push(`${LIST_PATH}/${target.spec_key}${search ? `?${search}` : ''}`);
  };

  const pagerNode =
    filtered.length > 0 ? (
      <RecordNavigation
        index={index >= 0 ? index + 1 : null}
        total={filtered.length}
        hasPrevious={index > 0}
        hasNext={index >= 0 && index < filtered.length - 1}
        onPrevious={() => stepTo(index - 1)}
        onNext={() => stepTo(index + 1)}
        ariaLabel="specification"
        disabled={record.mode === 'edit'}
      />
    ) : null;

  // Unsaved changes prompt via the browser's own dialog only (B.2) - no custom one.
  useEffect(() => {
    if (record.mode !== 'edit') return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [record.mode]);

  // Back sits with the title (D11, AC-S3.6): the header lives INSIDE a `Container`
  // of its own, the same gutter as the record card below it, in every state - not
  // as a sibling of the body's `Container`, which is what left it flush against
  // the window edge (review R1).
  const backLink = <BackToList listPath={LIST_PATH} label="Back to specifications" />;
  const header = <PageHeader title="Product Specifications" actions={backLink} />;

  if (isLoading) {
    return (
      <>
        <Container>{header}</Container>
        <Container>
          <div className="space-y-4">
            <Skeleton className="h-24 w-full rounded-xl" />
            <Skeleton className="h-96 w-full rounded-xl" />
          </div>
        </Container>
      </>
    );
  }

  if (isError || !row) {
    return (
      <>
        <Container>{header}</Container>
        <Container>
          <Card className="flex flex-col items-center gap-3 p-10 text-center">
            <div className="text-sm font-semibold">Specification not found</div>
            <p className="max-w-md text-sm text-muted-foreground">
              This specification doesn&apos;t exist, or it was removed after this link
              was made.
            </p>
            <Button variant="outline" onClick={() => router.push(LIST_PATH)}>
              Back to specifications
            </Button>
          </Card>
        </Container>
      </>
    );
  }

  const primary =
    record.mode === 'edit' ? (
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" onClick={record.cancel} disabled={record.saving}>
          Cancel
        </Button>
        <Button size="sm" onClick={() => void record.save()} disabled={record.saving}>
          {record.saving ? 'Saving...' : 'Save'}
        </Button>
      </div>
    ) : (
      <Button size="sm" onClick={record.edit}>
        <Pencil className="size-4" aria-hidden />
        Edit
      </Button>
    );

  return (
    <>
      <Container>
        {/* The record's identity lives on the header card below, as on every other
            record page (Users & Access), so the page title names the module. */}
        <PageHeader title="Product Specifications" crumbTitle={row.label} actions={backLink} />
      </Container>

      <Container className="flex flex-col gap-4">
        <SpecKeyRecordCard
          row={row}
          productsCount={coverage ? coverage.coverage[row.spec_key] ?? 0 : undefined}
          lastReadAt={coverage ? coverage.last_read?.[row.spec_key] ?? null : undefined}
          classChoiceCount={classLabels?.length}
          mode={record.mode}
          pagerNode={pagerNode}
          actions={actions}
          pending={pending}
          primary={primary}
        />

        {/* Details, Choices and words, How it is read, Products, in that order, the
            same in view and edit (AC-S1.11). Rules render on How it is read and
            nowhere else. */}
        <Tabs value={tab} onValueChange={setTab} className="w-full">
          <TabsList variant="line" className="mb-4 w-full justify-start">
            <TabsTrigger value="details">Details</TabsTrigger>
            <TabsTrigger value="choices">Choices and words</TabsTrigger>
            <TabsTrigger value="rules">How it is read</TabsTrigger>
            <TabsTrigger value="products">Products</TabsTrigger>
            <TabsTrigger value="history">History</TabsTrigger>
          </TabsList>

          <TabsContent value="details" className="mt-0 focus-visible:outline-none">
            <HeaderTab
              row={row}
              mode={record.mode}
              draft={record.draft}
              setDraft={record.setDraft}
              wordError={record.wordError}
            />
          </TabsContent>

          <TabsContent value="choices" className="mt-0 focus-visible:outline-none">
            <ValuesAndWordsTab
              row={row}
              mode={record.mode}
              draft={record.draft}
              setDraft={record.setDraft}
              wordError={record.wordError}
              onEnterEdit={() => {
                record.edit();
                setTab('choices');
              }}
            />
          </TabsContent>

          <TabsContent value="rules" className="mt-0 focus-visible:outline-none">
            <RulesTab
              row={row}
              registry={keys ?? []}
              mode={record.mode}
              draft={record.draft}
              setDraft={record.setDraft}
              onEnterEdit={record.edit}
            />
          </TabsContent>

          <TabsContent value="products" className="mt-0 focus-visible:outline-none">
            <SeenInProductsTab
              specKey={row.spec_key}
              label={row.label}
              unit={row.unit}
              valueLabels={row.value_labels}
            />
          </TabsContent>

          <TabsContent value="history" className="mt-0 focus-visible:outline-none">
            <AuditTrail entityType="product_spec_registry" entityId={row.id} title="History" />
          </TabsContent>
        </Tabs>
        {dialogs}
      </Container>
    </>
  );
}

export default SpecKeyRecordDetail;
