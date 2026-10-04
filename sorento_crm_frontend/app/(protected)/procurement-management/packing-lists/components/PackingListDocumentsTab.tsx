'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { Link as LinkIcon, Link2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import AttachmentFileCard from '@/components/common/AttachmentFileCard';
import LinkAttachmentBrowserDialog from '@/components/common/LinkAttachmentBrowserDialog';
import { usePackingListRecord } from '../[id]/components/packing-list-context';

/** Every file this container is answered by: its own attachment and the invoices behind it. */
export function PackingListDocumentsTab() {
  const {
    packingListId,
    packingList,
    canReadScm,
    sourceInvoices,
    sourceInvoicesFailed,
    update,
    updatePending,
  } = usePackingListRecord();
  const [linkDialogOpen, setLinkDialogOpen] = useState(false);
  const [unlinkOpen, setUnlinkOpen] = useState(false);

  const linkedAttachmentIds = useMemo(
    () =>
      packingList?.attachment_id
        ? new Set([packingList.attachment_id])
        : new Set<string>(),
    [packingList?.attachment_id],
  );

  if (!packingList) return null;

  const handleUnlink = async () => {
    try {
      await update({ attachment_id: null });
      setUnlinkOpen(false);
      toast.success('Attachment unlinked');
    } catch {
      toast.error('Failed to unlink attachment');
    }
  };

  const invoices = sourceInvoices?.invoices ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Related Documents</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {packingList.attachment_id && packingList.attachment ? (
          <div className="space-y-2">
            <p className="text-sm font-medium">Attachment</p>
            {/* The shared file row (S8), so this card and the proforma invoice's Source
                files read as one thing. The unlink flow behind it is unchanged. */}
            <AttachmentFileCard
              attachmentId={packingList.attachment_id}
              name={packingList.attachment.original_filename || 'Unknown'}
              typeLabel={packingList.attachment.attachment_type?.type_name}
              sizeBytes={packingList.attachment.file_size_bytes ?? null}
              onUnlink={() => setUnlinkOpen(true)}
              unlinkDisabled={updatePending}
            />
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-sm text-muted-foreground">
              No attachment linked. Link one to attach a document to this packing list.
            </p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setLinkDialogOpen(true)}
              className="gap-2"
            >
              <Link2 className="size-4" />
              Link attachment
            </Button>
          </div>
        )}

        {/* The proforma invoice files these lines were read from. Always rendered for a
            user who can read them: "none" is the honest answer for a container that came
            off a real packing list, and a section that vanishes teaches nobody where to
            look. Without SCM read "none" would be a guess, so it is not rendered at all
            (PL-TABS-ACCESS). */}
        {canReadScm && (
          <div className="space-y-2">
            <p className="text-sm font-medium">Proforma invoices</p>
            {sourceInvoicesFailed ? (
              <p className="text-sm text-destructive">Could not load the proforma invoices.</p>
            ) : invoices.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No proforma invoice behind this container.
              </p>
            ) : (
              <div className="space-y-2">
                {invoices.map((pi) => (
                  <div key={pi.id} className="flex items-center gap-2 rounded-lg border p-3">
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{pi.pi_number}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {pi.source_ref || 'No source file recorded'}
                        {pi.supplier_name ? ` • ${pi.supplier_name}` : ''}
                      </p>
                    </div>
                    <Link
                      href={`/scm/proforma-invoices/${pi.id}`}
                      className="shrink-0 text-sm text-primary hover:underline"
                    >
                      Open
                    </Link>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        <LinkAttachmentBrowserDialog
          open={linkDialogOpen}
          onOpenChange={setLinkDialogOpen}
          entityId={packingListId}
          linkedAttachmentIds={linkedAttachmentIds}
          linkAttachment={async (_entityId: string, attachmentId: string) => {
            await update({ attachment_id: attachmentId });
          }}
          invalidateQueryKeys={[['packing-list', packingListId], ['packing-lists']]}
          successEntityLabel="packing list"
          maxSelections={1}
        />

        {packingList.spo_allocations_count !== undefined &&
          packingList.spo_allocations_count > 0 && (
            <div>
              <Link
                href={`/procurement-management/spo-allocations?shipment_id=${packingListId}`}
                className="flex items-center gap-2 text-sm text-primary hover:underline"
              >
                <LinkIcon className="size-4" />
                SPO Allocations ({packingList.spo_allocations_count})
              </Link>
            </div>
          )}

        {/* Nothing is detached on one click - the CRUD standard's rule, and the one this
            page was breaking on Unlink. */}
        <AlertDialog open={unlinkOpen} onOpenChange={setUnlinkOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Unlink this attachment?</AlertDialogTitle>
              <AlertDialogDescription>
                {packingList.attachment?.original_filename
                  ? `${packingList.attachment.original_filename} stays in the file library; it just stops being attached to this packing list.`
                  : 'The file stays in the library; it just stops being attached to this packing list.'}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <Button variant="outline" onClick={() => setUnlinkOpen(false)}>
                Cancel
              </Button>
              <Button onClick={() => void handleUnlink()} disabled={updatePending}>
                Unlink
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </CardContent>
    </Card>
  );
}

export default PackingListDocumentsTab;
