/**
 * RevisionSnapshotDialog - the whole form as it stood at one revision.
 *
 * Rendered from the entry's own `snapshot_fields` (labeled by the backend),
 * never from the live row: the reader sees the values AS THEY WERE at that
 * version. Read only - no inputs, no actions.
 */
import type { ReactNode } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { RevisionSnapshotDialog, revisionAttachmentPreviewItem } from './RevisionSnapshotDialog';
import type { FormRevisionEntry } from './RevisionTimeline';

// The preview badge opens the real AttachmentPreviewModal (not mocked in this
// file), which mounts embla-carousel - it needs layout/observer APIs jsdom
// lacks. Same stub as AttachmentPreviewModal.test.tsx: the logic under test
// here is the badge/click wiring, not the carousel engine.
vi.mock('@/components/ui/carousel', () => ({
  Carousel: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CarouselContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CarouselItem: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  CarouselNext: () => <button type="button">next</button>,
  CarouselPrevious: () => <button type="button">prev</button>,
}));

function entry(overrides: Partial<FormRevisionEntry> = {}): FormRevisionEntry {
  return {
    id: 'rev-1',
    version_no: 1,
    revision_no: 1,
    kind: 'revision',
    label: 'Revision 1',
    reason: 'Wrong quantity',
    submitted_at: '2026-08-01T02:00:00',
    submitted_by: 'CONTACT X Lee',
    snapshot_fields: [
      { field: 'inquiry_number', label: 'Inquiry number', value: 'SI-26-0184', display: null },
      { field: 'product_code', label: 'Product code', value: 'SRTWT51030', display: null },
      { field: 'quantity', label: 'Quantity', value: '4', display: null },
      {
        field: 'delivery_date',
        label: 'Delivery date',
        value: '2026-08-20',
        display: '20/08/2026',
      },
      { field: 'remark', label: 'Remark', value: null, display: null },
    ],
    ...overrides,
  };
}

describe('RevisionSnapshotDialog', () => {
  it('renders nothing when no entry is selected', () => {
    render(<RevisionSnapshotDialog entry={null} onOpenChange={() => {}} />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders every field with its label and the value as it was', () => {
    render(<RevisionSnapshotDialog entry={entry()} onOpenChange={() => {}} />);

    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('Revision 1')).toBeInTheDocument();
    expect(screen.getByText('Inquiry number')).toBeInTheDocument();
    expect(screen.getByText('SI-26-0184')).toBeInTheDocument();
    expect(screen.getByText('Product code')).toBeInTheDocument();
    expect(screen.getByText('SRTWT51030')).toBeInTheDocument();
    expect(screen.getAllByTestId('snapshot-field')).toHaveLength(5);
  });

  it('shows an explicit empty state for a field with no value, never hides it', () => {
    render(<RevisionSnapshotDialog entry={entry()} onOpenChange={() => {}} />);

    expect(screen.getByText('Remark')).toBeInTheDocument();
    expect(screen.getByText('Not provided')).toBeInTheDocument();
  });

  it('names who sent the version and when', () => {
    render(<RevisionSnapshotDialog entry={entry()} onOpenChange={() => {}} />);
    expect(screen.getByText(/by CONTACT X Lee/)).toBeInTheDocument();
  });

  it('renders line items as a table, one row per product', () => {
    render(
      <RevisionSnapshotDialog
        entry={entry({
          snapshot_fields: [
            { field: 'project_title', label: 'Project title', value: 'Showroom' },
            {
              field: 'products',
              label: 'Products',
              value: [
                {
                  item_code: 'SRTWT51030',
                  quantity: '2',
                  unit_price: '1200',
                  total: '2400',
                  remark: null,
                },
                {
                  item_code: 'SRTBT2201',
                  quantity: '1',
                  unit_price: '800',
                  total: '800',
                  remark: 'White',
                },
              ],
            },
          ],
        })}
        onOpenChange={() => {}}
      />,
    );

    expect(screen.getByText('Products')).toBeInTheDocument();
    expect(screen.getAllByTestId('snapshot-product-row')).toHaveLength(2);
    expect(screen.getByText('SRTBT2201')).toBeInTheDocument();
    expect(screen.getByText('White')).toBeInTheDocument();
  });

  it('shows the date the backend rendered, not the raw ISO string it stored', () => {
    render(
      <RevisionSnapshotDialog
        entry={entry({
          snapshot_fields: [
            {
              field: 'delivery_date',
              label: 'Delivery date',
              value: '2026-08-20',
              display: '20/08/2026',
            },
          ],
        })}
        onOpenChange={() => {}}
      />,
    );

    expect(screen.getByText('20/08/2026')).toBeInTheDocument();
    expect(screen.queryByText('2026-08-20')).toBeNull();
  });

  it('shows the display the backend rendered for a lookup code, never the code', () => {
    render(
      <RevisionSnapshotDialog
        entry={entry({
          snapshot_fields: [
            {
              field: 'sales_type',
              label: 'Sales type',
              value: 'cash_sales',
              display: 'Cash sales',
            },
          ],
        })}
        onOpenChange={() => {}}
      />,
    );

    expect(screen.getByText('Cash sales')).toBeInTheDocument();
    expect(screen.queryByText('cash_sales')).toBeNull();
  });

  it('falls back to the stored value for a field the backend added no display to', () => {
    render(
      <RevisionSnapshotDialog
        entry={entry({
          snapshot_fields: [
            { field: 'project_name', label: 'Project name', value: 'Showroom KL', display: null },
            { field: 'remark', label: 'Remark', value: 'Deliver after 5pm' },
          ],
        })}
        onOpenChange={() => {}}
      />,
    );

    expect(screen.getByText('Showroom KL')).toBeInTheDocument();
    expect(screen.getByText('Deliver after 5pm')).toBeInTheDocument();
  });

  it('lists the files as they stood at this version, including one a later revision removed', () => {
    render(
      <RevisionSnapshotDialog
        entry={entry({
          attachments: [
            { attachment_id: 'att-1', filename: 'quote.pdf' },
            { attachment_id: 'att-2', filename: 'dropped-later.jpg' },
          ],
        })}
        onOpenChange={() => {}}
      />,
    );

    expect(screen.getAllByTestId('snapshot-attachment')).toHaveLength(2);
    expect(screen.getByText('quote.pdf')).toBeInTheDocument();
    expect(screen.getByText('dropped-later.jpg')).toBeInTheDocument();
  });

  it('renders the attachments section with an explicit empty state, never hidden', () => {
    render(<RevisionSnapshotDialog entry={entry({ attachments: [] })} onOpenChange={() => {}} />);

    expect(screen.getByText('Attachments')).toBeInTheDocument();
    expect(screen.getByText('None')).toBeInTheDocument();
  });

  it('keeps the attachment badge a clickable button that opens the preview, not a static label', () => {
    render(
      <RevisionSnapshotDialog
        entry={entry({ attachments: [{ attachment_id: 'att-1', filename: 'quote.pdf' }] })}
        onOpenChange={() => {}}
      />,
    );

    const badge = screen.getByTestId('snapshot-attachment');
    expect(badge.tagName).toBe('BUTTON');
    expect((badge as HTMLButtonElement).type).toBe('button');

    fireEvent.click(badge);

    // The preview modal stacks as a second dialog beside the snapshot dialog
    // (Escape closes the preview first, the version underneath stays open).
    // Dialogs are modal since UAC S1-01, so the version underneath is inerted
    // while the preview is open and no longer answers to the `dialog` ROLE -
    // stacking is asserted on the DOM instead, which is what "still open
    // underneath" actually means.
    expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(2);
    expect(screen.getByText('This attachment has no previewable URL.')).toBeInTheDocument();
  });

  it('contains no form controls: the view is read only', () => {
    render(<RevisionSnapshotDialog entry={entry()} onOpenChange={() => {}} />);
    const dialog = screen.getByRole('dialog');
    expect(dialog.querySelectorAll('input, textarea, select')).toHaveLength(0);
  });

  it('reports the close request through onOpenChange', () => {
    const onOpenChange = vi.fn();
    render(<RevisionSnapshotDialog entry={entry()} onOpenChange={onOpenChange} />);
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

/**
 * `revisionAttachmentPreviewItem` is the one place both sides of the dialog's
 * auth split (office `RevisionTimeline`, portal `RevisionHistory`) build a
 * preview item from a revision's snapshotted attachment, so it is worth
 * pinning on its own rather than only through each caller's render test.
 */
describe('revisionAttachmentPreviewItem', () => {
  it('maps attachment_id, filename and size, and uses the entry own signed url', () => {
    const item = revisionAttachmentPreviewItem({
      attachment_id: 'att-1',
      filename: 'quote.pdf',
      size: 1024,
      url: 'https://cdn.example.com/signed/quote.pdf?sig=1',
    });

    expect(item).toEqual({
      id: 'att-1',
      name: 'quote.pdf',
      url: 'https://cdn.example.com/signed/quote.pdf?sig=1',
      downloadUrl: undefined,
      sizeBytes: 1024,
    });
  });

  it('produces a downloadUrl only when the caller supplies an attachmentDownloadUrl builder', () => {
    const withBuilder = revisionAttachmentPreviewItem(
      { attachment_id: 'att-1', filename: 'quote.pdf' },
      (id) => `/download/${id}`,
    );
    expect(withBuilder.downloadUrl).toBe('/download/att-1');

    const withoutBuilder = revisionAttachmentPreviewItem({
      attachment_id: 'att-1',
      filename: 'quote.pdf',
    });
    expect(withoutBuilder.downloadUrl).toBeUndefined();
  });

  it('degrades rather than breaking for an attachment with no signed url', () => {
    const item = revisionAttachmentPreviewItem(
      { attachment_id: 'att-2', filename: 'no-url.jpg' },
      (id) => `/download/${id}`,
    );

    // The item still builds - the modal is what decides how to show the
    // fallback, not this mapper.
    expect(item.url).toBe('');
    expect(item.name).toBe('no-url.jpg');
    expect(item.downloadUrl).toBe('/download/att-2');
  });

  it('falls back to a generic name and no size when the attachment carries neither', () => {
    const item = revisionAttachmentPreviewItem({ attachment_id: 'att-3' });

    expect(item.name).toBe('Attachment');
    expect(item.sizeBytes).toBeNull();
  });

  it('never asks for a downloadUrl off an attachment with no id', () => {
    const attachmentDownloadUrl = vi.fn((id: string) => `/download/${id}`);
    const item = revisionAttachmentPreviewItem(
      { attachment_id: '', filename: 'orphan.jpg' },
      attachmentDownloadUrl,
    );

    expect(item.downloadUrl).toBeUndefined();
    expect(attachmentDownloadUrl).not.toHaveBeenCalled();
  });
});
