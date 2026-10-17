import { describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import * as client from './ProjectDetailClient';

describe('project detail tabs (AC-FP-32)', () => {
  it('has a Forms tab right after Sponsorships', () => {
    const tabs = (client as unknown as { TABS: ReadonlyArray<{ id: string; label: string }> }).TABS;
    const ids = tabs.map((t) => t.id);
    expect(ids[ids.indexOf('sponsorships') + 1]).toBe('forms');
    expect(tabs[ids.indexOf('forms')].label).toBe('Forms');
  });
});
