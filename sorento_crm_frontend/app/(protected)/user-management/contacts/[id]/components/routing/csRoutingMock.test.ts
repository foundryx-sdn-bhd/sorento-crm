/**
 * CS-ROUTING-TARGETS: saving the routing rules page never loses rules.
 *
 * The page used to DELETE every use_case's rules and then PUT them back one by one,
 * so one rule the server refused (a PIC who can no longer be pinned) left the contact
 * with only the rules before it. Save is now ONE request carrying every rule; the
 * server validates them all before it replaces anything.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiFetch }));

import { saveRoutingRules, type RoutingRule } from './csRoutingMock';

const CASH = [{ field: 'sales_type', operator: 'equals', value: 'cash_sales' }];

function rule(partial: Partial<RoutingRule>): RoutingRule {
  return {
    id: 'tmp',
    use_case: 'purchase_request',
    cs_pic_user_id: 'u-1',
    cs_pic_name: 'Aisyah',
    match_conditions: [],
    priority: 0,
    ...partial,
  } as RoutingRule;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

beforeEach(() => {
  apiFetch.mockReset();
});

describe('saveRoutingRules', () => {
  it('sends every complete rule in one PUT and never deletes first', async () => {
    const saved = [
      {
        id: 'r1',
        use_case: 'purchase_request',
        cs_pic_user_id: 'u-2',
        cs_pic_name: 'Lin',
        match_conditions: CASH,
        priority: 0,
      },
      {
        id: 'r2',
        use_case: 'sponsorship_form',
        cs_pic_user_id: 'u-1',
        cs_pic_name: 'Aisyah',
        match_conditions: [],
        priority: 2,
      },
    ];
    apiFetch.mockImplementation(async () => json({ pins: saved }));

    const out = await saveRoutingRules('c-1', [
      rule({
        id: 'a',
        cs_pic_user_id: 'u-2',
        match_conditions: CASH as RoutingRule['match_conditions'],
      }),
      rule({ id: 'b', cs_pic_user_id: null }), // incomplete: no PIC picked yet
      rule({ id: 'c', use_case: 'sponsorship_form', cs_pic_user_id: 'u-1' }),
    ]);

    expect(apiFetch).toHaveBeenCalledTimes(1);
    const [url, init] = apiFetch.mock.calls[0];
    expect(url).toBe('/api/v1/user-management/contacts/c-1/cs-routing');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(init.body)).toEqual({
      rules: [
        {
          use_case: 'purchase_request',
          cs_pic_user_id: 'u-2',
          match_conditions: CASH,
          priority: 0,
        },
        {
          use_case: 'sponsorship_form',
          cs_pic_user_id: 'u-1',
          match_conditions: [],
          priority: 2,
        },
      ],
    });
    expect(out.map((r) => r.id)).toEqual(['r1', 'r2']);
  });

  it('surfaces the server message and makes no other call when a rule is refused', async () => {
    apiFetch.mockImplementation(async () =>
      json(
        {
          message: 'Rule 2: ZZT Abel can no longer be assigned.',
          code: 'VALIDATION_ERROR',
        },
        400,
      ),
    );

    await expect(
      saveRoutingRules('c-1', [
        rule({ id: 'a' }),
        rule({
          id: 'b',
          cs_pic_user_id: 'u-9',
          match_conditions: CASH as RoutingRule['match_conditions'],
        }),
      ]),
    ).rejects.toThrow('Rule 2: ZZT Abel can no longer be assigned.');
    expect(apiFetch).toHaveBeenCalledTimes(1);
  });
});
