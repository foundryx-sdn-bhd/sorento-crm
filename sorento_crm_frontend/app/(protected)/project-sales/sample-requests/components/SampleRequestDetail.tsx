'use client';

import { useMemo, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { type ColumnDef } from '@tanstack/react-table';
import { Printer } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Dialog,
  DialogBody,
  DialogContent,
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
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDate } from '@/lib/helpers';
import { codeWithName } from '@/lib/product-display';
import { STATUS_PILL_BASE, statusPillClass } from '@/lib/status-pill';
import {
  DELIVERY_MODE_LABELS,
  GOODS_FROM_LABELS,
  type SampleRequestDetail as Detail,
  type SampleRequestLine,
  type SampleRequestStatus,
} from '@/types/sampleRequest';
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
  /** Who may make it: `process` holders, or the request's owner (a `create` holder). */
  needs: 'process' | 'owner' | 'either';
  ask?: 'date' | 'reason';
}

/** The moves the card allows from each status (rule 10); the server is the authority. */
const MOVES: Record<SampleRequestStatus, Move[]> = {
  draft: [{ to: 'cancelled', label: 'Cancel', needs: 'either' }],
  submitted: [
    { to: 'processing', label: 'Start processing', needs: 'process' },
    { to: 'cancelled', label: 'Cancel', needs: 'either' },
  ],
  processing: [
    { to: 'customer_confirmed', label: 'Customer confirmed', needs: 'process', ask: 'date' },
    { to: 'cancelled', label: 'Cancel', needs: 'process', ask: 'reason' },
  ],
  customer_confirmed: [{ to: 'delivered', label: 'Delivered', needs: 'process', ask: 'date' }],
  delivered: [],
  cancelled: [],
};

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0 space-y-0.5">
      <p className="text-xs text-muted-foreground">{label}</p>
      <div className="break-words text-sm">{children || '-'}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 sm:grid-cols-2">{children}</div>
      </CardContent>
    </Card>
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

export default function SampleRequestDetail({ requestId }: { requestId: string }) {
  const { data: request, isLoading, isError, error } = useSampleRequestQuery(requestId);
  const { submit, move, saveInternal } = useSampleRequestMutations();
  const canProcess = useHasPermission('projects.sample_requests.process');
  const canCreate = useHasPermission('projects.sample_requests.create');
  const [asking, setAsking] = useState<Move | null>(null);
  const [consignment, setConsignment] = useState<string | null>(null);
  const [poNo, setPoNo] = useState<string | null>(null);

  const lineColumns = useMemo<ColumnDef<SampleRequestLine>[]>(
    () => [
      {
        id: 'line_no',
        accessorKey: 'line_no',
        header: ({ column }) => <DataGridColumnHeader title="#" column={column} />,
        size: 60,
        cell: ({ row }) => row.original.line_no,
        meta: { headerTitle: '#' },
      },
      {
        id: 'item',
        accessorFn: (row) => codeWithName(row.product_code, row.product_name),
        header: ({ column }) => <DataGridColumnHeader title="Item" column={column} />,
        size: 320,
        cell: ({ row }) => {
          const text = codeWithName(row.original.product_code, row.original.product_name);
          return (
            <span className="truncate" title={text}>
              {text}
              {row.original.alt_product_code ? ` OR ${row.original.alt_product_code}` : ''}
            </span>
          );
        },
        meta: { headerTitle: 'Item' },
      },
      {
        id: 'brand_name',
        accessorFn: (row) => row.brand_name ?? '',
        header: ({ column }) => <DataGridColumnHeader title="Brand" column={column} />,
        size: 140,
        cell: ({ row }) => row.original.brand_name || '-',
        meta: { headerTitle: 'Brand' },
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
        header: ({ column }) => <DataGridColumnHeader title="Remarks" column={column} />,
        size: 220,
        cell: ({ row }) => (
          <span className="truncate" title={row.original.remark ?? undefined}>
            {row.original.remark || '-'}
          </span>
        ),
        meta: { headerTitle: 'Remarks' },
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
  const busy = move.isPending || submit.isPending;
  const allowed = (m: Move) =>
    m.needs === 'process' ? canProcess : m.needs === 'owner' ? canCreate : canProcess || canCreate;
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

  const internalValue = (current: string | null, edited: string | null) => edited ?? current ?? '';
  const internalDirty =
    (consignment !== null && consignment !== (r.consignment_no ?? '')) ||
    (poNo !== null && poNo !== (r.po_no ?? ''));

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 className="break-words text-2xl font-bold">Sample Request - {r.request_no}</h2>
          <p className="text-sm text-muted-foreground">
            Created: {r.created_at ? formatDate(new Date(r.created_at)) : '-'}
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
                Print
              </DropdownMenuItem>
            </DetailActionsMenu>
          }
          primary={
            <div className="flex flex-wrap items-center gap-2">
              {isDraft && canCreate && (
                <Button disabled={busy} onClick={() => submit.mutate(r.id)}>
                  Submit
                </Button>
              )}
              {moves.map((m) => (
                <Button
                  key={m.to}
                  variant={m.to === 'cancelled' ? 'outline' : 'primary'}
                  disabled={busy}
                  onClick={() => (m.ask ? setAsking(m) : void run(m))}
                >
                  {m.label}
                </Button>
              ))}
              {isDraft && canCreate && (
                <Button variant="outline" asChild>
                  <Link href={`${BASE_PATH}/${r.id}/edit`}>Edit</Link>
                </Button>
              )}
            </div>
          }
        />
      </div>

      <Section title="Customer & project">
        <Field label="Customer">
          {[r.customer_code, r.customer_name].filter(Boolean).join(' - ')}
        </Field>
        <Field label="Project">
          {r.project_id
            ? [r.project_code, r.project_name].filter(Boolean).join(' - ')
            : r.project_name}
        </Field>
        <Field label="Quotation">{r.quotation_label}</Field>
        <Field label="For (scope)">{r.scope_note}</Field>
        <Field label="Salesperson">{r.salesperson_name}</Field>
        <Field label="Requested">
          {r.request_date ? formatDate(new Date(r.request_date)) : null}
        </Field>
      </Section>

      <PanelDataGrid
        title="Lines"
        toolbar={
          r.board_mounting ? (
            <span className="text-sm text-muted-foreground">Board: {r.board_label || '-'}</span>
          ) : undefined
        }
        columns={lineColumns}
        rows={r.lines}
        getRowId={(row) => row.id}
        listingKey="projects.sample_requests.view::sample-request-lines"
        emptyTitle="No items"
        paginate={false}
      />

      <Section title="Delivery">
        <Field label="Delivery">
          {r.deliver_immediately
            ? 'Immediate'
            : r.delivery_date
              ? formatDate(new Date(r.delivery_date))
              : null}
        </Field>
        <Field label="Delivery mode">
          {r.delivery_mode ? DELIVERY_MODE_LABELS[r.delivery_mode] : null}
        </Field>
        <Field label="Ship to">{r.ship_to_name}</Field>
        <Field label="Shipping address">{r.ship_to_address}</Field>
        <Field label="Site contact">
          {[r.site_contact_name, r.site_contact_phone].filter(Boolean).join(' · ')}
        </Field>
        <Field label="Contact note">{r.site_contact_note}</Field>
      </Section>

      <Section title="Additional Information">
        <Field label="Loan period">
          {r.loan_value ? `${r.loan_value} ${r.loan_unit}` : null}
        </Field>
        <Field label="Goods from">{r.goods_from ? GOODS_FROM_LABELS[r.goods_from] : null}</Field>
        <Field label="Purpose">{r.purpose}</Field>
        <Field label="Return due">
          {r.return_due ? formatDate(new Date(r.return_due)) : null}
        </Field>
        {r.cancel_reason && <Field label="Cancel reason">{r.cancel_reason}</Field>}
      </Section>

      <Card>
        <CardHeader>
          <CardTitle>Consignment</CardTitle>
        </CardHeader>
        <CardContent>
          {canProcess && r.status !== 'cancelled' ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="sr-consignment">Consignment no</Label>
                <Input
                  id="sr-consignment"
                  value={internalValue(r.consignment_no, consignment)}
                  onChange={(e) => setConsignment(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="sr-po">PO no</Label>
                <Input
                  id="sr-po"
                  value={internalValue(r.po_no, poNo)}
                  onChange={(e) => setPoNo(e.target.value)}
                />
              </div>
              <div className="sm:col-span-2">
                <Button
                  variant="outline"
                  disabled={!internalDirty || saveInternal.isPending}
                  onClick={() =>
                    saveInternal.mutate(
                      {
                        id: r.id,
                        body: {
                          consignment_no: internalValue(r.consignment_no, consignment).trim() || null,
                          po_no: internalValue(r.po_no, poNo).trim() || null,
                        },
                      },
                      {
                        onSuccess: () => {
                          setConsignment(null);
                          setPoNo(null);
                        },
                      },
                    )
                  }
                >
                  Save
                </Button>
              </div>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Consignment no">{r.consignment_no}</Field>
              <Field label="PO no">{r.po_no}</Field>
            </div>
          )}
        </CardContent>
      </Card>

      <AuditTrail entityType="project_sample_requests" entityId={requestId} title="Audit Trail" />

      {asking && (
        <AskDialog
          move={asking}
          busy={move.isPending}
          onCancel={() => setAsking(null)}
          onConfirm={(value) => void run(asking, value)}
        />
      )}
    </div>
  );
}
