'use client';

/**
 * Shared rendering for a single "My Downloads" row + status badge.
 *
 * Used by both the top-nav drawer (MyDownloadsDrawer) and the per-entity
 * downloads modal (EntityDownloadsButton) so date pills, status, and the
 * click-to-download behaviour can't drift between the two surfaces.
 *
 * A ready row does two things. The row body is click-to-download: resolve a fresh
 * signed URL and open it. Beside it sits Preview, which opens the SAME modal the
 * resource attachments use, so a generated PDF or workbook can be read without
 * leaving the page. Preview is an addition, not a replacement - saving the file is
 * still the primary act for most kinds, and one shared row means every download
 * kind gained the preview at once rather than one domain forking its own copy.
 *
 * Non-ready rows are inert: there is nothing to open yet, and a control that
 * resolves to a 409 is worse than no control.
 */

import { useState } from 'react';
import { Download, Eye, FileText, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react';
import { toast } from '@/lib/toast';

import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import {
  downloadFilePath,
  fetchDownloadUrl,
  type MyDownload,
} from '@/services/myDownloadsService';

export const KIND_LABEL: Record<string, string> = {
  complaint_pdf: 'Complaint PDF',
  // Named after the document itself - the form is headed PRODUCT INQUIRY FORM -
  // while the entity key stays stock_inquiry.
  stock_inquiry_pdf: 'Product Inquiry PDF',
  chat_history_export: 'Chat History CSV',
  promotions_pdf: 'Promotions PDF',
  quotation_pdf: 'Quotation PDF',
  quotation_xlsx: 'Quotation Excel',
  report_xlsx: 'Report Excel',
  // The reorder plan's three printable views. The first two shipped without a label and
  // rendered their raw kind whenever the row carried no filename yet (AC-3).
  order_sheet_pdf: 'Order sheet PDF',
  order_sheet_xlsx: 'Order sheet Excel',
  low_stock_xlsx: 'Low stock report',
  // V1 (fix round 1, PLAN-pi-header-fields-convert-fixes-24sep.md): the async
  // packing-list export (E1/E2) writes rows with this kind; without a label here a
  // row with no filename yet falls back to the raw key.
  packing_list_xlsx: 'Packing list Excel',
  stock_debt_xlsx: 'Stock debt Excel',
  attachment_bulk_zip: 'Files archive (ZIP)',
};

export function StatusBadge({ status }: { status: MyDownload['status'] }) {
  if (status === 'ready') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700">
        <CheckCircle2 className="size-3.5" /> Ready
      </span>
    );
  }
  if (status === 'failed') {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-destructive">
        <AlertCircle className="size-3.5" /> Failed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground">
      <Loader2 className="size-3.5 animate-spin" />
      {status === 'pending' ? 'Queued' : 'Preparing'}
    </span>
  );
}

export function DownloadRow({ row }: { row: MyDownload }) {
  const [busy, setBusy] = useState(false);
  const [previewItem, setPreviewItem] = useState<AttachmentPreviewItem | null>(null);

  const ready = row.status === 'ready';
  const name = row.filename ?? KIND_LABEL[row.kind] ?? row.kind;

  const onDownload = async () => {
    setBusy(true);
    try {
      const { url } = await fetchDownloadUrl(row.id);
      window.open(url, '_blank', 'noopener,noreferrer');
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not open download');
    } finally {
      setBusy(false);
    }
  };

  /**
   * The signed URL is what the modal's `<iframe>`/`<img>` can load (they cannot send
   * an auth header); the same-origin `/file` path is what it reads spreadsheet bytes
   * and saves the file through. Both, always, so every kind previews correctly.
   */
  const onPreview = async () => {
    setBusy(true);
    try {
      const { url, filename } = await fetchDownloadUrl(row.id);
      setPreviewItem({
        id: row.id,
        name: filename ?? name,
        url,
        downloadUrl: downloadFilePath(row.id),
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not open preview');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div
        className={`flex items-start gap-2 border-b border-border px-4 py-3 ${
          busy ? 'opacity-60' : ''
        }`}
        aria-busy={busy || undefined}
      >
        {/* The row body, not the whole row, carries the download click: the Preview
            control has to be a sibling rather than a button nested inside another
            interactive element. */}
        <div
          role={ready ? 'button' : undefined}
          tabIndex={ready ? 0 : undefined}
          onClick={ready && !busy ? onDownload : undefined}
          onKeyDown={
            ready && !busy
              ? (e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onDownload();
                  }
                }
              : undefined
          }
          title={ready ? 'Click to download' : undefined}
          className={`flex min-w-0 flex-1 items-start gap-3 rounded-md ${
            ready ? 'cursor-pointer transition-colors hover:bg-muted/50' : ''
          }`}
        >
          <FileText className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium" title={row.filename ?? undefined}>
              {name}
            </p>
            <p className="text-xs text-muted-foreground">
              {KIND_LABEL[row.kind] ?? row.kind}
              {row.created_at ? ` · ${formatDateTimeInMalaysia(row.created_at)}` : ''}
            </p>
            <div className="mt-1 flex min-w-0 items-center gap-3">
              <StatusBadge status={row.status} />
              {row.status === 'failed' && row.error && (
                <span className="min-w-0 truncate text-xs text-destructive" title={row.error}>
                  {row.error}
                </span>
              )}
            </div>
          </div>
        </div>

        {ready && (
          <div className="mt-0.5 flex shrink-0 items-center gap-0.5">
            {row.kind !== 'attachment_bulk_zip' && (
            <button
              type="button"
              onClick={onPreview}
              disabled={busy}
              title="Preview"
              aria-label={`Preview ${name}`}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            >
              <Eye className="size-4" />
            </button>
            )}
            <button
              type="button"
              onClick={onDownload}
              disabled={busy}
              title="Download"
              aria-label={`Download ${name}`}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-50"
            >
              <Download className={`size-4 ${busy ? 'animate-pulse' : ''}`} />
            </button>
          </div>
        )}
      </div>

      <AttachmentPreviewModal
        open={previewItem !== null}
        onOpenChange={(next) => !next && setPreviewItem(null)}
        items={previewItem ? [previewItem] : []}
      />
    </>
  );
}
