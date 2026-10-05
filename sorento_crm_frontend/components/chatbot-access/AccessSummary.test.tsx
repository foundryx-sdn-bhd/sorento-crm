/**
 * Access model S6, mock v8 (AC-AM-7, AC-AM-25 customers label). Red test: AccessSummary.tsx does not exist.
 * Props: { mode:'contact'|'role', registry, effective:{domains,attributes,sees_all_customers},
 *          customersLabel:string }
 */
import React from 'react';
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';

import AccessSummary from './AccessSummary';

afterEach(cleanup);

const registry = {
  domains: [
    { name: 'inventory', label: 'Stock available', group: 'Stock and incoming', access_section: null, fields: [] },
    { name: 'order', label: 'Orders and deliveries', group: 'Orders', access_section: null, fields: [] },
    { name: 'sales', label: 'Sales report', group: 'Reports', access_section: 'reports', fields: [] },
  ],
};
const effective = { domains: ['inventory'], attributes: [], sees_all_customers: true };

function renderIt(mode: 'contact' | 'role') {
  return render(
    <AccessSummary mode={mode} registry={registry as never} effective={effective} customersLabel="all customers" />,
  );
}

describe('AccessSummary', () => {
  it('AC-AM-7 contact mode says "This contact can" and lists granted topics', () => {
    renderIt('contact');
    expect(screen.getByText(/This contact can/)).toBeTruthy();
    expect(screen.getByText(/ask about:/).textContent).toContain('Stock available');
    expect(screen.getByText(/ask about:/).textContent).not.toContain('Orders and deliveries');
  });

  it('AC-AM-7 role mode says "Contacts with this role can"', () => {
    renderIt('role');
    expect(screen.getByText(/Contacts with this role can/)).toBeTruthy();
  });

  it('AC-AM-7 "cannot ask:" lists the topics and reports not granted', () => {
    renderIt('contact');
    const t = screen.getByText(/cannot ask:/).textContent ?? '';
    expect(t).toContain('Orders and deliveries');
    expect(t).toContain('Sales report');
    expect(t).not.toContain('Stock available');
  });

  it('AC-AM-25 shows the customers label', () => {
    renderIt('contact');
    expect(document.body.textContent).toContain('all customers');
  });
});
