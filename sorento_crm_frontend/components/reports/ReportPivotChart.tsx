'use client';

import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts';
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart';
import type { ReportPivotLayout } from '@/services/reportService';
import { wholeRinggit } from './reportFormat';

/** Earlier rows in muted chart tones; the LAST row (the current year) in the primary. */
const EARLIER = [
  'var(--chart-3)',
  'var(--chart-2)',
  'var(--chart-5)',
  'var(--chart-4)',
];

/**
 * The pivot as a line chart: one line per row value (a year), the column values along x
 * (JAN to DEC), the first measure on y. The one new component the sales reports add
 * (PLAN-retail-sales-reports-26sep 0.1); it draws only what the engine computed.
 *
 * A cell with no value is a GAP in the line, never a zero: October to December of the
 * current year have not happened, and a line diving to 0 would read as a collapse.
 */
export function ReportPivotChart({ layout }: { layout: ReportPivotLayout }) {
  const measure = layout.measures[0];
  const rows = layout.row_values.filter((value) => value !== '(blank)');
  if (!measure || rows.length === 0) return null;

  const labels = layout.col_dim.value_labels ?? {};
  const data = layout.col_dim.values.map((colValue) => {
    const point: Record<string, string | number | null> = {
      x: labels[colValue] ?? colValue,
    };
    rows.forEach((row, index) => {
      const value = layout.cells[row]?.[colValue]?.[measure.key];
      point[`r${index}`] = value == null ? null : Number(value);
    });
    return point;
  });

  const config: ChartConfig = {};
  rows.forEach((row, index) => {
    const last = index === rows.length - 1;
    config[`r${index}`] = {
      label: row,
      color: last
        ? 'var(--primary)'
        : EARLIER[(rows.length - 2 - index) % EARLIER.length],
    };
  });

  return (
    // min-w-0 lets the ResponsiveContainer measure the card's width instead of bootstrapping
    // a min-width, so the chart fits at 375 and never pushes the page sideways.
    <ChartContainer
      config={config}
      className="aspect-auto h-64 w-full min-w-0"
      data-testid="report-pivot-chart"
      aria-label={`${layout.title} chart`}
    >
      <LineChart data={data} margin={{ left: 8, right: 8, top: 8, bottom: 8 }}>
        <CartesianGrid vertical={false} />
        <XAxis
          dataKey="x"
          tickLine={false}
          axisLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          tickLine={false}
          axisLine={false}
          width={64}
          tickFormatter={(value: number) => wholeRinggit(value)}
        />
        <ChartTooltip
          content={
            <ChartTooltipContent
              formatter={(value, name) => (
                // Whole ringgit, as the axis and the table read (AC-S1-16).
                <div className="flex w-full items-center justify-between gap-4">
                  <span className="text-muted-foreground">
                    {config[String(name)]?.label ?? name}
                  </span>
                  <span className="font-medium tabular-nums">
                    {wholeRinggit(Number(value))}
                  </span>
                </div>
              )}
            />
          }
        />
        <ChartLegend content={<ChartLegendContent className="flex-wrap" />} />
        {rows.map((_row, index) => (
          <Line
            key={`r${index}`}
            type="monotone"
            dataKey={`r${index}`}
            stroke={`var(--color-r${index})`}
            strokeWidth={index === rows.length - 1 ? 2.5 : 1.5}
            dot={false}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ChartContainer>
  );
}
