/**
 * The Sent tab (S2, AC-B1, AC-B4): what has already gone out for this plan. Moved out of
 * `ContainerRequestSection`'s inline `noticesCard` into its own component so it can be a tab
 * body; every "requests already sent" and "survives every early return" behaviour that used
 * to live in `ContainerRequestSection.test.tsx` lives here now, plus the new empty-state Send
 * button AC-B4 asks for.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import type { SupplierNotice } from '../../services/fulfilmentService';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

const getNoticeDocumentUrlMock = vi.fn();
vi.mock('../../services/fulfilmentService', async () => {
  const actual = await vi.importActual<typeof import('../../services/fulfilmentService')>(
    '../../services/fulfilmentService',
  );
  return {
    ...actual,
    getNoticeDocumentUrl: (...args: [string, string]) => getNoticeDocumentUrlMock(...args),
  };
});

import { SentRequestsPanel } from './SentRequestsPanel';

const onSend = vi.fn();

function renderPanel(over: Partial<React.ComponentProps<typeof SentRequestsPanel>> = {}) {
  return render(
    <SentRequestsPanel
      supplierName="Foshan Ceramics"
      notices={[]}
      onSend={onSend}
      {...over}
    />,
  );
}

const sentRequest = (): SupplierNotice => ({
  id: 'n-3',
  supplier_id: 'sup-1',
  supplier_name: 'Foshan Ceramics',
  loading_plan_id: null,
  notice_type: 'container_request',
  channel: 'email',
  recipient: 'sales@foshan.test',
  recipients: ['sales@foshan.test', 'person27@example.com'],
  opened_at: null,
  last_opened_at: null,
  open_count: 0,
  status: 'sent',
  status_reason: null,
  sent_at: '2026-08-18T02:00:00',
  attempt_count: 1,
  last_error: null,
  document_filename: 'container-request.pdf',
  has_document: true,
  xlsx_filename: 'container-request.xlsx',
  has_xlsx: true,
  public_url: 'https://crm.test/c/SRT/supplier-request/tok-1',
  link_retired: false,
  container_type: null,
  container_count: null,
  planned_cbm: null,
  line_count: 4,
  production_line_count: 0,
  created_at: '2026-08-18T02:00:00',
  created_by: 'Ms Tee',
});

beforeEach(() => {
  onSend.mockReset();
  getNoticeDocumentUrlMock.mockReset();
  getNoticeDocumentUrlMock.mockResolvedValue({ url: 'https://cdn.test/doc.pdf', filename: 'doc.pdf' });
});

describe('SentRequestsPanel - nothing sent yet (AC-B4)', () => {
  it('says nothing has been sent and offers a Send button', () => {
    renderPanel();

    expect(screen.getByText('Requests sent to Foshan Ceramics')).toBeInTheDocument();
    expect(screen.getByText('Nothing sent yet.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /send to supplier/i }));
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it('disables the empty-state Send button and states why, on a cancelled plan', () => {
    renderPanel({ sendDisabled: true, sendDisabledReason: 'This plan is cancelled.' });

    const send = screen.getByRole('button', { name: /send to supplier/i }) as HTMLButtonElement;
    expect(send.disabled).toBe(true);
    expect(send.getAttribute('title')).toBe('This plan is cancelled.');
  });
});

describe('SentRequestsPanel - requests already sent', () => {
  it('lists a previously sent request with its channel and status', () => {
    renderPanel({
      notices: [
        {
          ...sentRequest(),
          id: 'n-1',
          recipients: null,
          recipient: 'sales@foshan.test',
        },
      ],
    });

    expect(screen.getByText('Requests sent to Foshan Ceramics')).toBeInTheDocument();
    expect(screen.getByText('Email')).toBeInTheDocument();
    expect(screen.getByText('Sent')).toBeInTheDocument();
  });

  it('clicking PDF calls getNoticeDocumentUrl for that notice and opens the returned url', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderPanel({ notices: [sentRequest()] });

    fireEvent.click(
      within(screen.getByTestId('requests-sent')).getByRole('button', { name: /^pdf$/i }),
    );

    await waitFor(() => expect(getNoticeDocumentUrlMock).toHaveBeenCalledWith('n-3', 'pdf'));
    await waitFor(() =>
      expect(openSpy).toHaveBeenCalledWith('https://cdn.test/doc.pdf', '_blank', 'noopener'),
    );
    openSpy.mockRestore();
  });

  // AC-C4: the card offers all three of what the send produced.
  it('clicking XLSX asks for the spreadsheet, not the pdf', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    renderPanel({ notices: [sentRequest()] });

    fireEvent.click(
      within(screen.getByTestId('requests-sent')).getByRole('button', { name: /xlsx/i }),
    );

    await waitFor(() => expect(getNoticeDocumentUrlMock).toHaveBeenCalledWith('n-3', 'xlsx'));
    openSpy.mockRestore();
  });

  it('a notice sent before the spreadsheet existed offers no XLSX button', () => {
    renderPanel({ notices: [{ ...sentRequest(), has_xlsx: false, xlsx_filename: null }] });

    expect(
      within(screen.getByTestId('requests-sent')).queryByRole('button', { name: /xlsx/i }),
    ).not.toBeInTheDocument();
  });

  it('Copy link puts the supplier page on the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderPanel({ notices: [sentRequest()] });

    // Scoped to the card: the gear on the header offers the same action for the CURRENT
    // link, and this is about the row's own button.
    fireEvent.click(
      within(screen.getByTestId('requests-sent')).getByRole('button', { name: /copy link/i }),
    );

    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith('https://crm.test/c/SRT/supplier-request/tok-1'),
    );
  });

  it('a retired link says so instead of offering a dead button (AC-C8)', () => {
    // A copied dead link is worse than no button: the supplier opens it, is told it is gone,
    // and has no way to tell that a live one exists. Silence is not right either - the row
    // would read like one that never carried a link at all.
    renderPanel({ notices: [{ ...sentRequest(), public_url: null, link_retired: true }] });

    const card = within(screen.getByTestId('requests-sent'));
    expect(card.queryByRole('button', { name: /copy link/i })).not.toBeInTheDocument();
    expect(card.getByText('Link retired')).toBeInTheDocument();
  });

  it('a notice that never carried a link says nothing about one', () => {
    renderPanel({ notices: [{ ...sentRequest(), public_url: null, link_retired: false }] });

    const card = within(screen.getByTestId('requests-sent'));
    expect(card.queryByRole('button', { name: /copy link/i })).not.toBeInTheDocument();
    expect(card.queryByText('Link retired')).not.toBeInTheDocument();
  });

  it('lists every address the send named, not just the first (AC-C2)', () => {
    renderPanel({ notices: [sentRequest()] });

    expect(
      within(screen.getByTestId('requests-sent')).getByText(
        'sales@foshan.test, person27@example.com',
      ),
    ).toBeInTheDocument();
  });

  it('names the WeChat contact a chat send went to, never its id (AC-C2)', () => {
    renderPanel({
      notices: [
        {
          ...sentRequest(),
          id: 'n-9',
          channel: 'chat',
          recipient: null,
          recipients: [
            { respond_contact_id: '6b2f...uuid', name: 'Mr Chen (JBC)', channel: 'wechat' },
          ],
        },
      ],
    });

    const card = within(screen.getByTestId('requests-sent'));
    expect(card.getByText('WeChat')).toBeInTheDocument();
    expect(card.getByText('Mr Chen (JBC)')).toBeInTheDocument();
    expect(card.queryByText(/6b2f/)).not.toBeInTheDocument();
  });

  it('says whether the supplier has opened the link, and how often (AC-C8)', () => {
    renderPanel({
      notices: [{ ...sentRequest(), open_count: 3, last_opened_at: '2026-08-27T07:10:00' }],
    });

    expect(screen.getByTestId('notice-opens').textContent).toContain('Opened 3 times, last');
  });

  it('a link nobody has opened says so, rather than falling silent (AC-C8)', () => {
    renderPanel({ notices: [sentRequest()] });

    expect(screen.getByTestId('notice-opens').textContent).toBe('Not opened yet');
  });

  it('offers Copy link on BOTH rows of one send (AC-C8)', () => {
    // R23: one credential, delivered two ways. The chat row is the one Ms Tee copies from
    // for WeChat, so a link on the email row alone is a link she cannot reach where she
    // looks for it.
    renderPanel({
      notices: [sentRequest(), { ...sentRequest(), id: 'n-4', channel: 'chat', status: 'skipped' }],
    });

    expect(
      within(screen.getByTestId('requests-sent')).getAllByRole('button', { name: /copy link/i }),
    ).toHaveLength(2);
  });
});
