/**
 * CONTACT-TAGS S2, UAC T5.3: the Tag settings cell of the contacts list.
 * Assumed: ./ContactTagSettingsCell default export, props { contact: { tag_settings?:
 * { status: 'matches'|'differs'|'none', differ_count: number, differ_labels: string[] } } }.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import ContactTagSettingsCell from './ContactTagSettingsCell';

describe('ContactTagSettingsCell', () => {
  it('shows a Matches badge', () => {
    render(<ContactTagSettingsCell contact={{ tag_settings: { status: 'matches', differ_count: 0, differ_labels: [] } } as never} />);
    expect(screen.getByText('Matches')).toBeInTheDocument();
  });

  it('shows "Differs: 2" with both labels in the title', () => {
    render(
      <ContactTagSettingsCell
        contact={{ tag_settings: { status: 'differs', differ_count: 2, differ_labels: ['Stock checks', 'Tier'] } } as never}
      />,
    );
    const badge = screen.getByText('Differs: 2');
    const title = badge.closest('[title]')?.getAttribute('title') ?? '';
    expect(title).toContain('Stock checks');
    expect(title).toContain('Tier');
  });

  it('shows a dash for none or a missing field', () => {
    const { unmount } = render(
      <ContactTagSettingsCell contact={{ tag_settings: { status: 'none', differ_count: 0, differ_labels: [] } } as never} />,
    );
    expect(screen.getByText('-')).toBeInTheDocument();
    unmount();
    render(<ContactTagSettingsCell contact={{} as never} />);
    expect(screen.getByText('-')).toBeInTheDocument();
  });
});
