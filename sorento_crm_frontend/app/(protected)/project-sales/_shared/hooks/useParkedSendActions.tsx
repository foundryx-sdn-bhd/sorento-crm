'use client';

import { useMemo, useState } from 'react';
import { ParkedSendDialog } from '@/components/common/ParkedSendDialog';
import { useHasPermission } from '@/hooks/usePermissions';
import {
  useParkedPreview,
  useReserveStatusSummary,
  useSendParked,
  type ParkedKind,
} from './useOrderInquiry';

const ACKNOWLEDGE_PERMISSION = 'projects.order_inquiries.acknowledge';
const RESERVE_PERMISSION = 'projects.order_inquiries.reserve';

/**
 * The two "Send all parked ..." Actions items and their `ParkedSendDialog`, shared by the
 * Order inquiries list and the Stock Debt snapshot page (one parked queue, one send). `N` is
 * the presser's own parked items wherever they were parked. Pass `actions` to the toolbar's
 * `secondaryActions` and render `dialog` once.
 */
export function useParkedSendActions() {
  const canAcknowledge = useHasPermission(ACKNOWLEDGE_PERMISSION);
  const canReserve = useHasPermission(RESERVE_PERMISSION);
  const reserveSummary = useReserveStatusSummary();
  const [parkedKind, setParkedKind] = useState<ParkedKind | null>(null);
  const parkedPreview = useParkedPreview(parkedKind);
  const sendParked = useSendParked(parkedKind ?? 'requests');
  const myParkedRequests = reserveSummary.data?.my_parked_requests ?? 0;
  const myParkedNotices = reserveSummary.data?.my_parked_notices ?? 0;
  const actions = useMemo(
    () => [
      ...(canAcknowledge
        ? [
            {
              key: 'send-parked-requests',
              label: `Send all parked requests to CS (${myParkedRequests})`,
              disabled: myParkedRequests === 0,
              disabledReason: 'Nothing of yours is parked',
              onClick: () => setParkedKind('requests' as ParkedKind),
            },
          ]
        : []),
      ...(canReserve
        ? [
            {
              key: 'send-parked-notices',
              label: `Send all parked reserves to purchasing (${myParkedNotices})`,
              disabled: myParkedNotices === 0,
              disabledReason: 'Nothing of yours is parked',
              onClick: () => setParkedKind('notices' as ParkedKind),
            },
          ]
        : []),
    ],
    [canAcknowledge, canReserve, myParkedRequests, myParkedNotices],
  );

  const dialog = (
    <ParkedSendDialog
      open={parkedKind !== null}
      onOpenChange={(next) => {
        if (!next) setParkedKind(null);
      }}
      title={
        parkedKind === 'notices'
          ? 'Send all parked reserves to purchasing'
          : 'Send all parked requests to CS'
      }
      preview={parkedPreview.data}
      isLoading={parkedPreview.isLoading}
      onSend={() => sendParked.mutateAsync()}
      isSending={sendParked.isPending}
    />
  );

  return { actions, dialog };
}
