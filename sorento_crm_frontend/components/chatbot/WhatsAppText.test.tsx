/**
 * Red tests for #1277 (AC-7): the chatbot console's bot bubble renders WhatsApp
 * `*bold*` as bold, instead of the literal asterisks. `WhatsAppText` wraps the
 * existing parser (`lib/whatsappText.ts::parseWhatsAppText`) so both the console
 * and any other bot-bubble surface render the same segments the same way.
 *
 * No `dangerouslySetInnerHTML` (Cursor rule / repo convention): literal markup in
 * the text must render as text, never be interpreted as HTML.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import { WhatsAppText } from './WhatsAppText';

afterEach(() => cleanup());

describe('WhatsAppText', () => {
  it('renders a bold label and keeps the rest of the text, including the newline', () => {
    const { container } = render(<WhatsAppText text={'*Problem:* sales report\nplain'} />);

    const strong = screen.getByText('Problem:');
    expect(strong.tagName).toBe('STRONG');
    expect(container.textContent).toContain('sales report');
    expect(container.textContent).toContain('plain');
  });

  it('renders no <strong> for a lone asterisk with no closing pair', () => {
    render(<WhatsAppText text="2 * 3" />);
    expect(document.querySelector('strong')).toBeNull();
  });

  it('never interprets literal markup as HTML (no dangerouslySetInnerHTML)', () => {
    const { container } = render(<WhatsAppText text="<b>x</b>" />);
    expect(container.querySelector('b')).toBeNull();
    expect(container.textContent).toContain('<b>x</b>');
  });

  // PHOTO-SEND-CAP hand test (owner, 9 Oct 2026): in link mode the console shows the
  // reply's short links, which must open from the console, on the crew copy's port too.
  it.each([
    'http://photo-send-cap.localhost:3111/f/7K2QX9MWAPH3V8RZ',
    'https://crm.example.com/f/7K2QX9MWAPH3V8RZ',
  ])('makes a /f/ short link clickable after a bold label: %s', (url) => {
    render(<WhatsAppText text={`*File Name:* Vixx Loo A.pdf\n*Link:* ${url}`} />);
    const link = screen.getByRole('link', { name: url });
    expect(link.getAttribute('href')).toBe(url);
    expect(link.getAttribute('target')).toBe('_blank');
  });
});
