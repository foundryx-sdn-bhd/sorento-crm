import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import type { ChatbotStatusWord } from '../types/chatbotStatusWord.types';

const useChatbotStatusWordsQuery = vi.fn();
const useHasPermission = vi.fn();

vi.mock('../hooks/useChatbotStatusWords', () => ({
  useChatbotStatusWordsQuery: () => useChatbotStatusWordsQuery(),
}));
vi.mock('@/app/(protected)/system-management/chatbot-domains/hooks/useChatbotDomains', () => ({
  useChatbotDomainsQuery: () => ({ data: [{ name: 'sales', label: 'sales' }] }),
}));
vi.mock('@/hooks/usePermissions', () => ({
  useHasPermission: (slug: string) => useHasPermission(slug),
}));
vi.mock('./ChatbotStatusWordModal', () => ({ default: () => null }));
vi.mock('@/lib/listing-column-preferences/useListingColumnPreferences', () => ({
  useListingColumnPreferences: () => ({ resetToDefaults: vi.fn(), isLoading: false }),
}));

import ChatbotStatusWordsList from './ChatbotStatusWordsList';

const ROW: ChatbotStatusWord = {
  id: 's-1',
  domain: 'sales',
  value: 'sales_report',
  label: "a customer's sales figures by month",
  trigger_words: ['sales', 'sales report'],
  sort_order: 5,
  prompt_lists: ['status_field_values'],
  updated_at: '2026-09-30T09:00:00',
};

beforeEach(() => {
  useChatbotStatusWordsQuery.mockReset();
  useHasPermission.mockReset().mockReturnValue(true);
});
afterEach(() => cleanup());

describe('ChatbotStatusWordsList', () => {
  it('renders the status, its domain and its customer words', () => {
    useChatbotStatusWordsQuery.mockReturnValue({ data: [ROW], isLoading: false, isError: false });
    render(<ChatbotStatusWordsList />);
    expect(screen.getByText('sales_report')).toBeInTheDocument();
    expect(screen.getByText('sales, sales report')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add status word/i })).toBeInTheDocument();
  });

  it('hides Add without the manage grant', () => {
    useHasPermission.mockReturnValue(false);
    useChatbotStatusWordsQuery.mockReturnValue({ data: [ROW], isLoading: false, isError: false });
    render(<ChatbotStatusWordsList />);
    expect(screen.queryByRole('button', { name: /add status word/i })).not.toBeInTheDocument();
  });

  it('shows the error state', () => {
    useChatbotStatusWordsQuery.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<ChatbotStatusWordsList />);
    expect(screen.getByText(/could not be loaded/i)).toBeInTheDocument();
  });
});
