/**
 * SPEC-REGISTRY-UI F5 (red tests): a History tab after Products, rendering the
 * audit trail of this specification (entityType product_spec_registry).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('next/navigation', () => ({
  usePathname: () => '/master-data-management/product-specifications/finish',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const ROW = {
  id: 'row-1',
  spec_key: 'finish',
  label: 'Finish or colour',
  data_type: 'enum',
  unit: null,
  allowed_values: ['chrome'],
  synonyms: {},
  excluded_values: [],
  user_values: [],
  suppressed_values: [],
  value_weights: {},
  derivation_rules: [],
  effective_rules: [],
  applies_when: {},
  read_from: 'rules',
  rank_weight: 1,
  measured_coverage: null,
  source: 'seed',
  user_synonyms: {},
  suppressed_synonyms: {},
  match_tolerance: 0,
  match_decay: 0,
  is_active: true,
  parser_hint: null,
  label_owned: true,
};

vi.mock('../hooks/useSpecRegistryQuery', () => ({
  useSpecRegistryQuery: () => ({ data: [ROW], isLoading: false, isError: false }),
  selectSpecKey: (keys: unknown[] | undefined, key: string) =>
    (keys as { spec_key: string }[] | undefined)?.find((k) => k.spec_key === key),
  SPEC_REGISTRY_QUERY_KEY: ['spec-registry'],
}));
vi.mock('../hooks/useSpecCoverageQuery', () => ({
  useSpecCoverageQuery: () => ({ data: undefined }),
  SPEC_COVERAGE_QUERY_KEY: ['spec-coverage'],
}));
vi.mock('../hooks/useSpecKeyRecord', () => ({
  useSpecKeyRecord: () => ({
    mode: 'view',
    draft: null,
    saving: false,
    edit: vi.fn(),
    cancel: vi.fn(),
    save: vi.fn(),
    setDraft: vi.fn(),
  }),
}));
vi.mock('../actions', () => ({
  useSpecKeyActions: () => ({ actions: [], dialogs: null, pending: null }),
}));
vi.mock('../../product-categories/services/categoryService', () => ({
  getProductClassLabels: vi.fn().mockResolvedValue([]),
}));
vi.mock('@/components/common/container', () => ({
  Container: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock('@/components/common/PageHeader', () => ({ PageHeader: () => <div /> }));
vi.mock('@/components/common/BackToList', () => ({ __esModule: true, default: () => <div /> }));
vi.mock('@/components/common/RecordNavigation', () => ({ __esModule: true, default: () => <div /> }));
vi.mock('./record/SpecKeyRecordCard', () => ({ SpecKeyRecordCard: () => <div /> }));
vi.mock('./record/HeaderTab', () => ({ HeaderTab: () => <div>details body</div> }));
vi.mock('./record/ValuesAndWordsTab', () => ({ ValuesAndWordsTab: () => <div /> }));
vi.mock('./record/RulesTab', () => ({ RulesTab: () => <div /> }));
vi.mock('./record/SeenInProductsTab', () => ({ SeenInProductsTab: () => <div>products body</div> }));

const auditTrail = vi.fn((props: Record<string, unknown>) => (
  <div data-testid="audit-trail">{String(props.title)}</div>
));
vi.mock('@/components/audit/AuditTrail', () => ({
  __esModule: true,
  default: (props: Record<string, unknown>) => auditTrail(props),
}));

import { SpecKeyRecordDetail } from './SpecKeyRecordDetail';

function renderDetail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SpecKeyRecordDetail specKey="finish" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  cleanup();
  auditTrail.mockClear();
});

describe('SpecKeyRecordDetail - History tab (F5)', () => {
  it('lists History as the fifth tab, after Products', () => {
    renderDetail();

    const names = screen.getAllByRole('tab').map((t) => t.textContent);
    expect(names).toEqual([
      'Details',
      'Choices and words',
      'How it is read',
      'Products',
      'History',
    ]);
  });

  it('opening History renders the audit trail of this specification', async () => {
    renderDetail();

    const tab = screen.getByRole('tab', { name: 'History' });
    fireEvent.mouseDown(tab);
    fireEvent.click(tab);

    expect(await screen.findByTestId('audit-trail')).toBeInTheDocument();
    expect(auditTrail).toHaveBeenCalledWith(
      expect.objectContaining({
        entityType: 'product_spec_registry',
        entityId: 'row-1',
        title: 'History',
      }),
    );
  });
});
