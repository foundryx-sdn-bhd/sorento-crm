/**
 * Import dealer targets (lane SALES-TARGET-DEALER, UAC D3-1, D4-3): Test first, a per-row
 * report, and Import only after a Test of the same file.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

vi.mock('@/components/common/FileDropzone', () => ({
  FileDropzone: (props: { onFilesChange: (files: File[]) => void; inputTestId?: string }) => (
    <input
      data-testid={props.inputTestId}
      aria-label="Workbook"
      type="file"
      onChange={(e) => props.onFilesChange(Array.from(e.target.files ?? []))}
    />
  ),
}));

const run = vi.hoisted(() => ({ mutateAsync: vi.fn(), isPending: false }));
const template = vi.hoisted(() => ({ mutate: vi.fn(), isPending: false }));
vi.mock('../hooks/useSalesTargets', () => ({
  useImportDealerTargets: () => run,
  useDownloadDealerTemplate: () => template,
}));
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: (props: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    'aria-label'?: string;
  }) => (
    <select aria-label={props['aria-label']} value={props.value} onChange={(e) => props.onChange(e.target.value)}>
      <option value="" />
      {props.options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

import DealerTargetImportDialog from './DealerTargetImportDialog';

const REPORT = {
  dry_run: true,
  summary: { rows: 4, mapped: 2, auto_mapped: 0, needs_pick: 1, skipped: 0, not_representable: 1, targets_new: 2, targets_updated: 0 },
  notes: [],
  rows: [
    {
      sheet: 'Sorento 2026', row: 4, pick_key: 'Sorento 2026!4', match: 'code', suggestions: [], code: '300-A0123', name_in_file: 'Dealer A S/B', account_in_file: 'A/C I',
      customer: 'DEALER A SDN BHD', status: 'mapped', reason: null,
      warnings: ['name differs: the file says "Dealer A S/B", the CRM "DEALER A SDN BHD"'],
      target_keys: ['quarterly 2026-01 300-A0123'], target_nos: [],
    },
    {
      sheet: 'Sorento 2026', row: 5, pick_key: 'Sorento 2026!5', match: 'code', suggestions: [], code: '300-A0124', name_in_file: 'Dealer A', account_in_file: 'A/C III',
      customer: 'DEALER A SDN BHD (III)', status: 'mapped', reason: null, warnings: [],
      target_keys: ['quarterly 2026-01 300-A0123'], target_nos: [],
    },
    {
      sheet: 'Sorento 2026', row: 9, pick_key: 'Sorento 2026!9', match: null, suggestions: [], code: 'ZZZ', name_in_file: 'Dealer Z', account_in_file: null,
      customer: null, status: 'not_representable', reason: 'no customer with code ZZZ', warnings: [],
      target_keys: [], target_nos: [],
    },
    {
      sheet: 'Sorento 2026', row: 12, pick_key: 'Sorento 2026!12', match: null, code: null,
      name_in_file: 'India Marble Works', account_in_file: 'A/C I', customer: null, status: 'needs_pick',
      reason: 'no customer has this name and account', warnings: [], target_keys: [], target_nos: [],
      suggestions: [{ customer_id: 'c-1', code: '300-I0001', name: 'INDIA MARBLE SDN BHD - [A/C I]' }],
    },
  ],
};

beforeEach(() => {
  run.mutateAsync.mockReset();
  run.mutateAsync.mockResolvedValue(REPORT);
});

function pickFile() {
  const file = new File(['x'], 'targets.xlsx');
  fireEvent.change(screen.getByTestId('dealer-target-import-file'), { target: { files: [file] } });
}

describe('DealerTargetImportDialog', () => {
  it('Import stays off until a Test of the file; the Test reports every row with its reason', async () => {
    render(<DealerTargetImportDialog open onOpenChange={() => {}} />);
    pickFile();
    expect((screen.getByRole('button', { name: /^Import/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /Test/ }));
    await waitFor(() => expect(screen.getByText('no customer with code ZZZ')).toBeTruthy());
    expect(run.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ dryRun: true }));
    expect(screen.getByText(/name differs/)).toBeTruthy();
    expect(screen.getAllByText('Mapped').length).toBe(2);
    // The count tile and the row's badge.
    expect(screen.getAllByText('Not representable').length).toBe(2);
    const importButton = screen.getByRole('button', { name: 'Import 2 rows' }) as HTMLButtonElement;
    expect(importButton.disabled).toBe(false);
    fireEvent.click(importButton);
    await waitFor(() =>
      expect(run.mutateAsync).toHaveBeenLastCalledWith(expect.objectContaining({ dryRun: false })),
    );
  });

  it('a new file needs a new Test', async () => {
    render(<DealerTargetImportDialog open onOpenChange={() => {}} />);
    pickFile();
    fireEvent.click(screen.getByRole('button', { name: /Test/ }));
    await waitFor(() => expect(screen.getByText('no customer with code ZZZ')).toBeTruthy());
    pickFile();
    expect((screen.getByRole('button', { name: /^Import/ }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('a row the name match left open is picked or skipped, and the picks go with Import', async () => {
    render(<DealerTargetImportDialog open onOpenChange={() => {}} />);
    pickFile();
    fireEvent.click(screen.getByRole('button', { name: /Test/ }));
    await waitFor(() => expect(screen.getAllByText('Needs a pick').length).toBe(2));
    const select = screen.getByLabelText('Customer for Sorento 2026 row 12') as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.text)).toEqual([
      '',
      '300-I0001 - INDIA MARBLE SDN BHD - [A/C I]',
      'Skip this row',
    ]);
    fireEvent.change(select, { target: { value: 'c-1' } });
    const importButton = screen.getByRole('button', { name: 'Import 3 rows' }) as HTMLButtonElement;
    fireEvent.click(importButton);
    await waitFor(() =>
      expect(run.mutateAsync).toHaveBeenLastCalledWith(
        expect.objectContaining({ dryRun: false, picks: { 'Sorento 2026!12': 'c-1' } }),
      ),
    );
  });

  it('downloads the template for the year', () => {
    render(<DealerTargetImportDialog open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /Template/ }));
    expect(template.mutate).toHaveBeenCalledWith(Number(new Date().getFullYear()));
  });
});
