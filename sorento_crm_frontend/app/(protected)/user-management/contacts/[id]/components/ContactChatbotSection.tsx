'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ColumnDef, getCoreRowModel, useReactTable } from '@tanstack/react-table';
import { Pencil, Plus, Trash2 } from 'lucide-react';

import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardHeader,
  CardHeading,
  CardTable,
  CardTitle,
  CardToolbar,
} from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { FormDialogScaffold } from '@/components/common/FormDialogScaffold';
import { useDeferredRowAction } from '@/hooks/useDeferredRowAction';
import { formatDate, formatDateSafe, formatDateTimeInMalaysia } from '@/lib/helpers';

import {
  contactChatbotMemoryQueryKey,
  useContactChatbotMemory,
  useContactChatbotProfile,
  useSaveContactChatbotProfile,
  useSaveContactEscalation,
  useSaveContactFact,
} from '../hooks/useContactChatbot';
import {
  searchUsualBrandOptions,
  searchUsualProductOptions,
  searchUsualSiteOptions,
  type ChatbotFactSource,
  type ChatbotMemoryLevel,
  type ContactChatbotEpisodeCurrent,
  type ContactChatbotFact,
  type ContactChatbotMemory,
  type ContactChatbotVocabularyEntry,
} from '../services/contactChatbotService';

/**
 * Contact Details -> Chatbot (chatbot memory lane A). Round 3 mockup
 * `chatbot-memory-27sep-mockup-contact.html`; contract section 5.
 *
 * Four cards, each rendered by this file: Chatbot settings (editable), What the bot
 * knows, Conversations and Open orders (all three read-mostly, sourced from the one
 * memory GET). View and Edit are the same layout - the settings card swaps a value for
 * an input in place, nothing here is a separate "edit mode".
 */
export default function ContactChatbotSection({ contactId }: { contactId: string }) {
  const memoryQuery = useContactChatbotMemory(contactId);

  return (
    <div className="space-y-5">
      <ChatbotSettingsCard contactId={contactId} systemDefault={memoryQuery.data?.level.system_default} />
      <WhatTheBotKnowsCard
        contactId={contactId}
        memory={memoryQuery.data}
        isLoading={memoryQuery.isLoading}
        isError={memoryQuery.isError}
      />
      <ConversationsCard memory={memoryQuery.data} isLoading={memoryQuery.isLoading} />
      <OpenOrdersCard memory={memoryQuery.data} isLoading={memoryQuery.isLoading} />
    </div>
  );
}

const LEVEL_OPTIONS: { value: ChatbotMemoryLevel; label: string }[] = [
  { value: 'off', label: 'Off' },
  { value: 'conversation', label: 'This conversation' },
  { value: 'episodes', label: 'Past conversations' },
  { value: 'full', label: 'Full memory' },
];

const LEVEL_LABEL: Record<ChatbotMemoryLevel, string> = LEVEL_OPTIONS.reduce(
  (acc, option) => ({ ...acc, [option.value]: option.label }),
  {} as Record<ChatbotMemoryLevel, string>,
);

const TIER_OPTIONS = [
  { value: 'dealer', label: 'Dealer' },
  { value: 'office', label: 'Office' },
  { value: 'end_user', label: 'End user' },
];

function SwitchRow({
  id,
  label,
  hint,
  checked,
  disabled,
  onCheckedChange,
}: {
  id: string;
  label: string;
  /** One line under the label, the card's helper-text style. */
  hint?: string;
  checked: boolean;
  disabled?: boolean;
  onCheckedChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div className="space-y-0.5">
        <Label htmlFor={id} className="cursor-pointer font-normal">
          {label}
        </Label>
        {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      </div>
      <Switch
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={(v) => onCheckedChange(v === true)}
      />
    </div>
  );
}

function ChatbotSettingsCard({
  contactId,
  systemDefault,
}: {
  contactId: string;
  systemDefault: ChatbotMemoryLevel | undefined;
}) {
  const { data: profile, isLoading, isError } = useContactChatbotProfile(contactId);
  const save = useSaveContactChatbotProfile(contactId);
  const saveEscalation = useSaveContactEscalation(contactId);

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Chatbot settings</CardTitle>
        </CardHeader>
        <CardContent>
          <Skeleton className="h-40 w-full" />
        </CardContent>
      </Card>
    );
  }

  if (isError || !profile) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Chatbot settings</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-destructive">
            This contact&apos;s chatbot settings could not be loaded. Reload the page to try again.
          </p>
        </CardContent>
      </Card>
    );
  }

  const ownSet = profile.chatbot_memory_level != null;
  const systemDefaultLabel = LEVEL_LABEL[systemDefault ?? 'off'];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Chatbot settings</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Memory context level</Label>
            <SearchableSelect
              value={profile.chatbot_memory_level ?? ''}
              onChange={(v) =>
                save.mutate({
                  ...profile,
                  chatbot_memory_level: (v || null) as ChatbotMemoryLevel | null,
                })
              }
              clearable
              disabled={save.isPending}
              placeholder="(follow the system default)"
              options={LEVEL_OPTIONS}
            />
            <p className="text-xs text-muted-foreground">
              {ownSet ? `Own level · system default: ${systemDefaultLabel}` : `System default: ${systemDefaultLabel}`}
            </p>
          </div>
          <div className="space-y-1.5">
            <Label>Tier</Label>
            <SearchableSelect
              value={profile.tier ?? ''}
              onChange={(v) => save.mutate({ ...profile, tier: v || null })}
              clearable
              disabled={save.isPending}
              placeholder="(none)"
              options={TIER_OPTIONS}
            />
          </div>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <SwitchRow
            id="contact-chatbot-stock"
            label="Stock checks"
            checked={profile.stock_allowed}
            disabled={save.isPending}
            onCheckedChange={(checked) => save.mutate({ ...profile, stock_allowed: checked })}
          />
          <SwitchRow
            id="contact-chatbot-notify-salesman"
            label="Notify salesman"
            checked={profile.notify_salesman}
            disabled={save.isPending}
            onCheckedChange={(checked) => save.mutate({ ...profile, notify_salesman: checked })}
          />
          <SwitchRow
            id="contact-chatbot-packing-list"
            label="Packing list allowed"
            checked={profile.packing_list_allowed}
            disabled={save.isPending}
            onCheckedChange={(checked) => save.mutate({ ...profile, packing_list_allowed: checked })}
          />
          <SwitchRow
            id="contact-chatbot-eta-offset"
            label="ETA buffer days"
            checked={profile.eta_offset_applied}
            disabled={save.isPending}
            onCheckedChange={(checked) => save.mutate({ ...profile, eta_offset_applied: checked })}
          />
          <SwitchRow
            id="contact-chatbot-escalation"
            label="Chatbot hands over to support teams"
            // Owner-approved wording (1 Oct 2026, hand test of #1406).
            hint="When off, the chatbot tells this contact to refer to their salesman."
            checked={profile.escalation_allowed}
            disabled={save.isPending || saveEscalation.isPending}
            onCheckedChange={(checked) => saveEscalation.mutate(checked)}
          />
        </div>
      </CardContent>
    </Card>
  );
}

const SOURCE_BADGE: Record<ChatbotFactSource, { label: string; variant: BadgeProps['variant'] }> = {
  crm: { label: 'CRM', variant: 'secondary' },
  tallied: { label: 'Learned', variant: 'primary' },
  stated: { label: 'Said', variant: 'success' },
  staff: { label: 'Staff', variant: 'info' },
};

/** Fetch* wired per multi-kind vocabulary key (contract section 4). */
const MULTI_FETCH_OPTIONS: Record<string, (query: string) => Promise<{ value: string; label: string }[]>> = {
  usual_products: searchUsualProductOptions,
  usual_brands: searchUsualBrandOptions,
  usual_sites: searchUsualSiteOptions,
};

/**
 * The fact's own raw `value` (contract section 5: `"ms"`, `["SRTWB1455"]`), seeded
 * straight into the editor for an in-place edit or the Add modal - no reverse-mapping
 * through the vocabulary's labels, since the backend now sends the raw value directly.
 */
function rawValueFor(
  fact: Pick<ContactChatbotFact, 'value'>,
  vocab: ContactChatbotVocabularyEntry | undefined,
): string | string[] {
  if (fact.value == null) return vocab?.kind === 'multi' ? [] : '';
  return fact.value;
}

function FactValueEditor({
  vocab,
  value,
  onChange,
}: {
  vocab: ContactChatbotVocabularyEntry;
  value: string | string[];
  onChange: (value: string | string[]) => void;
}) {
  if (vocab.kind === 'choice') {
    return (
      <SearchableSelect
        value={typeof value === 'string' ? value : ''}
        onChange={onChange}
        options={vocab.options ?? []}
      />
    );
  }
  if (vocab.kind === 'multi') {
    const selected = Array.isArray(value) ? value : [];
    const fetchOptions = MULTI_FETCH_OPTIONS[vocab.key];
    return (
      <SearchableMultiSelect
        value={selected}
        onChange={onChange}
        selectedOptions={selected.map((v) => ({ value: v, label: v }))}
        fetchOptions={fetchOptions}
        placeholder="Search..."
      />
    );
  }
  return (
    <Input
      value={typeof value === 'string' ? value : ''}
      maxLength={vocab.max_length ?? undefined}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

function factCompositeId(contactId: string, key: string): string {
  return `${contactId}:${key}`;
}

function lastSeenLabel(fact: ContactChatbotFact): string {
  if (fact.source === 'crm') return 'live';
  return fact.last_seen ? formatDate(fact.last_seen) : '-';
}

function WhatTheBotKnowsCard({
  contactId,
  memory,
  isLoading,
  isError,
}: {
  contactId: string;
  memory: ContactChatbotMemory | undefined;
  isLoading: boolean;
  isError: boolean;
}) {
  const saveFact = useSaveContactFact(contactId);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState<string | string[]>('');
  const [addOpen, setAddOpen] = useState(false);

  // Delete asks nothing (D7): the row's actions become an inline countdown with Cancel.
  // The server commits `contact_chatbot_fact.delete` when the window lapses (or the tab
  // closes); `invalidateKeys` refetches the memory GET, which is what takes the row out.
  const deletion = useDeferredRowAction({
    actionKey: 'contact_chatbot_fact.delete',
    entityType: 'contact_chatbot_fact',
    verb: 'Deleting',
    successMessage: 'Fact deleted',
    surface: 'inline',
    invalidateKeys: [contactChatbotMemoryQueryKey(contactId)],
  });

  const vocabByKey = useMemo(
    () => Object.fromEntries((memory?.vocabulary ?? []).map((v) => [v.key, v])),
    [memory],
  );

  const startEdit = (fact: ContactChatbotFact) => {
    setEditingKey(fact.key);
    setEditValue(rawValueFor(fact, vocabByKey[fact.key]));
  };

  const saveEdit = (key: string) => {
    saveFact.mutate(
      { key, value: editValue },
      { onSuccess: () => setEditingKey(null) },
    );
  };

  const rows = memory?.facts ?? [];

  const columns = useMemo<ColumnDef<ContactChatbotFact>[]>(
    () => [
      {
        accessorKey: 'label',
        header: 'Fact',
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.label}>
            {row.original.label}
          </span>
        ),
        size: 130,
      },
      {
        id: 'value',
        header: 'Value',
        cell: ({ row }) => {
          const fact = row.original;
          if (editingKey === fact.key) {
            const vocab = vocabByKey[fact.key];
            return vocab ? (
              <FactValueEditor vocab={vocab} value={editValue} onChange={setEditValue} />
            ) : null;
          }
          if (fact.link) {
            return (
              <Link
                href={fact.link}
                className="truncate text-primary hover:underline"
                title={fact.display ?? ''}
              >
                {fact.display}
              </Link>
            );
          }
          return (
            <span className="truncate" title={fact.display ?? ''}>
              {fact.display ?? '-'}
            </span>
          );
        },
        size: 240,
      },
      {
        id: 'source',
        header: 'Source',
        cell: ({ row }) => {
          const badge = SOURCE_BADGE[row.original.source];
          return (
            <Badge variant={badge.variant} appearance="light" size="sm">
              {badge.label}
            </Badge>
          );
        },
        size: 90,
      },
      {
        id: 'last_seen',
        header: 'Last seen',
        cell: ({ row }) => (
          <span className="truncate text-muted-foreground" title={lastSeenLabel(row.original)}>
            {lastSeenLabel(row.original)}
          </span>
        ),
        size: 100,
      },
      {
        id: 'actions',
        header: () => <span className="sr-only">Actions</span>,
        cell: ({ row }) => {
          const fact = row.original;
          if (!fact.editable) {
            return <span className="text-xs text-muted-foreground">read-only</span>;
          }
          if (deletion.targetId === factCompositeId(contactId, fact.key)) {
            return deletion.countdown;
          }
          if (editingKey === fact.key) {
            return (
              <div className="flex justify-end gap-1">
                <Button size="sm" disabled={saveFact.isPending} onClick={() => saveEdit(fact.key)}>
                  Save
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setEditingKey(null)}>
                  Cancel
                </Button>
              </div>
            );
          }
          return (
            <div className="flex justify-end gap-1">
              <Button
                mode="icon"
                variant="ghost"
                size="sm"
                aria-label={`Edit ${fact.label}`}
                onClick={() => startEdit(fact)}
              >
                <Pencil className="size-4" />
              </Button>
              {/* A CRM fact (e.g. "segment") is a live join, never stored (contract
                  section 4) - there is nothing behind it to delete, so the countdown
                  used to commit and change nothing (review finding S11). */}
              {fact.source !== 'crm' && (
                <Button
                  mode="icon"
                  variant="ghost"
                  size="sm"
                  aria-label={`Delete ${fact.label}`}
                  onClick={() => {
                    deletion.run({
                      id: factCompositeId(contactId, fact.key),
                      subject: fact.label,
                      payload: { contact_id: contactId, key: fact.key },
                    });
                  }}
                >
                  <Trash2 className="size-4 text-destructive" />
                </Button>
              )}
            </div>
          );
        },
        size: 170,
        enableSorting: false,
        meta: { cellClassName: 'text-right' },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [contactId, editingKey, editValue, deletion.targetId, deletion.countdown, saveFact.isPending, vocabByKey],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.key,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <>
      <DataGrid
        table={table}
        recordCount={rows.length}
        isLoading={isLoading}
        listingKey={null}
        tableLayout={{ width: 'fixed', columnsResizable: true }}
        emptyMessage={isError ? 'Could not load. Reload the page to try again.' : 'Nothing learned yet.'}
      >
        <Card>
          <CardHeader>
            <CardHeading>
              <CardTitle>What the bot knows</CardTitle>
            </CardHeading>
            <CardToolbar>
              <Button size="sm" onClick={() => setAddOpen(true)}>
                <Plus className="size-4" />
                Add
              </Button>
            </CardToolbar>
          </CardHeader>
          <CardTable>
            <DataGridTable />
          </CardTable>
        </Card>
      </DataGrid>

      <AddFactModal
        open={addOpen}
        onOpenChange={setAddOpen}
        vocabulary={memory?.vocabulary ?? []}
        isPending={saveFact.isPending}
        onSubmit={(key, value) =>
          saveFact.mutate({ key, value }, { onSuccess: () => setAddOpen(false) })
        }
      />
    </>
  );
}

function AddFactModal({
  open,
  onOpenChange,
  vocabulary,
  isPending,
  onSubmit,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  vocabulary: ContactChatbotVocabularyEntry[];
  isPending: boolean;
  onSubmit: (key: string, value: string | string[]) => void;
}) {
  const [key, setKey] = useState('');
  const [value, setValue] = useState<string | string[]>('');

  useEffect(() => {
    if (!open) {
      setKey('');
      setValue('');
    }
  }, [open]);

  const vocab = vocabulary.find((v) => v.key === key);

  return (
    <FormDialogScaffold
      open={open}
      onOpenChange={onOpenChange}
      title="Add fact"
      isPending={isPending}
      onSubmit={(e) => {
        e.preventDefault();
        if (!key) return;
        onSubmit(key, value);
      }}
    >
      <div className="space-y-1.5">
        <Label>Fact</Label>
        <SearchableSelect
          value={key}
          onChange={(v) => {
            setKey(v);
            setValue(vocabulary.find((entry) => entry.key === v)?.kind === 'multi' ? [] : '');
          }}
          options={vocabulary.map((entry) => ({ value: entry.key, label: entry.label }))}
        />
      </div>
      {vocab && (
        <div className="space-y-1.5">
          <Label>Value</Label>
          <FactValueEditor vocab={vocab} value={value} onChange={setValue} />
        </div>
      )}
    </FormDialogScaffold>
  );
}

function ConversationsCard({
  memory,
  isLoading,
}: {
  memory: ContactChatbotMemory | undefined;
  isLoading: boolean;
}) {
  const episodes = memory?.episodes;
  const rows = useMemo(() => {
    const openRow = (current: ContactChatbotEpisodeCurrent | null | undefined, console: boolean) =>
      current
        ? [
            {
              id: console ? '__console_current__' : '__current__',
              date: current.started_at,
              domains: current.domains,
              topic: current.topic,
              summary: current.summary,
              turn_count: current.turn_count,
              close_reason: null as string | null,
              first_turn_id: current.first_turn_id,
              console,
              isCurrent: true,
            },
          ]
        : [];
    const closedRows = (episodes?.rows ?? []).map((row) => ({
      ...row,
      console: row.console === true,
      isCurrent: false,
    }));
    return [...openRow(episodes?.current, false), ...openRow(episodes?.console_current, true), ...closedRows];
  }, [episodes]);

  const columns = useMemo<ColumnDef<(typeof rows)[number]>[]>(
    () => [
      {
        id: 'when',
        header: 'When',
        cell: ({ row }) => (
          <span className="flex min-w-0 items-center gap-1.5">
            {row.original.isCurrent ? (
              <Badge variant="primary" appearance="light" size="sm">
                Now
              </Badge>
            ) : (
              <span className="truncate" title={formatDateTimeInMalaysia(row.original.date)}>
                {formatDateTimeInMalaysia(row.original.date)}
              </span>
            )}
            {row.original.console && (
              <Badge variant="secondary" appearance="light" size="sm">
                Console
              </Badge>
            )}
          </span>
        ),
        size: 190,
      },
      {
        id: 'topic',
        header: 'Topic',
        cell: ({ row }) => {
          const domains = row.original.domains;
          const label =
            row.original.topic ??
            (domains.length ? domains.map((d) => d[0].toUpperCase() + d.slice(1)).join(', ') : '-');
          return (
            <span className="truncate" title={label}>
              {label}
            </span>
          );
        },
        size: 140,
      },
      {
        accessorKey: 'summary',
        header: 'Summary',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.summary}>
            {row.original.summary}
          </span>
        ),
        size: 320,
      },
      {
        accessorKey: 'turn_count',
        header: 'Turns',
        cell: ({ row }) => <span>{row.original.turn_count}</span>,
        size: 70,
      },
      {
        id: 'ended_by',
        header: 'Ended by',
        cell: ({ row }) =>
          row.original.isCurrent ? (
            <Badge variant="primary" appearance="light" size="sm">
              Open
            </Badge>
          ) : (
            <span className="truncate">
              {row.original.close_reason === 'topic_switch' ? 'Topic switch' : (row.original.close_reason ?? '-')}
            </span>
          ),
        size: 110,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.id,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  // Security review 26 Sep 2026 (S3): `episodes` is `null` (never an empty list)
  // when the caller lacks `system.chat_history.view` - loaded but withheld, not
  // "no conversations yet". The section stays, with its own explicit empty state,
  // rather than being hidden.
  const noAccess = !isLoading && memory !== undefined && episodes === null;

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={isLoading}
      listingKey={null}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
      // Chat History deep-links a turn via `?turn=<id>` (`system-management/chat-history/
      // page.tsx`) - the same param the Chatbot Console's own trace link already uses,
      // opened straight into the turn's own drawer rather than searched for in the
      // WhatsApp message list.
      rowHref={(row) => `/system-management/chat-history?turn=${row.first_turn_id}`}
      emptyMessage={
        noAccess
          ? "You do not have permission to view this contact's conversation history."
          : 'No conversations recorded yet.'
      }
    >
      <Card>
        <CardHeader>
          <CardHeading>
            <CardTitle>Conversations</CardTitle>
          </CardHeading>
          {episodes && (
            <span className="text-xs text-muted-foreground">
              {episodes.kept} kept of {episodes.limit}
              {episodes.console_kept ? `, ${episodes.console_kept} console` : ''}
            </span>
          )}
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
      </Card>
    </DataGrid>
  );
}

function OpenOrdersCard({
  memory,
  isLoading,
}: {
  memory: ContactChatbotMemory | undefined;
  isLoading: boolean;
}) {
  const openOrders = memory?.open_orders;
  const rows = openOrders?.rows ?? [];

  const columns = useMemo<ColumnDef<(typeof rows)[number]>[]>(
    () => [
      {
        accessorKey: 'document',
        header: 'Document',
        cell: ({ row }) => (
          <span className="truncate font-medium" title={row.original.document}>
            {row.original.document}
          </span>
        ),
        size: 140,
      },
      {
        accessorKey: 'status',
        header: 'Status',
        cell: ({ row }) => <Badge status={row.original.status}>{row.original.status}</Badge>,
        size: 120,
      },
      {
        accessorKey: 'summary',
        header: 'Summary',
        cell: ({ row }) => (
          <span className="truncate" title={row.original.summary}>
            {row.original.summary}
          </span>
        ),
        size: 260,
      },
      {
        accessorKey: 'date',
        header: 'Date',
        cell: ({ row }) => <span>{formatDateSafe(row.original.date)}</span>,
        size: 100,
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    getRowId: (row) => row.document,
    getCoreRowModel: getCoreRowModel(),
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <DataGrid
      table={table}
      recordCount={rows.length}
      isLoading={isLoading}
      listingKey={null}
      tableLayout={{ width: 'fixed', columnsResizable: true }}
      rowHref={(row) => row.href}
      emptyMessage="No open orders for this contact's customer."
    >
      <Card>
        <CardHeader>
          <CardHeading>
            <CardTitle>Open orders</CardTitle>
          </CardHeading>
          {openOrders?.customer_name && (
            <span className="text-xs text-muted-foreground">live from {openOrders.customer_name}</span>
          )}
        </CardHeader>
        <CardTable>
          <DataGridTable />
        </CardTable>
      </Card>
    </DataGrid>
  );
}
