/**
 * SO-LINE-CHANGE-HISTORY F1 (red-first): `lineChangeAnnotation` reads the batch row's
 * `from` / `to` sides off the line, so the SO detail dialog can print a Was | Now table.
 */
import { describe, it, expect } from 'vitest';
import type { SalesOrderLine } from '@/app/(protected)/scm/types/scm.types';
import { lineChangeAnnotation } from './salesOrderLineChange';

const side = (qty: string | null, required_date: string | null) => ({
  qty,
  required_date,
  status: null,
  item_code: 'SKU-X',
});

const lineWith = (planning_change: Record<string, unknown> | null) =>
  ({
    id: 'l-1',
    sku: 'SKU-X',
    planning_change,
  }) as unknown as SalesOrderLine;

describe('lineChangeAnnotation sides', () => {
  it('AC-3: maps from to was and to to now', () => {
    const annotation = lineChangeAnnotation(
      lineWith({
        id: 'pcr-1',
        kind: 'qty_up',
        applied_state: 'applied',
        result: null,
        from: side('182', '2026-06-01'),
        to: side('280', '2027-03-01'),
      }),
      'SO314593',
    );
    expect(annotation?.was.qty).toBe('182');
    expect(annotation?.was.date).toBe('2026-06-01');
    expect(annotation?.now.qty).toBe('280');
    expect(annotation?.now.date).toBe('2027-03-01');
    expect(annotation?.was.decision).toBeNull();
    expect(annotation?.now.decision).toBeNull();
  });

  it('AC-5: no from/to leaves every was and now side null', () => {
    const annotation = lineChangeAnnotation(
      lineWith({ id: 'pcr-2', kind: 'qty_up', applied_state: 'applied', result: null }),
      'SO314593',
    );
    expect(annotation?.was).toEqual({ qty: null, date: null, decision: null });
    expect(annotation?.now).toEqual({ qty: null, date: null, decision: null });
  });

  it('AC-6: cancelled is closed, now sides stay null, was comes from from', () => {
    const annotation = lineChangeAnnotation(
      lineWith({
        id: 'pcr-3',
        kind: 'cancelled',
        applied_state: 'applied',
        result: null,
        from: side('3', '2026-08-30'),
        to: side('0', null),
      }),
      'SO314593',
    );
    expect(annotation?.closed).toBe(true);
    expect(annotation?.was.qty).toBe('3');
    expect(annotation?.was.date).toBe('2026-08-30');
    expect(annotation?.now).toEqual({ qty: null, date: null, decision: null });
  });
});
