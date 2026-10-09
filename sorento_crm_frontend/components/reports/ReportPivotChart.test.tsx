/**
 * The legend under the pivot chart wraps (CA-1, 375px): six series in one non-wrapping row
 * pushed the page 30px sideways. recharts draws nothing at jsdom's 0x0, so the chart
 * primitives are swapped for pass-through boxes and the Legend hands its content a payload.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';

vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>();
  const Box = ({ children }: { children?: React.ReactNode }) => <div>{children}</div>;
  const Nothing = () => null;
  return {
    ...actual,
    ResponsiveContainer: Box,
    LineChart: Box,
    CartesianGrid: Nothing,
    XAxis: Nothing,
    YAxis: Nothing,
    Line: Nothing,
    Tooltip: Nothing,
    Legend: ({ content }: { content: React.ReactElement }) =>
      React.cloneElement(content as React.ReactElement<Record<string, unknown>>, {
        payload: ['r0', 'r1', 'r2'].map((k) => ({ value: k, dataKey: k, color: 'red' })),
      }),
  };
});

import { ReportPivotChart } from './ReportPivotChart';
import type { ReportPivotLayout } from '@/services/reportService';

const LAYOUT = {
  key: 'summary',
  title: 'Outcome by week',
  row_dim: { key: 'outcome', label: 'Outcome' },
  col_dim: { key: 'week', label: 'Week', values: ['w1', 'w2'], value_labels: {} },
  measures: [{ key: 'asks', label: 'Asks', type: 'money' }],
  row_values: ['answered', 'missed', 'clarified'],
  cells: {
    answered: { w1: { asks: '5.00' } },
    missed: { w1: { asks: '1.00' } },
    clarified: { w2: { asks: '2.00' } },
  },
  row_totals: {},
  col_totals: {},
  grand_total: {},
} as unknown as ReportPivotLayout;

describe('ReportPivotChart legend', () => {
  it('wraps so many series never widen the page', () => {
    const { container } = render(<ReportPivotChart layout={LAYOUT} />);

    const legend = container.querySelector('[data-testid="report-pivot-chart"] .gap-4');
    expect(legend, 'legend row not rendered').not.toBeNull();
    expect(legend!.className).toContain('flex-wrap');
  });
});
