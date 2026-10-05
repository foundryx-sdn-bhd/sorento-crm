/**
 * Lane CONTACT-REGION, AC-CR-14..16 (Contact > Access > Chatbot card Regions control).
 * UAC `documentation/plans/chatbot/contact-region-acceptance-criteria.md`.
 *
 * RED until the card renders a Regions multi-select bound to `profile.regions`, and the
 * save body carries `regions`. The multi-select is stubbed as one checkbox per option so
 * the labels, the stored value and the emitted value are asserted directly.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import ContactChatbotSection from './ContactChatbotSection';
import { PACKING_LIST_REGION_OPTIONS } from '@/lib/packing-list-regions';

const seenOptions: unknown[] = [];

const useContactChatbotProfile = vi.fn();
const mutate = vi.fn();
const apiFetchMock = vi.fn();

vi.mock('@/lib/api', () => ({ apiFetch: (...a: unknown[]) => apiFetchMock(...a) }));

vi.mock('../hooks/useContactChatbot', () => ({
  useContactChatbotProfile: (...a: unknown[]) => useContactChatbotProfile(...a),
  useSaveContactChatbotProfile: () => ({ mutate, isPending: false }),
  useSaveContactEscalation: () => ({ mutate: vi.fn(), isPending: false }),
  useContactChatbotMemory: () => ({
    data: {
      level: { own: null, effective: 'off', system_default: 'off' },
      facts: [],
      vocabulary: [],
      episodes: { kept: 0, limit: 20, current: null, rows: [] },
      open_orders: { customer_name: null, rows: [] },
    },
    isLoading: false,
    isError: false,
  }),
  useSaveContactFact: () => ({ mutate: vi.fn(), isPending: false }),
  contactChatbotMemoryQueryKey: (contactId: string) => ['contact-chatbot-memory', contactId],
}));

type Opt = { value: string; label: string };
vi.mock('@/components/common/SearchableMultiSelect', () => ({
  SearchableMultiSelect: (props: {
    value: string[];
    onChange: (v: string[]) => void;
    options?: Opt[];
    selectedOptions?: Opt[];
    placeholder?: string;
  }) => {
    const opts = props.options ?? props.selectedOptions ?? [];
    seenOptions.push(props.options);
    // Only the region control passes static options; the facts grid's multi has none here.
    if (!opts.some((o) => /malaysia/i.test(o.label))) return null;
    return (
      <div data-testid="regions-multi">
        {opts.map((o) => (
          <label key={o.value}>
            <input
              type="checkbox"
              aria-label={o.label}
              checked={props.value.includes(o.value)}
              onChange={(e) =>
                props.onChange(
                  e.target.checked
                    ? [...props.value, o.value]
                    : props.value.filter((v) => v !== o.value),
                )
              }
            />
            {o.label}
          </label>
        ))}
      </div>
    );
  },
}));

const BASE_PROFILE = {
  chatbot_memory_level: null,
  tier: null,
  default_ledgers: [],
  stock_allowed: true,
  notify_salesman: false,
  packing_list_allowed: false,
  eta_offset_applied: true,
  escalation_allowed: true,
  regions: ['west'],
};

function renderCard(profile = BASE_PROFILE) {
  useContactChatbotProfile.mockReturnValue({ data: profile, isLoading: false, isError: false });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ContactChatbotSection contactId="c1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useContactChatbotProfile.mockReset();
  mutate.mockReset();
  apiFetchMock.mockReset();
  seenOptions.length = 0;
});
afterEach(() => cleanup());

describe('ContactChatbotSection - Regions (AC-CR-14..16)', () => {
  it('AC-CR-14: shows West Malaysia / East Malaysia options with the stored value checked', () => {
    renderCard({ ...BASE_PROFILE, regions: ['west', 'east'] });
    expect(screen.getByLabelText('West Malaysia')).toBeChecked();
    expect(screen.getByLabelText('East Malaysia')).toBeChecked();
  });

  it('AC-CR-14: a west-only contact shows only West checked', () => {
    renderCard();
    expect(screen.getByLabelText('West Malaysia')).toBeChecked();
    expect(screen.getByLabelText('East Malaysia')).not.toBeChecked();
  });

  it('AC-CR-22: the control gets PACKING_LIST_REGION_OPTIONS unchanged', () => {
    renderCard();
    expect(seenOptions.length).toBeGreaterThan(0);
    expect(seenOptions).toContainEqual(PACKING_LIST_REGION_OPTIONS);
  });

  it('AC-CR-14: no UUID or raw code is shown as text', () => {
    renderCard({ ...BASE_PROFILE, regions: ['east'] });
    expect(screen.queryByText(/^(west|east)$/)).not.toBeInTheDocument();
  });

  it('AC-CR-15: adding East saves at once with regions in the save input', () => {
    renderCard();
    fireEvent.click(screen.getByLabelText('East Malaysia'));
    expect(mutate).toHaveBeenCalledTimes(1);
    const input = mutate.mock.calls[0][0];
    expect(input.regions).toEqual(['west', 'east']);
    expect(input.stock_allowed).toBe(true);
  });

  it('AC-CR-16: removing the last region does not save', () => {
    renderCard();
    fireEvent.click(screen.getByLabelText('West Malaysia'));
    expect(mutate).not.toHaveBeenCalled();
  });

  it('AC-CR-15: the PUT body carries regions (service mapping)', async () => {
    apiFetchMock.mockResolvedValue({ ok: true, json: async () => ({}) } as unknown as Response);
    const { saveContactChatbotProfile } = await import('../services/contactChatbotService');
    await saveContactChatbotProfile('c1', { ...BASE_PROFILE, regions: ['east'] } as never).catch(() => {});
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled());
    const [url, init] = apiFetchMock.mock.calls[0];
    expect(String(url)).toContain('/contacts/c1/chatbot');
    expect(JSON.parse((init as { body: string }).body).regions).toEqual(['east']);
  });
});
