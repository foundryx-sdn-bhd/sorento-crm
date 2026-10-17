'use client';

import { useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { type ColumnDef, type ExpandedState } from '@tanstack/react-table';
import { ChevronDown, ChevronRight, Link2, Printer, Settings2, Trash2 } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import AuditTrail from '@/components/audit/AuditTrail';
import DetailActions from '@/components/common/DetailActions';
import { DetailActionsMenu } from '@/components/common/DetailActionsMenu';
import { PanelDataGrid } from '@/components/common/PanelDataGrid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useEffectiveUserId } from '@/hooks/useEffectiveUserId';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDateInMalaysia } from '@/lib/helpers';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import {
  DELIVERY_MODE_LABELS,
  GOODS_FROM_LABELS,
  type SampleRequestDetail as Detail,
  type SampleRequestLine,
  type SampleRequestStatus,
} from '@/types/sampleRequest';
import { OrderInquiryStockGrid } from '../../_shared/components/OrderInquiryStockGrid';
import {
  sampleRequestsPagerQuery,
  useSampleRequestMutations,
  useSampleRequestQuery,
} from '../hooks/useSampleRequests';
import { downloadSampleRequestPdf } from '../services/sampleRequestService';

const BASE_PATH = '/project-sales/sample-requests';

interface Move {
  to: SampleRequestStatus;
  label: string;
  /** Who may make it: `process` holders, or the request's owner too (`either`). */
  needs: 'process' | 'either';
  ask?: 'date' | 'reason';
  tone: 'emerald' | 'primary' | 'destructive';
}

/** The moves the card allows from each status (rule 10); the server is the authority. */
const MOVES: Record<SampleRequestStatus, Move[]> = {
  draft: [{ to: 'cancelled', label: 'Cancel request', needs: 'either', tone: 'destructive' }],
  submitted: [
    { to: 'processing', label: 'Change to processing', needs: 'process', tone: 'emerald' },
    { to: 'cancelled', label: 'Cancel request', needs: 'either', tone: 'destructive' },
  ],
  processing: [
    {
      to: 'customer_confirmed',
      label: 'Customer confirmed',
      needs: 'process',
      ask: 'date',
      tone: 'primary',
    },
    {
      to: 'cancelled',
      label: 'Cancel request',
      needs: 'process',
      ask: 'reason',
      tone: 'destructive',
    },
  ],
  customer_confirmed: [
    { to: 'delivered', label: 'Mark delivered', needs: 'process', ask: 'date', tone: 'primary' },
  ],
  delivered: [],
  cancelled: [],
};

const MOVE_CLASS: Record<Move['tone'], string> = {
  emerald: 'bg-emerald-600 text-white hover:bg-emerald-700',
  primary: '',
  destructive: 'border-destructive text-destructive hover:bg-destructive/10',
};

const day = (value: string | null | undefined) => (value ? formatDateInMalaysia(value) : '');

/** One label / value cell of the document card; an empty value reads as `empty`. */
function Field({
  label,
  children,
  empty = 'None',
  wide = false,
}: {
  label: string;
  children?: ReactNode;
  empty?: string;
  wide?: boolean;
}) {
  return (
    <div className={wide ? 'sm:col-span-2' : undefined}>
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="font-medium whitespace-pre-wrap">{children || empty}</p>
    </div>
  );
}

function AskDialog({
  move,
  busy,
  onCancel,
  onConfirm,
}: {
  move: Move;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (value: string) => void;
}) {
  const [value, setValue] = useState('');
  const ready = value.trim().length > 0;
  return (
    <Dialog open onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="w-full max-w-md">
        <DialogHeader>
          <DialogTitle>{move.label}</DialogTitle>
          <DialogDescription className="sr-only">
            {move.ask === 'date' ? 'Enter the date.' : 'Enter the reason.'}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-1.5">
          {move.ask === 'date' ? (
            <>
              <Label htmlFor="sr-move-date">Date *</Label>
              <Input
                id="sr-move-date"
                type="date"
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </>
          ) : (
            <>
              <Label htmlFor="sr-move-reason">Reason *</Label>
              <Textarea
                id="sr-move-reason"
                rows={3}
                value={value}
                onChange={(e) => setValue(e.target.value)}
              />
            </>
          )}
        </DialogBody>
        <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onCancel}>
            Back
          </Button>
          <Button disabled={!ready || busy} onClick={() => onConfirm(value.trim())}>
            {move.label}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function InternalDialog({
  request,
  busy,
  onCancel,
  onSave,
}: {
  request: Detail;
  busy: boolean;
  onCancel: () => void;
  onSave: (value: { consignment_no: string | null; po_no: string | null }) => void;
}) {
  const [consignment, setConsignment] = useState(request.consignment_no ?? '');
  const [poNo, setPoNo] = useState(request.po_no ?? '');
  return (
    <Dialog open onOpenChange={(next) => !next && onCancel()}>
      <DialogContent className="w-full max-w-md">
        <DialogHeader>
          <DialogTitle>Set consignment / PO no</DialogTitle>
          <DialogDescription className="sr-only">
            Enter the consignment and PO numbers.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="sr-consignment">Consignment no</Label>
            <Input
              id="sr-consignment"
              value={consignment}
              onChange={(e) => setConsignment(e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="sr-po">PO no</Label>
            <Input id="sr-po" value={poNo} onChange={(e) => setPoNo(e.target.value)} />
          </div>
        </DialogBody>
        <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
          <Button variant="outline" onClick={onCancel}>
            Back
          </Button>
          <Button
            disabled={busy}
            onClick={() =>
              onSave({ consignment_no: consignment.trim() || null, po_no: poNo.trim() || null })
            }
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function SampleRequestDetail({ requestId }: { requestId: string }) {
  const router = useRouter();
  const { data: request, isLoading, isError, error } = useSampleRequestQuery(requestId);
  const { submit, move, saveInternal } = useSampleRequestMutations();
  const canProcess = useHasPermission('projects.sample_requests.process');
  const canCreate = useHasPermission('projects.sample_requests.create');
  // The history endpoint answers only admins and `view` holders; a salesperson who holds
  // just `create` would get a 403 toast on every page.
  const canViewRecord = useHasPermission('projects.sample_requests.view');
  const userId = useEffectiveUserId();
  const [asking, setAsking] = useState<Move | null>(null);
  const [internalOpen, setInternalOpen] = useState(false);
  const [expanded, setExpanded] = useState<ExpandedState>({});

  // Delete draft asks nothing (D7): a countdown takes the primary area, Cancel is the way back.
  const deletion = useDeferredAction({
    actionKey: 'sample_request.delete',
    entityType: 'sample_request',
    entityId: requestId,
    verb: 'Deleting',
    subject: request?.request_no ?? 'this draft',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Draft deleted',
    invalidateKeys: [['sample-requests']],
    onCommitted: () => router.push(BASE_PATH),
  });

  const lineColumns = useMemo<ColumnDef<SampleRequestLine>[]>(
    () => [
      {
        id: 'line_no',
        accessorKey: 'line_no',
        header: ({ column }) => <DataGridColumnHeader title="No" column={column} />,
        size: 80,
        cell: ({ row }) => (
          <button
            type="button"
            className="inline-flex items-center gap-1"
            aria-label={`Stock for line ${row.original.line_no}`}
            aria-expanded={row.getIsExpanded()}
            onClick={(event) => {
              event.stopPropagation();
              row.toggleExpanded();
            }}
          >
            {row.getIsExpanded() ? (
              <ChevronDown className="size-4" />
            ) : (
              <ChevronRight className="size-4" />
            )}
            {row.original.line_no}
          </button>
        ),
        meta: {
          headerTitle: 'No',
          expandedContent: (line: SampleRequestLine) => (
            <div className="px-2 pb-3">
              <OrderInquiryStockGrid productId={line.product_id} location="BRW" />
            </div>
          ),
        },
      },
      {
        id: 'brand_name',
        accessorFn: (row) => row.brand_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Brand" column={column} />,
        size: 140,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.brand_name ?? undefined}>
            {row.original.brand_name || '-'}
          </span>
        ),
        meta: { headerTitle: 'Brand' },
      },
      {
        id: 'model',
        accessorFn: (row) => row.product_code ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Model" column={column} />,
        size: 170,
        cell: ({ row }) => (
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className="truncate" title={row.original.product_code ?? undefined}>
              {row.original.product_code || '-'}
            </span>
            {!row.original.product_active && <Badge variant="secondary">Inactive</Badge>}
          </div>
        ),
        meta: { headerTitle: 'Model' },
      },
      {
        id: 'description',
        accessorFn: (row) => row.product_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Description" column={column} />,
        size: 240,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.product_name ?? undefined}>
            {row.original.product_name || '-'}
          </span>
        ),
        meta: { headerTitle: 'Description' },
      },
      {
        id: 'qty',
        accessorKey: 'qty',
        header: ({ column }) => <DataGridColumnHeader title="Qty" column={column} />,
        size: 80,
        cell: ({ row }) => row.original.qty,
        meta: { headerTitle: 'Qty' },
      },
      {
        id: 'remark',
        accessorFn: (row) => row.remark ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Remark" column={column} />,
        size: 200,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.remark ?? undefined}>
            {row.original.remark || '-'}
          </span>
        ),
        meta: { headerTitle: 'Remark' },
      },
    ],
    [],
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }
  if (isError || !request) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 px-6 py-10 text-center text-sm">
        {error instanceof Error ? error.message : 'This sample request could not be loaded.'}
      </div>
    );
  }

  const r: Detail = request;
  const isDraft = r.status === 'draft';
  const isOwner = Boolean(userId) && r.salesperson_id === userId;
  const ownerActs = isOwner && canCreate;
  const busy = move.isPending || submit.isPending;
  const allowed = (m: Move) => canProcess || (m.needs === 'either' && ownerActs);
  const moves = MOVES[r.status].filter(allowed);

  const run = async (m: Move, value?: string) => {
    try {
      await move.mutateAsync({
        id: r.id,
        body: {
          to: m.to,
          ...(m.ask === 'date' && value ? { date: value } : {}),
          ...(m.ask === 'reason' && value ? { reason: value } : {}),
        },
      });
      setAsking(null);
    } catch {
      // the mutation's own onError toast names the refusal
    }
  };

  const stamp = r.submitted_at ?? r.created_at;
  const project = r.project_id
    ? [r.project_code, r.project_name].filter(Boolean).join(' - ')
    : r.project_name;
  const shipTo = [r.ship_to_name, r.ship_to_address].filter(Boolean).join(', ');
  const siteContact = [r.site_contact_name, r.site_contact_phone, r.site_contact_note]
    .filter(Boolean)
    .join(', ');

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 className="break-words text-2xl font-bold">Sample Request - {r.request_no}</h2>
          <p className="text-sm text-muted-foreground">
            {r.submitted_at ? 'Submitted' : 'Created'}: {stamp ? formatDateInMalaysia(stamp) : '-'}
            {' · '}
            <span className={`${STATUS_PILL_BASE} ${statusPillClass(r.status)}`}>
              {r.status_label}
            </span>
          </p>
        </div>
        <DetailActions
          pager={{
            ...sampleRequestsPagerQuery,
            detailPath: BASE_PATH,
            currentId: requestId,
            ariaLabel: 'sample request',
          }}
          gearLabel="Sample request actions"
          pendingAction={deletion.countdown}
          gear={
            <DetailActionsMenu ariaLabel="Sample request actions">
              <DropdownMenuItem
                onSelect={() => {
                  void downloadSampleRequestPdf(r.id, r.request_no).catch((e) =>
                    toast.error(e instanceof Error ? e.message : 'Failed to download the PDF'),
                  );
                }}
              >
                <Printer className="size-4" />
                Print / Download PDF
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => {
                  void navigator.clipboard
                    .writeText(window.location.href)
                    .then(() => toast.success('Link copied'))
                    .catch(() => toast.error('Could not copy the link'));
                }}
              >
                <Link2 className="size-4" />
                Copy link
              </DropdownMenuItem>
              {canProcess && r.status !== 'cancelled' && (
                <DropdownMenuItem onSelect={() => setInternalOpen(true)}>
                  <Settings2 className="size-4" />
                  Set consignment / PO no
                </DropdownMenuItem>
              )}
              {isDraft && ownerActs && (
                <DropdownMenuItem
                  variant="destructive"
                  disabled={deletion.isBlocked}
                  onSelect={() => deletion.start()}
                >
                  <Trash2 className="size-4" />
                  Delete draft
                </DropdownMenuItem>
              )}
            </DetailActionsMenu>
          }
          primary={
            <div className="flex flex-wrap items-center gap-2">
              {isDraft && ownerActs && (
                <Button disabled={busy} onClick={() => submit.mutate(r.id)}>
                  Submit
                </Button>
              )}
              {moves.map((m) => (
                <Button
                  key={m.to}
                  variant={m.tone === 'destructive' ? 'outline' : 'primary'}
                  className={MOVE_CLASS[m.tone]}
                  disabled={busy}
                  onClick={() => (m.ask ? setAsking(m) : void run(m))}
                >
                  {m.label}
                </Button>
              ))}
              {isDraft && ownerActs && (
                <Button variant="outline" asChild>
                  <Link href={`${BASE_PATH}/${r.id}/edit`}>Edit</Link>
                </Button>
              )}
            </div>
          }
        />
      </div>

      <Card className="border-2 shadow-sm">
        <CardContent className="px-5 pb-8 pt-6 sm:px-10">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Badge variant="secondary">{r.source === 'portal' ? 'Portal' : 'Staff'}</Badge>
            <span className={`${STATUS_PILL_BASE} ${statusPillClass(r.status)}`}>
              {r.status_label}
            </span>
          </div>
          <h2 className="mb-6 border-b border-border pb-4 text-center text-xl font-semibold tracking-tight">
            Sample Request
          </h2>
          <div className="grid grid-cols-1 gap-x-10 gap-y-5 sm:grid-cols-2 [&>div]:min-w-0 [&_p]:break-words">
            <Field label="Salesperson">{r.salesperson_name}</Field>
            <div>
              <p className="text-sm text-muted-foreground">Customer</p>
              <p className="flex flex-wrap items-center gap-2 font-medium">
                {[r.customer_code, r.customer_name].filter(Boolean).join(' - ') || 'None'}
                {!r.customer_code && r.customer_name && (
                  <Badge variant="secondary">No debtor code</Badge>
                )}
              </p>
            </div>
            <Field label="Project" wide>
              {project}
              {!r.project_id && r.project_name ? ' (not registered)' : ''}
            </Field>
            <Field label="For (scope)">{r.scope_note}</Field>
            <Field label="Request Delivery Date">
              {day(r.delivery_date)}
            </Field>
            <Field label="Delivery">
              {r.delivery_mode ? DELIVERY_MODE_LABELS[r.delivery_mode] : null}
            </Field>
            <Field label="Ship to" wide>
              {shipTo}
            </Field>
            <Field label="Site contact" wide>
              {siteContact}
            </Field>
            <Field label="Sample board" wide>
              {r.board_mounting ? `Mount & label: ${r.board_label ?? ''}` : null}
            </Field>
            <Field label="Loan Period">
              {r.loan_value ? `${r.loan_value} ${r.loan_unit}` : null}
            </Field>
            <Field label="Goods Taken From">
              {r.goods_from ? GOODS_FROM_LABELS[r.goods_from] : null}
            </Field>
            <Field label="Purpose" wide>
              {r.purpose}
            </Field>
            {r.confirmed_at && <Field label="Customer confirmed">{day(r.confirmed_at)}</Field>}
            {r.delivered_at && <Field label="Delivered">{day(r.delivered_at)}</Field>}
            {r.return_due && <Field label="Return due">{day(r.return_due)}</Field>}
            {r.cancel_reason && (
              <Field label="Cancel reason" wide>
                {r.cancel_reason}
              </Field>
            )}
            <Field label="Consignment No (internal)" empty="Not set">
              {r.consignment_no}
            </Field>
            <Field label="PO No (internal)" empty="Not set">
              {r.po_no}
            </Field>
          </div>

          <div className="mt-8">
            <p className="mb-3 text-sm font-medium">Line items</p>
            <PanelDataGrid
              columns={lineColumns}
              rows={r.lines}
              getRowId={(row) => row.id}
              // Column preferences are keyed by a slug the reader holds: view for the
              // coordinator, create for the owning salesperson (list_query._can_view_listing_key).
              listingKey={
                canViewRecord
                  ? 'projects.sample_requests.view::sample-request-lines'
                  : 'projects.sample_requests.create::sample-request-lines'
              }
              emptyTitle="No items"
              paginate={false}
              expanded={expanded}
              onExpandedChange={setExpanded}
            />
          </div>
        </CardContent>
      </Card>

      {canViewRecord && (
        <AuditTrail entityType="project_sample_requests" entityId={requestId} title="Audit Trail" />
      )}

      {asking && (
        <AskDialog
          move={asking}
          busy={move.isPending}
          onCancel={() => setAsking(null)}
          onConfirm={(value) => void run(asking, value)}
        />
      )}
      {internalOpen && (
        <InternalDialog
          request={r}
          busy={saveInternal.isPending}
          onCancel={() => setInternalOpen(false)}
          onSave={(body) =>
            saveInternal.mutate({ id: r.id, body }, { onSuccess: () => setInternalOpen(false) })
          }
        />
      )}
    </div>
  );
}
