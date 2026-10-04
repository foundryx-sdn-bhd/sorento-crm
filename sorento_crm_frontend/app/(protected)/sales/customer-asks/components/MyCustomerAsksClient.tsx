'use client';

import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { PageHeader } from '@/components/common/PageHeader';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { AskConversationPanel } from '@/components/stock-asks/AskConversationPanel';
import { AskTodoList } from '@/components/stock-asks/AskTodoList';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useListBoardViewPreference } from '@/hooks/useListBoardViewPreference';
import { useListingViewPreferences } from '@/lib/listing-column-preferences/useListingViewPreferences';
import type { StockAsk } from '@/lib/stock-asks';
import {
  ASK_LANDING_FIELDS,
  DEFAULT_ASK_SORT,
  askSortFromSorting,
  askSortToSorting,
  askToSummary,
  filterTodoPayload,
} from '@/lib/stock-asks-todo';
import { LandingToolbar } from '@/app/(auth)/portal/components/LandingToolbar';
import type { LandingFilters } from '@/app/(auth)/portal/lib/landing-fields';
import { getAskConversation } from '@/services/stockAskService';
import { useAskAgentsQuery, useAskDoneMutation, useCustomerAsksTodoQuery, useSalesAskThread } from '../hooks/useCustomerAsksTodo';

const ALL_AGENTS = 'all';
/** The remembered sort lives in the existing per-user view preference row (plan 3.2). */
const SORT_LISTING_KEY = 'sales.customer_asks.view::todo';
const DEFAULT_SORTING = askSortToSorting(DEFAULT_ASK_SORT);

/**
 * Sales > Customer asks: the signed-in salesperson's to-do (the same `AskTodoList` and
 * `LandingToolbar` the portal mounts). When the API lists agents the caller may pick (view_all,
 * or a team leader's team) an Agent select shows them with open counts; clearing it returns to
 * mine. A card or row opens the conversation in a right-hand Sheet.
 */
export function MyCustomerAsksClient() {
  const [agentId, setAgentId] = useState('');
  const [filters, setFilters] = useState<LandingFilters>({});
  const [opened, setOpened] = useState<StockAsk | null>(null);
  const todo = useCustomerAsksTodoQuery(agentId);
  const agents = useAskAgentsQuery();
  const prefs = useListingViewPreferences({
    listingKey: SORT_LISTING_KEY,
    defaultSorting: DEFAULT_SORTING,
    filtersVersion: 1,
  });
  const view = useListBoardViewPreference('sales-customer-asks', 'board');
  const save = useAskDoneMutation();
  const sort = askSortFromSorting(prefs.sorting);
  const canPick = (agents.data?.length ?? 0) > 0;

  const payload = useMemo(
    () => (todo.data ? filterTodoPayload(todo.data, filters) : null),
    [todo.data, filters],
  );
  const items = useMemo(
    () => (todo.data ? [...todo.data.open, ...todo.data.done_today].map(askToSummary) : []),
    [todo.data],
  );
  const current = useMemo(() => {
    if (!opened) return null;
    const all = todo.data ? [...todo.data.open, ...todo.data.done_today] : [];
    return all.find((a) => a.id === opened.id) ?? opened;
  }, [opened, todo.data]);

  // The anchor (`ask_message_ref`, `contact_id`) and the contact's thread, both keyed on the
  // opened ask; the thread is the ticket drawer's shared component fed by the CRM loaders.
  const conversation = useQuery({
    queryKey: ['customer-ask-conversation', opened?.id],
    queryFn: () => getAskConversation(opened!.id),
    enabled: Boolean(opened),
  });
  const thread = useSalesAskThread(opened?.id ?? null);

  const agentOptions = useMemo(
    () => [
      { value: ALL_AGENTS, label: 'All agents' },
      ...(agents.data ?? []).map((a) => ({
        value: a.agent_id,
        label: `${a.code} · ${a.open} open`,
        searchText: `${a.code} ${a.name}`,
      })),
    ],
    [agents.data],
  );

  const unlinked = !agentId && todo.data != null && todo.data.agent == null;
  const pendingAskId = save.isPending ? (save.variables?.askId ?? null) : null;
  const done = (askId: string) => save.mutate({ askId, patch: { state: 'done' } });
  const reopen = (askId: string) => save.mutate({ askId, patch: { state: 'open' } });

  return (
    <div className="space-y-5">
      <PageHeader
        title="Customer asks"
        actions={
          canPick ? (
            <div className="w-full sm:w-80">
              <label htmlFor="customer-asks-agent" className="sr-only">
                Agent
              </label>
              <SearchableSelect
                id="customer-asks-agent"
                value={agentId}
                onChange={setAgentId}
                options={agentOptions}
                placeholder="Agent"
                clearable
                size="sm"
              />
            </div>
          ) : null
        }
      />

      {unlinked ? (
        <div className="space-y-1 rounded-lg border px-6 py-8 text-center">
          <h2 className="font-medium">Not linked to a sales agent</h2>
          <p className="text-sm text-muted-foreground">
            Ask an admin to link your WhatsApp contact to your sales agent.
          </p>
        </div>
      ) : (
        <>
          <LandingToolbar
            fields={ASK_LANDING_FIELDS}
            items={items}
            filters={filters}
            onFiltersChange={setFilters}
            sort={sort}
            onSortChange={(next) => prefs.setSorting(askSortToSorting(next))}
            view={view.mode}
            onViewChange={view.setMode}
          />
          <AskTodoList
            payload={payload}
            loading={todo.isLoading || prefs.isLoading}
            error={todo.isError ? (todo.error instanceof Error ? todo.error.message : 'Try again shortly.') : null}
            view={view.mode}
            sort={sort}
            onSortChange={(next) => prefs.setSorting(askSortToSorting(next))}
            onOpen={setOpened}
            onDone={done}
            onReopen={reopen}
            showAgent={agentId === ALL_AGENTS}
            showDoneBy
            filtered={Object.keys(filters).length > 0}
            pendingAskId={pendingAskId}
            listingKey={SORT_LISTING_KEY}
          />
        </>
      )}

      <Sheet open={Boolean(opened)} onOpenChange={(next) => !next && setOpened(null)}>
        <SheetContent side="right" className="w-full gap-0 p-0 sm:max-w-lg">
          <SheetHeader className="sr-only">
            <SheetTitle>Customer ask</SheetTitle>
            <SheetDescription>The conversation around this ask</SheetDescription>
          </SheetHeader>
          {current ? (
            <AskConversationPanel
              key={current.id}
              ask={current}
              conversation={conversation.data}
              thread={thread}
              showOpenInConversations
              agentCode={current.agent_code ?? todo.data?.agent?.code ?? null}
              onNote={(askId, note) => save.mutateAsync({ askId, patch: { note } })}
              onDone={(askId) => {
                done(askId);
                setOpened(null);
              }}
              onReopen={(askId) => {
                reopen(askId);
                setOpened(null);
              }}
              pending={pendingAskId === current.id}
            />
          ) : null}
        </SheetContent>
      </Sheet>
    </div>
  );
}
