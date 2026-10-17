/**
 * SPEC-REGISTRY-UI F6 (red tests): the gear's secondary items - Try in chatbot,
 * Attachment Types, Lookup Sets - before Delete, and the dialog in the `dialogs` slot.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, render, screen, cleanup } from '@testing-library/react';

const push = vi.fn();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: vi.fn() }),
  usePathname: () => '/master-data-management/product-specifications/finish',
}));

let granted: Record<string, boolean> = {};
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => !!granted[slug],
}));

vi.mock('@/hooks/useDeferredAction', () => ({
  useDeferredAction: () => ({ start: vi.fn(), isPending: false, countdown: null }),
}));

vi.mock('./components/TryInChatbotDialog', () => ({
  __esModule: true,
  TryInChatbotDialog: ({ open }: { open: boolean }) =>
    open ? <div data-testid="try-dialog">Try in chatbot dialog</div> : null,
  default: ({ open }: { open: boolean }) =>
    open ? <div data-testid="try-dialog">Try in chatbot dialog</div> : null,
}));

import { useSpecKeyActions } from './actions';
import type { SpecRegistryKey } from './types/productSpec.types';

function userKey(): SpecRegistryKey {
  return {
    spec_key: 'custom_thing',
    label: 'Custom thing',
    data_type: 'text',
    unit: null,
    allowed_values: [],
    synonyms: {},
    source: 'user',
    is_active: true,
  } as unknown as SpecRegistryKey;
}

function setup(key: SpecRegistryKey = userKey()) {
  return renderHook(() => useSpecKeyActions(key)).result.current;
}

beforeEach(() => {
  push.mockReset();
  cleanup();
  granted = {
    'master_data.spec_registry.edit': true,
    'resource.attachment_types.view': true,
    'master_data.lookup_sets.view': true,
    'master_data.spec_registry.delete': true,
  };
});

describe('useSpecKeyActions - gear items (F6)', () => {
  it('offers Try in chatbot, Attachment Types and Lookup Sets before Delete', () => {
    const { actions } = setup();

    expect(actions.map((a) => a.key)).toEqual([
      'spec_key.try_chatbot',
      'spec_key.open_attachment_types',
      'spec_key.open_lookup_sets',
      'spec_key.delete',
    ]);
    expect(actions.slice(0, 3).map((a) => a.label)).toEqual([
      'Try in chatbot',
      'Attachment Types',
      'Lookup Sets',
    ]);
    expect(actions.slice(0, 3).every((a) => a.kind !== 'destructive')).toBe(true);
  });

  it('hides Try in chatbot without master_data.spec_registry.edit, keeps the links', () => {
    granted = {
      'master_data.spec_registry.view': true,
      'resource.attachment_types.view': true,
      'master_data.lookup_sets.view': true,
      'master_data.spec_registry.delete': true,
    };
    const { actions } = setup();

    expect(actions.map((a) => a.key)).toEqual([
      'spec_key.open_attachment_types',
      'spec_key.open_lookup_sets',
      'spec_key.delete',
    ]);
  });

  it('each link needs its own page view permission', () => {
    granted = { 'master_data.spec_registry.edit': true, 'resource.attachment_types.view': true };
    expect(setup({ ...userKey(), source: 'seed' } as SpecRegistryKey).actions.map((a) => a.key)).toEqual([
      'spec_key.try_chatbot',
      'spec_key.open_attachment_types',
    ]);

    granted = { 'master_data.lookup_sets.view': true };
    expect(setup({ ...userKey(), source: 'seed' } as SpecRegistryKey).actions.map((a) => a.key)).toEqual([
      'spec_key.open_lookup_sets',
    ]);
  });

  it('a built-in key still gets the three secondary items, never Delete', () => {
    const { actions } = setup({ ...userKey(), source: 'seed' } as SpecRegistryKey);

    expect(actions.map((a) => a.key)).toEqual([
      'spec_key.try_chatbot',
      'spec_key.open_attachment_types',
      'spec_key.open_lookup_sets',
    ]);
  });

  it('Attachment Types and Lookup Sets push their exact paths', () => {
    const { actions } = setup();

    act(() => {
      void actions.find((a) => a.key === 'spec_key.open_attachment_types')!.run();
    });
    expect(push).toHaveBeenLastCalledWith('/resource-management/attachment-types');

    act(() => {
      void actions.find((a) => a.key === 'spec_key.open_lookup_sets')!.run();
    });
    expect(push).toHaveBeenLastCalledWith('/master-data-management/lookup-sets');
  });

  it('Try in chatbot opens the dialog returned through the dialogs slot', () => {
    function Harness() {
      const { actions, dialogs } = useSpecKeyActions(userKey());
      return (
        <div>
          <button type="button" onClick={() => void actions[0].run()}>
            open
          </button>
          {dialogs}
        </div>
      );
    }
    render(<Harness />);
    expect(screen.queryByTestId('try-dialog')).toBeNull();

    act(() => {
      screen.getByText('open').click();
    });

    expect(screen.getByTestId('try-dialog')).toBeInTheDocument();
  });
});
