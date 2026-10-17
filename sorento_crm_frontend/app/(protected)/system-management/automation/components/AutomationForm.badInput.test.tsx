/** NUMBER-FIELDS round 5 (B1): an automation rule with bad number input on "=" / "not =" is refused on Save, with the sentence on the box, and nothing is sent. */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { typeBadInput } from '@/test-utils/badInput';

import AutomationForm from './AutomationForm';

const createMutateAsync = vi.fn().mockResolvedValue({});
const updateMutateAsync = vi.fn().mockResolvedValue({});

vi.mock('../hooks/useAutomations', () => ({
  useCreateAutomation: () => ({ mutateAsync: createMutateAsync, isPending: false }),
  useUpdateAutomation: () => ({ mutateAsync: updateMutateAsync, isPending: false }),
  useTriggerCatalog: () => ({
    data: {
      triggers: [
        {
          type: 'days_before_promotion_end',
          label: 'Days before promotion end',
          description: 'Fires for every active promotion',
          config_schema: {},
          fact_sources: ['promotion'],
        },
      ],
    },
  }),
}));
vi.mock('../../email-templates/hooks/useEmailTemplates', () => ({
  useEmailTemplates: () => ({
    data: { data: [{ id: 'tpl-1', name: 'Promo expire', code: 'promo expire' }] },
  }),
}));
vi.mock('@/lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));
vi.mock('./RecipientPicker', () => ({
  default: ({
    value,
    onChange,
  }: {
    value: Record<string, unknown>;
    onChange: (c: Record<string, unknown>) => void;
  }) => (
    <button
      type="button"
      data-testid="seed-recipient"
      onClick={() => onChange({ ...value, extra_emails: ['x@example.com'] })}
    >
      seed
    </button>
  ),
}));
vi.mock('@/components/rule-builder/useRuleFacts', () => ({
  useRuleFacts: () => ({
    data: [
      {
        key: 'promotion.endDate.daysUntil',
        label: 'Days until end',
        type: 'number',
        operators: ['eq', 'neq', 'gt'],
        source: 'promotion',
        sourceLabel: 'Promotion',
      },
    ],
    isLoading: false,
    isError: false,
    error: null,
  }),
}));
vi.mock('@/components/common/SearchableSelect', () => ({
  SearchableSelect: ({
    value,
    onChange,
    options,
    placeholder,
  }: {
    value: string;
    onChange: (v: string) => void;
    options: { value: string; label: string }[];
    placeholder?: string;
  }) => (
    <select
      aria-label={placeholder}
      data-testid={`select-${placeholder}`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {(options ?? []).map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));
vi.mock('@/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

beforeEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = vi.fn();
  Element.prototype.hasPointerCapture = vi.fn();
});

describe.each(['eq', 'neq'] as const)('AutomationForm rule with bad number input on %s', (operator) => {
  it('Save is refused with the sentence on the box and nothing is sent', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <AutomationForm open onOpenChange={vi.fn()} />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByPlaceholderText('Promotion expiry reminder'), {
      target: { value: 'My automation' },
    });
    fireEvent.click(screen.getByTestId('seed-recipient'));
    fireEvent.change(screen.getByTestId('select-Pick template'), { target: { value: 'tpl-1' } });

    fireEvent.click(screen.getByRole('button', { name: /add condition/i }));
    fireEvent.change(screen.getByTestId('select-Field'), {
      target: { value: 'promotion.endDate.daysUntil' },
    });
    fireEvent.change(screen.getByTestId('select-Operator'), { target: { value: operator } });
    typeBadInput(document.querySelector('input[type="number"]') as HTMLElement);

    fireEvent.click(screen.getByRole('button', { name: /create automation/i }));
    expect(createMutateAsync).not.toHaveBeenCalled();
    expect(screen.getByText(/Enter a number/)).toBeInTheDocument();
  });
});
