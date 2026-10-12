/**
 * Phase 2 red tests, PLANNING-WHY (AC-W12 sheet, AC-W14 email dialog, AC-W15 no UUIDs).
 *
 * Contract for the new component `PlanningHistorySheet.tsx` (props: open, onOpenChange, psoIds,
 * soNumbers?, initialSoId?):
 * - it reads `getPlanningHistory(psoIds)` (service `_shared/services/fulfilmentPlanningService`)
 *   through a hook `usePlanningHistory(psoIds, enabled)` in `_shared/hooks/useFulfilmentPlanning.ts`
 *   and renders a right Sheet titled "History" (role dialog, accessible name "History");
 * - events are grouped by day (dd/mm/yyyy heading, newest day first) in an element with
 *   data-testid="history-timeline"; each event is data-testid={`history-event-${event.id}`} and
 *   shows its kind label (Confirm refused, Revision confirmed, Order cancelled, AutoCount
 *   update, Email) and count badges; clicking an event expands a small table of its lines;
 * - the filters are labelled "Product" (clearable select over the item codes seen), "Event"
 *   (multi select over the five kind labels): product narrows each event's lines and hides
 *   events with none; event keeps only the chosen kinds;
 * - an email event has an "Open email" button opening a Dialog with subject, sent time, status,
 *   recipients and the stored text; the body row naming the filtered product carries
 *   data-highlighted="true" (rows are data-testid="history-email-row").
 * Selects are mocked as native selects here, labelled by `aria-label` or `placeholder`.
 */
import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getPlanningHistory = vi.fn();

vi.mock('../../_shared/services/fulfilmentPlanningService', () => ({
  getPlanningHistory: (...args: unknown[]) => getPlanningHistory(...args),
}));

vi.mock('@/lib/toast', () => ({
  toast: { success: vi.fn(), warning: vi.fn(), error: vi.fn(), dismiss: vi.fn() },
}));

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    value?: string | null;
    onChange: (next: string | null) => void;
    options?: { value: string; label: string }[];
    id?: string;
    placeholder?: string;
    'aria-label'?: string;
  }) => (
    <select
      aria-label={props['aria-label'] ?? props.placeholder ?? props.id}
      value={props.value ?? ''}
      onChange={(e) => props.onChange(e.target.value === '' ? null : e.target.value)}
    >
      <option value="" />
      {(props.options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value?: string[];
    onChange: (next: string[]) => void;
    options?: { value: string; label: string }[];
    id?: string;
    placeholder?: string;
    'aria-label'?: string;
  }) => (
    <select
      multiple
      aria-label={props['aria-label'] ?? props.placeholder ?? props.id}
      value={props.value ?? []}
      onChange={(e) =>
        props.onChange(Array.from(e.target.selectedOptions).map((option) => option.value))
      }
    >
      {(props.options ?? []).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  ),
}));

import { PlanningHistorySheet } from './PlanningHistorySheet';

const ITEM = 'SRTWC8840-SC';
const OTHER = 'SRTWCX8840-S-RL';
const EMAIL_BODY =
  'Order inquiry for SO422492\nBRW-NTC | SRTWC8840-SC | 25 | 0 | 09/11/2026 | CANCEL ALL\nBRW-NTC | SRTWT2219 | 6 | 0 | 09/11/2026 | ORDER BACK';

const EVENTS = [
  {
    id: 'ev-refused',
    kind: 'confirm_refused',
    at: '2026-10-04T08:30:00',
    actor: 'Cyndi Tan',
    pso_id: 'pso-1',
    so_no: 'SO422492',
    preview: true,
    lines: [
      {
        so_line_no: 592,
        item_code: ITEM,
        reason_code: 'not_decided',
        reason: 'Not decided',
        change: 'Qty 25 -> 22',
      },
    ],
  },
  {
    id: 'ev-manual',
    kind: 'oi_cancelled',
    at: '2026-10-03T15:00:00',
    actor: 'Aina Lee',
    pso_id: 'pso-1',
    so_no: 'SO422492',
    lines: [{ so_line_no: 800, item_code: 'SRTWCY8840', action: 'cancelled', qty: 3, cause: 'manual' }],
  },
  {
    id: 'ev-email',
    kind: 'email_sent',
    at: '2026-10-03T11:00:30',
    actor: null,
    pso_id: 'pso-1',
    so_no: 'SO422492',
    subject: 'OI: SO422492',
    status: 'sent',
    sent_at: '2026-10-03T11:00:30',
    recipients: ['buyer1@example.test', 'buyer2@example.test'],
    body_text: EMAIL_BODY,
    lines: [{ item_code: ITEM, remark: 'CANCEL ALL' }],
    counts: { cancel: 1, order: 0, order_back: 0 },
  },
  {
    id: 'ev-rev2',
    kind: 'revision_confirmed',
    at: '2026-10-03T11:00:00',
    actor: 'Cyndi Tan',
    pso_id: 'pso-1',
    so_no: 'SO422492',
    revision_no: 2,
    reissued_unchanged: 50,
    lines: [
      { so_line_no: 592, item_code: ITEM, action: 'cancelled', qty: 25, cause: 'undecided_change' },
      { so_line_no: 700, item_code: OTHER, action: 'cancelled', qty: 4, cause: 'not_recorded' },
    ],
  },
  {
    id: 'ev-update',
    kind: 'autocount_update',
    at: '2026-10-02T10:00:00',
    actor: 'Aina Lee',
    pso_id: 'pso-1',
    so_no: 'SO422492',
    counts: { qty_down: 1, not_decided: 1 },
    lines: [
      { so_line_no: 592, item_code: ITEM, kind: 'qty_down', change: 'Qty 25 -> 22', state: 'not_decided' },
    ],
  },
];

function renderSheet(props: Partial<React.ComponentProps<typeof PlanningHistorySheet>> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PlanningHistorySheet
        open
        onOpenChange={vi.fn()}
        psoIds={['pso-1']}
        soNumbers={['SO422492']}
        {...props}
      />
    </QueryClientProvider>,
  );
}

const eventRow = (id: string) => screen.getByTestId(`history-event-${id}`);

beforeEach(() => {
  vi.clearAllMocks();
  getPlanningHistory.mockResolvedValue({ events: EVENTS });
});

describe('AC-W12: the sheet', () => {
  it('is a dialog titled History that reads the history of the named orders', async () => {
    renderSheet({ psoIds: ['pso-1', 'pso-2'] });

    expect(await screen.findByRole('dialog', { name: 'History' })).toBeInTheDocument();
    await waitFor(() => expect(getPlanningHistory).toHaveBeenCalledTimes(1));
    expect(getPlanningHistory.mock.calls[0][0]).toEqual(['pso-1', 'pso-2']);
  });

  it('reads nothing while closed', async () => {
    renderSheet({ open: false });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(getPlanningHistory).not.toHaveBeenCalled();
  });

  it('groups the events by day, newest first', async () => {
    renderSheet();

    const timeline = await screen.findByTestId('history-timeline');

    const text = timeline.textContent ?? '';
    expect(within(timeline).getAllByText(/03\/10\/2026/)).toHaveLength(1);
    expect(within(timeline).getAllByText(/04\/10\/2026/)).toHaveLength(1);
    expect(within(timeline).getAllByText(/02\/10\/2026/)).toHaveLength(1);
    expect(text.indexOf('04/10/2026')).toBeLessThan(text.indexOf('03/10/2026'));
    expect(text.indexOf('03/10/2026')).toBeLessThan(text.indexOf('02/10/2026'));
    for (const event of EVENTS) {
      expect(eventRow(event.id)).toBeInTheDocument();
    }
  });

  it('names each kind and shows no UUID', async () => {
    renderSheet();
    await screen.findByTestId('history-timeline');

    expect(within(eventRow('ev-refused')).getByText(/Confirm refused/)).toBeInTheDocument();
    expect(within(eventRow('ev-rev2')).getByText(/Revision confirmed/)).toBeInTheDocument();
    expect(within(eventRow('ev-update')).getByText(/AutoCount update/)).toBeInTheDocument();
    expect(within(eventRow('ev-manual')).getByText(/Order cancelled/)).toBeInTheDocument();
    expect(within(eventRow('ev-email')).getByText(/Email/)).toBeInTheDocument();
    expect(screen.getByTestId('history-timeline').textContent).not.toMatch(
      /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i,
    );
  });

  it('opens an event on click to a small table of its lines', async () => {
    renderSheet();
    await screen.findByTestId('history-timeline');
    expect(within(eventRow('ev-rev2')).queryByRole('table')).toBeNull();

    fireEvent.click(within(eventRow('ev-rev2')).getByRole('button'));

    const table = await within(eventRow('ev-rev2')).findByRole('table');
    expect(within(table).getByText(ITEM)).toBeInTheDocument();
    expect(within(table).getByText(OTHER)).toBeInTheDocument();
  });

  it('says so when there is nothing to show', async () => {
    getPlanningHistory.mockResolvedValue({ events: [] });
    renderSheet();

    expect(await screen.findByText(/no history/i)).toBeInTheDocument();
    expect(screen.queryByTestId('history-timeline')).toBeNull();
  });
});

describe('AC-W12: the filters', () => {
  it('a product filter narrows each event to its matching lines and hides events with none', async () => {
    renderSheet();
    await screen.findByTestId('history-timeline');

    fireEvent.change(screen.getByLabelText(/product/i), { target: { value: ITEM } });

    await waitFor(() => expect(screen.queryByTestId('history-event-ev-manual')).toBeNull());
    fireEvent.click(within(eventRow('ev-rev2')).getByRole('button'));
    const table = await within(eventRow('ev-rev2')).findByRole('table');
    expect(within(table).getByText(ITEM)).toBeInTheDocument();
    expect(within(table).queryByText(OTHER)).toBeNull();
    expect(eventRow('ev-refused')).toBeInTheDocument();
    expect(eventRow('ev-update')).toBeInTheDocument();
  });

  it('clearing the product filter brings every event back', async () => {
    renderSheet();
    await screen.findByTestId('history-timeline');
    fireEvent.change(screen.getByLabelText(/product/i), { target: { value: ITEM } });
    await waitFor(() => expect(screen.queryByTestId('history-event-ev-manual')).toBeNull());

    fireEvent.change(screen.getByLabelText(/product/i), { target: { value: '' } });

    expect(await screen.findByTestId('history-event-ev-manual')).toBeInTheDocument();
  });

  it('an event filter keeps only the chosen kinds', async () => {
    renderSheet();
    await screen.findByTestId('history-timeline');
    const filter = screen.getByLabelText(/event/i) as HTMLSelectElement;

    Array.from(filter.options).forEach((option) => {
      option.selected = option.text === 'Email';
    });
    fireEvent.change(filter);

    await waitFor(() => expect(screen.queryByTestId('history-event-ev-rev2')).toBeNull());
    expect(eventRow('ev-email')).toBeInTheDocument();
    expect(screen.queryByTestId('history-event-ev-refused')).toBeNull();
    expect(screen.queryByTestId('history-event-ev-update')).toBeNull();
  });
});

describe('AC-W14: Open email', () => {
  it('shows subject, sent time, status, recipients and the stored text', async () => {
    renderSheet();
    await screen.findByTestId('history-timeline');

    fireEvent.click(within(eventRow('ev-email')).getByRole('button', { name: 'Open email' }));

    const dialog = await screen.findByRole('dialog', { name: /OI: SO422492/ });
    expect(within(dialog).getByText('OI: SO422492')).toBeInTheDocument();
    expect(within(dialog).getByText(/03\/10\/2026/)).toBeInTheDocument();
    expect(within(dialog).getByText(/sent/i)).toBeInTheDocument();
    expect(within(dialog).getByText(/buyer1@example\.test/)).toBeInTheDocument();
    expect(within(dialog).getByText(/buyer2@example\.test/)).toBeInTheDocument();
    expect(within(dialog).getByText(/CANCEL ALL/)).toBeInTheDocument();
    expect(within(dialog).getByText(/ORDER BACK/)).toBeInTheDocument();
  });

  it('highlights the rows naming the filtered product', async () => {
    renderSheet();
    await screen.findByTestId('history-timeline');
    fireEvent.change(screen.getByLabelText(/product/i), { target: { value: ITEM } });
    await waitFor(() => expect(screen.queryByTestId('history-event-ev-manual')).toBeNull());

    fireEvent.click(within(eventRow('ev-email')).getByRole('button', { name: 'Open email' }));

    const dialog = await screen.findByRole('dialog', { name: /OI: SO422492/ });
    const rows = within(dialog).getAllByTestId('history-email-row');
    const named = rows.filter((row) => row.textContent?.includes(ITEM));
    const others = rows.filter((row) => !row.textContent?.includes(ITEM));
    expect(named.length).toBe(1);
    expect(named[0]).toHaveAttribute('data-highlighted', 'true');
    expect(others.length).toBeGreaterThan(0);
    for (const row of others) {
      expect(row).not.toHaveAttribute('data-highlighted', 'true');
    }
  });
});

describe('fix round 1: badges follow the filter, tables scroll', () => {
  const MIXED = {
    id: 'ev-mixed',
    kind: 'revision_confirmed',
    at: '2026-10-05T09:00:00',
    actor: 'Cyndi Tan',
    pso_id: 'pso-1',
    so_no: 'SO422492',
    revision_no: 3,
    reissued_unchanged: 0,
    lines: [
      { so_line_no: 592, item_code: ITEM, action: 'cancelled', qty: 25, cause: 'superseded' },
      { so_line_no: 700, item_code: OTHER, action: 'ordered', qty: 4, cause: null },
    ],
  };

  it('after a product filter the count badges reflect only the visible lines', async () => {
    getPlanningHistory.mockResolvedValue({ events: [MIXED] });
    renderSheet();
    await screen.findByTestId('history-timeline');
    expect(within(eventRow('ev-mixed')).getByText(/Ordered 1/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/product/i), { target: { value: ITEM } });

    await waitFor(() =>
      expect(within(eventRow('ev-mixed')).queryByText(/Ordered/)).toBeNull(),
    );
    expect(within(eventRow('ev-mixed')).getByText(/Cancelled 1/)).toBeInTheDocument();
  });

  it('wraps the expanded line table in a horizontally scrollable container', async () => {
    renderSheet();
    await screen.findByTestId('history-timeline');

    fireEvent.click(within(eventRow('ev-rev2')).getByRole('button'));

    const table = await within(eventRow('ev-rev2')).findByRole('table');
    expect(table.closest('.overflow-x-auto')).not.toBeNull();
    expect(eventRow('ev-rev2').contains(table.closest('.overflow-x-auto'))).toBe(true);
  });
});
