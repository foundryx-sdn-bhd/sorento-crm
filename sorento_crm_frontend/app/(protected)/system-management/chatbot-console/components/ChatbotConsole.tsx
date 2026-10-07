'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { Loader2, Mic, Paperclip, RotateCcw, SendHorizonal, Square } from 'lucide-react';
import { TurnAttachments } from '@/components/chatbot/TurnAttachments';
import { WhatsAppText } from '@/components/chatbot/WhatsAppText';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect, type SearchableSelectOption } from '@/components/common/SearchableSelect';
import { formatElapsed, useVoiceRecorder } from '@/components/common/conversation/useVoiceRecorder';
import { cn } from '@/lib/utils';
import { getRespondContactsOutbound } from '../../respond-contacts/services/respondContactOutboundService';
import { useChatbotConsole } from '../hooks/useChatbotConsole';
import type { ChatbotConsoleMessage, ConsoleMediaInput } from '../types/chatbotConsole.types';

const REPLY_TO_LABEL_MAX = 60;

export const CHATBOT_CONSOLE_VIEW_PERMISSION = 'system.chat_history.view';

async function fetchContactOptions(query: string): Promise<SearchableSelectOption[]> {
  const result = await getRespondContactsOutbound({ pageIndex: 0, pageSize: 20, searchQuery: query });
  return result.data
    .filter((row) => Boolean(row.respond_io_id))
    .map((row) => ({
      value: row.respond_io_id as string,
      label: row.name || row.phone_number || (row.respond_io_id as string),
      description: row.name && row.phone_number ? row.phone_number : undefined,
    }));
}

/** "v18 · full · production": version, lineage, label when any - never a UUID. */
function promptVersionLabel(version: { version: number; label: string | null; base: string }): string {
  return [`v${version.version}`, version.base, version.label].filter(Boolean).join(' \u00b7 ');
}

function MediaPreview({ message }: { message: ChatbotConsoleMessage }) {
  if (!message.mediaKind || !message.mediaUrl) return null;
  if (message.mediaKind === 'image') {
    return (
      <a href={message.mediaUrl} target="_blank" rel="noreferrer noopener" className="mb-1.5 block">
        {/* A local blob: URL, not a remote one - next/image cannot optimise it, so a
            plain img is correct here rather than a config exception. */}
        <img src={message.mediaUrl} alt="Attached" className="max-h-48 rounded-lg object-cover" />
      </a>
    );
  }
  return (
    <audio controls src={message.mediaUrl} className="mb-1.5 h-9 max-w-full">
      Your browser cannot play this audio.
    </audio>
  );
}

function MessageBubble({
  message,
  onQuickReply,
  onRetryMedia,
}: {
  message: ChatbotConsoleMessage;
  onQuickReply: (text: string) => void;
  onRetryMedia: (media: ConsoleMediaInput) => void;
}) {
  const isUser = message.role === 'user';
  const isMediaPending = message.mediaStatus === 'pending';
  return (
    <div className={cn('flex', isUser ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[85%] rounded-2xl px-3 py-2.5 text-sm leading-relaxed shadow-sm',
          isUser ? 'bg-primary/15 text-foreground' : 'bg-muted text-foreground',
        )}
      >
        {isUser && message.quotedText ? (
          <p className="mb-1 line-clamp-3 break-words border-l-2 border-border pl-2 text-xs text-muted-foreground">
            {message.quotedText}
          </p>
        ) : null}
        <MediaPreview message={message} />
        <p
          className={cn(
            'whitespace-pre-wrap break-words',
            !isUser && (isMediaPending || message.mediaStatus) && 'italic text-muted-foreground',
          )}
        >
          {isMediaPending ? <Loader2 className="mr-1 inline size-3.5 animate-spin" /> : null}
          {isUser ? message.text : <WhatsAppText text={message.text} />}
        </p>
        {message.attachments && message.attachments.length > 0 ? (
          <TurnAttachments attachments={message.attachments} />
        ) : null}
        {message.mediaStatus === 'failed' && message.mediaRetry ? (
          <div className="mt-1.5">
            <button
              type="button"
              onClick={() => onRetryMedia(message.mediaRetry as ConsoleMediaInput)}
              className="rounded-full border bg-secondary px-3 py-1 text-xs hover:bg-secondary/80"
            >
              Retry
            </button>
          </div>
        ) : null}
        {message.branchKind || (!isUser && message.promptVersion != null) ? (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {message.branchKind ? (
              <Badge variant="secondary" size="sm">
                {message.branchKind}
              </Badge>
            ) : null}
            {!isUser && message.promptVersion != null ? (
              <span
                className="rounded-full border border-border/60 px-1.5 py-0.5 text-2xs leading-none text-muted-foreground"
                title="Parser prompt version this reply ran"
                data-testid="chatbot-console-prompt-pill"
              >
                v{message.promptVersion}
              </span>
            ) : null}
            {message.turnId ? (
              <Link
                href={`/system-management/chat-history?turn=${encodeURIComponent(message.turnId)}`}
                target="_blank"
                rel="noreferrer noopener"
                className="text-xs text-muted-foreground underline underline-offset-2 hover:text-primary"
              >
                trace
              </Link>
            ) : null}
          </div>
        ) : null}
        {!isUser && message.routingLine ? (
          <p
            className="mt-1 break-words text-xs text-muted-foreground"
            data-testid="chatbot-console-routing-line"
          >
            {message.routingLine}
          </p>
        ) : null}
        {message.quickReplies && message.quickReplies.length > 0 ? (
          <div className="mt-2 flex flex-wrap gap-2">
            {message.quickReplies.map((chip, index) => (
              <button
                key={`${index}-${chip}`}
                type="button"
                onClick={() => onQuickReply(chip)}
                className="rounded-full border bg-secondary px-3 py-1 text-xs hover:bg-secondary/80"
                title={chip}
              >
                {chip}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex justify-start" data-testid="chatbot-console-typing">
      <div className="rounded-2xl bg-muted px-3 py-2.5 shadow-sm">
        <div className="flex items-center gap-1">
          <span className="size-2 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:-0.3s]" />
          <span className="size-2 animate-bounce rounded-full bg-muted-foreground/70 [animation-delay:-0.15s]" />
          <span className="size-2 animate-bounce rounded-full bg-muted-foreground/70" />
        </div>
      </div>
    </div>
  );
}

export default function ChatbotConsole() {
  const {
    contactId,
    contactLabel,
    setContact,
    promptVersionId,
    setPromptVersionId,
    promptVersions,
    promptVersionsLoading,
    messages,
    sending,
    sendText,
    sendQuickReply,
    sendMedia,
    retryMedia,
    reset,
  } = useChatbotConsole();

  const [draft, setDraft] = useState('');
  const [replyToId, setReplyToId] = useState('');
  const threadEndRef = useRef<HTMLDivElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages, sending]);

  const promptVersionOptions = useMemo<SearchableSelectOption[]>(
    () =>
      promptVersions.map((version) => ({
        value: version.id,
        label: promptVersionLabel(version),
      })),
    [promptVersions],
  );

  // The version actually in effect, spelled out next to the composer - "Parser v38", never
  // the raw id. `promptVersionId === null` after the versions have loaded is an explicit
  // in-session pick of the live production label (the select's "clear" option), not a
  // loading state.
  const activePromptVersion = promptVersions.find((v) => v.id === promptVersionId);
  const activePromptLabel = promptVersionsLoading
    ? null
    : activePromptVersion
      ? `Parser v${activePromptVersion.version}`
      : promptVersions.length > 0
        ? 'Parser live (production)'
        : null;

  // The bubbles a message can quote: bot replies of a real turn and the customer's own sent messages
  // (never the greeting lines), newest first.
  const quotableBubbles = useMemo(
    () => messages.filter((m) => (m.role === 'bot' ? Boolean(m.turnId) : Boolean(m.messageId))).reverse(),
    [messages],
  );
  const replyToOptions = useMemo<SearchableSelectOption[]>(
    () =>
      quotableBubbles.map((m) => ({
        value: m.id,
        label: m.text.length > REPLY_TO_LABEL_MAX ? `${m.text.slice(0, REPLY_TO_LABEL_MAX)}...` : m.text,
      })),
    [quotableBubbles],
  );

  const handleSend = () => {
    if (!draft.trim() || sending) return;
    const text = draft;
    const quoted = quotableBubbles.find((m) => m.id === replyToId);
    setDraft('');
    setReplyToId('');
    void sendText(
      text,
      quoted
        ? quoted.role === 'user'
          ? { id: quoted.messageId as string, text: quoted.text, source: 'contact' }
          : { id: quoted.id, text: quoted.text, source: 'n8n' }
        : null,
    );
  };

  const handleFilePicked = (file: File | null) => {
    if (!file) return;
    const kind = file.type.startsWith('audio/') ? 'audio' : 'image';
    const caption = draft;
    setDraft('');
    void sendMedia(kind, file, caption);
  };

  const voice = useVoiceRecorder({
    onClip: (file) => {
      const caption = draft;
      setDraft('');
      void sendMedia('audio', file, caption);
    },
  });

  return (
    <div
      className="flex min-h-[70dvh] flex-col gap-3 lg:h-[calc(100dvh-13rem)]"
      data-testid="chatbot-console"
    >
      {/* Header row: contact, prompt version, reset. */}
      <div className="flex flex-wrap items-end gap-3 border-b pb-3">
        <div className="min-w-[220px] flex-1 sm:flex-none">
          <Label className="mb-1 block text-xs text-muted-foreground">Contact</Label>
          <SearchableSelect
            value={contactId ?? ''}
            onChange={() => {
              /* handled via onOptionChange, which also carries the label */
            }}
            onOptionChange={(option) => {
              if (option) setContact(option.value, option.label);
            }}
            fetchOptions={fetchContactOptions}
            selectedOption={contactId ? { value: contactId, label: contactLabel } : undefined}
            placeholder="Pick a contact..."
            size="sm"
          />
        </div>
        <div className="min-w-[220px] flex-1 sm:flex-none">
          <Label className="mb-1 block text-xs text-muted-foreground">Prompt version</Label>
          <SearchableSelect
            value={promptVersionId ?? ''}
            onChange={(value) => setPromptVersionId(value || null)}
            options={promptVersionOptions}
            clearable
            placeholder="Live (production label)"
            size="sm"
          />
        </div>
        <Button type="button" variant="outline" size="sm" onClick={reset} className="gap-1.5">
          <RotateCcw className="size-3.5" />
          Reset
        </Button>
      </div>

      {/* Thread - the ONLY scrolling region. */}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1" data-testid="chatbot-console-thread">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} onQuickReply={sendQuickReply} onRetryMedia={retryMedia} />
        ))}
        {sending ? <TypingIndicator /> : null}
        <div ref={threadEndRef} />
      </div>

      {/* Composer - pinned at the bottom. */}
      <div className="border-t pt-3">
        {activePromptLabel ? (
          <div
            className="mb-1.5 text-2xs text-muted-foreground"
            title="The parser prompt version this turn will run"
            data-testid="chatbot-console-active-prompt"
          >
            {activePromptLabel}
          </div>
        ) : null}
        {quotableBubbles.length > 0 ? (
          <div className="mb-2">
            <SearchableSelect
              value={replyToId}
              onChange={setReplyToId}
              options={replyToOptions}
              clearable
              placeholder="Reply to"
              aria-label="Reply to"
              size="sm"
            />
          </div>
        ) : null}
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*,audio/*"
          aria-label="Attach image or audio"
          className="hidden"
          onChange={(e) => {
            handleFilePicked(e.target.files?.[0] ?? null);
            e.target.value = '';
          }}
        />
        <div className="flex items-end gap-2">
          <Button
            type="button"
            variant="outline"
            size="icon"
            disabled={sending || voice.recording}
            onClick={() => fileInputRef.current?.click()}
            aria-label="Attach image or audio"
          >
            <Paperclip className="size-4" />
          </Button>
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder="Ask, or attach voice/image"
            rows={2}
            disabled={sending || voice.recording}
            className="min-w-0 flex-1 resize-none"
          />
          {voice.available ? (
            <Button
              type="button"
              variant={voice.recording ? 'destructive' : 'outline'}
              size="icon"
              disabled={sending && !voice.recording}
              aria-label={voice.recording ? `Recording, ${formatElapsed(voice.seconds)}, release to send` : 'Hold to record a voice message'}
              title={voice.reason ?? undefined}
              onPointerDown={(e) => {
                e.preventDefault();
                void voice.start();
              }}
              onPointerUp={() => voice.recording && voice.stop()}
              onPointerLeave={() => voice.recording && voice.cancel()}
            >
              {voice.recording ? (
                <span className="flex items-center gap-1 text-2xs tabular-nums">
                  <Square className="size-3.5" />
                  {formatElapsed(voice.seconds)}
                </span>
              ) : (
                <Mic className="size-4" />
              )}
            </Button>
          ) : null}
          <Button
            type="button"
            size="icon"
            disabled={sending || !draft.trim()}
            onClick={handleSend}
            aria-label={sending ? 'Sending' : 'Send message'}
          >
            {sending ? <Loader2 className="size-4 animate-spin" /> : <SendHorizonal className="size-4" />}
          </Button>
        </div>
      </div>
    </div>
  );
}
