'use client';

import { useCallback } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import {
  createChatbotStatusWord,
  listChatbotStatusWords,
  updateChatbotStatusWord,
} from '../services/chatbotStatusWordService';
import type { ChatbotStatusWordInput } from '../types/chatbotStatusWord.types';

export const CHATBOT_STATUS_WORDS_QUERY_KEY = ['chatbot-status-words'];

export function useChatbotStatusWordsQuery() {
  return useQuery({ queryKey: CHATBOT_STATUS_WORDS_QUERY_KEY, queryFn: listChatbotStatusWords });
}

export function useCreateChatbotStatusWord() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ChatbotStatusWordInput) => createChatbotStatusWord(input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CHATBOT_STATUS_WORDS_QUERY_KEY });
      toast.success('Status word created');
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to create status word');
    },
  });
}

export function useUpdateChatbotStatusWord() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: ChatbotStatusWordInput }) =>
      updateChatbotStatusWord(id, input),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: CHATBOT_STATUS_WORDS_QUERY_KEY });
      toast.success('Status word saved');
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : 'Failed to save status word');
    },
  });
}

/** Deferred hard delete (D7): `chatbot_status_word.delete` is parked server side. */
export function useChatbotStatusWordDeletion() {
  const deletion = useDeferredRowAction({
    actionKey: 'chatbot_status_word.delete',
    entityType: 'chatbot_status_word',
    successMessage: 'Status word deleted',
    invalidateKeys: [CHATBOT_STATUS_WORDS_QUERY_KEY],
  });
  const isRowPending = useCallback(
    (id: string) => deletion.targetId === id,
    [deletion.targetId],
  );
  return { run: deletion.run, isRowPending };
}
