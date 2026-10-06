/**
 * Chatbot Status Words service (PLAN-prompt-dynamic-30sep D6).
 * Layering: UI -> hooks (useChatbotStatusWords) -> THIS service -> lib/api -> backend.
 *
 *   GET  /api/v1/system/chatbot/status-words       list, `system.chat_history.view`
 *   POST /api/v1/system/chatbot/status-words       create, `system.chatbot_config.manage`
 *   PUT  /api/v1/system/chatbot/status-words/{id}  update, same grant
 *
 * One page at `limit=200` is the whole table (a small reference table, the Chatbot
 * Domains idiom). No delete here: `chatbot_status_word.delete` is a server-deferred
 * pending action run through `useDeferredRowAction`.
 */

import { apiFetch } from '@/lib/api';
import { extractApiError } from '@/lib/api-client';
import type { ChatbotStatusWord, ChatbotStatusWordInput } from '../types/chatbotStatusWord.types';

const BASE = '/api/v1/system/chatbot/status-words';

export async function listChatbotStatusWords(): Promise<ChatbotStatusWord[]> {
  const response = await apiFetch(`${BASE}?limit=200&sort=sort_order&dir=asc`);
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to load status words'));
  }
  const body: { data: ChatbotStatusWord[] } = await response.json();
  return body.data;
}

export async function createChatbotStatusWord(
  input: ChatbotStatusWordInput,
): Promise<ChatbotStatusWord> {
  const response = await apiFetch(BASE, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to create the status word'));
  }
  return response.json();
}

export async function updateChatbotStatusWord(
  id: string,
  input: ChatbotStatusWordInput,
): Promise<ChatbotStatusWord> {
  const response = await apiFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await extractApiError(response, 'Failed to save the status word'));
  }
  return response.json();
}
