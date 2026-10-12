'use client';

import * as React from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { DateRangePicker } from '@/components/ui/date-range-picker';
import { Sheet, SheetBody, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { EventTimeline, type TimelineEvent } from '@/components/common/EventTimeline';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import { usePlanningHistory } from '../../_shared/hooks/useFulfilmentPlanning';
import type {
  PlanningHistoryEvent,
  PlanningHistoryKind,
  PlanningHistoryLine,
} from '../../_shared/types/fulfilmentPlanning.types';

const KIND_LABEL: Record<PlanningHistoryKind, string> = {
  confirm_refused: 'Confirm refused',
  revision_confirmed: 'Revision confirmed',
  oi_cancelled: 'Order cancelled',
  autocount_update: 'AutoCount update',
  email_sent: 'Email',
};
const KIND_OPTIONS = (Object.keys(KIND_LABEL) as PlanningHistoryKind[]).map((value) => ({
  value,
  label: KIND_LABEL[value],
}));

const CAUSE_LABEL: Record<string, string> = {
  undecided_change: 'Change not decided',
  superseded: 'Replaced by a revision',
  book_left_nothing: 'Book left nothing to buy',
  cancel_so: 'Moved to Cancel SO',
  planning_change_retired: 'Line closed in the book',
  reserve_moved: 'Reserve moved',
  manual: 'Cancelled by hand',
  borrow_retired: 'Borrow released',
  not_recorded: 'Not recorded',
};
const ACTION_LABEL: Record<string, string> = {
  ordered: 'Ordered',
  cancelled: 'Cancelled',
  reduced: 'Reduced',
  order_back: 'Order back',
};
const COUNT_LABEL: Record<string, string> = {
  qty_down: 'Qty down',
  qty_up: 'Qty up',
  delayed: 'Delayed',
  advanced: 'Advanced',
  cancelled: 'Cancelled',
  added: 'Added',
  product_changed: 'Product changed',
  confirmed: 'Confirmed',
  not_decided: 'Not decided',
  replaced: 'Replaced',
  decided: 'Decided',
  failed: 'Failed',
};
const EMAIL_COUNT_LABEL: Record<string, string> = {
  cancel: 'Cancel',
  order: 'Order',
  order_back: 'Order back',
};

type Tone = 'destructive' | 'info' | 'warning' | 'secondary';

type Chip = { label: string; count: number; tone: Tone };

/** The count badges an event carries beside its title. */
function chipsOf(event: PlanningHistoryEvent): Chip[] {
  const chips: Chip[] = [];
  if (event.kind === 'revision_confirmed') {
    for (const action of ['cancelled', 'ordered', 'order_back', 'reduced'] as const) {
      const count = event.lines.filter((line) => line.action === action).length;
      if (count > 0)
        chips.push({
          label: ACTION_LABEL[action],
          count,
          tone: action === 'cancelled' ? 'destructive' : 'info',
        });
    }
    if ((event.reissued_unchanged ?? 0) > 0)
      chips.push({ label: 'Re-issued unchanged', count: event.reissued_unchanged ?? 0, tone: 'secondary' });
  } else if (event.kind === 'confirm_refused') {
    const byReason = new Map<string, number>();
    for (const line of event.lines)
      byReason.set(line.reason ?? 'Refused', (byReason.get(line.reason ?? 'Refused') ?? 0) + 1);
    byReason.forEach((count, label) => chips.push({ label, count, tone: 'warning' }));
  } else if (event.kind === 'autocount_update') {
    for (const [key, count] of Object.entries(event.counts ?? {})) {
      if (COUNT_LABEL[key] && count > 0)
        chips.push({ label: COUNT_LABEL[key], count, tone: key === 'not_decided' ? 'warning' : 'secondary' });
    }
  } else if (event.kind === 'email_sent') {
    for (const [key, count] of Object.entries(event.counts ?? {})) {
      if (EMAIL_COUNT_LABEL[key] && count > 0)
        chips.push({
          label: EMAIL_COUNT_LABEL[key],
          count,
          tone: key === 'cancel' ? 'destructive' : 'info',
        });
    }
  } else if (event.kind === 'oi_cancelled') {
    const cause = event.lines[0]?.cause;
    if (cause) chips.push({ label: CAUSE_LABEL[cause] ?? cause, count: 0, tone: 'secondary' });
  }
  return chips;
}

/** `dd/mm/yyyy` (Malaysia) to `yyyy-mm-dd`, so a day compares with the range picker's value. */
function isoDay(at: string | null): string | null {
  if (!at) return null;
  const [d, m, y] = formatDateInMalaysia(at).split('/');
  return y && m && d ? `${y}-${m}-${d}` : null;
}

function qtyText(value?: number | null): string {
  return value == null ? '' : String(value);
}

type ColumnSet = { head: string[]; cells: (line: PlanningHistoryLine) => React.ReactNode[] };

const COLUMNS: Record<PlanningHistoryKind, ColumnSet> = {
  confirm_refused: {
    head: ['Line', 'Product', 'Change', 'Reason'],
    cells: (l) => [l.so_line_no, l.item_code, l.change ?? '', l.reason ?? ''],
  },
  revision_confirmed: {
    head: ['Line', 'Product', 'Action', 'Qty', 'Cause'],
    cells: (l) => [
      l.so_line_no,
      l.item_code,
      ACTION_LABEL[l.action ?? ''] ?? l.action ?? '',
      l.action === 'reduced' && l.previous_qty != null
        ? `${qtyText(l.previous_qty)} -> ${qtyText(l.qty)}`
        : qtyText(l.qty),
      [l.cause ? (CAUSE_LABEL[l.cause] ?? l.cause) : '', l.change].filter(Boolean).join(' · '),
    ],
  },
  oi_cancelled: {
    head: ['Line', 'Product', 'Qty', 'Cause'],
    cells: (l) => [
      l.so_line_no,
      l.item_code,
      qtyText(l.qty),
      l.cause ? (CAUSE_LABEL[l.cause] ?? l.cause) : '',
    ],
  },
  autocount_update: {
    head: ['Line', 'Product', 'Change', 'Now'],
    cells: (l) => [
      l.so_line_no,
      l.item_code,
      l.change ?? '',
      COUNT_LABEL[l.state ?? ''] ?? l.state ?? '',
    ],
  },
  email_sent: {
    head: ['Product', 'Remark'],
    cells: (l) => [l.item_code, l.remark ?? ''],
  },
};

function LinesTable({ event, lines }: { event: PlanningHistoryEvent; lines: PlanningHistoryLine[] }) {
  const columns = COLUMNS[event.kind];
  if (lines.length === 0) return null;
  return (
    <div className="mt-2 overflow-x-auto">
    <Table className="text-xs">
      <TableHeader>
        <TableRow>
          {columns.head.map((name) => (
            <TableHead key={name} className="h-8 px-2">
              {name}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.map((line, index) => (
          <TableRow key={`${line.so_line_no ?? ''}-${line.item_code ?? ''}-${index}`}>
            {columns.cells(line).map((cell, cellIndex) => (
              <TableCell key={cellIndex} className="px-2 py-1.5 break-words">
                {cell}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
    </div>
  );
}

/** The stored email's body: a line holding `|` is a table row, any other line is plain text. */
function EmailBody({ text, product }: { text: string; product: string | null }) {
  const lines = text.split('\n').filter((line) => line.trim() !== '');
  const rows = lines.filter((line) => line.includes('|'));
  const prose = lines.filter((line) => !line.includes('|'));
  return (
    <div className="space-y-2 text-sm">
      {prose.map((line, index) => (
        <p key={index} className="break-words">
          {line}
        </p>
      ))}
      {rows.length > 0 && (
        <Table className="text-xs">
          <TableBody>
            {rows.map((row, index) => {
              const named = product != null && row.includes(product);
              return (
                <TableRow
                  key={index}
                  data-testid="history-email-row"
                  data-highlighted={named ? 'true' : undefined}
                  className={named ? 'bg-amber-50 dark:bg-amber-950/30' : undefined}
                >
                  {row.split('|').map((cell, cellIndex) => (
                    <TableCell key={cellIndex} className="px-2 py-1.5 break-words">
                      {cell.trim()}
                    </TableCell>
                  ))}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

function EmailDialog({
  event,
  product,
  onClose,
}: {
  event: PlanningHistoryEvent | null;
  product: string | null;
  onClose: () => void;
}) {
  return (
    <Dialog open={event !== null} onOpenChange={(next) => !next && onClose()}>
      {event && (
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{event.subject}</DialogTitle>
            <DialogDescription className="sr-only">Stored copy of the email</DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-3">
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Time</dt>
              <dd>{event.sent_at ? formatDateTimeInMalaysia(event.sent_at) : ''}</dd>
              <dt className="text-muted-foreground">Status</dt>
              <dd>
                <Badge variant="secondary" appearance="light" size="sm">
                  {event.status}
                </Badge>
              </dd>
              <dt className="text-muted-foreground">To</dt>
              <dd className="flex flex-col break-all">
                {(event.recipients ?? []).map((address) => (
                  <span key={address}>{address}</span>
                ))}
              </dd>
            </dl>
            <EmailBody text={event.body_text ?? ''} product={product} />
          </DialogBody>
        </DialogContent>
      )}
    </Dialog>
  );
}

export interface PlanningHistorySheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  psoIds: string[];
  /** The board's SO numbers, in the order of `psoIds`, for the SO filter. */
  soNumbers?: string[];
  /** Opens narrowed to this order. A one-order board starts on that order anyway. */
  initialSoId?: string;
}

export function PlanningHistorySheet({
  open,
  onOpenChange,
  psoIds,
  soNumbers,
  initialSoId,
}: PlanningHistorySheetProps) {
  const history = usePlanningHistory(psoIds, open);
  const [soId, setSoId] = React.useState<string | null>(
    initialSoId ?? (psoIds.length === 1 ? psoIds[0] : null),
  );
  const [product, setProduct] = React.useState<string | null>(null);
  const [line, setLine] = React.useState<string | null>(null);
  const [kinds, setKinds] = React.useState<string[]>([]);
  const [user, setUser] = React.useState<string | null>(null);
  const [range, setRange] = React.useState<{ from: string | null; to: string | null }>({
    from: null,
    to: null,
  });
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());
  const [email, setEmail] = React.useState<PlanningHistoryEvent | null>(null);

  // A different order asked for (the refusal block's History link) re-aims the filter.
  const psoKey = psoIds.join(',');
  React.useEffect(() => {
    if (open) setSoId(initialSoId ?? (psoKey.split(',').length === 1 ? psoKey : null));
  }, [open, initialSoId, psoKey]);

  const events = React.useMemo(() => history.data?.events ?? [], [history.data]);

  const options = React.useMemo(() => {
    const soLabel = new Map<string, string>();
    psoIds.forEach((id, index) => {
      if (soNumbers?.[index]) soLabel.set(id, soNumbers[index]);
    });
    events.forEach((event) => {
      if (!soLabel.has(event.pso_id) && event.so_no) soLabel.set(event.pso_id, event.so_no);
    });
    const products = new Set<string>();
    const lineNos = new Set<number>();
    const users = new Set<string>();
    events.forEach((event) => {
      event.lines.forEach((entry) => {
        if (entry.item_code) products.add(entry.item_code);
        if (entry.so_line_no != null) lineNos.add(entry.so_line_no);
      });
      if (event.actor) users.add(event.actor);
    });
    const text = (value: string) => ({ value, label: value });
    return {
      so: [...soLabel].map(([value, label]) => ({ value, label })),
      product: [...products].sort().map(text),
      line: [...lineNos].sort((a, b) => a - b).map((n) => text(String(n))),
      user: [...users].sort().map(text),
    };
  }, [events, psoIds, soNumbers]);

  // The items a line number stands for, so an email (which names items, not lines) can follow
  // the line filter.
  const itemsOfLine = React.useMemo(() => {
    if (!line) return new Set<string>();
    const items = new Set<string>();
    events.forEach((event) =>
      event.lines.forEach((entry) => {
        if (String(entry.so_line_no) === line && entry.item_code) items.add(entry.item_code);
      }),
    );
    return items;
  }, [events, line]);

  const visible = React.useMemo(() => {
    const out: { event: PlanningHistoryEvent; lines: PlanningHistoryLine[] }[] = [];
    for (const event of events) {
      if (soId && event.pso_id !== soId) continue;
      if (kinds.length > 0 && !kinds.includes(event.kind)) continue;
      if (user && event.actor !== user) continue;
      const day = isoDay(event.at);
      if (range.from && (!day || day < range.from)) continue;
      if (range.to && (!day || day > range.to)) continue;
      let lines = event.lines;
      if (product) lines = lines.filter((entry) => entry.item_code === product);
      if (line)
        lines = lines.filter((entry) =>
          entry.so_line_no != null
            ? String(entry.so_line_no) === line
            : entry.item_code != null && itemsOfLine.has(entry.item_code),
        );
      if ((product || line) && lines.length === 0) continue;
      out.push({ event, lines });
    }
    return out;
  }, [events, soId, kinds, user, range, product, line, itemsOfLine]);

  const timeline: TimelineEvent[] = visible.map(({ event, lines }) => {
    const isOpen = expanded.has(event.id);
    // The badges count what is on show: a product or line filter narrows `lines`.
    const narrowed = lines.length !== event.lines.length;
    const chips = chipsOf(narrowed ? { ...event, lines, reissued_unchanged: 0 } : event);
    return {
      id: event.id,
      testId: `history-event-${event.id}`,
      at: event.at,
      actor: event.actor,
      tone: event.kind === 'confirm_refused' ? 'alert' : 'default',
      title: (
        <button
          type="button"
          aria-expanded={isOpen}
          className="flex items-center gap-1 text-start"
          onClick={() =>
            setExpanded((current) => {
              const next = new Set(current);
              if (next.has(event.id)) next.delete(event.id);
              else next.add(event.id);
              return next;
            })
          }
        >
          {isOpen ? (
            <ChevronDown className="size-3.5 shrink-0" aria-hidden />
          ) : (
            <ChevronRight className="size-3.5 shrink-0" aria-hidden />
          )}
          <span>{KIND_LABEL[event.kind]}</span>
        </button>
      ),
      tags: (
        <>
          {event.so_no && <span className="text-xs text-muted-foreground">{event.so_no}</span>}
          {event.kind === 'revision_confirmed' && (
            <span className="text-xs text-muted-foreground">{`Rev ${event.revision_no}`}</span>
          )}
        </>
      ),
      detail: (
        <div>
          {event.kind === 'email_sent' && event.subject && (
            <p className="break-words">{event.subject}</p>
          )}
          {(chips.length > 0 || event.kind === 'email_sent') && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5">
              {chips.map((chip) => (
                <Badge key={chip.label} variant={chip.tone} appearance="light" size="sm">
                  {chip.count > 0 ? `${chip.label} ${chip.count}` : chip.label}
                </Badge>
              ))}
              {event.kind === 'email_sent' && (
                <Button type="button" variant="link" size="sm" onClick={() => setEmail(event)}>
                  Open email
                </Button>
              )}
            </div>
          )}
          {isOpen && <LinesTable event={event} lines={lines} />}
        </div>
      ),
    };
  });

  const clearAll = () => {
    setSoId(null);
    setProduct(null);
    setLine(null);
    setKinds([]);
    setUser(null);
    setRange({ from: null, to: null });
  };

  return (
    <>
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent side="right" className="w-full sm:max-w-2xl">
          <SheetHeader className="space-y-3">
            <SheetTitle>History</SheetTitle>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <SearchableSelect
                clearable
                value={soId ?? ''}
                onChange={(next) => setSoId(next || null)}
                options={options.so}
                placeholder="SO"
                aria-label="SO"
              />
              <SearchableSelect
                clearable
                value={product ?? ''}
                onChange={(next) => setProduct(next || null)}
                options={options.product}
                placeholder="Product"
                aria-label="Product"
              />
              <SearchableSelect
                clearable
                value={line ?? ''}
                onChange={(next) => setLine(next || null)}
                options={options.line}
                placeholder="Line"
                aria-label="Line"
              />
              <SearchableMultiSelect
                clearable
                value={kinds}
                onChange={setKinds}
                options={KIND_OPTIONS}
                placeholder="Event"
                id="history-event-filter"
              />
              <SearchableSelect
                clearable
                value={user ?? ''}
                onChange={(next) => setUser(next || null)}
                options={options.user}
                placeholder="User"
                aria-label="User"
              />
              <DateRangePicker
                from={range.from}
                to={range.to}
                onChange={setRange}
                aria-label="Date range"
              />
            </div>
            <div>
              <Button type="button" variant="ghost" size="sm" onClick={clearAll}>
                Clear
              </Button>
            </div>
          </SheetHeader>
          <SheetBody>
            {history.isLoading ? (
              <div className="space-y-3">
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
                <Skeleton className="h-10 w-full" />
              </div>
            ) : history.isError ? (
              <div className="py-10 text-center">
                <h3 className="text-sm font-semibold">Could not load the history</h3>
                <Button type="button" variant="outline" size="sm" className="mt-3" onClick={() => history.refetch()}>
                  Retry
                </Button>
              </div>
            ) : events.length === 0 ? (
              <div className="py-10 text-center">
                <h3 className="text-sm font-semibold">No history</h3>
                <p className="mt-1 text-sm text-muted-foreground">Nothing recorded for these orders yet.</p>
              </div>
            ) : timeline.length === 0 ? (
              <div className="py-10 text-center">
                <h3 className="text-sm font-semibold">No events match</h3>
              </div>
            ) : (
              <div data-testid="history-timeline">
                <EventTimeline events={timeline} />
              </div>
            )}
          </SheetBody>
        </SheetContent>
      </Sheet>
      <EmailDialog event={email} product={product} onClose={() => setEmail(null)} />
    </>
  );
}
