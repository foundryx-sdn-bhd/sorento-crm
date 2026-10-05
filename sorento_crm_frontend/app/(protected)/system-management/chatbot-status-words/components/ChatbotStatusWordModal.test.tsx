import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ChatbotStatusWord } from '../types/chatbotStatusWord.types';

const createMutate = vi.fn();
const updateMutate = vi.fn();

vi.mock('../hooks/useChatbotStatusWords', () => ({
  useCreateChatbotStatusWord: () => ({ mutate: createMutate, isPending: false }),
  useUpdateChatbotStatusWord: () => ({ mutate: updateMutate, isPending: false }),
  useChatbotStatusWordDeletion: () => ({ run: vi.fn() }),
}));

import ChatbotStatusWordModal from './ChatbotStatusWordModal';

// Owner answer 4 (2 Oct 2026, PLAN-prompt-dynamic-30sep D-B4): a status row says which
// parser prompt lists it is in.
const ROW: ChatbotStatusWord = {
  id: 's-1',
  domain: 'order',
  value: 'outstanding',
  label: 'orders NOT yet delivered',
  trigger_words: ['outstanding'],
  sort_order: 0,
  prompt_lists: ['statuses', 'status_values'],
  updated_at: '2026-10-02T09:00:00',
};

function renderModal(row: ChatbotStatusWord | null) {
  return render(
    <ChatbotStatusWordModal
      open
      onOpenChange={() => {}}
      statusId={row ? row.id : null}
      rows={row ? [row] : []}
      domainOptions={[{ value: 'order', label: 'orders' }]}
      onNavigate={() => {}}
      canManage
    />,
  );
}

beforeEach(() => {
  createMutate.mockReset();
  updateMutate.mockReset();
});
afterEach(() => cleanup());

describe('ChatbotStatusWordModal prompt lists', () => {
  it('shows the lists the row is in', () => {
    renderModal(ROW);
    expect(screen.getByText('Parser prompt lists')).toBeInTheDocument();
    expect(screen.getByText(/Status bullets/)).toBeInTheDocument();
    expect(screen.getByText(/order_status field values/)).toBeInTheDocument();
  });

  it('sends the row lists on save', () => {
    renderModal(ROW);
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(updateMutate).toHaveBeenCalledTimes(1);
    expect(updateMutate.mock.calls[0][0].input.prompt_lists).toEqual(['statuses', 'status_values']);
  });

  it('a new row starts in no list', () => {
    renderModal(null);
    fireEvent.change(screen.getByLabelText('Status value'), { target: { value: 'zzt_new' } });
    fireEvent.change(screen.getByLabelText('Meaning'), { target: { value: 'a test' } });
    // No domain picked yet: Save stays disabled; the draft's lists are still empty.
    expect(screen.queryByText(/Status bullets/)).not.toBeInTheDocument();
  });
});
