/**
 * The shared status-pill palette. Covers the `processing` entry added for the
 * flyer-read background job (AC-FE.2 / AC-FE.6): a queued read must read as
 * "waiting on something" - the same amber as the other pending states - not as
 * an outcome.
 */
import { describe, expect, it } from 'vitest';

import { statusPillClass, statusTextClass } from './status-pill';

describe('statusPillClass', () => {
  it('is the amber "waiting" colour for a flyer reading that is processing', () => {
    expect(statusPillClass('processing')).toBe('bg-amber-100 text-amber-800');
  });

  it('is the same amber as the other pending statuses, not a colour of its own', () => {
    expect(statusPillClass('processing')).toBe(statusPillClass('pending'));
  });

  it('normalises case and spacing the same way every other status does', () => {
    expect(statusPillClass('Processing')).toBe('bg-amber-100 text-amber-800');
    expect(statusPillClass(' processing ')).toBe('bg-amber-100 text-amber-800');
  });

  it('falls back to neutral for an unrecognised status', () => {
    expect(statusPillClass('made-up-status')).toBe('bg-muted text-muted-foreground');
    expect(statusPillClass(null)).toBe('bg-muted text-muted-foreground');
    expect(statusPillClass(undefined)).toBe('bg-muted text-muted-foreground');
  });
});

/**
 * S6 (reviewer round, `PLAN-oi-request-cs-reserve.md`): an icon-only control reads
 * the SAME palette as a pill, just the `text-*` half - the OI Lines grid's own Reserve
 * icon-button used to hardcode `text-amber-600`/`text-emerald-600` rather than this
 * ONE source of truth.
 */
describe('statusTextClass', () => {
  it('is only the text-* class off the same palette statusPillClass reads', () => {
    expect(statusTextClass('pending')).toBe('text-amber-800');
    expect(statusTextClass('done')).toBe('text-emerald-800');
  });

  it('falls back to the neutral text colour for an unrecognised status', () => {
    expect(statusTextClass('made-up-status')).toBe('text-muted-foreground');
  });
});

// QUOTE-APPROVAL-UX: the quotation chip reads this shared palette (header and list).
describe('statusPillClass: quotation customer outcomes', () => {
  it('issued and accepted are the emerald "done" colour', () => {
    expect(statusPillClass('issued')).toBe('bg-emerald-100 text-emerald-800');
    expect(statusPillClass('accepted')).toBe('bg-emerald-100 text-emerald-800');
  });

  it('changes_requested is the amber "waiting on you" colour', () => {
    expect(statusPillClass('changes_requested')).toBe('bg-amber-100 text-amber-800');
    expect(statusPillClass('Changes requested')).toBe('bg-amber-100 text-amber-800');
  });
});
