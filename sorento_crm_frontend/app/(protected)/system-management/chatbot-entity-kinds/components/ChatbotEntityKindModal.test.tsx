/**
 * AC-1711 (chatbot-answer-half-reattach, R3): the Chatbot entity kinds config
 * screen edits `roster_cap` as a number field (min 2), prefilled from the row, and
 * sends it as `roster_cap` on save. Mirrors `ChatbotDomainModal.test.tsx`'s
 * convention (data hooks mocked directly, no QueryClientProvider needed here since
 * this modal's own hooks are mutations only).
 *
 * `ChatbotEntityKind`/`ChatbotEntityKindInput` (`../types/chatbotEntityKind.types.ts`)
 * carry no `roster_cap` field yet - measured, not guessed - so every test below is
 * RED today: no "Roster cap" label exists to find, and `roster_cap` never reaches
 * `update.mutate`'s input.
 */
import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ChatbotEntityKind } from '../types/chatbotEntityKind.types';

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
Element.prototype.hasPointerCapture = Element.prototype.hasPointerCapture ?? (() => false);
Element.prototype.setPointerCapture = Element.prototype.setPointerCapture ?? (() => {});
Element.prototype.releasePointerCapture = Element.prototype.releasePointerCapture ?? (() => {});
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView ?? (() => {});
if (!window.matchMedia) {
  (window as unknown as { matchMedia: unknown }).matchMedia = () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
  });
}

const createMutate = vi.fn();
const updateMutate = vi.fn();
vi.mock('../hooks/useChatbotEntityKinds', () => ({
  useCreateChatbotEntityKind: () => ({ mutate: createMutate, isPending: false }),
  useUpdateChatbotEntityKind: () => ({ mutate: updateMutate, isPending: false }),
}));

import ChatbotEntityKindModal from './ChatbotEntityKindModal';

// `roster_cap` cast through `as ChatbotEntityKind` deliberately - the row shape does
// not declare the field yet (that is exactly what is red here); this is the shape
// the row WILL carry once the type gains it.
const PRODUCT_KIND = {
  code: 'product',
  label: 'Product',
  resolved_against: 'products (code, name, family)',
  did_you_mean: true,
  default_narrowing: 'narrow_to_code',
  family_grouping: 'by base code',
  base_property_words: {},
  roster_cap: 7,
} as unknown as ChatbotEntityKind;

function renderModal(props: Partial<React.ComponentProps<typeof ChatbotEntityKindModal>> = {}) {
  return render(
    <ChatbotEntityKindModal
      open
      onOpenChange={vi.fn()}
      entityKindCode={null}
      rows={[PRODUCT_KIND]}
      onNavigate={vi.fn()}
      canManage
      {...props}
    />,
  );
}

beforeEach(() => {
  createMutate.mockReset();
  updateMutate.mockReset();
});
afterEach(() => cleanup());

describe('ChatbotEntityKindModal - AC-1711 roster cap', () => {
  it('renders a Roster cap number field with min 2, prefilled from the row', () => {
    renderModal({ entityKindCode: PRODUCT_KIND.code });
    const field = screen.getByLabelText(/Roster cap/i) as HTMLInputElement;
    expect(field).toBeInTheDocument();
    expect(field).toHaveAttribute('type', 'number');
    expect(field).toHaveAttribute('min', '2');
    expect(field).toHaveValue(7);
  });

  it('sends the edited value as roster_cap on save', () => {
    renderModal({ entityKindCode: PRODUCT_KIND.code });
    const field = screen.getByLabelText(/Roster cap/i) as HTMLInputElement;
    fireEvent.change(field, { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(updateMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        code: PRODUCT_KIND.code,
        input: expect.objectContaining({ roster_cap: 5 }),
      }),
      expect.anything(),
    );
  });

  it('a new kind defaults roster_cap to the ceiling, 50 (PICKER-NO-CAP)', () => {
    renderModal({ entityKindCode: null });
    const field = screen.getByLabelText(/Roster cap/i) as HTMLInputElement;
    expect(field).toHaveValue(50);
  });
});
