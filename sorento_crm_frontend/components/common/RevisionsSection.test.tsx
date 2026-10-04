import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

import { RevisionsSection } from './RevisionsSection';
import type { FormRevisionEntry } from './RevisionTimeline';

function entry(overrides: Partial<FormRevisionEntry> = {}): FormRevisionEntry {
  return {
    id: overrides.id ?? 'rev-0',
    version_no: overrides.version_no ?? 0,
    revision_no: overrides.revision_no ?? 0,
    kind: overrides.kind ?? 'original',
    label: overrides.label ?? 'Original',
    reason: overrides.reason ?? null,
    submitted_at: overrides.submitted_at ?? '2026-07-01T02:00:00',
    submitted_by: overrides.submitted_by ?? 'Alex Tan',
    is_reconstructed: overrides.is_reconstructed ?? false,
    snapshot: overrides.snapshot ?? {},
    attachments: overrides.attachments ?? [],
    invalidated: overrides.invalidated ?? null,
    voided_stage_code: overrides.voided_stage_code ?? null,
    voided_assignee_name: overrides.voided_assignee_name ?? null,
    voided_stages: overrides.voided_stages ?? null,
    changes: overrides.changes ?? [],
    snapshot_fields: overrides.snapshot_fields ?? null,
  };
}

describe('RevisionsSection', () => {
  it('always renders the section, even with no data at all', () => {
    render(<RevisionsSection entries={undefined} />);
    expect(screen.getByText('Revisions')).toBeInTheDocument();
  });

  it('shows the empty state with zero entries', () => {
    render(<RevisionsSection entries={[]} />);
    expect(
      screen.getByText('No revisions - this is the original submission.'),
    ).toBeInTheDocument();
  });

  it('shows the empty state with exactly one entry at revision 0', () => {
    render(<RevisionsSection entries={[entry()]} />);
    expect(
      screen.getByText('No revisions - this is the original submission.'),
    ).toBeInTheDocument();
  });

  it('renders the timeline once a revision exists', () => {
    render(
      <RevisionsSection
        entries={[
          entry(),
          entry({
            id: 'rev-1',
            version_no: 1,
            revision_no: 1,
            kind: 'revision',
            label: 'Revision 1',
            reason: 'Wrong quantity',
            changes: [{ field: 'quantity', label: 'Quantity', from: 5, to: 8 }],
          }),
        ]}
      />,
    );
    expect(
      screen.queryByText('No revisions - this is the original submission.'),
    ).not.toBeInTheDocument();
    expect(screen.getByText('Original')).toBeInTheDocument();
    expect(screen.getByText('Revision 1')).toBeInTheDocument();
    expect(screen.getByText('Wrong quantity')).toBeInTheDocument();
  });

  it('renders many entries in the order supplied', () => {
    const entries = [
      entry(),
      entry({ id: 'r1', version_no: 1, revision_no: 1, label: 'Revision 1' }),
      entry({ id: 'r2', version_no: 2, revision_no: 2, label: 'Revision 2' }),
      entry({ id: 'r3', version_no: 3, revision_no: 3, label: 'Revision 3' }),
    ];
    render(<RevisionsSection entries={entries} />);
    const rendered = screen
      .getAllByRole('listitem')
      .map((li) => li.textContent ?? '');
    expect(rendered).toHaveLength(4);
    expect(rendered[0]).toContain('Original');
    expect(rendered[3]).toContain('Revision 3');
  });

  it('shows the voided stage context on the office side (UAC H3)', () => {
    render(
      <RevisionsSection
        entries={[
          entry(),
          entry({
            id: 'r1',
            version_no: 1,
            revision_no: 1,
            label: 'Revision 1',
            voided_stage_code: 'purchasing_response',
            voided_assignee_name: 'Mei Ling',
          }),
        ]}
      />,
    );
    expect(screen.getByText(/Voided stage: Purchasing Response/)).toBeInTheDocument();
    expect(screen.getByText(/Mei Ling/)).toBeInTheDocument();
  });

  /**
   * UAC H3a. A purchase request can sit with project sales AND approval open at
   * once; a revision voids both and tells both handlers. Naming only the newest
   * under-reports a cancellation two people were just notified about.
   */
  it('names every stage a revision voided, not just the newest', () => {
    render(
      <RevisionsSection
        entries={[
          entry(),
          entry({
            id: 'r1',
            version_no: 1,
            revision_no: 1,
            label: 'Revision 1',
            // The scalar pair stays populated with the newest stage; the list is
            // what must drive the render.
            voided_stage_code: 'approval',
            voided_assignee_name: 'CONTACT AC',
            voided_stages: [
              { stage_code: 'approval', assignee_name: 'CONTACT AC' },
              { stage_code: 'project_sales', assignee_name: 'Mei Ling' },
            ],
          }),
        ]}
      />,
    );

    const lines = screen
      .getAllByTestId('revision-voided-stage')
      .map((el) => el.textContent ?? '');
    expect(lines).toHaveLength(2);
    // Pluralised, and the label leads the first line only.
    expect(lines[0]).toBe('Voided stages: Approval · CONTACT AC');
    expect(lines[1]).toBe('Project Sales · Mei Ling');
  });

  it('reads a voided stage with no assignee without a dangling separator', () => {
    render(
      <RevisionsSection
        entries={[
          entry(),
          entry({
            id: 'r1',
            version_no: 1,
            revision_no: 1,
            label: 'Revision 1',
            voided_stages: [
              { stage_code: 'approval', assignee_name: null },
              { stage_code: 'project_sales', assignee_name: 'Mei Ling' },
            ],
          }),
        ]}
      />,
    );

    const lines = screen
      .getAllByTestId('revision-voided-stage')
      .map((el) => el.textContent ?? '');
    expect(lines[0]).toBe('Voided stages: Approval');
    expect(lines[1]).toBe('Project Sales · Mei Ling');
  });

  it('falls back to the scalar stage for a row written before the list existed', () => {
    render(
      <RevisionsSection
        entries={[
          entry(),
          entry({
            id: 'r1',
            version_no: 1,
            revision_no: 1,
            label: 'Revision 1',
            voided_stage_code: 'purchasing_response',
            voided_assignee_name: 'Mei Ling',
            // `voided_stages_json` is NULL on those rows, so the payload's list is
            // empty rather than absent.
            voided_stages: [],
          }),
        ]}
      />,
    );

    const lines = screen
      .getAllByTestId('revision-voided-stage')
      .map((el) => el.textContent ?? '');
    expect(lines).toEqual(['Voided stage: Purchasing Response · Mei Ling']);
  });

  it('renders no voided-stage line when a revision voided nothing', () => {
    render(
      <RevisionsSection
        entries={[
          entry(),
          entry({
            id: 'r1',
            version_no: 1,
            revision_no: 1,
            label: 'Revision 1',
            voided_stage_code: null,
            voided_assignee_name: null,
            voided_stages: [],
          }),
        ]}
      />,
    );

    expect(screen.queryAllByTestId('revision-voided-stage')).toHaveLength(0);
    expect(screen.queryByText(/Voided stage/)).toBeNull();
  });

  it('never prints a raw id from the invalidated stage output', () => {
    render(
      <RevisionsSection
        entries={[
          entry(),
          entry({
            id: 'r1',
            version_no: 1,
            revision_no: 1,
            label: 'Revision 1',
            invalidated: {
              purchasing_response: 'In stock, RM 1,200',
              last_responded_by: '4f1c0b2e-1111-2222-3333-444455556666',
            },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/In stock, RM 1,200/)).toBeInTheDocument();
    expect(
      screen.queryByText(/4f1c0b2e-1111-2222-3333-444455556666/),
    ).not.toBeInTheDocument();
  });

  /**
   * The captain's ask: not just the diff, the EXACT form at each version. Every
   * entry carrying a labeled snapshot offers "View full form", and the dialog
   * renders that entry's own values - the superseded ones, not today's.
   */
  it('opens the full form of a chosen revision, rendered from its snapshot', () => {
    render(
      <RevisionsSection
        entries={[
          entry({
            snapshot_fields: [
              { field: 'product_code', label: 'Product code', value: 'SRT-OLD' },
              { field: 'quantity', label: 'Quantity', value: '5' },
            ],
          }),
          entry({
            id: 'rev-1',
            version_no: 1,
            revision_no: 1,
            kind: 'revision',
            label: 'Revision 1',
            snapshot_fields: [
              { field: 'product_code', label: 'Product code', value: 'SRT-NEW' },
              { field: 'quantity', label: 'Quantity', value: '8' },
            ],
          }),
        ]}
      />,
    );

    const buttons = screen.getAllByTestId('revision-view-form');
    expect(buttons).toHaveLength(2);

    // Open the ORIGINAL: it must show its own superseded values.
    fireEvent.click(buttons[0]);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Product code')).toBeInTheDocument();
    expect(within(dialog).getByText('SRT-OLD')).toBeInTheDocument();
    expect(within(dialog).queryByText('SRT-NEW')).toBeNull();
  });

  it('offers no full-form view for an entry without a snapshot payload', () => {
    render(
      <RevisionsSection
        entries={[
          entry(),
          entry({ id: 'r1', version_no: 1, revision_no: 1, label: 'Revision 1' }),
        ]}
      />,
    );
    expect(screen.queryAllByTestId('revision-view-form')).toHaveLength(0);
  });

  it('shows a loading state instead of the empty state while fetching', () => {
    render(<RevisionsSection entries={undefined} isLoading />);
    expect(
      screen.queryByText('No revisions - this is the original submission.'),
    ).not.toBeInTheDocument();
  });

  it('reports a load failure rather than claiming there are no revisions', () => {
    render(<RevisionsSection entries={undefined} isError />);
    expect(screen.getByText('Could not load revisions.')).toBeInTheDocument();
  });
});
