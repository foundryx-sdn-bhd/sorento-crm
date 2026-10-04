/**
 * Chatbot stock ask v2 S5: the customer's stock asks on the CRM (Asks tab).
 * The portal's Customer asks page reads the same rows through the portal client
 * (`app/(auth)/portal/lib/customer-asks-service.ts`).
 */
import { apiFetch } from '@/lib/api';
import { buildDataGridParams, extractApiError } from '@/lib/api-client';
import type { StockAsk, StockAskPage, StockAskPatch } from '@/lib/stock-asks';
import type { AskAgentSummary, AskConversation, AskTodoPayload } from '@/lib/stock-asks-todo';
import type {
  ConversationSearchMatch,
  ConversationThreadPage,
} from '@/components/common/conversation/useConversationThread';

const BASE = '/api/v1/order-management/customers';
const SALES_BASE = '/api/v1/sales/customer-asks';

/*
 * Sales-asks-todo S2 (plan 3.4) API CONTRACT, module `sales`, permission `sales.customer_asks.view`
 * (`.edit` gates the PATCH). The agents a caller may pick: everyone with `.view_all`, else the
 * current members of the active teams they lead (plus themselves), else only themselves:
 *   GET   /api/v1/sales/customer-asks/todo[?agent_id=]
 *     -> AskTodoPayload (see `lib/stock-asks-todo.ts`) plus `agent: {code, name} | null`.
 *        No `agent_id`: my list (user -> respond_contact_id -> agent). A user linked to no agent
 *        gets empty arrays and `agent: null` (200, not 403). `agent_id` outside the pickable set:
 *        403 NOT_YOUR_AGENT; unknown agent: 404.
 *        Addition to plan 3.4 (the manager's "All agents" choice, AC-ST210): `agent_id=all`
 *        returns every agent's rows, each StockAsk carrying `agent_code`; every pickable agent's rows (view_all: everyone, a
 *        team leader: their team; a plain user: 403).
 *   GET   /api/v1/sales/customer-asks/agents
 *     -> [{ agent_id, code, name, open }], the pickable agents (view_all: those
 *        with an open ask; a team leader: every current member, 0 allowed; otherwise `[]`, 200),
 *        counted with the same rules as the to-do payload.
 *   PATCH /api/v1/sales/customer-asks/{ask_id}  { state?, note? } -> StockAsk
 *        Scope = the view scope: the ask's customer's agent is in my pickable set (me, my led
 *        team's current members, or everyone with view_all); otherwise 404. A transition
 *        to done stamps `done_at` and the actor (the user id, server side); `done_by` on the wire is
 *        a label (my name), never an id; a transition to open clears both.
 *   GET   /api/v1/sales/customer-asks/{ask_id}/conversation  (S3, plan 3.6; ASKS-UX)
 *     -> { messages: [{ id, direction: 'in' | 'out', text, at }], ask_message_id: id | null,
 *          ask_message_ref: <Respond message id> | null, contact_id }
 *        Same scope as the PATCH (404 outside it). The opened card reads `ask_message_ref` (the
 *        anchor the shared thread highlights) and `contact_id` (Open in Conversations) from it.
 *   GET   /api/v1/sales/customer-asks/{ask_id}/conversation/page?before|after|around&limit
 *   GET   /api/v1/sales/customer-asks/{ask_id}/conversation/search?q&limit  (ASKS-UX item 3)
 *     -> the SAME shapes as `.../conversation-sla-tracking/{id}/conversation/{page,search}`
 *        (`ConversationThreadPage`, `{ items: ConversationSearchMatch[] }`), the two loaders
 *        `useConversationThread` takes. Same scope as the PATCH.
 */

export async function listCustomerAsks(
  customerId: string,
  params: { pageIndex: number; pageSize: number },
): Promise<StockAskPage> {
  const qs = buildDataGridParams({ pageIndex: params.pageIndex, pageSize: params.pageSize });
  const response = await apiFetch(`${BASE}/${customerId}/asks?${qs.toString()}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load stock asks'));
  return response.json();
}

export async function updateCustomerAsk(
  customerId: string,
  askId: string,
  patch: StockAskPatch,
): Promise<StockAsk> {
  const response = await apiFetch(`${BASE}/${customerId}/asks/${askId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to update the ask'));
  return response.json();
}

/** The salesperson's to-do (mine, one chosen agent, or 'all' with `sales.customer_asks.view_all`). */
export async function getCustomerAsksTodo(agentId?: string): Promise<AskTodoPayload> {
  const qs = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
  const response = await apiFetch(`${SALES_BASE}/todo${qs}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load customer asks'));
  return response.json();
}

/** The manager's Agent select: every agent with open counts (view_all only). */
export async function listAskAgents(): Promise<AskAgentSummary[]> {
  const response = await apiFetch(`${SALES_BASE}/agents`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load sales agents'));
  return response.json();
}

/** Done, Reopen and Note from the to-do (not scoped to one customer). */
export async function updateSalesAsk(askId: string, patch: StockAskPatch): Promise<StockAsk> {
  const response = await apiFetch(`${SALES_BASE}/${encodeURIComponent(askId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to update the ask'));
  return response.json();
}

/** The ask's anchor in its thread (`ask_message_ref`) and its contact, for the opened card. */
export async function getAskConversation(askId: string): Promise<AskConversation> {
  const response = await apiFetch(`${SALES_BASE}/${encodeURIComponent(askId)}/conversation`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load the conversation'));
  return response.json();
}

/** One window of the ask's contact thread; no cursor is the live tail. */
export async function getAskConversationPage(
  askId: string,
  params: { before?: string; after?: string; around?: string; limit?: number },
): Promise<ConversationThreadPage> {
  const sp = new URLSearchParams();
  if (params.before) sp.set('before', params.before);
  if (params.after) sp.set('after', params.after);
  if (params.around) sp.set('around', params.around);
  if (params.limit != null) sp.set('limit', String(params.limit));
  const qs = sp.toString();
  const response = await apiFetch(`${SALES_BASE}/${encodeURIComponent(askId)}/conversation/page${qs ? `?${qs}` : ''}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Failed to load earlier messages'));
  return response.json();
}

/** In-thread search over the ask's contact thread, newest first. */
export async function searchAskConversation(
  askId: string,
  query: string,
  limit = 100,
): Promise<ConversationSearchMatch[]> {
  const sp = new URLSearchParams({ q: query, limit: String(limit) });
  const response = await apiFetch(`${SALES_BASE}/${encodeURIComponent(askId)}/conversation/search?${sp.toString()}`);
  if (!response.ok) throw new Error(await extractApiError(response, 'Search failed'));
  const body = (await response.json()) as { items?: ConversationSearchMatch[] };
  return body.items ?? [];
}
