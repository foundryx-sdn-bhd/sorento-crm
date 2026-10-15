'use client';

import * as React from 'react';
import { ParkedSendDialog } from '@/components/common/ParkedSendDialog';
import { Checkbox } from '@/components/ui/checkbox';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import { toast } from '@/lib/toast';
import {
  useHandoverResendPreview,
  useSendHandoverResend,
} from '../hooks/useOrderInquiry';
import type {
  HandoverLastSent,
  HandoverResendLine,
  HandoverResendSkipReason,
} from '../services/orderInquiryHandoverResendService';

const SKIP_TEXT: Record<HandoverResendSkipReason, string> = {
  nothing_open: 'nothing open, not sent',
  no_recipient: 'no purchasing recipient, not sent',
  failed: 'could not be sent',
};

export function lastSentText(last: HandoverLastSent | null): string {
  if (!last) return 'No handover email on record';
  const who =
    last.kind === 'resent' ? `resent by ${last.by ?? 'someone'}` : 'automatic';
  return `Last sent to purchasing: ${formatDateTimeInMalaysia(last.at)} (${who})`;
}

/**
 * Send to purchasing (OI-RESEND-PURCHASING): one preview of the handover email per order
 * inquiry, then one Send. The shared parked-send dialog carries the recipients and the
 * rendered email; this adds the switched-off warning, each email's "Last sent" line and the
 * order inquiries that will not be sent. A partial failure keeps the dialog open on the
 * order inquiries that did not go.
 *
 * For ONE order inquiry the preview lists its open lines with a tick each (all ticked to
 * start; a line already in an earlier handover email reads "sent DD/MM" and stays tickable):
 * the email and its Excel then carry only the ticked lines, and Send waits for at least one.
 * A bulk send always takes every open line.
 */
export function HandoverResendDialog({
  inquiryIds,
  onClose,
  onSent,
}: {
  inquiryIds: string[] | null;
  onClose: () => void;
  /** Called once every order inquiry in the preview went. */
  onSent?: () => void;
}) {
  const [ids, setIds] = React.useState<string[] | null>(inquiryIds);
  // `ticked` null = every open line (the start). `rowIds` is what the preview and the send
  // ask for: null = every open line; it keeps its last non-empty value while nothing is
  // ticked, so the email stays on screen and only Send waits.
  const [ticked, setTicked] = React.useState<string[] | null>(null);
  const [rowIds, setRowIds] = React.useState<string[] | null>(null);
  React.useEffect(() => {
    setIds(inquiryIds);
    setTicked(null);
    setRowIds(null);
  }, [inquiryIds]);
  const single = ids !== null && ids.length === 1;
  const preview = useHandoverResendPreview(ids, single ? rowIds : null);
  const send = useSendHandoverResend();
  const data = preview.data;
  const emails = data?.emails ?? [];
  const skipped = data?.skipped ?? [];
  const lines: HandoverResendLine[] = single ? (emails[0]?.lines ?? []) : [];
  const tickedIds = ticked ?? lines.map((line) => line.row_id);

  function toggle(rowId: string, on: boolean) {
    const next = on
      ? lines
          .map((line) => line.row_id)
          .filter((id) => id === rowId || tickedIds.includes(id))
      : tickedIds.filter((id) => id !== rowId);
    setTicked(next);
    if (next.length > 0) setRowIds(next.length === lines.length ? null : next);
  }

  // A preview that cannot load closes with the reason, never a dialog stuck on skeletons.
  const previewError = preview.error;
  React.useEffect(() => {
    if (previewError) {
      toast.error(previewError.message);
      onClose();
    }
  }, [previewError, onClose]);

  async function handleSend() {
    const result = await send.mutateAsync({
      ids: emails.map((email) => email.inquiry_id),
      rowIds: single ? rowIds : null,
    });
    const failed = result.skipped.filter((skip) => skip.reason === 'failed');
    if (failed.length > 0) {
      setIds(failed.map((skip) => skip.inquiry_id));
      throw new Error('partly sent');
    }
    onSent?.();
  }

  const lineTicks =
    lines.length > 0 ? (
      <div className="space-y-1 text-sm">
        <p className="font-medium">Lines to send</p>
        <ul className="space-y-1">
          {lines.map((line) => (
            <li key={line.row_id}>
              <label className="flex min-w-0 items-center gap-2">
                <Checkbox
                  checked={tickedIds.includes(line.row_id)}
                  onCheckedChange={(value) =>
                    toggle(line.row_id, value === true)
                  }
                  aria-label={`Send ${line.item_code ?? 'line'}`}
                />
                <span className="truncate" title={line.item_code ?? ''}>
                  {`${line.item_code ?? ''}, qty ${line.qty}${line.location ? `, ${line.location}` : ''}${line.delivery_date ? `, ${formatDateInMalaysia(line.delivery_date)}` : ''}`}
                </span>
                {line.sent_at ? (
                  <span className="shrink-0 text-muted-foreground">
                    {`sent ${formatDateInMalaysia(line.sent_at).slice(0, 5)}`}
                  </span>
                ) : null}
              </label>
            </li>
          ))}
        </ul>
        {tickedIds.length === 0 ? (
          <p className="text-muted-foreground">
            Tick at least one line to send.
          </p>
        ) : null}
      </div>
    ) : null;

  const notice =
    data && (!data.automation_enabled || skipped.length > 0 || lineTicks) ? (
      <div className="space-y-1 text-sm">
        {!data.automation_enabled ? (
          <p className="font-medium text-destructive">
            The automatic email to purchasing is switched off. This send goes
            out anyway.
          </p>
        ) : null}
        {skipped.map((skip) => (
          <p key={skip.inquiry_id} className="text-muted-foreground">
            {`${skip.inquiry_no ?? 'Order inquiry'}: ${SKIP_TEXT[skip.reason]}`}
          </p>
        ))}
        {lineTicks}
      </div>
    ) : null;

  return (
    <ParkedSendDialog
      open={inquiryIds !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title="Send to purchasing"
      preview={data}
      isLoading={preview.isLoading}
      onSend={handleSend}
      isSending={send.isPending}
      sendDisabled={(single && tickedIds.length === 0) || preview.isFetching}
      notice={notice}
      tabLabel={(email, index) =>
        emails[index]?.inquiry_no ?? `Email to ${email.to.name}`
      }
      emailNote={(index) => {
        const current = emails[index];
        if (!current) return null;
        return (
          <>
            <p className="text-muted-foreground">
              {lastSentText(current.last_sent)}
            </p>
            {current.attachments?.length ? (
              <p className="text-muted-foreground">{`Attached: ${current.attachments.join(', ')}`}</p>
            ) : null}
          </>
        );
      }}
    />
  );
}

export default HandoverResendDialog;
