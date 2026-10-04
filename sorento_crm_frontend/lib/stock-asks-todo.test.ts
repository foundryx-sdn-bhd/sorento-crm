/**
 * AC-ST113 (rewritten by AC-ST303) and AC-ST301, AC-ST302: `bucketTodo`, the ask -> landing
 * summary adapter and the asks field contract, with today_start = 2026-09-28T16:00Z (Malaysia
 * midnight of 29 Sep 2026). The server owns the boundary; nothing guesses a zone.
 */
import { describe, expect, it } from 'vitest';
import type { StockAsk } from '@/lib/stock-asks';
import {
  ASK_LANDING_FIELDS,
  askAnswerText,
  askProductText,
  askToSummary,
  bucketTodo,
  filterTodoPayload,
  type AskTodoPayload,
} from '@/lib/stock-asks-todo';
import { applyLandingFilters, sortLandingItems } from '@/app/(auth)/portal/lib/landing-fields';

const TODAY_START = '2026-09-28T16:00:00Z';

function ask(id: string, created_at: string, over: Partial<StockAsk> = {}): StockAsk {
  return {
    id,
    customer_name: `Customer ${id}`,
    contact_name: 'Ah Seng',
    product_code: `SRT-${id}`,
    product_name: null,
    quantity: 10,
    branch: 'in_stock',
    answer_summary: 'answer',
    notified_agent: true,
    notify_skip_reason: null,
    state: 'open',
    note: null,
    created_at,
    updated_at: null,
    ...over,
  };
}

const JUST_BEFORE = ask('a', '2026-09-28T15:59:00Z');
const AT_START = ask('b', '2026-09-28T16:00:00Z');
const YESTERDAY = ask('c', '2026-09-27T20:00:00Z');
const LAST_WEEK = ask('d', '2026-09-22T05:00:00Z');

function payload(open: StockAsk[], done_today: StockAsk[] = []): AskTodoPayload {
  return { today_start: TODAY_START, open, done_today, truncated: false };
}

const ASC = { key: 'created_at', dir: 'asc' } as const;

function shape(out: ReturnType<typeof bucketTodo>) {
  return out.sections.map((sec) => ({
    key: sec.key,
    label: sec.label,
    days: sec.days.map((d) => ({ ids: d.asks.map((a) => a.id) })),
  }));
}

describe('bucketTodo (CUSTOMER-ASKS-REFER-ONLY: one Open section)', () => {
  it('builds ONE Open section, oldest first, whatever the day, and counts', () => {
    const done = [ask('e', '2026-09-20T01:00:00Z', { state: 'done', done_at: '2026-09-29T02:00:00Z', done_by: 'Sean' })];
    const out = bucketTodo(payload([JUST_BEFORE, AT_START, YESTERDAY, LAST_WEEK], done), ASC);
    expect(out.counts).toEqual({ open: 4, done_today: 1 });
    expect(shape(out)).toEqual([
      // No Needs attention / Today split (owner ruling 1 Oct 2026): every open ask in one group,
      // oldest first (22 Sep 05:00Z, 27 Sep 20:00Z, 28 Sep 15:59Z, 28 Sep 16:00Z).
      { key: 'open', label: 'Open', days: [{ ids: ['d', 'c', 'a', 'b'] }] },
    ]);
    expect(out.done.map((a) => a.id)).toEqual(['e']);
  });

  it('does not split at today_start', () => {
    expect(shape(bucketTodo(payload([JUST_BEFORE, AT_START]), ASC)).map((s) => s.key)).toEqual(['open']);
  });

  it('has no section when nothing is open', () => {
    expect(bucketTodo(payload([]), ASC).sections).toEqual([]);
  });

  it('treats a created_at with no zone as UTC (the backend sends naive UTC)', () => {
    const out = bucketTodo(payload([ask('n2', '2026-09-28T16:00:00'), ask('n1', '2026-09-28T15:59:00')]), ASC);
    expect(shape(out)[0].days[0].ids).toEqual(['n1', 'n2']);
  });

  it('counts an incoming and a console ask like any other (Q5 (a))', () => {
    const out = bucketTodo(
      payload([ask('i', '2026-09-29T01:00:00Z', { branch: 'incoming' }), ask('k', '2026-09-29T02:00:00Z', { source: 'console' })]),
      ASC,
    );
    expect(out.counts.open).toBe(2);
  });
});

describe('bucketTodo sort inside the Open section (AC-ST303, AC-ST304)', () => {
  const day = (id: string, hour: number, over: Partial<StockAsk> = {}) =>
    ask(id, `2026-09-29T0${hour}:00:00Z`, over);
  const rows = [
    day('r1', 1, { customer_name: 'Charlie' }),
    day('r2', 2, { customer_name: 'Alpha' }),
    day('r3', 3, { customer_name: 'Bravo' }),
  ];
  const ids = (sort?: { key: string; dir: 'asc' | 'desc' }) =>
    bucketTodo(payload(rows), sort as never).sections[0].days[0].asks.map((a) => a.id);

  it('the default sort is Created ascending: the oldest first', () => {
    expect(ids()).toEqual(['r1', 'r2', 'r3']);
    expect(ids({ key: 'created_at', dir: 'asc' })).toEqual(['r1', 'r2', 'r3']);
  });

  it('orders by customer A to Z and reverses on desc', () => {
    expect(ids({ key: 'customer_name', dir: 'asc' })).toEqual(['r2', 'r3', 'r1']);
    expect(ids({ key: 'customer_name', dir: 'desc' })).toEqual(['r1', 'r3', 'r2']);
    expect(ids({ key: 'created_at', dir: 'desc' })).toEqual(['r3', 'r2', 'r1']);
  });

  it('a newest-first sort reorders the whole Open list, across days', () => {
    const out = bucketTodo(payload([AT_START, LAST_WEEK, YESTERDAY]), { key: 'created_at', dir: 'desc' });
    expect(out.sections.map((s) => s.key)).toEqual(['open']);
    expect(out.sections[0].days[0].asks.map((a) => a.id)).toEqual(['b', 'c', 'd']);
  });
});

// ---- AC-ST301 -----------------------------------------------------------------------------

const ANSWER = 'SRT5674 x 50: yes, we have stock. Please refer to your salesman.';

describe('askAnswerText (AC-ST301)', () => {
  it('strips the "CODE x Q:" prefix and upper-cases the first letter', () => {
    expect(askAnswerText(ask('t', '2026-09-29T01:00:00Z', { answer_summary: ANSWER }))).toBe(
      'Yes, we have stock. Please refer to your salesman.',
    );
  });

  // REFER-SALESMAN (AC-RS22): the new rows store the answer without a "CODE x Q:" prefix.
  it.each([
    'ETA: 2026-09-08. Please refer to your salesman.',
    "Here's what you want:\n• product: SRT1\n\nBut no incoming matched these.\n\nPlease refer to your salesman.",
    'Please refer to your salesman.',
  ])('returns a referred or incoming_eta answer as stored: %s', (answer_summary) => {
    expect(askAnswerText(ask('t', '2026-09-29T01:00:00Z', { answer_summary }))).toBe(answer_summary);
  });

  it.each([
    ['SRT-OLD x 1500: no stock and no incoming.', 'No stock and no incoming.'],
    ['CWCX604 x 300: the quantity is more than I can confirm.', 'The quantity is more than I can confirm.'],
    ['SRTW2000 x 150: no stock at the moment, ETA 19/10/2026.', 'No stock at the moment, ETA 19/10/2026.'],
  ])('reads %s as %s', (answer_summary, expected) => {
    expect(askAnswerText(ask('t', '2026-09-29T01:00:00Z', { answer_summary }))).toBe(expected);
  });

  it('returns an answer_summary without the prefix as it is', () => {
    expect(askAnswerText(ask('t', '2026-09-29T01:00:00Z', { answer_summary: 'answer' }))).toBe('answer');
    expect(askAnswerText(ask('t', '2026-09-29T01:00:00Z', { answer_summary: 'Already fine.' }))).toBe('Already fine.');
  });
});

describe('askToSummary (AC-ST301)', () => {
  it('maps an ask to the landing summary shape', () => {
    const row = ask('s1', '2026-09-29T01:00:00Z', {
      customer_name: 'Hock Lee Trading',
      contact_name: 'Ah Seng',
      product_code: 'SRT5674',
      quantity: 50,
      branch: 'in_stock',
      answer_summary: ANSWER,
      state: 'open',
    });
    expect(askToSummary(row)).toMatchObject({
      id: 's1',
      title: 'SRT5674 x 50',
      customer_name: 'Hock Lee Trading',
      contact_name: 'Ah Seng',
      created_at: '2026-09-29T01:00:00Z',
      status: 'open',
      answer: 'Yes, we have stock. Please refer to your salesman.',
      branch: 'in_stock',
    });
  });

  // REFER-SALESMAN (AC-RS21): an incoming or referred ask has no quantity.
  it('titles a quantity-less ask by its code alone, never "CODE x null"', () => {
    const row = ask('s3', '2026-09-29T01:00:00Z', { product_code: 'SRTWC286-SH-NEW', quantity: null, branch: 'incoming_eta' });
    expect(askProductText(row)).toBe('SRTWC286-SH-NEW');
    expect(askToSummary(row).title).toBe('SRTWC286-SH-NEW');
    expect(askProductText(ask('s4', '2026-09-29T01:00:00Z', { product_code: 'SRT5674', quantity: 50 }))).toBe('SRT5674 x 50');
  });

  it('carries a done ask as status done', () => {
    expect(askToSummary(ask('s2', '2026-09-29T01:00:00Z', { state: 'done' })).status).toBe('done');
  });
});

// ---- AC-ST302 -----------------------------------------------------------------------------

describe('ASK_LANDING_FIELDS (AC-ST302)', () => {
  it('is Customer, Contact, Product, Answer, Created, State in that order, with their types and keys', () => {
    expect(ASK_LANDING_FIELDS.map((f) => [f.label, f.type])).toEqual([
      ['Customer', 'text'],
      ['Contact', 'text'],
      ['Product', 'text'],
      ['Answer', 'text'],
      ['Created', 'date'],
      ['State', 'status'],
    ]);
    const key = (label: string) => ASK_LANDING_FIELDS.find((f) => f.label === label)!.key;
    expect(key('Customer')).toBe('customer_name');
    expect(key('Contact')).toBe('contact_name'); // ASKS-UX item 1: filter by the asker
    expect(key('Product')).toBe('title');
    expect(key('Created')).toBe('created_at');
  });

  const rowsOf = () => [
    ask('f1', '2026-09-29T03:00:00Z', { customer_name: 'Hock Lee Trading' }),
    ask('f2', '2026-09-27T03:00:00Z', { customer_name: 'Ah Huat Trading' }),
    ask('f3', '2026-09-28T03:00:00Z', { customer_name: 'Seri Indah Renovation' }),
    ask('f4', '2026-09-26T03:00:00Z', { customer_name: 'Hock Lee Trading' }),
  ].map((a) => askToSummary(a));

  it('a Customer filter keeps only that customer', () => {
    const kept = applyLandingFilters(rowsOf(), ASK_LANDING_FIELDS, { customer_name: 'Hock Lee Trading' });
    expect(kept.map((r) => r.id).sort()).toEqual(['f1', 'f4']);
  });

  // ASKS-UX item 1 (AC-AU01): the asker is a filter on both mounts.
  it('a Contact filter keeps only that contact, in open and done_today alike', () => {
    const jayson = { contact_name: 'Jayson' };
    const out = filterTodoPayload(
      payload(
        [ask('j1', '2026-09-29T03:00:00Z', jayson), ask('s1', '2026-09-29T03:00:00Z', { contact_name: 'Ah Seng' })],
        [ask('j2', '2026-09-28T03:00:00Z', { ...jayson, state: 'done' }), ask('s2', '2026-09-28T03:00:00Z', { state: 'done' })],
      ),
      { contact_name: 'Jayson' },
    );
    expect(out.open.map((a) => a.id)).toEqual(['j1']);
    expect(out.done_today.map((a) => a.id)).toEqual(['j2']);
  });

  it('sorts by Product (the CODE x Q title) A to Z', () => {
    const rows = [
      ask('p1', '2026-09-29T03:00:00Z', { product_code: 'SRT9', quantity: 1 }),
      ask('p2', '2026-09-29T03:00:00Z', { product_code: 'BLT2', quantity: 1 }),
    ].map((a) => askToSummary(a));
    expect(sortLandingItems(rows, ASK_LANDING_FIELDS, { key: 'title', dir: 'asc' }).map((r) => r.id)).toEqual(['p2', 'p1']);
  });

  it('sorts by Created oldest first and by Customer A to Z', () => {
    const oldest = sortLandingItems(rowsOf(), ASK_LANDING_FIELDS, { key: 'created_at', dir: 'asc' });
    expect(oldest.map((r) => r.id)).toEqual(['f4', 'f2', 'f3', 'f1']);
    const az = sortLandingItems(rowsOf(), ASK_LANDING_FIELDS, { key: 'customer_name', dir: 'asc' });
    expect(az.map((r) => r.customer_name)).toEqual([
      'Ah Huat Trading',
      'Hock Lee Trading',
      'Hock Lee Trading',
      'Seri Indah Renovation',
    ]);
  });
});
