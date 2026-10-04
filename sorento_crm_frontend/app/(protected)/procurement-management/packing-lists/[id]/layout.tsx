'use client';

import React, { use, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Boxes,
  Download,
  Edit,
  FileSpreadsheet,
  FileText,
  Files,
  History,
  Info,
  Settings,
  Trash2,
  Upload,
  X,
} from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { formatDate } from '@/lib/helpers';
import { enqueuePackingListExport } from '@/app/(protected)/scm/services/fulfilmentService';
import { EntityDownloadsButton } from '@/components/my-downloads/EntityDownloadsButton';
import { ENTITY_DOWNLOADS_QUERY_KEY, MY_DOWNLOADS_QUERY_KEY } from '@/services/myDownloadsService';
import DetailActions from '@/components/common/DetailActions';
import BackToList, { useBackToListHref } from '@/components/common/BackToList';
import { packingListsPagerQuery } from '../hooks/usePackingLists';
import PackingListDeleteDialog from '../components/packing-list-delete-dialog';
import ContainerStatusImportDialog from '../components/ContainerStatusImportDialog';
import {
  PackingListProvider,
  usePackingListRecord,
} from './components/packing-list-context';

/**
 * The packing list reads like a user record: one toolbar over routed tabs.
 *
 * Same shape as `user-management/users/[id]` - Toolbar with prev/next, one primary action,
 * the occasional ones behind a gear, and a `TabsList variant="line"` whose tabs are ROUTES.
 * Routes rather than local state so a tab is linkable, survives a refresh and keeps its
 * place in the back button; the edit draft lives above them all in `PackingListProvider`,
 * because Edit is on this toolbar and Save is still ONE `PUT` for the whole record.
 */

/** `scm` marks a tab that is nothing but an SCM read: it is not offered to a user without
 *  `SCM_READ_PERMISSION`, and its page guards a deep link the same way (PL-TABS-ACCESS). */
const TAB_ORDER = [
  { key: 'details', title: 'Details', icon: Info, segment: '', scm: false },
  {
    key: 'proforma-invoices',
    title: 'Proforma invoices',
    icon: FileText,
    segment: 'proforma-invoices',
    scm: true,
  },
  { key: 'lines', title: 'Shipment lines', icon: Boxes, segment: 'lines', scm: false },
  { key: 'documents', title: 'Documents', icon: Files, segment: 'documents', scm: false },
  { key: 'spo', title: 'SPO planner', icon: FileSpreadsheet, segment: 'spo', scm: true },
  { key: 'timeline', title: 'Timeline', icon: History, segment: 'timeline', scm: false },
] as const;

/** What the old `?tab=` values mean now, so a link somebody saved still lands. */
const LEGACY_TAB_SEGMENT: Record<string, string> = {
  timeline: 'timeline',
  details: '',
  documents: 'documents',
  lines: 'lines',
  spo: 'spo',
};

function PackingListToolbar({ id }: { id: string }) {
  const router = useRouter();
  const backHref = useBackToListHref('/procurement-management/packing-lists');
  const {
    packingList,
    isLoading,
    canReadScm,
    lineSupplierNames,
    editing,
    saving,
    beginEdit,
    cancelEdit,
    saveEdit,
  } = usePackingListRecord();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [downloadsOpen, setDownloadsOpen] = useState(false);
  const queryClient = useQueryClient();

  /** E1/E2: "Download packing list" enqueues rather than downloads a blob straight from
   *  the click - same shape as a complaint's PDF export - and the file shows up in My
   *  Downloads / this record's own Download history instead. */
  const enqueueExport = useMutation({
    mutationFn: () => enqueuePackingListExport(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: MY_DOWNLOADS_QUERY_KEY });
      queryClient.invalidateQueries({
        queryKey: [...ENTITY_DOWNLOADS_QUERY_KEY, 'inbound_shipment', id],
      });
      toast.success('Added to My Downloads');
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const title =
    packingList?.shipping_container_number ||
    packingList?.shipment_number ||
    'Packing list';

  const subtitle = packingList
    ? [
        packingList.supplier?.supplier_name || lineSupplierNames || 'No supplier',
        packingList.shipment_date
          ? `Shipment date ${formatDate(new Date(packingList.shipment_date))}`
          : null,
      ]
        .filter(Boolean)
        .join(' • ')
    : '';

  return (
    <>
      <PageHeader
        title={isLoading ? <Skeleton className="h-6 w-48" /> : title}
        // The skeleton is a node, so the trail is told the container number
        // separately: otherwise it ends on "Packing Lists" while the record
        // loads and that crumb stops being a link.
        crumbTitle={title}
        actions={
          <BackToList
            listPath="/procurement-management/packing-lists"
            label="Back to packing lists"
          />
        }
      />
      {/* Read-only record metadata: under the header (which has no subtitle slot, PR
          #1336), never inside a tab body, so view and edit stay the same. */}
      <p className="text-sm text-muted-foreground break-words">{subtitle}</p>

      {/* The record's own actions: pager, gear, primary (D6). They sit under the
          toolbar rather than on it, and wrap under the title at 375. */}
      <div className="mb-5">
        {editing ? (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button onClick={() => void saveEdit()} disabled={saving}>
              Save packing list
            </Button>
            <Button variant="outline" onClick={cancelEdit} disabled={saving}>
              <X className="size-4" />
              Cancel
            </Button>
          </div>
        ) : (
          <DetailActions
            pager={{
              ...packingListsPagerQuery,
              detailPath: '/procurement-management/packing-lists',
              currentId: id,
              ariaLabel: 'packing list',
            }}
            gearLabel="Packing list options"
            gear={
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="icon" aria-label="Packing list options">
                    <Settings className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  {/* An SCM export: 403 without SCM read (PL-TABS-ACCESS). */}
                  {canReadScm && (
                    <DropdownMenuItem
                      onClick={() => enqueueExport.mutate()}
                      disabled={enqueueExport.isPending || !packingList}
                    >
                      <Download className="size-4" />
                      Download packing list
                    </DropdownMenuItem>
                  )}
                  <DropdownMenuItem onClick={() => setDownloadsOpen(true)}>
                    <History className="size-4" />
                    Download history
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setImportOpen(true)}>
                    <Upload className="size-4" />
                    Import Container Status workbook
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    variant="destructive"
                    onClick={() => setDeleteOpen(true)}
                    disabled={!packingList}
                  >
                    <Trash2 className="size-4" />
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            }
            primary={
              <Button onClick={beginEdit} disabled={!packingList}>
                <Edit className="size-4" />
                Edit
              </Button>
            }
          />
        )}
      </div>

      {packingList && (
        <PackingListDeleteDialog
          open={deleteOpen}
          closeDialog={() => setDeleteOpen(false)}
          packingList={packingList}
          onSuccess={() => router.push(backHref)}
        />
      )}
      <ContainerStatusImportDialog open={importOpen} onOpenChange={setImportOpen} />
      <EntityDownloadsButton
        entityType="inbound_shipment"
        entityId={id}
        label={title}
        open={downloadsOpen}
        onOpenChange={setDownloadsOpen}
      />
    </>
  );
}

function PackingListTabs({ id }: { id: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { packingList, isLoading, canReadScm } = usePackingListRecord();
  const base = `/procurement-management/packing-lists/${id}`;
  const tabs = TAB_ORDER.filter((t) => canReadScm || !t.scm);

  const active = useMemo(() => {
    const tail = pathname.startsWith(base) ? pathname.slice(base.length) : '';
    const segment = tail.replace(/^\//, '');
    return TAB_ORDER.find((t) => t.segment === segment)?.key ?? 'details';
  }, [pathname, base]);

  const lineCount = packingList?.shipment_lines?.length ?? 0;
  // Tabs are routes, so the click must carry the list position (page/sort/search)
  // the detail URL is holding, or the record pager falls back to "row 1" the
  // moment a tab is clicked (it reads that query string, not local state).
  const query = searchParams.toString();

  return (
    <Tabs value={active}>
      <TabsList variant="line" className="mb-5 w-full justify-start">
        {tabs.map(({ key, title, icon: Icon, segment }) => (
          <TabsTrigger
            key={key}
            value={key}
            disabled={isLoading}
            onClick={() => {
              const target = segment ? `${base}/${segment}` : base;
              router.push(query ? `${target}?${query}` : target);
            }}
          >
            <Icon />
            <span>{title}</span>
            {key === 'lines' && lineCount > 0 ? (
              <Badge variant="secondary" className="ms-2">
                {lineCount}
              </Badge>
            ) : null}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}

/** `?tab=lines` used to be how a tab was linked. Those links still land on the right one. */
function LegacyTabRedirect({ id }: { id: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const base = `/procurement-management/packing-lists/${id}`;

  useEffect(() => {
    const requested = searchParams.get('tab');
    if (!requested || pathname !== base) return;
    const segment = LEGACY_TAB_SEGMENT[requested];
    if (segment === undefined) return;
    const params = new URLSearchParams(searchParams.toString());
    params.delete('tab');
    const qs = params.toString();
    router.replace(`${segment ? `${base}/${segment}` : base}${qs ? `?${qs}` : ''}`);
  }, [searchParams, pathname, base, router]);

  return null;
}

export default function PackingListLayout({
  params,
  children,
}: {
  params: Promise<{ id: string }>;
  children: React.ReactNode;
}) {
  const { id } = use(params);

  return (
    <PackingListProvider packingListId={id}>
      <Container>
        <LegacyTabRedirect id={id} />
        <PackingListToolbar id={id} />
        <PackingListTabs id={id} />
        {children}
      </Container>
    </PackingListProvider>
  );
}
