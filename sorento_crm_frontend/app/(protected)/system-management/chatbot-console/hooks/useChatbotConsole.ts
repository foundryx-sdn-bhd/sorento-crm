'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { extractTurnAttachments } from '@/components/chatbot/TurnAttachments';
import { toast } from '@/lib/toast';
import { getChatbotTurns } from '../../chat-history/services/chatbotTurnService';
import { getRespondContactsOutbound } from '../../respond-contacts/services/respondContactOutboundService';
import {
  getConsoleMediaStatus,
  getConsolePromptVersions,
  postConsoleTurn,
} from '../services/chatbotConsoleService';
import {
  CONSOLE_GREETING_MESSAGES,
  type ChatbotConsoleMessage,
  type ConsoleMediaInput,
  type ConsolePromptVersion,
  type ConsoleTurnResponse,
} from '../types/chatbotConsole.types';

const LAST_CONTACT_STORAGE_KEY = 'chatbot-console:last-contact';
// How often the "still reading/transcribing" poll checks back, once a media turn's own
// synchronous wait already timed out server-side. Matches the plan's own "every 2 s".
const MEDIA_POLL_INTERVAL_MS = 2000;

interface StoredContact {
  id: string;
  label: string;
}

function newRunId(): string {
  const rand =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `console-${rand}`;
}

function greetingMessages(): ChatbotConsoleMessage[] {
  return CONSOLE_GREETING_MESSAGES.map((text, index) => ({
    id: `greeting-${index}`,
    role: 'bot',
    text,
  }));
}

function readStoredContact(): StoredContact | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(LAST_CONTACT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredContact>;
    if (typeof parsed.id === 'string' && typeof parsed.label === 'string') {
      return { id: parsed.id, label: parsed.label };
    }
  } catch {
    // Ignored - a corrupt value is the same as absent.
  }
  return null;
}

function writeStoredContact(contact: StoredContact): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(LAST_CONTACT_STORAGE_KEY, JSON.stringify(contact));
  } catch {
    // Storage can be full or disabled; the console still works, it just forgets next visit.
  }
}

/** Item 6: the version the console pins when the operator has not chosen one - the NEWEST
 * `full` body, never the newest overall and never the production label (which locally
 * sits on the compact lineage: the owner's turns ran the compact base twice unnoticed).
 * Null when no full version exists. */
export function defaultPromptVersionId(versions: ConsolePromptVersion[]): string | null {
  const full = versions.filter((v) => v.base === 'full');
  if (full.length === 0) return null;
  return full.reduce((best, v) => (v.version > best.version ? v : best)).id;
}

/** A contact row's display label, the same "no UUIDs in the UI" rule every other picker
 * follows - falls back to the phone, then to the id only when NEITHER name nor phone is on
 * file (a respond.io id is a short reference number, not an internal UUID). */
function contactLabel(row: { name: string | null; phone_number: string | null; respond_io_id: string | null }): string {
  return row.name || row.phone_number || row.respond_io_id || 'Unknown contact';
}

/** `reply_text` plus one bubble per `send_messages` entry, exactly the shape the plan
 * describes - shared between a plain text turn and the tail of a resolved media turn.
 * The turn's own `send_attachments` files (if any) ride the LAST bubble, the same
 * place `quickReplies` already rides. */
function turnBubbles(result: ConsoleTurnResponse, fallbackId: string): ChatbotConsoleMessage[] {
  const bodies = [...(result.reply_text ? [result.reply_text] : []), ...result.send_messages];
  if (bodies.length === 0) bodies.push('(no reply)');
  const attachments = extractTurnAttachments(result.actions);
  return bodies.map((body, index) => ({
    id: `bot-${result.turn_id ?? fallbackId}-${index}`,
    role: 'bot' as const,
    text: body,
    turnId: result.turn_id,
    branchKind: index === 0 ? result.branch_kind : undefined,
    promptVersion: result.prompt_version ?? null,
    routingLine: index === 0 ? (result.trace_summary?.routing_line ?? null) : undefined,
    quickReplies: index === bodies.length - 1 ? result.quick_replies : undefined,
    attachments: index === bodies.length - 1 && attachments.length > 0 ? attachments : undefined,
  }));
}

function mediaReadLine(kind: 'image' | 'audio', text: string): string {
  return kind === 'image' ? `Read from image: ${text}` : `Heard: ${text}`;
}

function mediaStatusLine(kind: 'image' | 'audio'): string {
  return kind === 'image' ? 'Reading image...' : 'Transcribing...';
}

export function useChatbotConsole() {
  const [contactId, setContactIdState] = useState<string | null>(null);
  const [contactLabelState, setContactLabelState] = useState<string>('');
  const [promptVersionId, setPromptVersionIdState] = useState<string | null>(null);
  // Item 6 (revised, PR #1273 retest): the operator's pick lives in React state ONLY for
  // this page session - never localStorage/sessionStorage/URL - so a reload always starts
  // from the newest full body again rather than replaying a stale pick from a prior visit.
  // This flag guards against re-defaulting over an explicit in-session pick (including an
  // explicit "live label" clear, id === null) once the versions list refetches.
  const promptChoiceApplied = useRef(false);
  const [runId, setRunId] = useState<string>(() => newRunId());
  const [sessionVars, setSessionVars] = useState<Record<string, unknown> | null>({});
  const [messages, setMessages] = useState<ChatbotConsoleMessage[]>(() => greetingMessages());
  const [sending, setSending] = useState(false);
  const bootstrapped = useRef(false);
  // A poll scheduled against a Reset/contact-switch mid-flight must not keep writing
  // bubbles into a thread that has moved on - bumped by `resetThread` and `setContact`,
  // and every poll tick checks it is still the generation that scheduled it.
  const generationRef = useRef(0);

  const promptVersionsQuery = useQuery({
    queryKey: ['chatbot-console', 'prompt-versions'],
    queryFn: getConsolePromptVersions,
    staleTime: 60_000,
  });

  const promptVersions = promptVersionsQuery.data;
  useEffect(() => {
    if (promptChoiceApplied.current || !promptVersions) return;
    promptChoiceApplied.current = true;
    setPromptVersionIdState(defaultPromptVersionId(promptVersions));
  }, [promptVersions]);

  const setPromptVersionId = useCallback((id: string | null) => {
    promptChoiceApplied.current = true;
    setPromptVersionIdState(id);
  }, []);

  // Default contact: last used from localStorage, else the contact of the most recent
  // LIVE turn (excludes test/console turns by construction - `getChatbotTurns` defaults
  // `include_test=false`). Runs once.
  useEffect(() => {
    if (bootstrapped.current) return;
    bootstrapped.current = true;
    const stored = readStoredContact();
    if (stored) {
      setContactIdState(stored.id);
      setContactLabelState(stored.label);
      return;
    }
    (async () => {
      try {
        const turns = await getChatbotTurns({ limit: 1 });
        const latestId = turns.items[0]?.contact_respond_id;
        if (!latestId) return;
        const contacts = await getRespondContactsOutbound({
          pageIndex: 0,
          pageSize: 1,
          searchQuery: latestId,
        });
        const row = contacts.data[0];
        const label = row ? contactLabel(row) : latestId;
        setContactIdState(latestId);
        setContactLabelState(label);
      } catch {
        // No default resolvable (no prior turns, or the lookups failed) - the contact
        // picker simply opens empty and the operator picks one.
      }
    })();
  }, []);

  const resetThread = useCallback(() => {
    generationRef.current += 1;
    setRunId(newRunId());
    setSessionVars({});
    setMessages(greetingMessages());
  }, []);

  const setContact = useCallback(
    (id: string, label: string) => {
      setContactIdState(id);
      setContactLabelState(label);
      writeStoredContact({ id, label });
      // A different contact is a different borrowed session - carrying the old one
      // forward would silently mix two contacts' state in one reply.
      resetThread();
    },
    [resetThread],
  );

  const sendText = useCallback(
    async (text: string, quote?: { id: string; text: string } | null) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
      if (!contactId) {
        toast.error('Pick a contact before sending a message.');
        return;
      }
      const userMessage: ChatbotConsoleMessage = {
        id: `user-${Date.now()}-${Math.random().toString(16).slice(2)}`,
        role: 'user',
        text: trimmed,
        ...(quote ? { quotedText: quote.text } : {}),
      };
      setMessages((prev) => [...prev, userMessage]);
      setSending(true);
      try {
        const result = await postConsoleTurn({
          contact_respond_id: contactId,
          text: trimmed,
          session_vars: sessionVars,
          prompt_version_id: promptVersionId,
          run_id: runId,
          ...(quote ? { reply_to: { id: quote.id, text: quote.text } } : {}),
        });
        setMessages((prev) => [...prev, ...turnBubbles(result, runId)]);
        // Membership matters (see the service doc-comment): only overwrite when this
        // turn actually produced a patch. A turn that produced none (a failed lane)
        // must not be read as "the contact now remembers nothing".
        if (result.session_vars !== null) {
          setSessionVars(result.session_vars);
        }
        writeStoredContact({ id: contactId, label: contactLabelState });
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to run the turn');
      } finally {
        setSending(false);
      }
    },
    [contactId, contactLabelState, promptVersionId, runId, sending, sessionVars],
  );

  const sendQuickReply = useCallback((text: string) => sendText(text), [sendText]);

  // -------------------------------------------------------------------------------- //
  // Media (commit 2): image and voice through the real extractor.
  // -------------------------------------------------------------------------------- //

  /** Once a media turn resolves - synchronously, or after the poll below - run the
   * extracted text as an ordinary turn and append its reply bubbles. Caller already
   * pushed/updated the "Read from image: ..." / "Heard: ..." bubble. */
  const runMediaFollowUpTurn = useCallback(
    async (extractedText: string, caption: string) => {
      const text = (extractedText || caption).trim();
      if (!text || !contactId) return;
      try {
        const result = await postConsoleTurn({
          contact_respond_id: contactId,
          text,
          session_vars: sessionVars,
          prompt_version_id: promptVersionId,
          run_id: runId,
        });
        setMessages((prev) => [...prev, ...turnBubbles(result, runId)]);
        if (result.session_vars !== null) setSessionVars(result.session_vars);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to run the turn');
      }
    },
    [contactId, promptVersionId, runId, sessionVars],
  );

  const pollMediaStatus = useCallback(
    (mediaId: string, statusBubbleId: string, media: ConsoleMediaInput, caption: string, generation: number) => {
      const tick = async () => {
        if (generationRef.current !== generation) return; // superseded by Reset/contact switch
        try {
          const status = await getConsoleMediaStatus(mediaId);
          if (generationRef.current !== generation) return;
          if (status.status === 'pending') {
            window.setTimeout(tick, MEDIA_POLL_INTERVAL_MS);
            return;
          }
          if (status.status === 'failed') {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === statusBubbleId
                  ? { ...m, text: status.error || 'Could not read the attachment.', mediaStatus: 'failed', mediaRetry: media }
                  : m,
              ),
            );
            setSending(false);
            return;
          }
          const text = status.text || '';
          setMessages((prev) =>
            prev.map((m) =>
              m.id === statusBubbleId ? { ...m, text: mediaReadLine(media.kind, text), mediaStatus: 'done' } : m,
            ),
          );
          await runMediaFollowUpTurn(text, caption);
        } catch (err) {
          toast.error(err instanceof Error ? err.message : 'Failed to check the attachment status');
        } finally {
          if (generationRef.current === generation) setSending(false);
        }
      };
      window.setTimeout(tick, MEDIA_POLL_INTERVAL_MS);
    },
    [runMediaFollowUpTurn],
  );

  const sendMediaPayload = useCallback(
    async (media: ConsoleMediaInput, caption: string) => {
      if (!contactId) {
        toast.error('Pick a contact before sending a message.');
        return;
      }
      const generation = generationRef.current;
      setSending(true);
      try {
        const result = await postConsoleTurn({
          contact_respond_id: contactId,
          text: caption,
          session_vars: sessionVars,
          prompt_version_id: promptVersionId,
          run_id: runId,
          media,
        });
        if (generationRef.current !== generation) return;

        if (result.media_status === 'pending' && result.media_id) {
          const statusId = `media-status-${result.media_id}`;
          setMessages((prev) => [
            ...prev,
            { id: statusId, role: 'bot', text: mediaStatusLine(media.kind), mediaStatus: 'pending', mediaId: result.media_id },
          ]);
          pollMediaStatus(result.media_id, statusId, media, caption, generation);
          return; // pollMediaStatus clears `sending` once it settles
        }

        if (result.media_status === 'failed') {
          setMessages((prev) => [
            ...prev,
            {
              id: `media-failed-${Date.now()}`,
              role: 'bot',
              text: result.media_error || 'Could not read the attachment.',
              mediaStatus: 'failed',
              mediaRetry: media,
            },
          ]);
          // A caption fallback may still have produced a real turn (turn_id present).
          if (result.turn_id) {
            setMessages((prev) => [...prev, ...turnBubbles(result, runId)]);
            if (result.session_vars !== null) setSessionVars(result.session_vars);
          }
          return;
        }

        // "done": resolved inline, within the request's own synchronous wait.
        if (result.media_text) {
          setMessages((prev) => [
            ...prev,
            {
              id: `media-read-${result.media_id ?? Date.now()}`,
              role: 'bot',
              text: mediaReadLine(media.kind, result.media_text as string),
              mediaStatus: 'done',
              mediaId: result.media_id,
            },
          ]);
        }
        setMessages((prev) => [...prev, ...turnBubbles(result, runId)]);
        if (result.session_vars !== null) setSessionVars(result.session_vars);
        writeStoredContact({ id: contactId, label: contactLabelState });
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to send the attachment');
      } finally {
        if (generationRef.current === generation) setSending(false);
      }
    },
    [contactId, contactLabelState, pollMediaStatus, promptVersionId, runId, sessionVars],
  );

  const readFileAsBase64 = (file: File): Promise<string> =>
    new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = String(reader.result || '');
        const comma = result.indexOf(',');
        resolve(comma >= 0 ? result.slice(comma + 1) : result);
      };
      reader.onerror = () => reject(reader.error ?? new Error('Could not read the file'));
      reader.readAsDataURL(file);
    });

  const sendMedia = useCallback(
    async (kind: 'image' | 'audio', file: File, caption: string) => {
      if (sending) return;
      if (!contactId) {
        toast.error('Pick a contact before sending a message.');
        return;
      }
      const objectUrl = URL.createObjectURL(file);
      setMessages((prev) => [
        ...prev,
        {
          id: `user-media-${Date.now()}`,
          role: 'user',
          text: caption.trim(),
          mediaKind: kind,
          mediaUrl: objectUrl,
        },
      ]);
      try {
        const content_base64 = await readFileAsBase64(file);
        const media: ConsoleMediaInput = {
          kind,
          filename: file.name || (kind === 'image' ? 'photo.jpg' : 'voice.webm'),
          mime: file.type || (kind === 'image' ? 'image/jpeg' : 'audio/webm'),
          content_base64,
        };
        await sendMediaPayload(media, caption.trim());
      } catch (err) {
        toast.error(err instanceof Error ? err.message : 'Failed to send the attachment');
      }
    },
    [contactId, sending, sendMediaPayload],
  );

  const retryMedia = useCallback(
    (media: ConsoleMediaInput) => {
      if (sending) return;
      void sendMediaPayload(media, '');
    },
    [sending, sendMediaPayload],
  );

  return {
    contactId,
    contactLabel: contactLabelState,
    setContact,
    promptVersionId,
    setPromptVersionId,
    promptVersions: promptVersionsQuery.data ?? [],
    promptVersionsLoading: promptVersionsQuery.isLoading,
    messages,
    sending,
    sendText,
    sendQuickReply,
    sendMedia,
    retryMedia,
    reset: resetThread,
    runId,
  };
}
