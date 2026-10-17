/**
 * AC-SR-50: the project detail has a "Sample requests" tab immediately before "Samples".
 *
 * Requires `export const TABS` in ProjectDetailClient.tsx (it is a module-private const today).
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

import * as client from './ProjectDetailClient';

describe('project detail tabs (AC-SR-50)', () => {
  it('has a Sample requests tab immediately before Samples', () => {
    const tabs = (client as unknown as { TABS?: ReadonlyArray<{ id: string; label: string }> }).TABS;
    expect(tabs, 'ProjectDetailClient must export TABS').toBeDefined();
    const ids = tabs!.map((t) => t.id);
    const at = ids.indexOf('sample-requests');
    expect(at).toBeGreaterThan(-1);
    expect(ids[at + 1]).toBe('samples');
    expect(tabs![at].label).toBe('Sample requests');
  });
});
