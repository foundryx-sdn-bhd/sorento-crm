/**
 * The attachment preview auth split across the RevisionSnapshotDialog seam
 * (round 7 gap).
 *
 * The office reads bytes with its JWT session, so BOTH the timeline's own
 * attachment badges and the snapshot dialog's "View full form" attachments
 * must reach `AttachmentPreviewModal` with no `fetchBytes` override - the
 * modal's default `apiFetch` is what carries the session. The portal side of
 * the same seam is covered in `RevisionHistory.test.tsx`, asserting the
 * opposite: `fetchBytes=portalFetchBytes`. If either side ever converges on
 * the other's reader, every historical attachment breaks silently for one of
 * the two audiences.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

import { RevisionTimeline, type FormRevisionEntry } from './RevisionTimeline';

const previewCalls: Array<{
  open: boolean;
  items: { id: string; name: string; url: string; downloadUrl?: string }[];
  startIndex?: number;
  fetchBytes?: unknown;
}> = [];

vi.mock('@/components/common/AttachmentPreviewModal', () => ({
  __esModule: true,
  default: (props: {
    open: boolean;
    items: { id: string; name: string; url: string; downloadUrl?: string }[];
    startIndex?: number;
    fetchBytes?: unknown;
  }) => {
    previewCalls.push(props);
    if (!props.open) return null;
    return (
      <div data-testid="preview-modal">
        {props.items.map((i) => (
          <span key={i.id}>{i.name}</span>
        ))}
      </div>
    );
  },
}));

function entry(overrides: Partial<FormRevisionEntry> = {}): FormRevisionEntry {
  return {
    id: 'rev-1',
    version_no: 1,
    revision_no: 1,
    kind: 'revision',
    label: 'Revision 1',
    reason: null,
    submitted_at: '2026-08-01T02:00:00',
    submitted_by: 'CONTACT X Lee',
    snapshot: {},
    snapshot_fields: [{ field: 'quantity', label: 'Quantity', value: '4' }],
    attachments: [
      { attachment_id: 'att-1', filename: 'quote.pdf' },
      { attachment_id: 'att-2', filename: 'photo.jpg' },
    ],
    changes: [],
    ...overrides,
  };
}

beforeEach(() => {
  previewCalls.length = 0;
});

describe('RevisionTimeline attachment preview auth split', () => {
  it('opens the timeline entry badge preview with no fetchBytes override (JWT default), at the clicked index', () => {
    render(<RevisionTimeline entries={[entry()]} />);

    const badges = screen.getAllByTestId('revision-attachment');
    expect(badges).toHaveLength(2);

    // The second file, not the first - a stale hardcoded startIndex of 0 would
    // still pass a test that only ever clicked index 0.
    fireEvent.click(badges[1]);

    const opened = previewCalls.filter((c) => c.open).at(-1);
    expect(opened).toBeDefined();
    expect(opened?.fetchBytes).toBeUndefined();
    expect(opened?.startIndex).toBe(1);
    expect(opened?.items[0]).toMatchObject({
      id: 'att-1',
      downloadUrl: '/api/v1/resource-management/attachments/att-1/download',
    });
    expect(opened?.items[1]).toMatchObject({
      id: 'att-2',
      downloadUrl: '/api/v1/resource-management/attachments/att-2/download',
    });
  });

  it('opens the snapshot dialog preview with no fetchBytes override, using the office download route', () => {
    render(<RevisionTimeline entries={[entry()]} />);

    fireEvent.click(screen.getByTestId('revision-view-form'));
    const dialog = screen.getByRole('dialog');
    const badges = within(dialog).getAllByTestId('snapshot-attachment');
    expect(badges).toHaveLength(2);

    fireEvent.click(badges[1]);

    const opened = previewCalls.filter((c) => c.open).at(-1);
    expect(opened).toBeDefined();
    // No fetchBytes here means the modal falls back to its own apiFetch, which
    // carries the office JWT session - this is the whole point of the split.
    expect(opened?.fetchBytes).toBeUndefined();
    expect(opened?.startIndex).toBe(1);
    expect(opened?.items[1]).toMatchObject({
      id: 'att-2',
      downloadUrl: '/api/v1/resource-management/attachments/att-2/download',
    });
  });
});
