'use client';

import { useState, useMemo } from 'react';
import type { ColumnDef } from '@tanstack/react-table';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  BadgeCheck,
  Boxes,
  ClipboardList,
  ExternalLink,
  Eye,
  Megaphone,
  Package,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { useQueryClient } from '@tanstack/react-query';
import { LoaderCircleIcon } from 'lucide-react';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { formatDate } from '@/lib/helpers';
import DetailActions from '@/components/common/DetailActions';
import {
  useDeleteAttachment,
  useUpdateAttachment,
  attachmentsPagerQuery,
} from '../hooks/useAttachments';
import { AccessLevelsMultiSelect } from './AccessLevelsMultiSelect';
import { getAttachmentMetadata } from '../services/attachmentService';
import { attachmentCompanyLabel, type Attachment, type LinkedEntityRef } from '../types/attachment.types';
import SetCompanyDialog from './SetCompanyDialog';
import { useAttachmentActions } from '../actions';
import { useContactAccessTypes } from '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes';

const ENTITY_ROUTES = {
  product: { label: 'Product', path: '/master-data-management/products' },
  promotion: { label: 'Promotion', path: '/marketing-management/promotions' },
  form: { label: 'Form', path: '/forms-management/forms' },
  packing_list: { label: 'Packing List', path: '/procurement-management/packing-lists' },
  certificate: { label: 'Certificate', path: '/master-data-management/certificates' },
} as const;

function LinkedEntityLink({
  type,
  id,
  name,
}: {
  type: keyof typeof ENTITY_ROUTES;
  id: string;
  name: string;
}) {
  const config = ENTITY_ROUTES[type];
  const href = `${config.path}/${id}`;
  return (
    <Link
      href={href}
      className="text-primary hover:underline inline-flex items-center gap-1"
    >
      {name}
      <ExternalLink className="size-3.5 shrink-0" />
    </Link>
  );
}

function LinkagesTable({
  type,
  items,
  emptyMessage,
}: {
  type: keyof typeof ENTITY_ROUTES;
  items: LinkedEntityRef[];
  emptyMessage: string;
}) {
  const columns = useMemo<ColumnDef<LinkedEntityRef>[]>(
    () => [
      {
        accessorKey: 'name',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        cell: ({ row }) => {
          // Only a SHARED attachment's rows carry a company (R3, S5): a
          // single-company attachment's items have no company_name and render
          // exactly as before, no badges anywhere in the row.
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
        size: 240,
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
        size: 320,
        meta: { headerTitle: 'Description' },
      },
      {
        id: 'action',
        header: () => <span className="sr-only">Action</span>,
        cell: ({ row }) => {
          const outOfScope = row.original.in_scope === false;
          return outOfScope ? (
            <span className="text-xs text-muted-foreground">-</span>
          ) : (
            <Link
              href={`${ENTITY_ROUTES[type].path}/${row.original.id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline inline-flex items-center gap-1 text-sm"
              onClick={(e) => e.stopPropagation()}
            >
              View
              <ExternalLink className="size-3.5 shrink-0" />
            </Link>
          );
        },
        size: 80,
        enableResizing: false,
        meta: { headerTitle: 'Action' },
      },
    ],
    [type],
  );

  return (
    <PanelDataGrid<LinkedEntityRef>
      columns={columns}
      rows={items}
      getRowId={(row) => row.id}
      listingKey={`resource.attachments.view::linkages-${type}`}
      emptyTitle={emptyMessage}
    />
  );
}

function LinkagesTabs({ attachment }: { attachment: Attachment }) {
  const products = attachment.linked_products ?? [];
  const promotions = attachment.linked_promotions ?? [];
  const form = attachment.linked_form ?? null;
  const packingLists = attachment.linked_packing_lists ?? [];
  // Read-only: a file is linked to a certificate by BEING one of its filed
  // revisions, so there is nothing here to attach or detach - unlinking would
  // leave the revision with no document behind it.
  const certificates = attachment.linked_certificates ?? [];

  return (
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
        />
      </TabsContent>
      <TabsContent value="promotions" className="mt-4">
        <LinkagesTable
          type="promotion"
          items={promotions}
          emptyMessage="No promotions linked to this attachment."
        />
      </TabsContent>
      <TabsContent value="forms" className="mt-4">
        {form ? (
          <LinkagesTable
            type="form"
            items={[form]}
            emptyMessage="No form linked to this attachment."
          />
        ) : (
          <p className="text-sm text-muted-foreground">No form linked to this attachment.</p>
        )}
      </TabsContent>
      <TabsContent value="packing_lists" className="mt-4">
        <LinkagesTable
          type="packing_list"
          items={packingLists}
          emptyMessage="No packing lists linked to this attachment."
        />
      </TabsContent>
      <TabsContent value="certificates" className="mt-4">
        <LinkagesTable
          type="certificate"
          items={certificates}
          emptyMessage="This file is not filed as a certificate."
        />
      </TabsContent>
    </Tabs>
  );
}

interface AttachmentDetailProps {
  attachmentId: string;
  fromDirectories?: boolean;
  directoryId?: string;
}

export default function AttachmentDetail({
  attachmentId,
  fromDirectories = false,
  directoryId,
}: AttachmentDetailProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [previewOpen, setPreviewOpen] = useState(false);
  const [descriptionEdit, setDescriptionEdit] = useState<string | null>(null);
  const [accessLevelsEdit, setAccessLevelsEdit] = useState<string[] | null>(null);
  const [companyDialogOpen, setCompanyDialogOpen] = useState(false);
  const {
    data: accessTypeOptions = [],
    error: accessTypesError,
    refetch: refetchAccessTypes,
  } = useContactAccessTypes();
  const defaultAccessLevels = accessTypeOptions.length > 0 ? accessTypeOptions.map((o) => o.code) : ['dealer', 'end_user'];
  const codeToName = Object.fromEntries(accessTypeOptions.map((o) => [o.code, o.name || o.code]));
  const deleteMutation = useDeleteAttachment();
  const updateMutation = useUpdateAttachment();

  const { data: attachment, isLoading } = useQuery({
    queryKey: ['attachment-metadata', attachmentId],
    queryFn: () => getAttachmentMetadata(attachmentId),
    enabled: !!attachmentId,
    retry: 1,
  });

  // The set the browser's row menu renders too (D15); Rename and Delete bring
  // their own dialogs. Preview stays the primary button below.
  const { actions, dialogs } = useAttachmentActions(attachment, {
    onDeleted: () => router.push(listPath),
  });

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

  const formatFileSize = (bytes: number | null | undefined) => {
    if (!bytes) return '-';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(2)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  const listPath = fromDirectories
    ? `/resource-management/attachment-directories${directoryId ? `?directoryId=${directoryId}` : ''}`
    : '/resource-management/attachments';

  if (!attachment) {
    return (
      <div className="text-center py-12">
        <p className="text-muted-foreground">Attachment not found</p>
        <Button
          variant="outline"
          onClick={() => router.push(listPath)}
          className="mt-4"
        >
          Back to {fromDirectories ? 'Files' : 'Attachments'}
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1 min-w-0">
          <h2 className="text-2xl font-bold break-words">{attachment.original_filename}</h2>
          <p className="text-sm text-muted-foreground">
            Uploaded: {formatDate(new Date(attachment.uploaded_at))}
          </p>
        </div>
        <DetailActions
          pager={{
            ...attachmentsPagerQuery,
            detailPath: '/resource-management/attachments',
            currentId: attachmentId,
            ariaLabel: 'attachment',
          }}
          actions={actions}
          dialogs={dialogs}
          gearLabel="Attachment options"
          primary={
            <Button onClick={() => setPreviewOpen(true)}>
              <Eye className="size-4" />
              Preview
            </Button>
          }
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Attachment Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
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
              <p className="text-sm text-muted-foreground">File Size</p>
              <p className="font-medium">{formatFileSize(attachment.file_size_bytes)}</p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Uploaded By</p>
              <p className="font-medium">
                {attachment.uploaded_by_user?.name ?? attachment.uploaded_by_user?.email ?? attachment.uploaded_by ?? '-'}
              </p>
            </div>
            <div>
              <p className="text-sm text-muted-foreground">Company</p>
              <div className="flex items-center justify-between gap-2">
                <p className="font-medium">
                  {attachmentCompanyLabel(attachment)}
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setCompanyDialogOpen(true)}
                  data-testid="attachment-company-edit"
                >
                  Edit
                </Button>
              </div>
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
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Linkages</CardTitle>
        </CardHeader>
        <CardContent>
          <LinkagesTabs attachment={attachment} />
        </CardContent>
      </Card>


      <AttachmentPreviewModal
        open={previewOpen}
        onOpenChange={setPreviewOpen}
        items={previewItems}
      />

      {companyDialogOpen && (
        <SetCompanyDialog
          open={companyDialogOpen}
          onOpenChange={setCompanyDialogOpen}
          fileIds={[attachment.id]}
          folderIds={[]}
        />
      )}
    </div>
  );
}
