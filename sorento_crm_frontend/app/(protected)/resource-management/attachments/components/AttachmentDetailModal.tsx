'use client';

import { useState, useEffect, useMemo } from 'react';
import Link from 'next/link';
import type { ColumnDef } from '@tanstack/react-table';
import {
  BadgeCheck,
  Boxes,
  ClipboardList,
  Download,
  ExternalLink,
  Eye,
  Info,
  Link2,
  LoaderCircleIcon,
  Megaphone,
  Package,
  Plug,
  Plus,
  RefreshCw,
  SlidersHorizontal,
  Trash2,
  Unlink,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { formatDate } from '@/lib/helpers';
import {
  useDownloadAttachment,
  useResubmitAttachmentWebhook,
  useRestoreAttachment,
  useUpdateAttachment,
} from '../hooks/useAttachments';
import { getAttachmentMetadata } from '../services/attachmentService';
import { attachmentCompanyLabel, type Attachment } from '../types/attachment.types';
import type { LinkedEntityRef } from '../types/attachment.types';
import RecordNavigation from '@/components/common/RecordNavigation';
import AttachmentDeleteDialog from './attachment-delete-dialog';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import ManageFieldLinksDialog from './ManageFieldLinksDialog';
import EditAttachmentTypeDialog from './EditAttachmentTypeDialog';
import SetCompanyDialog from './SetCompanyDialog';
import {
  IntegrationPanel,
  IntegrationStatusChip,
} from '@/components/upload-activity';
import { useCreateProductAttachment, useDeleteProductAttachment } from '@/app/(protected)/master-data-management/product-attachments/hooks/useProductAttachments';
import { useCreatePromotionAttachment, useDeletePromotionAttachment } from '@/app/(protected)/marketing-management/promotion-attachments/hooks/usePromotionAttachments';
import { useForms } from '@/app/(protected)/forms-management/forms/hooks/useForms';
import { useUpdateForm } from '@/app/(protected)/forms-management/forms/hooks/useForms';
import { getProducts } from '@/app/(protected)/master-data-management/products/services/productService';
import { getPromotions } from '@/app/(protected)/marketing-management/promotions/services/promotionService';
import { getPackingLists } from '@/app/(protected)/procurement-management/packing-lists/services/packingListService';
import { linkAttachmentToPackingList, deleteAttachmentLink, unlinkPackingListFromAttachment } from '../services/attachmentService';
import { useContactAccessTypes } from '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { AccessLevelsMultiSelect } from './AccessLevelsMultiSelect';

const ENTITY_ROUTES = {
  product: { label: 'Product', path: '/master-data-management/products' },
  promotion: { label: 'Promotion', path: '/marketing-management/promotions' },
  form: { label: 'Form', path: '/forms-management/forms' },
  packing_list: { label: 'Packing List', path: '/procurement-management/packing-lists' },
  certificate: { label: 'Certificate', path: '/master-data-management/certificates' },
} as const;

function LinkagesTable({
  type,
  items,
  emptyMessage,
  onUnlink,
  onLink,
  attachmentId,
  refetch,
}: {
  type: keyof typeof ENTITY_ROUTES;
  items: LinkedEntityRef[];
  emptyMessage: string;
  onUnlink?: (item: LinkedEntityRef) => void;
  onLink?: () => void;
  attachmentId: string;
  refetch: () => void;
}) {
  const hasLinkId = type === 'product' || type === 'promotion';
  const canUnlink = hasLinkId ? (item: LinkedEntityRef) => !!item.link_id : () => true;
  const canUnlinkPackingList = true;
  const [manageFieldLinksFor, setManageFieldLinksFor] = useState<string | null>(null);

  const columns = useMemo<ColumnDef<LinkedEntityRef>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => {
          // Only a SHARED attachment's rows carry a company (R3, S5): a
          // single-company attachment's items have no company_name and
          // render exactly as before, no badges anywhere in the row.
          const outOfScope = row.original.in_scope === false;
          return (
            <span className="inline-flex items-center gap-1.5 font-medium">
              {outOfScope ? (
                <span className="text-muted-foreground">{row.original.name}</span>
              ) : (
                row.original.name
              )}
              {row.original.company_name && (
                <Badge appearance="light" size="sm">
                  {row.original.company_name}
                </Badge>
              )}
            </span>
          );
        },
        size: 220,
        meta: { headerTitle: 'Name' },
      },
      {
        id: 'description',
        accessorFn: (row) => row.description ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        cell: ({ row }) => (
          <span
            className="text-muted-foreground block max-w-md line-clamp-2"
            title={row.original.description ?? undefined}
          >
            {row.original.description ?? '-'}
          </span>
        ),
        size: 280,
        meta: { headerTitle: 'Description' },
      },
      {
        id: 'action',
        header: () => <span className="sr-only">Action</span>,
        cell: ({ row }) => {
          const item = row.original;
          const outOfScope = item.in_scope === false;
          if (outOfScope) return <span className="text-xs text-muted-foreground">-</span>;
          return (
            <div className="flex items-center gap-2">
              <Link
                href={`${ENTITY_ROUTES[type].path}/${item.id}`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline inline-flex items-center gap-1 text-sm"
                onClick={(e) => e.stopPropagation()}
              >
                View
                <ExternalLink className="size-3.5 shrink-0" />
              </Link>
              {type === 'product' && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setManageFieldLinksFor(item.id);
                  }}
                  title="Manage field links"
                  data-testid={`linkages-manage-field-links-${item.id}`}
                >
                  <SlidersHorizontal className="size-3.5" />
                  Fields
                </Button>
              )}
              {onUnlink && (type === 'packing_list' ? canUnlinkPackingList : canUnlink(item)) && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  onClick={(e) => {
                    e.stopPropagation();
                    onUnlink(item);
                  }}
                >
                  <Unlink className="size-3.5" />
                  Unlink
                </Button>
              )}
            </div>
          );
        },
        size: 220,
        enableResizing: false,
        meta: { headerTitle: 'Action' },
      },
    ],
    // canUnlink/canUnlinkPackingList are pure functions of `type`, already a dep -
    // omitted here for the same reason `type` alone already drives their behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [type, onUnlink],
  );

  return (
    <div className="space-y-3">
      <PanelDataGrid<LinkedEntityRef>
        columns={columns}
        rows={items}
        getRowId={(row) => row.id}
        listingKey={`resource.attachments.view::modal-linkages-${type}`}
        emptyTitle={emptyMessage}
        // SF-1 (M5 run 3 review): this grid renders inside DialogContent,
        // which caps its own height and scrolls its body, so the grid must
        // not add a SECOND bounded scrollport nested inside it - same defect
        // db7718a29 fixed elsewhere.
        scrollerMaxHeight={false}
        toolbar={
          onLink ? (
            <Button variant="outline" size="sm" onClick={onLink}>
              <Plus className="size-4" />
              Link
            </Button>
          ) : undefined
        }
      />
      {type === 'product' && manageFieldLinksFor && (
        <ManageFieldLinksDialog
          open={!!manageFieldLinksFor}
          onOpenChange={(open) => !open && setManageFieldLinksFor(null)}
          productId={manageFieldLinksFor}
          attachmentId={attachmentId}
          onSaved={() => {
            setManageFieldLinksFor(null);
            refetch();
          }}
        />
      )}
    </div>
  );
}

function LinkagesTabs({
  attachment,
  onRefetch,
  defaultAccessLevels = ['dealer', 'end_user'],
}: {
  attachment: Attachment;
  onRefetch: () => void;
  defaultAccessLevels?: string[];
}) {
  const queryClient = useQueryClient();
  const [linkDialogTab, setLinkDialogTab] = useState<keyof typeof ENTITY_ROUTES | null>(null);
  const [linkProductId, setLinkProductId] = useState('');
  const [linkPromotionId, setLinkPromotionId] = useState('');
  const [linkFormId, setLinkFormId] = useState('');
  const [linkPackingListId, setLinkPackingListId] = useState('');

  const products = attachment.linked_products ?? [];
  const promotions = attachment.linked_promotions ?? [];
  const form = attachment.linked_form ?? null;
  const packingLists = attachment.linked_packing_lists ?? [];
  // Read-only: a file is linked to a certificate by BEING one of its filed
  // revisions, so there is nothing here for a user to attach or detach -
  // unlinking would leave the revision with no document behind it.
  const certificates = attachment.linked_certificates ?? [];

  const createProductLink = useCreateProductAttachment();
  const deleteProductLink = useDeleteProductAttachment();
  const createPromotionLink = useCreatePromotionAttachment();
  const deletePromotionLink = useDeletePromotionAttachment();
  const updateFormMutation = useUpdateForm();

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['attachment-metadata', attachment.id] });
    queryClient.invalidateQueries({ queryKey: ['attachments'] });
    queryClient.invalidateQueries({ queryKey: ['product-attachments'] });
    queryClient.invalidateQueries({ queryKey: ['promotion-attachments'] });
    queryClient.invalidateQueries({ queryKey: ['forms'] });
    onRefetch();
  };

  const handleUnlinkPackingList = (item: LinkedEntityRef) => {
    const promise = item.link_id
      ? deleteAttachmentLink(item.link_id, 'inbound_shipment')
      : unlinkPackingListFromAttachment(attachment.id, item.id);
    void promise.then(() => invalidate());
  };

  const [linkPackingListPending, setLinkPackingListPending] = useState(false);
  const handleLinkPackingList = () => {
    if (!linkPackingListId || !attachment.id) return;
    setLinkPackingListPending(true);
    linkAttachmentToPackingList(attachment.id, linkPackingListId)
      .then(() => {
        invalidate();
        setLinkDialogTab(null);
        setLinkPackingListId('');
      })
      .finally(() => setLinkPackingListPending(false));
  };

  const handleUnlinkProduct = (item: LinkedEntityRef) => {
    if (!item.link_id) return;
    deleteProductLink.mutate(item.link_id, {
      onSuccess: () => invalidate(),
    });
  };

  const handleUnlinkPromotion = (item: LinkedEntityRef) => {
    if (!item.link_id) return;
    deletePromotionLink.mutate(item.link_id, {
      onSuccess: () => invalidate(),
    });
  };

  const handleUnlinkForm = (item: LinkedEntityRef) => {
    updateFormMutation.mutate(
      { id: item.id, data: { attachment_id: null } },
      { onSuccess: () => invalidate() }
    );
  };

  const handleLinkProduct = () => {
    if (!linkProductId || !attachment.id) return;
    createProductLink.mutate(
      { product_id: linkProductId, attachment_id: attachment.id, access_levels: defaultAccessLevels },
      {
        onSuccess: () => {
          invalidate();
          setLinkDialogTab(null);
          setLinkProductId('');
        },
      }
    );
  };

  const handleLinkPromotion = () => {
    if (!linkPromotionId || !attachment.id) return;
    createPromotionLink.mutate(
      { promotion_id: linkPromotionId, attachment_id: attachment.id },
      {
        onSuccess: () => {
          invalidate();
          setLinkDialogTab(null);
          setLinkPromotionId('');
        },
      }
    );
  };

  const handleLinkForm = () => {
    if (!linkFormId || !attachment.id) return;
    updateFormMutation.mutate(
      { id: linkFormId, data: { attachment_id: attachment.id } },
      {
        onSuccess: () => {
          invalidate();
          setLinkDialogTab(null);
          setLinkFormId('');
        },
      }
    );
  };

  return (
    <>
      <Tabs defaultValue="products" className="w-full">
        {/* Five link kinds wrapped onto three rows of squeezed pills at 375; the
            line strip scrolls on one row and the icon survives the scroll. */}
        <TabsList className="mb-1">
          <TabsTrigger value="products">
            <Package />
            <span>Products {products.length > 0 && `(${products.length})`}</span>
          </TabsTrigger>
          <TabsTrigger value="promotions">
            <Megaphone />
            <span>Promotions {promotions.length > 0 && `(${promotions.length})`}</span>
          </TabsTrigger>
          <TabsTrigger value="forms">
            <ClipboardList />
            <span>Forms {form ? '(1)' : ''}</span>
          </TabsTrigger>
          <TabsTrigger value="packing_lists">
            <Boxes />
            <span>Packing Lists {packingLists.length > 0 && `(${packingLists.length})`}</span>
          </TabsTrigger>
          <TabsTrigger value="certificates">
            <BadgeCheck />
            <span>Certificates {certificates.length > 0 && `(${certificates.length})`}</span>
          </TabsTrigger>
        </TabsList>
        <TabsContent value="products" className="mt-4">
          <LinkagesTable
            type="product"
            items={products}
            emptyMessage="No products linked to this attachment."
            onUnlink={handleUnlinkProduct}
            onLink={() => setLinkDialogTab('product')}
            attachmentId={attachment.id}
            refetch={onRefetch}
          />
        </TabsContent>
        <TabsContent value="promotions" className="mt-4">
          <LinkagesTable
            type="promotion"
            items={promotions}
            emptyMessage="No promotions linked to this attachment."
            onUnlink={handleUnlinkPromotion}
            onLink={() => setLinkDialogTab('promotion')}
            attachmentId={attachment.id}
            refetch={onRefetch}
          />
        </TabsContent>
        <TabsContent value="forms" className="mt-4">
          <LinkagesTable
            type="form"
            items={form ? [form] : []}
            emptyMessage="No form linked to this attachment."
            onUnlink={form ? handleUnlinkForm : undefined}
            onLink={!form ? () => setLinkDialogTab('form') : undefined}
            attachmentId={attachment.id}
            refetch={onRefetch}
          />
        </TabsContent>
        <TabsContent value="packing_lists" className="mt-4">
          <LinkagesTable
            type="packing_list"
            items={packingLists}
            emptyMessage="No packing lists linked to this attachment."
            onUnlink={handleUnlinkPackingList}
            onLink={() => setLinkDialogTab('packing_list')}
            attachmentId={attachment.id}
            refetch={onRefetch}
          />
        </TabsContent>
        <TabsContent value="certificates" className="mt-4">
          {/* No onLink / onUnlink on purpose - see the `certificates` comment
              above. A file becomes a certificate by being filed as one. */}
          <LinkagesTable
            type="certificate"
            items={certificates}
            emptyMessage="This file is not filed as a certificate."
            attachmentId={attachment.id}
            refetch={onRefetch}
          />
        </TabsContent>
      </Tabs>

      {/* Link dialogs */}
      <Dialog open={linkDialogTab === 'product'} onOpenChange={(o) => !o && setLinkDialogTab(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Link Product</DialogTitle>
          </DialogHeader>
          <LinkProductForm
            attachmentId={attachment.id}
            linkedProductIds={products.map((p) => p.id)}
            selectedId={linkProductId}
            onSelect={setLinkProductId}
            onSubmit={handleLinkProduct}
            onCancel={() => setLinkDialogTab(null)}
            isPending={createProductLink.isPending}
          />
        </DialogContent>
      </Dialog>
      <Dialog open={linkDialogTab === 'promotion'} onOpenChange={(o) => !o && setLinkDialogTab(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Link Promotion</DialogTitle>
          </DialogHeader>
          <LinkPromotionForm
            attachmentId={attachment.id}
            linkedPromotionIds={promotions.map((p) => p.id)}
            selectedId={linkPromotionId}
            onSelect={setLinkPromotionId}
            onSubmit={handleLinkPromotion}
            onCancel={() => setLinkDialogTab(null)}
            isPending={createPromotionLink.isPending}
          />
        </DialogContent>
      </Dialog>
      <Dialog open={linkDialogTab === 'form'} onOpenChange={(o) => !o && setLinkDialogTab(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Link Form</DialogTitle>
          </DialogHeader>
          <LinkFormForm
            attachmentId={attachment.id}
            selectedId={linkFormId}
            onSelect={setLinkFormId}
            onSubmit={handleLinkForm}
            onCancel={() => setLinkDialogTab(null)}
            isPending={updateFormMutation.isPending}
          />
        </DialogContent>
      </Dialog>
      <Dialog open={linkDialogTab === 'packing_list'} onOpenChange={(o) => !o && setLinkDialogTab(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Link Packing List</DialogTitle>
          </DialogHeader>
          <LinkPackingListForm
            attachmentId={attachment.id}
            linkedPackingListIds={packingLists.map((p) => p.id)}
            selectedId={linkPackingListId}
            onSelect={setLinkPackingListId}
            onSubmit={handleLinkPackingList}
            onCancel={() => setLinkDialogTab(null)}
            isPending={linkPackingListPending}
          />
        </DialogContent>
      </Dialog>
    </>
  );
}

function LinkProductForm({
  attachmentId,
  linkedProductIds,
  selectedId,
  onSelect,
  onSubmit,
  onCancel,
  isPending,
}: {
  attachmentId: string;
  linkedProductIds: string[];
  selectedId: string;
  onSelect: (id: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const { setValue: setSearch, debouncedValue: debounced } = useDebouncedSearch();
  const [pickedLabel, setPickedLabel] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedId) setPickedLabel(null);
  }, [selectedId]);
  const { data, isFetching } = useQuery({
    queryKey: ['products-for-link', attachmentId, debounced],
    queryFn: () => getProducts({ pageIndex: 0, pageSize: 25, sorting: [], searchQuery: debounced }),
  });
  const products = (data?.data ?? []).filter((p) => !linkedProductIds.includes(p.id));
  const selectedFromList = products.find((p) => p.id === selectedId);
  const selectedLabel = selectedFromList
    ? (selectedFromList.product_name || selectedFromList.product_code || selectedFromList.id)
    : pickedLabel;
  const options = products.map((p) => ({
    value: p.id,
    label: p.product_name || p.product_code || p.id,
    searchText: [p.product_name, p.product_code, p.id].filter(Boolean).join(' '),
  }));
  if (selectedId && selectedLabel && !options.some((o) => o.value === selectedId)) {
    options.unshift({ value: selectedId, label: selectedLabel, searchText: selectedLabel });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Product</Label>
        <SearchableSelect
          value={selectedId}
          onChange={(id) => {
            onSelect(id);
            const p = products.find((x) => x.id === id);
            if (p) setPickedLabel(p.product_name || p.product_code || p.id);
          }}
          options={options}
          onSearchChange={setSearch}
          placeholder="Select a product..."
          emptyMessage={isFetching ? 'Searching…' : 'No product found.'}
          triggerClassName="w-full"
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={onSubmit} disabled={!selectedId || isPending}>
          {isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : <Link2 className="size-4" />}
          Link
        </Button>
      </div>
    </div>
  );
}

function LinkPromotionForm({
  attachmentId,
  linkedPromotionIds,
  selectedId,
  onSelect,
  onSubmit,
  onCancel,
  isPending,
}: {
  attachmentId: string;
  linkedPromotionIds: string[];
  selectedId: string;
  onSelect: (id: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const { setValue: setSearch, debouncedValue: debounced } = useDebouncedSearch();
  const [pickedLabel, setPickedLabel] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedId) setPickedLabel(null);
  }, [selectedId]);
  const { data, isFetching } = useQuery({
    queryKey: ['promotions-for-link', attachmentId, debounced],
    queryFn: () => getPromotions({ pageIndex: 0, pageSize: 25, sorting: [], searchQuery: debounced }),
  });
  const promotions = (data?.data ?? []).filter((p) => !linkedPromotionIds.includes(p.id));
  const selectedFromList = promotions.find((p) => p.id === selectedId);
  const selectedLabel = selectedFromList
    ? (selectedFromList.description || selectedFromList.id)
    : pickedLabel;
  const options = promotions.map((p) => ({
    value: p.id,
    label: p.description || p.id,
  }));
  if (selectedId && selectedLabel && !options.some((o) => o.value === selectedId)) {
    options.unshift({ value: selectedId, label: selectedLabel });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Promotion</Label>
        <SearchableSelect
          value={selectedId}
          onChange={(id) => {
            onSelect(id);
            const p = promotions.find((x) => x.id === id);
            if (p) setPickedLabel(p.description || p.id);
          }}
          options={options}
          onSearchChange={setSearch}
          placeholder="Select a promotion..."
          emptyMessage={isFetching ? 'Searching…' : 'No promotion found.'}
          triggerClassName="w-full"
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={onSubmit} disabled={!selectedId || isPending}>
          {isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : <Link2 className="size-4" />}
          Link
        </Button>
      </div>
    </div>
  );
}

function LinkFormForm({
  attachmentId,
  selectedId,
  onSelect,
  onSubmit,
  onCancel,
  isPending,
}: {
  attachmentId: string;
  selectedId: string;
  onSelect: (id: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const { setValue: setSearch, debouncedValue: debounced } = useDebouncedSearch();
  const [pickedLabel, setPickedLabel] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedId) setPickedLabel(null);
  }, [selectedId]);
  const { data, isFetching } = useForms({ pageIndex: 0, pageSize: 25, sorting: [], searchQuery: debounced });
  const forms = (data?.data ?? []).filter((f) => f.attachment_id !== attachmentId);
  const selectedFromList = forms.find((f) => f.id === selectedId);
  const selectedLabel = selectedFromList
    ? (selectedFromList.name || selectedFromList.code || selectedFromList.id)
    : pickedLabel;
  const options = forms.map((f) => ({
    value: f.id,
    label: f.name || f.code || f.id,
    searchText: [f.name, f.code, f.id].filter(Boolean).join(' '),
  }));
  if (selectedId && selectedLabel && !options.some((o) => o.value === selectedId)) {
    options.unshift({ value: selectedId, label: selectedLabel, searchText: selectedLabel });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Form</Label>
        <SearchableSelect
          value={selectedId}
          onChange={(id) => {
            onSelect(id);
            const f = forms.find((x) => x.id === id);
            if (f) setPickedLabel(f.name || f.code || f.id);
          }}
          options={options}
          onSearchChange={setSearch}
          placeholder="Select a form..."
          emptyMessage={isFetching ? 'Searching…' : 'No form found.'}
          triggerClassName="w-full"
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={onSubmit} disabled={!selectedId || isPending}>
          {isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : <Link2 className="size-4" />}
          Link
        </Button>
      </div>
    </div>
  );
}

function LinkPackingListForm({
  attachmentId,
  linkedPackingListIds,
  selectedId,
  onSelect,
  onSubmit,
  onCancel,
  isPending,
}: {
  attachmentId: string;
  linkedPackingListIds: string[];
  selectedId: string;
  onSelect: (id: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  isPending: boolean;
}) {
  const { setValue: setSearch, debouncedValue: debounced } = useDebouncedSearch();
  const [pickedLabel, setPickedLabel] = useState<string | null>(null);
  useEffect(() => {
    if (!selectedId) setPickedLabel(null);
  }, [selectedId]);
  const { data, isFetching } = useQuery({
    queryKey: ['packing-lists-for-link', attachmentId, debounced],
    queryFn: () => getPackingLists({ pageIndex: 0, pageSize: 25, sorting: [], searchQuery: debounced }),
  });
  const packingLists = (data?.data ?? []).filter((p) => !linkedPackingListIds.includes(p.id));
  const selectedFromList = packingLists.find((p) => p.id === selectedId);
  const selectedLabel = selectedFromList
    ? (selectedFromList.shipment_number ||
        selectedFromList.shipping_container_number ||
        selectedFromList.id)
    : pickedLabel;
  const options = packingLists.map((p) => ({
    value: p.id,
    label: p.shipment_number || p.shipping_container_number || p.id,
    searchText: [p.shipment_number, p.shipping_container_number, p.id]
      .filter(Boolean)
      .join(' '),
  }));
  if (selectedId && selectedLabel && !options.some((o) => o.value === selectedId)) {
    options.unshift({ value: selectedId, label: selectedLabel, searchText: selectedLabel });
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label>Packing List</Label>
        <SearchableSelect
          value={selectedId}
          onChange={(id) => {
            onSelect(id);
            const p = packingLists.find((x) => x.id === id);
            if (p) setPickedLabel(p.shipment_number || p.shipping_container_number || p.id);
          }}
          options={options}
          onSearchChange={setSearch}
          placeholder="Select a packing list..."
          emptyMessage={isFetching ? 'Searching…' : 'No packing list found.'}
          triggerClassName="w-full"
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button onClick={onSubmit} disabled={!selectedId || isPending}>
          {isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : <Link2 className="size-4" />}
          Link
        </Button>
      </div>
    </div>
  );
}

interface AttachmentDetailModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  attachmentId: string | null;
  /** Current page attachments for prev/next navigation. When provided with onAttachmentChange, chevron nav is shown. */
  neighbourItems?: Array<{ id: string }>;
  /** Called when user clicks prev/next to switch attachment in modal. */
  onAttachmentChange?: (id: string) => void;
}

export default function AttachmentDetailModal({
  open,
  onOpenChange,
  attachmentId,
  neighbourItems = [],
  onAttachmentChange,
}: AttachmentDetailModalProps) {
  // Where the open attachment sits in the page the modal was opened from.
  const modalIndex = attachmentId
    ? neighbourItems.findIndex((item) => item.id === attachmentId)
    : -1;
  const queryClient = useQueryClient();
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [descriptionEdit, setDescriptionEdit] = useState<string | null>(null);
  const [accessLevelsEdit, setAccessLevelsEdit] = useState<string[] | null>(null);
  const [editAttachmentTypeOpen, setEditAttachmentTypeOpen] = useState(false);
  const [setCompanyOpen, setSetCompanyOpen] = useState(false);
  // Tab inside the top card - Attachment Details vs. Integration. Default
  // to "details" since that's the primary surface for the modal.
  const [detailsTab, setDetailsTab] = useState<'details' | 'integration'>(
    'details',
  );

  const {
    data: accessTypeOptions = [],
    error: accessTypesError,
    refetch: refetchAccessTypes,
  } = useContactAccessTypes();
  const defaultAccessLevels = accessTypeOptions.length > 0 ? accessTypeOptions.map((o) => o.code) : ['dealer', 'end_user'];
  const codeToName = Object.fromEntries(accessTypeOptions.map((o) => [o.code, o.name || o.code]));

  const { data: attachment, isLoading } = useQuery({
    queryKey: ['attachment-metadata', attachmentId],
    queryFn: () => getAttachmentMetadata(attachmentId!),
    enabled: !!attachmentId && open,
    retry: 1,
  });

  useEffect(() => {
    setDescriptionEdit(null);
    setAccessLevelsEdit(null);
  }, [attachmentId]);

  const downloadMutation = useDownloadAttachment();
  const resubmitMutation = useResubmitAttachmentWebhook();
  const restoreMutation = useRestoreAttachment();
  const updateMutation = useUpdateAttachment();

  const formatFileSize = (bytes: number | null | undefined) => {
    if (!bytes) return '-';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  const handleDownload = async (file: Attachment) => {
    try {
      const blob = await downloadMutation.mutateAsync(file.id);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = file.original_filename || 'download';
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch {
      // handled by toast
    }
  };

  const handlePreview = () => {
    setPreviewOpen(true);
  };

  const previewItems = useMemo<AttachmentPreviewItem[]>(() => {
    if (!attachment) return [];
    const fp = attachment.file_path || '';
    const cdn = fp.startsWith('http') ? fp : '';
    return [
      {
        id: attachment.id,
        name: attachment.original_filename,
        url: cdn,
        downloadUrl:
          typeof window !== 'undefined'
            ? `/api/v1/resource-management/attachments/${attachment.id}/download`
            : undefined,
        sizeBytes: attachment.file_size_bytes,
      },
    ];
  }, [attachment]);

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-2xl max-h-[90vh] flex flex-col overflow-hidden p-0">
          <DialogHeader className="px-6 pt-6 pb-0 pr-12 flex flex-row items-center justify-between gap-4">
            <DialogTitle className="text-xl truncate min-w-0 flex-1">
              {attachment?.original_filename ??
                (isLoading ? (
                  // A `<h2>` (DialogTitle's element) may only hold phrasing content, so
                  // this is `Skeleton` rendered `asChild` onto a `span` (M5-01/M5-02
                  // review N1) rather than the shared `div` - same bar, still carries
                  // `Skeleton`'s own `data-slot="skeleton"`.
                  <Skeleton asChild className="inline-block h-5 w-48 align-middle">
                    <span />
                  </Skeleton>
                ) : (
                  'Attachment'
                ))}
            </DialogTitle>
            {neighbourItems.length > 0 && onAttachmentChange && attachmentId && (
              <RecordNavigation
                index={modalIndex + 1}
                total={neighbourItems.length}
                hasPrevious={modalIndex > 0}
                hasNext={modalIndex >= 0 && modalIndex < neighbourItems.length - 1}
                onPrevious={() => onAttachmentChange!(neighbourItems[modalIndex - 1].id)}
                onNext={() => onAttachmentChange!(neighbourItems[modalIndex + 1].id)}
                ariaLabel="attachment"
              />
            )}
          </DialogHeader>
          <div className="flex-1 min-h-0 overflow-y-auto px-6 pb-6">
            {isLoading ? (
              <div className="space-y-4 py-6">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-32 w-full" />
                <Skeleton className="h-32 w-full" />
              </div>
            ) : !attachment ? (
              <p className="py-8 text-muted-foreground text-center">Attachment not found</p>
            ) : (
              <div className="space-y-6 py-6">
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handlePreview}
                    title="Preview"
                  >
                    <Eye className="size-4" />
                    Preview
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleDownload(attachment)}
                    disabled={downloadMutation.isPending}
                  >
                    <Download className="size-4" />
                    Download
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => resubmitMutation.mutate(attachment.id)}
                    disabled={resubmitMutation.isPending || attachment.is_deleted}
                  >
                    <RefreshCw className={`size-4 ${resubmitMutation.isPending ? 'animate-spin' : ''}`} />
                    Resubmit
                  </Button>
                  {attachment.is_deleted ? (
                    <>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => restoreMutation.mutate(attachment.id)}
                        disabled={restoreMutation.isPending}
                      >
                        Restore
                      </Button>
                      <Button variant="destructive" size="sm" onClick={() => setDeleteDialogOpen(true)}>
                        <Trash2 className="size-4" />
                        Permanently Delete
                      </Button>
                    </>
                  ) : (
                    <Button variant="destructive" size="sm" onClick={() => setDeleteDialogOpen(true)}>
                      <Trash2 className="size-4" />
                      Move to Trash
                    </Button>
                  )}
                  <div className="ms-auto">
                    <IntegrationStatusChip
                      attachmentId={attachment.id}
                      attachment={attachment}
                    />
                  </div>
                </div>

                <Card>
                  <Tabs
                    value={detailsTab}
                    onValueChange={(v) =>
                      setDetailsTab(v as 'details' | 'integration')
                    }
                  >
                    <CardHeader className="py-3 pb-0">
                      <TabsList className="px-0">
                        <TabsTrigger value="details">
                          <Info />
                          <span>Attachment Details</span>
                        </TabsTrigger>
                        <TabsTrigger value="integration">
                          <Plug />
                          <span>Integration</span>
                        </TabsTrigger>
                      </TabsList>
                    </CardHeader>
                    <CardContent className="pt-4">
                      <TabsContent value="integration" className="mt-0">
                        <IntegrationPanel
                          attachmentId={attachment.id}
                          attachment={attachment}
                        />
                      </TabsContent>
                      <TabsContent value="details" className="mt-0 space-y-4">
                        <div>
                          <p className="text-sm text-muted-foreground">Directory</p>
                      <p className="font-medium text-sm break-words">
                        {attachment.full_directory_path?.trim() || '-'}
                      </p>
                      {attachment.directory_id ? (
                        <a
                          href={`/resource-management/attachment-directories?directoryId=${attachment.directory_id}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-primary hover:underline text-xs mt-1 inline-block"
                        >
                          Open folder →
                        </a>
                      ) : null}
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <p className="text-sm text-muted-foreground">File Type</p>
                        <p className="font-medium">{attachment.mime_type || '-'}</p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Attachment Type</p>
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-medium">
                            {attachment.attachment_type?.type_name ?? '-'}
                          </p>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-primary"
                            onClick={() => setEditAttachmentTypeOpen(true)}
                            data-testid="attachment-type-edit"
                          >
                            Edit
                          </Button>
                        </div>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">File Size</p>
                        <p className="font-medium">{formatFileSize(attachment.file_size_bytes)}</p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Uploaded By</p>
                        <p className="font-medium">
                          {attachment.uploaded_by_user?.name ??
                            attachment.uploaded_by_user?.email ??
                            attachment.uploaded_by ??
                            '-'}
                        </p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Company</p>
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-medium">{attachmentCompanyLabel(attachment)}</p>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-primary"
                            onClick={() => setSetCompanyOpen(true)}
                            data-testid="attachment-company-edit"
                          >
                            Edit
                          </Button>
                        </div>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Uploaded</p>
                        <p className="font-medium">{formatDate(new Date(attachment.uploaded_at))}</p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Status</p>
                        <Badge
                          variant={attachment.is_deleted ? 'destructive' : 'success'}
                        >
                          <BadgeDot />
                          {attachment.is_deleted ? 'Deleted' : 'Active'}
                        </Badge>
                      </div>
                    </div>
                    <div className="space-y-2">
                      <Label className="text-sm text-muted-foreground">Description</Label>
                      {descriptionEdit !== null ? (
                        <div className="space-y-2">
                          <textarea
                            className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                            value={descriptionEdit}
                            onChange={(e) => setDescriptionEdit(e.target.value)}
                            placeholder="Add a description..."
                          />
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              onClick={() => {
                                updateMutation.mutate(
                                  { attachmentId: attachment.id, data: { description: descriptionEdit || null } },
                                  {
                                    onSuccess: () => {
                                      queryClient.invalidateQueries({ queryKey: ['attachment-metadata', attachment.id] });
                                      setDescriptionEdit(null);
                                    },
                                  }
                                );
                              }}
                              disabled={updateMutation.isPending}
                            >
                              {updateMutation.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : 'Save'}
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => setDescriptionEdit(null)}>
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-start justify-between gap-2">
                          <p className="font-medium text-sm min-h-[1.5rem]">
                            {attachment.description?.trim() || '-'}
                          </p>
                          <Button variant="ghost" size="sm" onClick={() => setDescriptionEdit(attachment.description ?? '')}>
                            {attachment.description?.trim() ? 'Edit' : 'Add'}
                          </Button>
                        </div>
                      )}
                    </div>
                    <div className="space-y-2">
                      <Label className="text-sm text-muted-foreground">Access levels</Label>
                      {accessLevelsEdit !== null ? (
                        <div className="space-y-2">
                          <AccessLevelsMultiSelect
                            options={accessTypeOptions}
                            loadError={accessTypesError}
                            onRetry={() => void refetchAccessTypes()}
                            value={accessLevelsEdit}
                            onChange={setAccessLevelsEdit}
                          />
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              onClick={() => {
                                const levels = accessLevelsEdit?.length ? accessLevelsEdit : defaultAccessLevels;
                                updateMutation.mutate(
                                  { attachmentId: attachment.id, data: { access_levels: levels } },
                                  {
                                    onSuccess: () => {
                                      queryClient.invalidateQueries({ queryKey: ['attachment-metadata', attachment.id] });
                                      setAccessLevelsEdit(null);
                                    },
                                  }
                                );
                              }}
                              disabled={updateMutation.isPending}
                            >
                              {updateMutation.isPending ? <LoaderCircleIcon className="size-4 animate-spin" /> : 'Save'}
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => setAccessLevelsEdit(null)}>
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex flex-wrap gap-2">
                            {(attachment.access_levels ?? []).length === 0 ? (
                              <span className="text-sm text-muted-foreground"> - </span>
                            ) : (
                              (attachment.access_levels ?? []).map((level) => (
                                <Badge key={level} variant="secondary">
                                  {codeToName[level] ?? level}
                                </Badge>
                              ))
                            )}
                          </div>
                          <Button variant="ghost" size="sm" onClick={() => setAccessLevelsEdit(attachment.access_levels ?? defaultAccessLevels)}>
                            Edit
                          </Button>
                        </div>
                      )}
                    </div>
                      </TabsContent>
                    </CardContent>
                  </Tabs>
                </Card>

                <Card>
                  <CardHeader className="py-4">
                    <CardTitle className="text-base">Linkages</CardTitle>
                  </CardHeader>
                  <CardContent className="pt-0">
                    <LinkagesTabs attachment={attachment} onRefetch={() => queryClient.invalidateQueries({ queryKey: ['attachments'] })} defaultAccessLevels={defaultAccessLevels} />
                  </CardContent>
                </Card>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      <AttachmentPreviewModal
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        items={previewItems}
      />

      {attachment && (
        <AttachmentDeleteDialog
          open={deleteDialogOpen}
          onOpenChange={setDeleteDialogOpen}
          attachment={attachment}
          permanent={attachment.is_deleted}
          onSuccess={() => {
            onOpenChange(false);
            queryClient.invalidateQueries({ queryKey: ['attachments'] });
          }}
        />
      )}
      {attachment && (
        <EditAttachmentTypeDialog
          open={editAttachmentTypeOpen}
          onOpenChange={setEditAttachmentTypeOpen}
          attachmentIds={[attachment.id]}
          initialAttachmentTypeId={attachment.attachment_type_id ?? null}
          onSaved={() => {
            queryClient.invalidateQueries({ queryKey: ['attachment-metadata', attachment.id] });
            queryClient.invalidateQueries({ queryKey: ['attachments'] });
          }}
        />
      )}
      {attachment && setCompanyOpen && (
        <SetCompanyDialog
          open={setCompanyOpen}
          onOpenChange={setSetCompanyOpen}
          fileIds={[attachment.id]}
          folderIds={[]}
        />
      )}
    </>
  );
}
