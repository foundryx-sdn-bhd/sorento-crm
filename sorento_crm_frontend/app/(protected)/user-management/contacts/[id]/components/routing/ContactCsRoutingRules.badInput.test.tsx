/** NUMBER-FIELDS round 4 (S1): Save with bad input in a numeric predicate value sends nothing and keeps the sentence on the box. */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { typeBadInput } from '@/test-utils/badInput';

vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));

const saveRoutingRules = vi.fn(async (_id: string, rules: unknown[]) => rules);
vi.mock('./csRoutingMock', () => ({
  CS_USE_CASES: [{ key: 'purchase_request', label: 'Purchase Request' }],
  getRoutingRules: vi.fn(async () => [
    {
      id: 'r1',
      use_case: 'purchase_request',
      cs_pic_user_id: 'u-1',
      cs_pic_name: 'ZZT Dina',
      match_conditions: [
        { field: 'total_project_value', operator: 'equals', value: '5' },
      ],
      priority: 0,
    },
  ]),
  getCsCandidates: vi.fn(async () => [{ id: 'u-1', name: 'ZZT Dina', email: 'dina@t.zzt' }]),
  getRoutableFields: vi.fn(async () => [
    { field: 'total_project_value', label: 'Total Project Value', type: 'numeric' },
  ]),
  saveRoutingRules: (id: string, rules: unknown[]) => saveRoutingRules(id, rules),
}));

import ContactCsRoutingRules from './ContactCsRoutingRules';

describe('ContactCsRoutingRules bad number input', () => {
  it('Save refuses a numeric predicate holding bad input: nothing sent, sentence on the box', async () => {
    Element.prototype.scrollIntoView = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <ContactCsRoutingRules contactId="c-1" />
      </QueryClientProvider>,
    );
    typeBadInput(await screen.findByLabelText('Predicate value'));
    fireEvent.click(screen.getByRole('button', { name: /save routing rules/i }));
    expect(saveRoutingRules).not.toHaveBeenCalled();
    expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
  });
});
