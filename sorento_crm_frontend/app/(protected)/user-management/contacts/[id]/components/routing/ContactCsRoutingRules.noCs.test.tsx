/**
 * RED tests - FORM-ROUTING-CONDITIONS NC-6.
 *
 * The CS PIC dropdown offers "No CS (finish at approval)"; choosing it saves the rule with
 * `cs_pic_user_id: null`; a rule loaded with a null PIC shows it selected; a new rule with
 * nothing chosen at all is still skipped on save.
 *
 * Mocks the HTTP seam (`@/lib/api` apiFetch), not csRoutingMock, because the skip-on-save
 * decision lives in the feature service and the assertion is on the PUT body.
 */
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

const NO_CS = 'No CS (finish at approval)';

vi.mock('@/hooks/usePermissions', () => ({ useHasPermission: () => true }));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

let storedPins: unknown[] = [];
const putBodies: Array<{ rules: Array<Record<string, unknown>> }> = [];

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  const json = (body: unknown) =>
    ({ ok: true, status: 200, json: async () => body }) as unknown as Response;
  return {
    ...actual,
    apiFetch: vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        const body = JSON.parse(String(init.body));
        putBodies.push(body);
        return json({
          pins: body.rules.map((r: Record<string, unknown>, i: number) => ({
            id: `saved-${i}`,
            cs_pic_name: null,
            ...r,
          })),
        });
      }
      if (url.endsWith('/cs-routing/candidates'))
        return json({ candidates: [{ id: 'u-1', name: 'CS PIC X', email: 'x@t.zzt' }] });
      if (url.includes('/cs-routing/fields')) return json({ fields: [] });
      return json({ pins: storedPins });
    }),
  };
});

import ContactCsRoutingRules from './ContactCsRoutingRules';

function renderRules() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactCsRoutingRules contactId="c-1" />
    </QueryClientProvider>,
  );
}

const assignTo = () => screen.getAllByRole('combobox')[1]; // [0] = Form type, [1] = Assign to

beforeEach(() => {
  storedPins = [];
  putBodies.length = 0;
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe('ContactCsRoutingRules No CS', () => {
  it('offers "No CS (finish at approval)" in the CS PIC dropdown', async () => {
    storedPins = [
      { id: 'r1', use_case: 'purchase_request', cs_pic_user_id: 'u-1', cs_pic_name: 'CS PIC X', match_conditions: [], priority: 0 },
    ];
    renderRules();
    await screen.findByText('Priority 1');

    fireEvent.click(assignTo());

    const labels = [...document.querySelectorAll('[role="option"]')].map((o) => o.textContent?.trim());
    expect(labels.some((l) => l?.startsWith(NO_CS))).toBe(true);
    expect(labels.some((l) => l?.startsWith('CS PIC X'))).toBe(true);
  });

  it('shows a rule loaded with a null PIC as "No CS (finish at approval)"', async () => {
    storedPins = [
      { id: 'r1', use_case: 'sponsorship_form', cs_pic_user_id: null, cs_pic_name: null, match_conditions: [], priority: 0 },
    ];
    renderRules();
    await screen.findByText('Priority 1');

    expect(within(assignTo()).getByText(NO_CS)).toBeInTheDocument();
  });

  it('saves a rule that picked No CS with cs_pic_user_id null', async () => {
    storedPins = [
      { id: 'r1', use_case: 'purchase_request', cs_pic_user_id: 'u-1', cs_pic_name: 'CS PIC X', match_conditions: [], priority: 0 },
    ];
    renderRules();
    await screen.findByText('Priority 1');

    fireEvent.click(assignTo());
    const option = [...document.querySelectorAll('[role="option"]')].find((o) =>
      o.textContent?.trim().startsWith(NO_CS),
    ) as HTMLElement;
    expect(option).toBeTruthy();
    fireEvent.click(option);
    fireEvent.click(screen.getByRole('button', { name: /save routing rules/i }));

    await waitFor(() => expect(putBodies).toHaveLength(1));
    expect(putBodies[0].rules).toEqual([
      { use_case: 'purchase_request', cs_pic_user_id: null, match_conditions: [], priority: 0 },
    ]);
  });

  it('still skips a new rule where nothing was chosen', async () => {
    storedPins = [
      { id: 'r1', use_case: 'purchase_request', cs_pic_user_id: 'u-1', cs_pic_name: 'CS PIC X', match_conditions: [], priority: 0 },
    ];
    renderRules();
    await screen.findByText('Priority 1');

    fireEvent.click(screen.getByRole('button', { name: /add rule/i }));
    await screen.findByText('Priority 2');
    fireEvent.click(screen.getByRole('button', { name: /save routing rules/i }));

    await waitFor(() => expect(putBodies).toHaveLength(1));
    expect(putBodies[0].rules).toHaveLength(1);
    expect(putBodies[0].rules[0].cs_pic_user_id).toBe('u-1');
  });
});
