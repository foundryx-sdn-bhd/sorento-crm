'use client';

import * as React from 'react';
import { ParkedSendDialog } from '@/components/common/ParkedSendDialog';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { toast } from '@/lib/toast';
import {
  useHandoverResendPreview,
  useSendHandoverResend,
} from '../hooks/useOrderInquiry';
import type {
  HandoverLastSent,
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
  React.useEffect(() => setIds(inquiryIds), [inquiryIds]);
  const preview = useHandoverResendPreview(ids);
  const send = useSendHandoverResend();
  const data = preview.data;
  const emails = data?.emails ?? [];
  const skipped = data?.skipped ?? [];

  // A preview that cannot load closes with the reason, never a dialog stuck on skeletons.
  const previewError = preview.error;
  React.useEffect(() => {
    if (previewError) {
      toast.error(previewError.message);
      onClose();
    }
  }, [previewError, onClose]);

  async function handleSend() {
    const result = await send.mutateAsync(
      emails.map((email) => email.inquiry_id),
    );
    const failed = result.skipped.filter((skip) => skip.reason === 'failed');
    if (failed.length > 0) {
      setIds(failed.map((skip) => skip.inquiry_id));
      throw new Error('partly sent');
    }
    onSent?.();
  }

  const notice =
    data && (!data.automation_enabled || skipped.length > 0) ? (
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
      notice={notice}
      tabLabel={(email, index) =>
        emails[index]?.inquiry_no ?? `Email to ${email.to.name}`
      }
      emailNote={(index) =>
        emails[index] ? (
          <p className="text-muted-foreground">
            {lastSentText(emails[index].last_sent)}
          </p>
        ) : null
      }
    />
  );
}

export default HandoverResendDialog;
