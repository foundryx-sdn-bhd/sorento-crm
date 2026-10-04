/**
 * RevisionHistory - the contact-side lineage
 * (UAC-portal-submission-revisions G3 / G4 / G2 / I4).
 *
 * Always rendered, even with one entry. Each entry carries its label, when, who,
 * why, what changed, and the files as they stood at that version - previewable
 * in place through the shared modal with the portal's own byte reader.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

import { RevisionHistory } from './RevisionHistory';
import { portalFetchBytes } from '../lib/portal-preview';
import type { PortalRevisionEntry } from '../lib/portal-client';
import { formatDateTimeInMalaysia } from '@/lib/helpers';

// Captures every render of the shared modal so the preview test can assert on
// the exact items and the byte reader it was handed.
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

function entry(over: Partial<PortalRevisionEntry> = {}): PortalRevisionEntry {
  return {
    id: 'rev-0',
    version_no: 0,
    revision_no: 0,
    kind: 'original',
    label: 'Original',
    reason: null,
    submitted_at: '2026-07-20T02:00:00',
    submitted_by: 'CONTACT X Lee',
    is_reconstructed: false,
    snapshot: {},
    attachments: [],
    invalidated: null,
    voided_stage_code: null,
    voided_assignee_name: null,
    changes: [],
    ...over,
  };
}

beforeEach(() => {
  previewCalls.length = 0;
});

describe('RevisionHistory', () => {
  it('renders the section with a status line when there is no history row yet', () => {
    render(<RevisionHistory entries={[]} />);

    expect(screen.getByText('Revision history')).toBeInTheDocument();
    expect(screen.getByText('Original submission only.')).toBeInTheDocument();
  });

  it('renders a single original entry without pretending there are revisions', () => {
    render(<RevisionHistory entries={[entry()]} />);

    expect(screen.getAllByTestId('revision-entry')).toHaveLength(1);
    expect(screen.getByText('Original')).toBeInTheDocument();
    expect(screen.getByText('CONTACT X Lee')).toBeInTheDocument();
    expect(screen.queryByText('Original submission only.')).toBeNull();
  });

  it('renders every entry oldest first, with each reason verbatim', () => {
    render(
      <RevisionHistory
        entries={[
          entry(),
          entry({
            id: 'rev-1',
            version_no: 1,
            revision_no: 1,
            kind: 'revision',
            label: 'Revision 1',
            reason: 'Customer moved the delivery date.',
          }),
          entry({
            id: 'rev-2',
            version_no: 2,
            revision_no: 2,
            kind: 'revision',
            label: 'Revision 2',
            reason: 'Wrong product code.',
          }),
        ]}
      />,
    );

    const rows = screen.getAllByTestId('revision-entry');
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText('Original')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Revision 1')).toBeInTheDocument();
    expect(
      within(rows[1]).getByText('Customer moved the delivery date.'),
    ).toBeInTheDocument();
    expect(within(rows[2]).getByText('Wrong product code.')).toBeInTheDocument();
  });

  it('labels a reconstructed original so it is never read as verbatim', () => {
    render(
      <RevisionHistory
        entries={[entry({ label: 'Original (reconstructed)', is_reconstructed: true })]}
      />,
    );

    expect(screen.getByText('Original (reconstructed)')).toBeInTheDocument();
  });

  it('renders the changes diff, old value and new value, for each changed field', () => {
    render(
      <RevisionHistory
        entries={[
          entry(),
          entry({
            id: 'rev-1',
            version_no: 1,
            revision_no: 1,
            kind: 'revision',
            label: 'Revision 1',
            reason: 'Qty was wrong.',
            changes: [
              { field: 'quantity', label: 'Quantity', from: '5', to: '8' },
              { field: 'remark', label: 'Remark', from: null, to: 'Urgent' },
            ],
          }),
        ]}
      />,
    );

    const changes = screen.getAllByTestId('revision-change');
    expect(changes).toHaveLength(2);
    expect(changes[0]).toHaveTextContent('Quantity:');
    expect(changes[0]).toHaveTextContent('5');
    expect(changes[0]).toHaveTextContent('8');
    // An empty previous value reads as empty, not as a blank gap.
    expect(changes[1]).toHaveTextContent('(empty)');
    expect(changes[1]).toHaveTextContent('Urgent');
  });

  /**
   * The captain's ask: the whole form at any earlier version, not just the
   * diff. Each entry carrying a labeled snapshot offers "View full form"; the
   * dialog renders that entry's OWN values - superseded ones, never today's.
   */
  it('opens the full form of a chosen revision, rendered from its snapshot', () => {
    render(
      <RevisionHistory
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

    fireEvent.click(buttons[0]);
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('Product code')).toBeInTheDocument();
    expect(within(dialog).getByText('SRT-OLD')).toBeInTheDocument();
    expect(within(dialog).queryByText('SRT-NEW')).toBeNull();
  });

  it('offers no full-form view for an entry without a snapshot payload', () => {
    render(<RevisionHistory entries={[entry()]} />);
    expect(screen.queryAllByTestId('revision-view-form')).toHaveLength(0);
  });

  /**
   * The other half of the round-7 auth split (mirrored in
   * `RevisionTimeline.attachmentAuth.test.tsx` for the office side, which
   * asserts the opposite: no fetchBytes override). The snapshot dialog's own
   * attachment badges - opened via "View full form" - must reach the shared
   * modal with the portal's token-authenticated reader, exactly like the
   * entry-level badges already covered above. A silent regression back onto
   * the modal's JWT default would 401 every historical attachment for a
   * contact and nothing here would fail without this test.
   */
  it('opens the full-form dialog preview with the portal byte reader, not the JWT default', () => {
    render(
      <RevisionHistory
        entries={[
          entry({
            snapshot_fields: [{ field: 'quantity', label: 'Quantity', value: '4' }],
            attachments: [
              { attachment_id: 'att-1', link_id: null, filename: 'quote.pdf', size: 10 },
              { attachment_id: 'att-2', link_id: null, filename: 'photo.jpg', size: 20 },
            ],
          }),
        ]}
      />,
    );

    fireEvent.click(screen.getByTestId('revision-view-form'));
    const dialog = screen.getByRole('dialog');
    const badges = within(dialog).getAllByTestId('snapshot-attachment');
    expect(badges).toHaveLength(2);

    fireEvent.click(badges[1]);

    const opened = previewCalls.filter((c) => c.open).at(-1);
    expect(opened).toBeDefined();
    expect(opened?.fetchBytes).toBe(portalFetchBytes);
    expect(opened?.startIndex).toBe(1);
    expect(opened?.items[1]).toMatchObject({
      id: 'att-2',
      downloadUrl: '/api/v1/public/portal/attachments/att-2/download',
    });
  });

  it('opens the shared preview modal in place from a history entry, with the portal byte reader', () => {
    render(
      <RevisionHistory
        entries={[
          entry({
            attachments: [
              { attachment_id: 'att-1', link_id: 'link-1', filename: 'quote.pdf', size: 10 },
              { attachment_id: 'att-2', link_id: null, filename: 'old-photo.jpg', size: 20 },
            ],
          }),
        ]}
        currentAttachments={[
          {
            link_id: 'link-1',
            attachment_id: 'att-1',
            filename: 'quote.pdf',
            size: 10,
            url: 'https://cdn.example.com/quote.pdf',
          },
        ]}
      />,
    );

    // No new tab: the chip is a button, never an anchor to a protected route.
    expect(document.body.querySelector('a[target="_blank"]')).toBeNull();
    fireEvent.click(screen.getByLabelText('Preview old-photo.jpg'));

    expect(screen.getByTestId('preview-modal')).toBeInTheDocument();
    const opened = previewCalls.filter((c) => c.open).at(-1);
    expect(opened?.startIndex).toBe(1);
    expect(opened?.fetchBytes).toBe(portalFetchBytes);
    // Keyed on attachment_id (a file dropped by a later revision has no link left),
    // and the CDN url is borrowed only where the file is still linked today.
    expect(opened?.items[0]).toMatchObject({
      id: 'att-1',
      url: 'https://cdn.example.com/quote.pdf',
      downloadUrl: '/api/v1/public/portal/attachments/att-1/download',
    });
    expect(opened?.items[1]).toMatchObject({
      id: 'att-2',
      url: '',
      downloadUrl: '/api/v1/public/portal/attachments/att-2/download',
    });
  });

  // The snapshot's own signed url is the whole mechanism that keeps a file an
  // earlier revision removed previewable (UAC I2a / G6). The backend resolves one
  // per snapshot entry at read time; discarding it sends every historical file to
  // the download-card fallback, which looks like a backend gap and is not one.
  it('previews a historical file inline off the snapshot url alone', () => {
    render(
      <RevisionHistory
        entries={[
          entry({
            attachments: [
              {
                attachment_id: 'att-9',
                link_id: null,
                filename: 'removed-quote.pdf',
                size: 42,
                url: 'https://cdn.example.com/signed/removed-quote.pdf?sig=1',
              },
            ],
          }),
        ]}
        // The file is gone from the live list - which is exactly the case that
        // used to lose its url.
        currentAttachments={[]}
      />,
    );

    fireEvent.click(screen.getByLabelText('Preview removed-quote.pdf'));

    const opened = previewCalls.filter((c) => c.open).at(-1);
    expect(opened?.items[0]).toMatchObject({
      id: 'att-9',
      url: 'https://cdn.example.com/signed/removed-quote.pdf?sig=1',
    });
  });

  it('prefers the snapshot url over the live list for the same attachment', () => {
    render(
      <RevisionHistory
        entries={[
          entry({
            attachments: [
              {
                attachment_id: 'att-1',
                link_id: 'link-1',
                filename: 'quote.pdf',
                size: 10,
                url: 'https://cdn.example.com/snapshot/quote.pdf?sig=fresh',
              },
            ],
          }),
        ]}
        currentAttachments={[
          {
            link_id: 'link-1',
            attachment_id: 'att-1',
            filename: 'quote.pdf',
            size: 10,
            url: 'https://cdn.example.com/live/quote.pdf?sig=stale',
          },
        ]}
      />,
    );

    fireEvent.click(screen.getByLabelText('Preview quote.pdf'));

    const opened = previewCalls.filter((c) => c.open).at(-1);
    expect(opened?.items[0].url).toBe(
      'https://cdn.example.com/snapshot/quote.pdf?sig=fresh',
    );
  });

  /**
   * The regression guard for the timezone divergence.
   *
   * `submitted_at` arrives as naive UTC with no `Z`, so `new Date(value)` reads it
   * as the viewer's local clock: the same revision showed 1:30 PM on the office
   * timeline and 05:30 here. Asserted two ways on purpose - the literal Malaysia
   * wall clock (which fails loudly the moment anyone swaps back to `new Date`),
   * and equality with the helper the office side uses, so the two can never drift
   * apart again whatever the format becomes.
   */
  describe('timestamps', () => {
    const NAIVE_UTC = '2026-07-20T05:30:00'; // 13:30 in Malaysia

    /** ICU spells the space before am/pm differently across versions: a narrow
     *  no-break space on newer builds, a plain one on older. */
    const flatten = (s: string) => s.replace(/[\u202f\u00a0]/g, ' ');

    it('renders a naive-UTC submitted_at as the Malaysia wall clock', () => {
      render(<RevisionHistory entries={[entry({ submitted_at: NAIVE_UTC })]} />);

      const row = screen.getByTestId('revision-entry');
      const shown = flatten(row.textContent ?? '');
      expect(shown).toContain('20/07/2026, 1:30 pm');
      // The naive string read as local time - the bug - would surface 05:30.
      expect(shown).not.toMatch(/\b0?5:30\b/);
    });

    it('shows the same wall clock the office timeline shows for that event', () => {
      render(<RevisionHistory entries={[entry({ submitted_at: NAIVE_UTC })]} />);

      const row = screen.getByTestId('revision-entry');
      // `RevisionTimeline` (office) renders `formatDateTimeInMalaysia(submitted_at)`.
      expect(flatten(row.textContent ?? '')).toContain(
        flatten(formatDateTimeInMalaysia(NAIVE_UTC)),
      );
    });
  });

  it('shows the superseded stage answer beside the version it answered, and no ids', () => {
    render(
      <RevisionHistory
        entries={[
          entry({
            id: 'rev-1',
            version_no: 1,
            revision_no: 1,
            kind: 'revision',
            label: 'Revision 1',
            reason: 'Quantity changed.',
            invalidated: {
              purchasing_response: 'We have 20 units in stock.',
              last_responded_by: '0f0c9a52-1b6f-4f0a-9c9e-6f0f2f4b7c11',
              last_responded_at: '2026-07-25T02:00:00',
            },
          }),
        ]}
      />,
    );

    expect(screen.getByTestId('revision-superseded')).toHaveTextContent(
      'Superseded reply: We have 20 units in stock.',
    );
    expect(document.body.textContent).not.toContain('0f0c9a52');
  });

  /**
   * UAC H3a on the contact's side. A revision can stop two stages at once (a
   * purchase request with project sales and approval both open), so the history
   * lists all of them - the newest scalar alone tells the contact that one thing
   * stopped when two did.
   */
  describe('stopped stages', () => {
    it('lists every stage the revision stopped', () => {
      render(
        <RevisionHistory
          entries={[
            entry({
              id: 'rev-1',
              version_no: 1,
              revision_no: 1,
              kind: 'revision',
              label: 'Revision 1',
              // The scalar pair still holds the newest stage; the list drives it.
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
      expect(lines).toEqual(['Stopped: approval (CONTACT AC)', 'project sales (Mei Ling)']);
    });

    it('falls back to the scalar stage for a row written before the list existed', () => {
      render(
        <RevisionHistory
          entries={[
            entry({
              id: 'rev-1',
              version_no: 1,
              revision_no: 1,
              kind: 'revision',
              label: 'Revision 1',
              voided_stage_code: 'purchasing_response',
              voided_assignee_name: 'Mei Ling',
              voided_stages: [],
            }),
          ]}
        />,
      );

      expect(
        screen.getAllByTestId('revision-voided-stage').map((el) => el.textContent),
      ).toEqual(['Stopped: purchasing response (Mei Ling)']);
    });

    it('renders nothing when the revision stopped no stage at all', () => {
      render(
        <RevisionHistory
          entries={[
            entry({
              id: 'rev-1',
              version_no: 1,
              revision_no: 1,
              kind: 'revision',
              label: 'Revision 1',
            }),
          ]}
        />,
      );

      expect(screen.queryAllByTestId('revision-voided-stage')).toHaveLength(0);
      expect(screen.queryByText(/Stopped/)).toBeNull();
    });
  });

  it('surfaces a load failure instead of an empty timeline', () => {
    render(<RevisionHistory entries={[]} error="Failed to load revision history." />);
    expect(screen.getByText('Failed to load revision history.')).toBeInTheDocument();
    expect(screen.queryByText('Original submission only.')).toBeNull();
  });
});
