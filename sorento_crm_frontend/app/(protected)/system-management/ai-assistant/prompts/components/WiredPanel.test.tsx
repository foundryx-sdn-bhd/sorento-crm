/** PROMPT-DYNAMIC R5a: the "Wired to this agent" panel. Red before the component exists. */
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { WiredPanel } from './WiredPanel';
import type { RegistryVariableRow } from '../../services/aiPromptsService';

const ROWS: RegistryVariableRow[] = [
  { name: 'domains', label: 'Domains', source: 'Chatbot Domains', href: '/system-management/chatbot-domains', count: 15, last_changed: '2026-09-30T09:00:00', rendered: 'a | b', used: false },
  { name: 'brands', label: 'Brands', source: 'Brands', href: '/master-data-management/brands', count: 9, last_changed: null, rendered: 'Sorento (SRT)', used: false },
];

afterEach(() => cleanup());

describe('WiredPanel', () => {
  it('lists each source with its count, link and whether the wording uses it', () => {
    const onInsert = vi.fn();
    render(<WiredPanel variables={ROWS} draft={'x {{domains}}'} onInsert={onInsert} isLoading={false} />);
    const domains = screen.getByTestId('wired-row-domains');
    expect(domains).toHaveTextContent('Domains');
    expect(domains).toHaveTextContent('15');
    expect(domains).toHaveTextContent('In wording');
    expect(domains.querySelector('a')!.getAttribute('href')).toBe('/system-management/chatbot-domains');
    const brands = screen.getByTestId('wired-row-brands');
    expect(brands).toHaveTextContent('Also sent every turn');
    fireEvent.click(screen.getByTestId('wired-insert-brands'));
    expect(onInsert).toHaveBeenCalledWith('brands');
    expect(screen.queryByTestId('wired-insert-domains')).toBeNull();
  });

  it('shows an explicit empty state', () => {
    render(<WiredPanel variables={[]} draft="" onInsert={() => {}} isLoading={false} />);
    expect(screen.getByTestId('wired-empty')).toBeInTheDocument();
  });
});

describe('WiredPanel, reviewer pass 2', () => {
  it('opens a source in a new tab so an unsaved draft is never lost', () => {
    render(<WiredPanel variables={ROWS} draft="" onInsert={() => {}} isLoading={false} />);
    const link = screen.getByTestId('wired-row-domains').querySelector('a')!;
    expect(link.getAttribute('target')).toBe('_blank');
  });

  it('a source with no admin page gets no link', () => {
    render(<WiredPanel variables={[{ ...ROWS[0], href: '' }]} draft="" onInsert={() => {}} isLoading={false} />);
    expect(screen.getByTestId('wired-row-domains').querySelector('a')).toBeNull();
  });
});
