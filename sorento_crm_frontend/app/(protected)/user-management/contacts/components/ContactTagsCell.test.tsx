/**
 * CONTACT-TAGS S1, UAC T5.3: the Tags cell of the contacts list. Reuses PillOverflow (the "+N"
 * folding lives there); empty shows a dash.
 * Assumed: ./ContactTagsCell default export, props { contact: { id, name, phone_number, tags } }.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

const seen = vi.hoisted(() => ({ items: null as null | { key: string; label: string }[] }));
vi.mock('@/components/common/PillOverflow', () => ({
  PillOverflow: (props: { items: { key: string; label: string }[] }) => {
    seen.items = props.items;
    return (
      <div data-testid="pill-overflow">
        {props.items.map((i) => (
          <span key={i.key}>{i.label}</span>
        ))}
      </div>
    );
  },
  PillOverflowList: () => null,
}));

import ContactTagsCell from './ContactTagsCell';

const contact = { id: 'c1', name: 'Alice', phone_number: '6011' };

describe('ContactTagsCell', () => {
  it('hands every tag name to PillOverflow, in the order given', () => {
    render(
      <ContactTagsCell
        contact={{
          ...contact,
          tags: [
            { id: 't1', name: 'Dealer' },
            { id: 't2', name: 'VIP' },
            { id: 't3', name: 'West' },
          ],
        } as never}
      />,
    );
    expect(seen.items?.map((i) => i.label)).toEqual(['Dealer', 'VIP', 'West']);
    expect(screen.getByText('Dealer')).toBeInTheDocument();
  });

  it('shows a dash and no pills when the contact has no tags', () => {
    render(<ContactTagsCell contact={{ ...contact, tags: [] } as never} />);
    expect(screen.queryByTestId('pill-overflow')).toBeNull();
    expect(screen.getByText('-')).toBeInTheDocument();
  });

  it('treats a missing tags field as empty', () => {
    render(<ContactTagsCell contact={contact as never} />);
    expect(screen.getByText('-')).toBeInTheDocument();
  });
});
