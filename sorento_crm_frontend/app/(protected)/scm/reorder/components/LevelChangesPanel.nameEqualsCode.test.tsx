/**
 * Two-line code-over-name cell (AC-4): LevelChangesPanel's product cell prints the code once
 * when the name equals the code, and still prints the differing name under it.
 */
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { LevelChangesPanel } from './LevelChangesPanel';
import type { LevelSuggestion } from '../lib/levelSuggestion';

const suggestion = (code: string, name: string): LevelSuggestion => ({
  product_id: 'p1',
  warehouse_id: 'w1',
  product_code: code,
  product_name: name,
  warehouse_code: 'BRW',
  warehouse_name: 'Branch West',
  current_level: 20,
  current_source: 'autocount',
  suggested_level: 24,
  suggested_at: null,
  amended_level: null,
  amended_at: null,
  suggested_quantity: null,
  master_reorder_quantity: null,
  basis: {
    adu: 0.4, lead_time_days: 30, lead_time_source: 'supplier', safety_days: 14,
    safety_stock: 5.6, window_days: 90, window_qty: 36, raw_level: 17.6,
    months: [], no_movement: false,
  },
});

function productCellText(code: string, name: string) {
  render(<LevelChangesPanel suggestions={{ a: suggestion(code, name) }} />);
  fireEvent.click(screen.getByRole('button', { name: /AutoCount level to update/i }));
  const rows = [...document.querySelectorAll('tbody tr')];
  return rows[0].querySelector('td')!.textContent;
}

afterEach(() => cleanup());

describe('LevelChangesPanel product cell (AC-4)', () => {
  it('prints the code once when the name equals the code', () => {
    expect(productCellText('1861', '1861')).toBe('1861');
  });

  it('prints the code once when the name differs only by case', () => {
    expect(productCellText('11X11', '11x11')).toBe('11X11');
  });

  it('keeps the differing name under the code', () => {
    expect(productCellText('SRT6641', 'FLOOR TRAP')).toBe('SRT6641FLOOR TRAP');
  });
});
