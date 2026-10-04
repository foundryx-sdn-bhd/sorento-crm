/**
 * The summary pivot's money cells (AC-G5).
 *
 * Three readings, three renderings, and the difference matters to whoever is checking a
 * salesman's month: a NUMBER is money, a DASH is "nothing here" (zero is as much nothing
 * as missing, which is why the client's own sheet prints RM- for it), and an EMPTY cell is
 * a month that agent filed no form in at all.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { ReportPivotTable } from './ReportPivotTable';
import type { ReportPivotLayout } from '@/services/reportService';

const LAYOUT: ReportPivotLayout = {
  key: 'summary',
  title: 'Summary by salesman',
  row_dim: { key: 'sales_agent', label: 'Sales agent' },
  col_dim: {
    key: 'month',
    label: 'Month',
    values: ['2025-01', '2025-02'],
    value_labels: { '2025-01': "Jan'25", '2025-02': "Feb'25" },
  },
  measures: [{ key: 'project_value', label: 'Project value', type: 'money' }],
  row_values: ['ACT', 'CONTACT U'],
  cells: {
    ACT: { '2025-01': { project_value: '0.00' }, '2025-02': { project_value: '985884.00' } },
    'CONTACT U': {},
  },
  row_totals: { ACT: { project_value: '985884.00' } },
  col_totals: { '2025-01': { project_value: '0.00' } },
  grand_total: { project_value: '985884.00' },
};

describe('ReportPivotTable money cells', () => {
  it('prints a zero as "-" and a real amount as money', () => {
    render(<ReportPivotTable layout={LAYOUT} />);

    expect(screen.queryByText('0.00')).not.toBeInTheDocument();
    expect(screen.getAllByText('-').length).toBeGreaterThan(0);
    expect(screen.getAllByText('985,884.00').length).toBeGreaterThan(0);
  });

  it('leaves a month with no form at all blank', () => {
    render(<ReportPivotTable layout={LAYOUT} />);

    const row = screen.getByText('CONTACT U').closest('tr') as HTMLTableRowElement;
    const cells = Array.from(row.querySelectorAll('td')).slice(1);
    expect(cells.every((cell) => cell.textContent === '')).toBe(true);
  });
});
