/** AC-SN-44: the shared "send all parked" dialog. */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const frameProps = vi.fn();
vi.mock('@/app/(protected)/system-management/email-templates/components/EmailPreviewFrame', () => ({
  EmailPreviewFrame: (props: { subject?: string | null; html?: string | null }) => {
    frameProps(props);
    return <div data-testid="email-frame">{props.html}</div>;
  },
}));

import { ParkedSendDialog } from './ParkedSendDialog';

const PREVIEW = {
  emails: [
    {
      to: { name: 'Eling' },
      cc: [{ name: 'Joey' }],
      inquiry_count: 9,
      line_count: 180,
      subject: 'Reserve requests: 9 order inquiries (Joey)',
      body_html: '<p>nine-inquiries-body</p>',
    },
    {
      to: { name: 'Niki' },
      cc: [{ name: 'Joey' }],
      inquiry_count: 8,
      line_count: 58,
      subject: 'Reserve requests: 8 order inquiries (Joey)',
      body_html: '<p>eight-inquiries-body</p>',
    },
  ],
  unresolved: [] as { inquiry_count: number; line_count: number }[],
};

const onSend = vi.fn(async () => ({ emails_sent: 2, items_sent: 17, unresolved: 0 }));

function renderDialog(over: Partial<React.ComponentProps<typeof ParkedSendDialog>> = {}) {
  return render(
    <ParkedSendDialog
      open
      onOpenChange={vi.fn()}
      title="Send all parked requests to CS"
      preview={PREVIEW}
      onSend={onSend}
      {...over}
    />,
  );
}

beforeEach(() => vi.clearAllMocks());

describe('AC-SN-44 ParkedSendDialog', () => {
  it('shows the title and the subtitle counts', () => {
    renderDialog();
    expect(screen.getByText('Send all parked requests to CS')).toBeInTheDocument();
    expect(screen.getByText('17 order inquiries, 2 emails')).toBeInTheDocument();
  });

  it('shows the recipient table with a total row', () => {
    renderDialog();
    const table = screen.getByRole('table');
    for (const h of ['To', 'Cc', 'Order inquiries', 'Lines']) {
      expect(within(table).getByRole('columnheader', { name: h })).toBeInTheDocument();
    }
    const eling = within(table).getByText('Eling').closest('tr') as HTMLElement;
    expect(within(eling).getByText('180')).toBeInTheDocument();
    const total = within(table).getByText('2 emails').closest('tr') as HTMLElement;
    expect(within(total).getByText('17')).toBeInTheDocument();
    expect(within(total).getByText('238')).toBeInTheDocument();
  });

  it('one tab per email, first selected', () => {
    renderDialog();
    expect(screen.getByRole('tab', { name: 'Email to Eling (9)' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Email to Niki (8)' })).toHaveAttribute('aria-selected', 'false');
  });

  it('the preview shows the selected email subject and passes its body_html to the frame', () => {
    renderDialog();
    expect(screen.getByText('Reserve requests: 9 order inquiries (Joey)')).toBeInTheDocument();
    expect(frameProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ html: '<p>nine-inquiries-body</p>' }),
    );
  });

  it('clicking a table line selects that email tab', () => {
    renderDialog();
    const table = screen.getByRole('table');
    fireEvent.click(within(table).getByText('Niki'));
    expect(screen.getByRole('tab', { name: 'Email to Niki (8)' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Reserve requests: 8 order inquiries (Joey)')).toBeInTheDocument();
    expect(frameProps).toHaveBeenLastCalledWith(
      expect.objectContaining({ html: '<p>eight-inquiries-body</p>' }),
    );
  });

  it('clicking a tab switches the preview', () => {
    renderDialog();
    fireEvent.click(screen.getByRole('tab', { name: 'Email to Niki (8)' }));
    expect(screen.getByText('Reserve requests: 8 order inquiries (Joey)')).toBeInTheDocument();
  });

  it('shows a No recipient line per unresolved group', () => {
    renderDialog({
      preview: { ...PREVIEW, unresolved: [{ inquiry_count: 1, line_count: 3 }] },
    });
    const line = screen.getByText(/no recipient/i).closest('tr') as HTMLElement;
    expect(within(line).getByText(/stays parked/i)).toBeInTheDocument();
    expect(within(line).getByText('3')).toBeInTheDocument();
  });

  it('Send 2 emails calls onSend once', async () => {
    renderDialog();
    fireEvent.click(screen.getByRole('button', { name: 'Send 2 emails' }));
    await waitFor(() => expect(onSend).toHaveBeenCalledTimes(1));
  });

  it('Send is disabled while sending', () => {
    renderDialog({ isSending: true });
    expect(screen.getByRole('button', { name: /send 2 emails/i })).toBeDisabled();
  });

  it('loading state shows no table and no enabled send', () => {
    renderDialog({ preview: undefined, isLoading: true });
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^send \d+ emails?$/i })).not.toBeInTheDocument();
  });
});
