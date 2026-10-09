/**
 * Configure summary on a report with `datasets_by` (CA-1): the catalog is the UNION of the
 * datasets' columns, so the dialog offers only what the chosen dataset carries. A choice
 * that then errors ("'product' is not a dimension this report can group rows by") breaks
 * the clear-UI rule. Contract pinned here: the dialog takes `datasetValue` (the current
 * value of the dataset param), reads each catalog column's `unavailable_for`, and shows
 * `data-testid="summary-choice-note"` when it cleared a slot held by an unavailable column.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    id,
    value,
    onChange,
    options = [],
  }: {
    id?: string;
    value?: string;
    onChange?: (v: string) => void;
    options?: Array<{ value: string; label: string }>;
  }) => (
    <select id={id} value={value} onChange={(e) => onChange?.(e.target.value)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: ({
    value,
    onChange,
    options = [],
  }: {
    value: string[];
    onChange: (v: string[]) => void;
    options?: Array<{ value: string; label: string }>;
  }) => (
    <div aria-label="Measures">
      {options.map((o) => (
        <label key={o.value}>
          <input
            type="checkbox"
            aria-label={o.label}
            checked={value.includes(o.value)}
            onChange={(e) =>
              onChange(e.target.checked ? [...value, o.value] : value.filter((v) => v !== o.value))
            }
          />
          {o.label}
        </label>
      ))}
    </div>
  ),
}));

import { ConfigureSummaryDialog } from './ConfigureSummaryDialog';
import type { ReportCatalogColumn } from '@/services/reportService';

const CATALOG = [
  { key: 'outcome', label: 'Outcome', type: 'text', tag: 'dimension', unavailable_for: [] },
  { key: 'week', label: 'Week', type: 'text', tag: 'dimension', unavailable_for: [] },
  { key: 'failure_reason', label: 'Failure reason', type: 'text', tag: 'dimension', unavailable_for: ['products'] },
  { key: 'branch_kind', label: 'Branch', type: 'text', tag: 'dimension', unavailable_for: ['products'] },
  { key: 'product', label: 'Product', type: 'text', tag: 'dimension', unavailable_for: ['asks'] },
  { key: 'asks', label: 'Asks', type: 'count', tag: 'measure', unavailable_for: [] },
  { key: 'failed', label: 'Failed', type: 'count', tag: 'measure', unavailable_for: [] },
  { key: 'named', label: 'Named', type: 'count', tag: 'measure', unavailable_for: ['asks'] },
  { key: 'shown', label: 'Shown', type: 'count', tag: 'measure', unavailable_for: ['asks'] },
] as unknown as ReportCatalogColumn[];

function open(datasetValue: string, value: { rows: string; cols: string; measures: string[] }, onApply = vi.fn()) {
  render(
    <ConfigureSummaryDialog
      open
      onOpenChange={vi.fn()}
      catalog={CATALOG}
      value={value}
      onApply={onApply}
      // @ts-expect-error new prop, the contract under test
      datasetValue={datasetValue}
    />,
  );
  return { onApply };
}

const labelsOf = (el: HTMLElement) => Array.from((el as HTMLSelectElement).options).map((o) => o.textContent);
const checkboxes = () => screen.queryAllByRole('checkbox').map((c) => c.getAttribute('aria-label'));

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('ConfigureSummaryDialog with a dataset param', () => {
  const VALID = { rows: 'outcome', cols: 'week', measures: ['asks'] };

  it('with Count by = asks offers no Product, Named or Shown', () => {
    open('asks', VALID);

    for (const id of ['Rows', 'Columns']) {
      const labels = labelsOf(screen.getByLabelText(id));
      expect(labels).not.toContain('Product');
      expect(labels).toContain('Failure reason');
      expect(labels).toContain('Branch');
    }
    const measures = checkboxes();
    expect(measures).toEqual(expect.arrayContaining(['Asks', 'Failed']));
    expect(measures).not.toContain('Named');
    expect(measures).not.toContain('Shown');
  });

  it('with Count by = products offers Product, Named and Shown and no Failure reason or Branch', () => {
    open('products', VALID);

    for (const id of ['Rows', 'Columns']) {
      const labels = labelsOf(screen.getByLabelText(id));
      expect(labels).toContain('Product');
      expect(labels).not.toContain('Failure reason');
      expect(labels).not.toContain('Branch');
    }
    expect(checkboxes()).toEqual(expect.arrayContaining(['Asks', 'Failed', 'Named', 'Shown']));
  });

  it('clears a slot holding a choice the dataset lacks, names it, and keeps the valid slots', () => {
    const { onApply } = open('asks', { rows: 'product', cols: 'week', measures: ['asks'] });

    const note = screen.getByTestId('summary-choice-note');
    expect(note.textContent).toContain('Product');
    expect((screen.getByLabelText('Columns') as HTMLSelectElement).value).toBe('week');
    expect(screen.getByLabelText('Asks')).toBeChecked();

    // Rows is empty now, so Apply is refused rather than sending 'product'.
    screen.getByRole('button', { name: 'Apply' }).click();
    expect(onApply).not.toHaveBeenCalled();
  });

  it('shows no note when every held choice is available', () => {
    open('asks', VALID);
    expect(screen.queryByTestId('summary-choice-note')).not.toBeInTheDocument();
  });
});
