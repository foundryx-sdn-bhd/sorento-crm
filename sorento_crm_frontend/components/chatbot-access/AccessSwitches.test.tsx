/**
 * Access model S6, mock v8 (AC-AM-7, AC-AM-4b, AC-AM-24). Red test: AccessSwitches.tsx does not exist.
 * Props: groups (buildRows output), mode 'contact'|'role', onToggle(key), onResetRow(key), onToggleField(key).
 * Details content is unmounted while closed. Stamp = row.stamp { key, on, label, previewOn, previewOff }.
 */
import React from 'react';
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';

import AccessSwitches from './AccessSwitches';

afterEach(cleanup);

const groups = [
  {
    label: 'Stock and incoming',
    rows: [
      {
        key: 'incoming', label: 'Incoming stock', on: true, changed: true, addedCount: 1, removedCount: 0,
        fields: [
          { key: 'incoming_stock.eta', label: 'ETA', on: true, changed: false },
          { key: 'incoming_stock.consignee', label: 'Consignee', on: true, changed: true },
          { key: 'incoming_stock.liner', label: 'Liner code', on: false, changed: false },
        ],
        stamp: {
          key: 'stamp.incoming', on: true, label: 'Show has / no incoming in pick lists',
          previewOn: '3. SRT5674-N - has incoming', previewOff: '3. SRT5674-N',
        },
      },
      { key: 'order', label: 'Orders and deliveries', on: false, changed: false, addedCount: 0, removedCount: 0, fields: [] },
    ],
  },
  { label: 'Reports', rows: [
    { key: 'low_stock', label: 'Low stock report', on: true, changed: false, addedCount: 0, removedCount: 0, needs: 'Stock available', blocked: true, fields: [] },
  ] },
];

function setup(mode: 'contact' | 'role' = 'contact') {
  const h = { onToggle: vi.fn(), onResetRow: vi.fn(), onToggleField: vi.fn() };
  render(<AccessSwitches groups={groups as never} mode={mode} {...h} />);
  return h;
}

describe('AccessSwitches', () => {
  it('AC-AM-7 renders group headings from props and one switch per row', () => {
    setup();
    expect(screen.getByText('Stock and incoming')).toBeTruthy();
    expect(screen.getByText('Reports')).toBeTruthy();
    expect(screen.getByRole('switch', { name: 'Incoming stock' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: 'Orders and deliveries' }).getAttribute('aria-checked')).toBe('false');
  });

  it('AC-AM-7 clicking a switch calls onToggle with the row key', () => {
    const h = setup();
    fireEvent.click(screen.getByRole('switch', { name: 'Orders and deliveries' }));
    expect(h.onToggle).toHaveBeenCalledWith('order');
  });

  it('AC-AM-7 shows "Details shown: n of m" and opens one level of checkboxes', () => {
    const h = setup();
    expect(screen.queryByText('Consignee')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Details shown: 2 of 3/ }));
    expect(screen.getByRole('checkbox', { name: 'ETA' })).toBeTruthy();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Liner code' }));
    expect(h.onToggleField).toHaveBeenCalledWith('incoming_stock.liner');
  });

  it('AC-AM-24 the stamp switch and its before/after preview sit inside the details', () => {
    const h = setup();
    expect(screen.queryByRole('switch', { name: 'Show has / no incoming in pick lists' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Details shown/ }));
    fireEvent.click(screen.getByRole('switch', { name: 'Show has / no incoming in pick lists' }));
    expect(h.onToggleField).toHaveBeenCalledWith('stamp.incoming');
    expect(screen.getByText(/On: they see/)).toBeTruthy();
    expect(screen.getByText(/Off: they see/)).toBeTruthy();
    expect(screen.getByText(/has incoming/)).toBeTruthy();
  });

  it('AC-AM-7 a changed row says "Changed for this contact" and Reset to role calls onResetRow', () => {
    const h = setup('contact');
    expect(screen.getByText(/Changed for this contact/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Reset to role' }));
    expect(h.onResetRow).toHaveBeenCalledWith('incoming');
  });

  it('AC-AM-7 role mode never shows the changed marker or Reset to role', () => {
    setup('role');
    expect(screen.queryByText(/Changed for this contact/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reset to role' })).toBeNull();
  });

  it('AC-AM-7 shows no role badges, keys or codes anywhere, even with details open', () => {
    setup();
    fireEvent.click(screen.getByRole('button', { name: /Details shown/ }));
    const text = document.body.textContent ?? '';
    expect(text).not.toMatch(/from [A-Z]/);
    for (const raw of ['incoming_stock.consignee', 'incoming_stock.eta', 'stamp.incoming', 'purchase_orders.cost']) {
      expect(text).not.toContain(raw);
    }
  });

  it('AC-AM-4b a blocked report shows what it needs', () => {
    setup();
    expect(screen.getByText(/Needs Stock available/)).toBeTruthy();
  });
});
