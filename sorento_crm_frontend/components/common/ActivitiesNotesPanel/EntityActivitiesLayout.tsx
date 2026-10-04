'use client';

/**
 * Overlay layout primitive for the Activities & Notes panel.
 *
 * Wrap a detail page's body in this; the panel slides in as a fixed-position
 * 420 px column on `lg+` (full-width on smaller screens) via its own
 * `translate-x` - it OVERLAYS the content rather than pushing it (M3-03,
 * `ui-motion-round2`): the main column never changes width, so a DataGrid or
 * any other width-sensitive child does not re-lay-out when the panel opens.
 * This used to push the content left via an animated `margin` on `lg+`; the
 * push was dropped, not just the animation, because a layout-affecting
 * property costs a reflow on every frame regardless of whether it is
 * transitioned. A floating red pulse-icon launcher sits pinned to the
 * bottom-right edge while the panel is closed.
 *
 * Usage:
 *   <EntityActivitiesLayout entityType="ticket" entityId={id}>
 *     <TicketDetailBody ticket={ticket} />
 *   </EntityActivitiesLayout>
 */
import { useEffect, useState, type ReactNode } from 'react';
import { sanitizedHtml } from '@/lib/sanitize';
import { Activity, FileText, MessageSquare, X } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import {
  RichTextEditor,
  extractMentionedUserIds,
} from '@/components/ui/rich-text-editor';
import { getUserLookup } from '@/services/userSelectService';
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from '@/components/ui/tabs';
import { cn } from '@/lib/utils';
import {
  deleteNote,
  listActivities,
  listEntityContacts,
  listMessages,
  listNotes,
  postActivity,
  postNote,
  sendMessage,
} from './activitiesPanelService';
import type {
  ActivityEvent,
  EntityMessage,
  EntityRespondContact,
  InternalNote,
  PanelTab,
} from './types';

const PERSIST_KEY = 'sorento:activities-panel:tab';

function htmlToText(html: string): string {
  if (typeof window === 'undefined') return html.replace(/<[^>]+>/g, '').trim();
  const doc = new DOMParser().parseFromString(html || '', 'text/html');
  return (doc.body.textContent || '').trim();
}

function relativeTime(iso: string): string {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleString();
}

function formatSystemEvent(
  template: string | null | undefined,
  payload: Record<string, unknown> | null | undefined,
): string {
  const t = template ?? '';
  const p = payload ?? {};
  switch (t) {
    case 'entity.created':
      return `Ticket ${(p.ticket_number as string) ?? ''} was created.`;
    case 'status.changed':
      return `Status changed: ${(p.from as string) ?? '?'} → ${(p.to as string) ?? '?'}${
        p.note ? ` - ${p.note as string}` : ''
      }`;
    case 'assignee.changed':
      return 'Assignee changed.';
    case 'response.updated':
      return p.first_response ? 'First response posted.' : 'Response updated.';
    case 'resolution.updated':
      return 'Resolution updated.';
    case 'watchers.added':
      return 'Watchers added.';
    case 'watchers.removed':
      return 'Watcher removed.';
    case 'message.sent':
      return 'Outbound message sent.';
    default:
      return t || 'System event';
  }
}

interface Props {
  entityType: string;
  entityId: string;
  children: ReactNode;
}

export default function EntityActivitiesLayout({
  entityType,
  entityId,
  children,
}: Props) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<PanelTab>('activities');

  // Persist last-opened tab keyed on entityType so users land on the same
  // tab they last used per resource.
  useEffect(() => {
    try {
      const stored = localStorage.getItem(`${PERSIST_KEY}:${entityType}`);
      if (stored === 'activities' || stored === 'notes' || stored === 'messages') {
        setTab(stored);
      }
    } catch {
      /* ignore */
    }
  }, [entityType]);

  function changeTab(next: PanelTab) {
    setTab(next);
    try {
      localStorage.setItem(`${PERSIST_KEY}:${entityType}`, next);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="relative flex w-full">
      {/* The panel is a fixed-position overlay (see `aside` below, `translate-x-full`
          when closed) - the content column never resizes to make room for it
          (M3-03): a DataGrid or any other width-sensitive child on this page reports
          zero layout changes when the panel opens. */}
      <main className="min-w-0 flex-1">{children}</main>

      {/* Floating launcher - pinned bottom-right while panel is closed. */}
      {!open && (
        <Button
          variant="primary"
          size="icon"
          className="fixed end-12 bottom-4 z-30 size-12 rounded-full shadow-xl bg-destructive hover:bg-destructive/90"
          aria-label="Open activities & notes"
          onClick={() => setOpen(true)}
        >
          <Activity className="size-5 text-white" />
        </Button>
      )}

      {/* Side panel - fixed-position, slides in via translate-x. */}
      <aside
        className={cn(
          'fixed top-0 right-0 z-30 h-full bg-background border-l shadow-xl flex flex-col',
          'w-full lg:w-[420px]',
          // `motion-reduce:` at the site, not in the reduced-motion block in
          // css/styles.css: a NAMED duration utility matches none of that
          // block's selectors and this <aside> carries no data-slot either, so
          // the panel slid its full 200ms for someone who asked for no motion
          // (measured, evidence/M3/README.md M3-04). This is the shared pattern
          // DESIGN-LANGUAGE names for a one-off transition.
          'transition-transform duration-(--duration-base) ease-(--ease-standard) motion-reduce:transition-none',
          open ? 'translate-x-0' : 'translate-x-full',
        )}
        aria-hidden={!open}
      >
        <div className="flex items-start gap-2 p-5 border-b">
          <div className="flex-1">
            <h2 className="text-lg font-semibold">Activities &amp; notes</h2>
            <p className="text-sm text-muted-foreground mt-1">
              Activities are visible to everyone with access. Internal notes are
              private to you.
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={() => setOpen(false)}
            aria-label="Close panel"
          >
            <X className="size-4" />
          </Button>
        </div>

        <Tabs
          value={tab}
          onValueChange={(v) => changeTab(v as PanelTab)}
          className="flex-1 flex flex-col overflow-hidden"
        >
          <div className="px-3 pt-3">
            <TabsList variant="default" className="w-full">
              <TabsTrigger value="activities" aria-label="Activities">
                <Activity className="size-4" />
              </TabsTrigger>
              <TabsTrigger value="notes" aria-label="Internal notes">
                <FileText className="size-4" />
              </TabsTrigger>
              <TabsTrigger value="messages" aria-label="Messages">
                <MessageSquare className="size-4" />
              </TabsTrigger>
            </TabsList>
          </div>
          <TabsContent value="activities" className="flex-1 flex flex-col overflow-hidden m-0">
            <ActivitiesTab
              entityType={entityType}
              entityId={entityId}
              active={open && tab === 'activities'}
            />
          </TabsContent>
          <TabsContent value="notes" className="flex-1 flex flex-col overflow-hidden m-0">
            <NotesTab
              entityType={entityType}
              entityId={entityId}
              active={open && tab === 'notes'}
            />
          </TabsContent>
          <TabsContent value="messages" className="flex-1 flex flex-col overflow-hidden m-0">
            <MessagesTab
              entityType={entityType}
              entityId={entityId}
              active={open && tab === 'messages'}
            />
          </TabsContent>
        </Tabs>
      </aside>
    </div>
  );
}

// --------- Activities tab ---------

function ActivitiesTab({
  entityType,
  entityId,
  active,
}: {
  entityType: string;
  entityId: string;
  active: boolean;
}) {
  const [items, setItems] = useState<ActivityEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [posting, setPosting] = useState(false);

  async function reload() {
    setLoading(true);
    try {
      const res = await listActivities(entityType, entityId, { limit: 50 });
      setItems(res.items);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (active) void reload();
     
  }, [active, entityType, entityId]);

  async function submit() {
    if (!htmlToText(draft)) return;
    setPosting(true);
    try {
      await postActivity(entityType, entityId, {
        body_html: draft,
        body_text: htmlToText(draft),
        mentioned_user_ids: extractMentionedUserIds(draft),
      });
      setDraft('');
      await reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading ? (
          <Skeleton className="h-20 w-full" />
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center pt-4">No activity yet.</p>
        ) : (
          items.map((it) => (
            <div key={it.id} className="bg-muted/30 border rounded-md p-3">
              <div className="flex items-center gap-2 mb-1">
                {it.kind === 'system' ? (
                  <Badge variant="secondary">System</Badge>
                ) : (
                  <>
                    <Avatar className="size-6">
                      <AvatarImage src={it.actor?.avatar_url ?? undefined} />
                      <AvatarFallback>
                        {(it.actor?.name ?? '?').charAt(0).toUpperCase()}
                      </AvatarFallback>
                    </Avatar>
                    <span className="text-sm font-medium">{it.actor?.name ?? 'Unknown'}</span>
                  </>
                )}
                <span className="ms-auto text-xs text-muted-foreground">
                  {relativeTime(it.created_at)}
                </span>
              </div>
              {it.kind === 'system' ? (
                <p className="text-sm">
                  {formatSystemEvent(it.system_template, it.system_payload)}
                </p>
              ) : it.body_html ? (
                <div
                  className="text-sm prose prose-sm max-w-none"
                  dangerouslySetInnerHTML={sanitizedHtml(it.body_html)}
                />
              ) : (
                <div className="text-sm whitespace-pre-wrap">{it.body_text ?? ''}</div>
              )}
            </div>
          ))
        )}
      </div>
      <div className="border-t p-3 space-y-2">
        <RichTextEditor
          value={draft}
          onChange={setDraft}
          placeholder="Share an update… Use **bold**, *italic*, lists, links, @mention."
          minHeight={120}
          mentionUsersFetcher={() => getUserLookup()}
        />
        <div className="flex justify-end">
          <Button size="sm" disabled={!htmlToText(draft) || posting} onClick={submit}>
            {posting ? 'Posting…' : 'Post'}
          </Button>
        </div>
      </div>
    </div>
  );
}

// --------- Notes tab ---------

function NotesTab({
  entityType,
  entityId,
  active,
}: {
  entityType: string;
  entityId: string;
  active: boolean;
}) {
  const [items, setItems] = useState<InternalNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [draft, setDraft] = useState('');
  const [posting, setPosting] = useState(false);

  async function reload() {
    setLoading(true);
    try {
      const res = await listNotes(entityType, entityId, { limit: 50 });
      setItems(res.items);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (active) void reload();
     
  }, [active, entityType, entityId]);

  async function submit() {
    if (!htmlToText(draft)) return;
    setPosting(true);
    try {
      await postNote(entityType, entityId, {
        body_html: draft,
        body_text: htmlToText(draft),
      });
      setDraft('');
      await reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPosting(false);
    }
  }

  async function remove(noteId: string) {
    try {
      await deleteNote(noteId);
      await reload();
    } catch (e) {
      toast.error((e as Error).message);
    }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading ? (
          <Skeleton className="h-20 w-full" />
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center pt-4">No internal notes yet.</p>
        ) : (
          items.map((n) => (
            <div key={n.id} className="bg-muted/30 border rounded-md p-3">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs text-muted-foreground">{relativeTime(n.created_at)}</span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="ms-auto h-6 px-2 text-xs"
                  onClick={() => remove(n.id)}
                >
                  Delete
                </Button>
              </div>
              {n.body_html ? (
                <div
                  className="text-sm prose prose-sm max-w-none"
                  dangerouslySetInnerHTML={sanitizedHtml(n.body_html)}
                />
              ) : (
                <div className="text-sm whitespace-pre-wrap">{n.body_text ?? ''}</div>
              )}
            </div>
          ))
        )}
      </div>
      <div className="border-t p-3 space-y-2">
        <RichTextEditor
          value={draft}
          onChange={setDraft}
          placeholder="Private notes (only you can see these)…"
          minHeight={120}
        />
        <div className="flex justify-end">
          <Button size="sm" disabled={!htmlToText(draft) || posting} onClick={submit}>
            {posting ? 'Saving…' : 'Save note'}
          </Button>
        </div>
      </div>
    </div>
  );
}

// --------- Messages tab ---------

function MessagesTab({
  entityType,
  entityId,
  active,
}: {
  entityType: string;
  entityId: string;
  active: boolean;
}) {
  const [contacts, setContacts] = useState<EntityRespondContact[]>([]);
  const [contactId, setContactId] = useState<string>('');
  const [items, setItems] = useState<EntityMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!active) return;
    listEntityContacts(entityType, entityId)
      .then((cs) => {
        setContacts(cs);
        const primary = cs.find((c) => c.is_primary) ?? cs[0];
        if (primary) setContactId(primary.contact_id);
      })
      .catch((e: Error) => toast.error(e.message));
  }, [active, entityType, entityId]);

  useEffect(() => {
    if (!contactId) return;
    setLoading(true);
    listMessages(entityType, entityId, { contact_id: contactId })
      .then((res) => setItems(res.items))
      .catch((e: Error) => toast.error(e.message))
      .finally(() => setLoading(false));
  }, [contactId, entityType, entityId]);

  async function send() {
    if (!contactId || !draft.trim()) return;
    setSending(true);
    try {
      await sendMessage(entityType, entityId, { contact_id: contactId, body: draft });
      setDraft('');
      toast.success('Message sent');
      // Reload to surface the just-sent message in the thread.
      const res = await listMessages(entityType, entityId, { contact_id: contactId });
      setItems(res.items);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="p-3 border-b">
        <SearchableSelect
          value={contactId}
          onChange={setContactId}
          disabled={contacts.length === 0}
          options={contacts.map((c) => ({
            value: c.contact_id,
            label: `${c.name ?? c.phone ?? c.contact_id}${c.is_primary ? ' (primary)' : ''}`,
          }))}
          placeholder={contacts.length === 0 ? 'No contacts linked' : 'Choose a contact'}
        />
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-2">
        {!contactId ? (
          <p className="text-sm text-muted-foreground text-center pt-4">
            Choose a contact to view conversation.
          </p>
        ) : loading ? (
          <Skeleton className="h-20 w-full" />
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center pt-4">
            No messages yet for this contact.
          </p>
        ) : (
          items.map((m) => (
            <div
              key={m.id}
              className={
                m.direction === 'outgoing'
                  ? 'ms-auto max-w-[80%] bg-primary text-primary-foreground rounded-md p-2 text-sm'
                  : 'me-auto max-w-[80%] bg-muted rounded-md p-2 text-sm'
              }
            >
              {m.body ?? ''}
              <div className="text-[10px] opacity-70 mt-1">{relativeTime(m.sent_at)}</div>
            </div>
          ))
        )}
      </div>
      <div className="border-t p-3 space-y-2">
        <Textarea
          placeholder={contactId ? 'Type a reply…' : 'Choose a contact to reply.'}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          disabled={!contactId}
        />
        <div className="flex justify-end">
          <Button size="sm" disabled={!contactId || !draft.trim() || sending} onClick={send}>
            {sending ? 'Sending…' : 'Send'}
          </Button>
        </div>
      </div>
    </div>
  );
}
