'use client';

import { useState } from 'react';
import Link from 'next/link';
import {
  AlertCircle,
  CloudUpload,
  Pencil,
  Send,
  ThumbsDown,
  ThumbsUp,
} from 'lucide-react';

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import { formatDateTimeInMalaysia } from '@/lib/helpers';

import {
  useApproveEdition,
  useEditionQuery,
  useEditionReviewQuery,
  usePublishEdition,
  useRejectEdition,
  useReopenEdition,
  useSubmitEdition,
} from '../../hooks/useEditions';
import { distinctName } from '@/lib/product-display';

/**
 * One Edition, and the single decision available at this point in its life.
 *
 * The header carries EXACTLY ONE primary action, chosen by status - Send for
 * approval, then Approve, then Publish. Reject sits beside it as the outline
 * destructive, because it is the other half of one decision rather than a
 * lesser version of it. Everything else is under the gear.
 *
 * That shape is deliberate and is the pattern the rest of the system uses
 * (purchase requests, complaints, stock inquiries). The Dealer Kit page editor
 * had six equal-weight buttons in a row until it was cut back to two and a
 * gear; this screen starts where that one ended up.
 *
 * Actions that cannot apply are HIDDEN, not disabled. A disabled Approve on a
 * draft invites somebody to hunt for what would enable it, and the answer is
 * "be a different person, later".
 */
export function EditionDetail({ editionId }: { editionId: string }) {
  const { data: edition, isLoading, isError, error } = useEditionQuery(editionId);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState('');

  const submit = useSubmitEdition(editionId);
  const approve = useApproveEdition(editionId);
  const reject = useRejectEdition(editionId);
  const reopen = useReopenEdition(editionId);
  const publish = usePublishEdition(editionId);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (isError || !edition) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="size-4" />
        <AlertTitle>Could not open this edition</AlertTitle>
        <AlertDescription>
          {error instanceof Error ? error.message : 'It does not exist, or you cannot see it.'}
        </AlertDescription>
      </Alert>
    );
  }

  const busy =
    submit.isPending ||
    approve.isPending ||
    reject.isPending ||
    reopen.isPending ||
    publish.isPending;

  return (
    <div className="flex flex-col gap-5">
      <Card>
        {/* Wraps on a phone rather than letting the actions overlap a long
            catalogue name, which is the header bug this repo has fixed on
            every other detail page. */}
        <CardContent className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="min-w-0 break-words text-lg font-semibold">{edition.name}</h2>
              <span className={`${STATUS_PILL_BASE} ${statusPillClass(edition.status)}`}>
                {edition.statusLabel}
              </span>
            </div>
            <Link
              href={`/dealer-kit/pages/${edition.pageId}`}
              className="text-sm text-muted-foreground hover:underline"
            >
              {edition.pageName ?? 'Open the catalogue'}
            </Link>
          </div>

          <div className="flex flex-wrap items-center gap-2 sm:justify-end">
            {edition.status === 'draft' && (
              <Button disabled={busy} onClick={() => submit.mutate()}>
                <Send className="size-4" />
                {submit.isPending ? 'Sending' : 'Send for approval'}
              </Button>
            )}

            {edition.status === 'pending_approval' && (
              <>
                <Button disabled={busy} onClick={() => approve.mutate()}>
                  <ThumbsUp className="size-4" />
                  {approve.isPending ? 'Approving' : 'Approve'}
                </Button>
                <Button
                  variant="outline"
                  className="border-destructive text-destructive hover:bg-destructive/10"
                  disabled={busy}
                  onClick={() => {
                    setReason('');
                    setRejectOpen(true);
                  }}
                >
                  <ThumbsDown className="size-4" />
                  Reject
                </Button>
              </>
            )}

            {edition.status === 'approved' && (
              <Button disabled={busy} onClick={() => publish.mutate()}>
                <CloudUpload className="size-4" />
                {publish.isPending ? 'Publishing' : 'Publish'}
              </Button>
            )}

            {edition.status === 'rejected' && (
              <Button disabled={busy} onClick={() => reopen.mutate()}>
                <Pencil className="size-4" />
                {reopen.isPending ? 'Reopening' : 'Reopen for editing'}
              </Button>
            )}

            <DetailActionsMenu ariaLabel="Edition actions">
              <DropdownMenuItem asChild>
                <Link href={`/dealer-kit/pages/${edition.pageId}`}>Open the catalogue</Link>
              </DropdownMenuItem>
            </DetailActionsMenu>
          </div>
        </CardContent>
      </Card>

      {/* The one thing a Designer coming back to rejected work needs to read,
          so it is the first thing on the page and not a field in a grid. */}
      {edition.rejectionReason && (
        <Alert variant="destructive" appearance="light" data-testid="dk-ed-rejection">
          <AlertCircle className="size-4" />
          <AlertTitle>Sent back</AlertTitle>
          <AlertDescription>{edition.rejectionReason}</AlertDescription>
        </Alert>
      )}

      <EditionChanges editionId={editionId} />

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">History</CardTitle>
        </CardHeader>
        {/* Every row renders even when empty, per the CRUD standard: a section
            that vanishes on missing data reads as a section that failed. */}
        <CardContent className="grid gap-3 sm:grid-cols-3">
          <Fact label="Started" value={formatDateTimeInMalaysia(edition.createdAt)} />
          <Fact
            label="Sent for approval"
            value={
              edition.submittedAt ? formatDateTimeInMalaysia(edition.submittedAt) : 'Not yet'
            }
          />
          <Fact
            label="Approved"
            value={edition.approvedAt ? formatDateTimeInMalaysia(edition.approvedAt) : 'Not yet'}
          />
        </CardContent>
      </Card>

      <Dialog open={rejectOpen} onOpenChange={setRejectOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Send this back?</DialogTitle>
            <DialogDescription>The designer sees your reason.</DialogDescription>
          </DialogHeader>
          <Textarea
            id="dk-ed-reject-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="What needs to change?"
            rows={4}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejectOpen(false)}>
              Cancel
            </Button>
            <Button
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              // The server refuses a blank reason too; this stops the round trip
              // rather than being the only thing standing between a blank
              // rejection and the database.
              disabled={!reason.trim() || reject.isPending}
              onClick={() =>
                reject.mutate(reason.trim(), { onSuccess: () => setRejectOpen(false) })
              }
            >
              {reject.isPending ? 'Sending back' : 'Send back'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  );
}

const DROPPED_REASON: Record<string, string> = {
  discontinued: 'Discontinued',
  inactive: 'Deactivated',
  missing: 'Deleted',
};

/**
 * What the inherited catalogue no longer says truthfully (AC-L9).
 *
 * `dropped` is the load-bearing half. The collection resolver filters
 * discontinued and inactive products out of its candidate set, so a product
 * discontinued since the catalogue was built does NOT render struck through -
 * it disappears and the tile count quietly falls. This is the only screen that
 * says so, which is why it renders even when the list is empty: "nothing has
 * gone" is the answer somebody came here for.
 */
function EditionChanges({ editionId }: { editionId: string }) {
  const { data, isLoading, isError } = useEditionReviewQuery(editionId);

  if (isLoading) return <Skeleton className="h-32 w-full" />;

  // The section stays, per the CRUD standard and for the same reason the empty
  // case renders: somebody came here to find out what changed, and a card that
  // vanishes answers "nothing" when the truth is "we could not work it out".
  if (isError || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">What changed</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <AlertCircle className="size-4 shrink-0" />
            Could not work out what changed. Reload to try again.
          </div>
        </CardContent>
      </Card>
    );
  }

  const newSince = data.members.filter((row) => row.isNewSincePrevious);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">
          What changed
          {data.previousEditionName ? ` since ${data.previousEditionName}` : ''}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-wrap gap-2" data-testid="dk-ed-change-counts">
          <Count label="In the catalogue" value={data.members.length} />
          <Count label="No longer available" value={data.dropped.length} tone="warn" />
          <Count label="New since last time" value={newSince.length} />
        </div>

        {data.dropped.length > 0 && (
          <ul
            className="flex flex-col gap-1 rounded-lg border border-border px-4 py-3 text-sm"
            data-testid="dk-ed-dropped"
          >
            {data.dropped.map((row) => (
              <li key={row.productId} className="flex flex-wrap items-center gap-2">
                <span className="font-mono line-through text-muted-foreground">
                  {row.productCode ?? 'Unknown product'}
                </span>
                <span className="truncate text-muted-foreground">
                  {distinctName(row.productCode, row.productName) ?? ''}
                </span>
                <span className={`${STATUS_PILL_BASE} bg-amber-100 text-amber-800`}>
                  {DROPPED_REASON[row.reason] ?? row.reason}
                </span>
              </li>
            ))}
          </ul>
        )}

        {newSince.length > 0 && (
          <ul
            className="flex flex-col gap-1 rounded-lg border border-border px-4 py-3 text-sm"
            data-testid="dk-ed-new-since"
          >
            {newSince.map((row) => (
              <li key={row.productId} className="flex flex-wrap items-center gap-2">
                <span className="font-mono">{row.productCode}</span>
                <span className="truncate text-muted-foreground">
                  {distinctName(row.productCode, row.productName)}
                </span>
                <span className={`${STATUS_PILL_BASE} bg-sky-100 text-sky-800`}>New</span>
                <span className="text-xs text-muted-foreground">
                  {row.stockOnHand} in stock
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function Count({
  label,
  value,
  tone = 'plain',
}: {
  label: string;
  value: number;
  tone?: 'plain' | 'warn';
}) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <p
        className={
          tone === 'warn' && value > 0
            ? 'text-xl font-semibold text-amber-600'
            : 'text-xl font-semibold text-foreground'
        }
      >
        {value}
      </p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </div>
  );
}
