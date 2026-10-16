'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { TurnAttachments } from '@/components/chatbot/TurnAttachments';
import { WhatsAppText } from '@/components/chatbot/WhatsAppText';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import MessageBubbleActions from '@/components/common/conversation/MessageBubbleActions';
import QuotedContextBlock from '@/components/common/conversation/QuotedContextBlock';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { splitQuotedPrefix } from '@/lib/respondIoChatRender';
import { stripWhatsAppMarkup } from '@/lib/whatsappText';
import type { ChatMessageRow } from '../types/chatHistory.types';
import type { ChatbotTurn, ChatbotTurnMedia } from '../types/chatbotTurn.types';
import { isMediaDenied, mediaReadCount, turnAttachments } from '../turnPresentation';
import { StateTracePanel } from './StateTracePanel';
import { TurnPanel } from './TurnPanel';

interface ChatTranscriptProps {
  messages: ChatMessageRow[];
  isLoading?: boolean;
  /** Message to centre on and ring-highlight when there is no active search. */
  anchorId?: number | null;
  emptyText?: string;
  /** The turn recorded for each incoming message, keyed by respond message id. */
  turnsByMessageId?: Map<string, ChatbotTurn>;
  /** AC-255: show only the incoming messages whose turn failed, plus their replies. */
  failedTurnsOnly?: boolean;
  /** When set, Retry is disabled everywhere in this transcript, with this as the reason. */
  retryUnavailableReason?: string | null;
}

function latencyVariant(seconds: number): string {
  return seconds > 30 ? 'destructive' : seconds > 10 ? 'warning' : 'success';
}

function fmtLatency(seconds: number): string {
  return seconds < 1 ? `${Math.round(seconds * 1000)}ms` : `${seconds.toFixed(1)}s`;
}

export function ChatTranscript({
  messages,
  isLoading,
  anchorId,
  emptyText,
  turnsByMessageId,
  failedTurnsOnly = false,
  retryUnavailableReason = null,
}: ChatTranscriptProps) {
  const [term, setTerm] = useState('');
  const [activeMatch, setActiveMatch] = useState(0);
  // The image lightbox: one shared instance for the whole transcript (the same primitive
  // every other CRM attachment viewer uses), opened with whichever bubble's photo was
  // clicked rather than one modal per message.
  const [previewItem, setPreviewItem] = useState<AttachmentPreviewItem | null>(null);
  const turnFor = (m: ChatMessageRow) =>
    m.message_id ? turnsByMessageId?.get(m.message_id) : undefined;
  /** The turn's media, unless a `media_denied` turn stopped before any of it could be
   *  shown (AC-1846). */
  const mediaFor = (turn: ChatbotTurn | undefined): ChatbotTurnMedia | null =>
    turn?.media && !isMediaDenied(turn) ? turn.media : null;

  // AC-255. A failed turn is a PAIR the operator wants to read: what the customer sent
  // and what the bot sent back. Filtering the incoming message alone would leave the
  // error reply out of the transcript, which is half the evidence.
  const visible = useMemo(() => {
    if (!failedTurnsOnly) return messages;
    const failedTurnIds = new Set(
      messages
        .map((m) => turnFor(m))
        .filter((t): t is ChatbotTurn => Boolean(t) && t!.status === 'failed')
        .map((t) => t.id),
    );
    if (failedTurnIds.size === 0) return [];
    const keepTurnIds = new Set(
      messages
        .filter((m) => {
          const turn = turnFor(m);
          return turn ? failedTurnIds.has(turn.id) : false;
        })
        .map((m) => m.turn_id)
        .filter(Boolean) as string[],
    );
    return messages.filter((m) => {
      const turn = turnFor(m);
      if (turn && failedTurnIds.has(turn.id)) return true;
      return Boolean(m.turn_id && keepTurnIds.has(m.turn_id));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, failedTurnsOnly, turnsByMessageId]);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const matchRefs = useRef<(HTMLDivElement | null)[]>([]);

  // Message ids that contain the term, in transcript order - the navigable match set.
  // Matched against the text as RENDERED (WhatsApp markers stripped), so a counted hit
  // always has a mark on screen and a search for `*` does not hit the hidden markup.
  const matches = useMemo(() => {
    const q = term.trim().toLowerCase();
    if (!q) return [] as number[];
    return visible
      .filter((m) => stripWhatsAppMarkup(m.message).toLowerCase().includes(q))
      .map((m) => m.id);
  }, [visible, term]);

  useEffect(() => {
    setActiveMatch(0);
  }, [term]);

  // Jump to the active match as the user steps through them.
  useEffect(() => {
    if (!matches.length) return;
    const el = matchRefs.current[activeMatch];
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }, [activeMatch, matches]);

  // With no search, land on the clicked message (bot replies can be very long).
  useEffect(() => {
    if (isLoading || term || !visible.length) return;
    const id = window.requestAnimationFrame(() =>
      anchorRef.current?.scrollIntoView({ block: 'center' }),
    );
    return () => window.cancelAnimationFrame(id);
  }, [isLoading, term, visible, anchorId]);

  const step = (delta: number) => {
    if (!matches.length) return;
    setActiveMatch((i) => (i + delta + matches.length) % matches.length);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-2 border-b bg-background/80 sticky top-0 z-10">
        <div className="relative flex items-center gap-2">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
          <Input
            className="pl-9 pr-2 h-9"
            placeholder="Search in conversation"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') step(e.shiftKey ? -1 : 1);
            }}
          />
          {term && (
            <div className="flex items-center gap-1 shrink-0">
              <span className="text-xs text-muted-foreground tabular-nums min-w-14 text-right">
                {matches.length ? `${activeMatch + 1} / ${matches.length}` : '0 / 0'}
              </span>
              <Button
                variant="outline"
                size="icon"
                className="size-8"
                disabled={!matches.length}
                onClick={() => step(-1)}
                aria-label="Previous match"
              >
                <ChevronUp className="size-4" />
              </Button>
              <Button
                variant="outline"
                size="icon"
                className="size-8"
                disabled={!matches.length}
                onClick={() => step(1)}
                aria-label="Next match"
              >
                <ChevronDown className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-8"
                onClick={() => setTerm('')}
                aria-label="Clear search"
              >
                <X className="size-4" />
              </Button>
            </div>
          )}
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {isLoading && (
          <>
            <Skeleton className="h-16 w-3/4" />
            <Skeleton className="h-16 w-3/4 ml-auto" />
            <Skeleton className="h-16 w-2/3" />
          </>
        )}

        {!isLoading && visible.length === 0 && (
          <div className="text-center py-12">
            <p className="text-sm text-muted-foreground">
              {failedTurnsOnly
                ? 'No failed turns in this conversation.'
                : (emptyText ?? 'No messages stored for this contact.')}
            </p>
          </div>
        )}

        {!isLoading &&
          visible.map((m) => {
            const outgoing = m.type === 'outgoing';
            const isAnchor = m.id === anchorId;
            const matchIdx = matches.indexOf(m.id);
            const isMatch = matchIdx !== -1;
            const isActive = isMatch && matchIdx === activeMatch;
            const turn = turnFor(m);
            const media = !outgoing ? mediaFor(turn) : null;
            // Voice notes carry no caption on WhatsApp - the transcript is shown instead,
            // never alongside an already-identical caption (plan S1, "never duplicate the
            // caption").
            // #1317: our own reply-to carries its quote as a leading ">" line;
            // only OUTGOING text is ours to split (a contact's ">" is theirs).
            const { quoted, body } = outgoing
              ? splitQuotedPrefix(m.message)
              : { quoted: null, body: m.message };
            const bubbleText =
              media?.modality === 'voice' && !body.trim()
                ? (media.transcript_or_rendered_text ?? '')
                : body;
            return (
              <div
                key={m.id}
                ref={(el) => {
                  if (isAnchor) anchorRef.current = el;
                  if (isMatch) matchRefs.current[matchIdx] = el;
                }}
                className="space-y-1.5"
              >
                <div className={`flex ${outgoing ? 'justify-end' : 'justify-start'}`}>
                {/* #1317: the same bubble menu as every thread surface. Read-only
                    here (no message box), so Copy only and no swipe. */}
                <MessageBubbleActions
                  enabled
                  copyText={bubbleText}
                  className={[
                    'max-w-[85%] rounded-lg px-3 py-2 text-sm',
                    outgoing ? 'bg-primary/10' : 'bg-muted',
                    isActive ? 'ring-2 ring-amber-500' : isAnchor && !term ? 'ring-2 ring-primary' : '',
                  ].join(' ')}
                >
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <span className="text-[11px] text-muted-foreground">
                      {formatDateTimeInMalaysia(m.sent_at)}
                    </span>
                    {m.latency_seconds != null && (
                      <Badge variant={latencyVariant(m.latency_seconds) as never} className="ml-2 shrink-0">
                        {fmtLatency(m.latency_seconds)}
                      </Badge>
                    )}
                  </div>
                  {quoted && (
                    <QuotedContextBlock
                      context={{ messageId: null, excerpt: quoted, sender: null }}
                      agentLabel="Sorento"
                    />
                  )}
                  {media && <MediaBlock media={media} onOpenImage={setPreviewItem} />}
                  {/* WhatsApp markup rendered as the handset shows it: bold labels,
                      clickable links (CHAT-HISTORY-FORMAT). */}
                  <p className="whitespace-pre-wrap break-words">
                    <WhatsAppText text={bubbleText} term={term.trim()} />
                  </p>
                  {m.delivery_status && (
                    <span className="text-[11px] text-muted-foreground">{m.delivery_status}</span>
                  )}
                </MessageBubbleActions>
                </div>
                {/* Diagnosis surface, FULL WIDTH under the bubble rather than inside it:
                    the timeline needs the room, and at 375 a panel squeezed into 85% of
                    the column wraps every sentence to three lines. The turn trace is the
                    one to read; the older raw state trace stays as the fallback for
                    messages that predate the turn engine, and goes when every message has
                    a turn (Phase 2). */}
                {!outgoing && turn && (
                  <div className="max-w-[85%]">
                    <TurnAttachments attachments={turnAttachments(turn)} />
                  </div>
                )}
                {!outgoing &&
                  (turn ? (
                    <TurnPanel turn={turn} retryUnavailableReason={retryUnavailableReason} />
                  ) : (
                    m.state_trace && <StateTracePanel trace={m.state_trace} />
                  ))}
              </div>
            );
          })}
      </div>

      <AttachmentPreviewModal
        open={Boolean(previewItem)}
        onOpenChange={(open) => !open && setPreviewItem(null)}
        items={previewItem ? [previewItem] : []}
      />
    </div>
  );
}

const IMAGE_EXT_BY_MIME: Record<string, string> = {
  'image/jpeg': 'jpg',
  'image/png': 'png',
  'image/webp': 'webp',
  'image/gif': 'gif',
};

/** A filename `AttachmentPreviewModal` recognises as an image, since it reads the
 *  extension off the name rather than `mime_type`. */
function mediaFileName(media: ChatbotTurnMedia): string {
  return `photo.${IMAGE_EXT_BY_MIME[media.mime_type ?? ''] ?? 'jpg'}`;
}

/**
 * The photo or voice note behind an incoming bubble (AC-1842 to AC-1846), above the
 * message text and never duplicating it. An image opens the shared attachment
 * lightbox on click; a voice note is a plain player, its transcript already in the
 * bubble text below.
 */
function MediaBlock({
  media,
  onOpenImage,
}: {
  media: ChatbotTurnMedia;
  onOpenImage: (item: AttachmentPreviewItem) => void;
}) {
  const readCount = mediaReadCount(media);
  if (media.modality === 'voice') {
    return (
      <div className="mb-1.5">
        {media.url && (
          <audio
            controls
            src={media.url}
            aria-label="Voice note"
            className="h-9 w-full max-w-full"
          />
        )}
      </div>
    );
  }

  const caption = media.transcript_or_rendered_text || 'Photo';
  return (
    <div className="mb-1.5 space-y-1">
      {media.url && (
        <button
          type="button"
          onClick={() =>
            onOpenImage({
              id: media.attachment_id ?? 'chat-media',
              // AttachmentPreviewModal picks its slide type off the FILE NAME'S
              // extension, not `mimeType` - a name without one (the caption has
              // no extension) opens as "no inline preview" instead of the image.
              name: mediaFileName(media),
              url: media.url as string,
            })
          }
          aria-label="Open photo"
          className="block w-full overflow-hidden rounded-md"
        >
          {/* Respond.io/CDN media: a plain <img> (next/image needs configured hosts). */}
          <img
            src={media.url}
            alt={caption}
            // Review round nit: the standard scale's `h-28` (7rem/112px), not an
            // arbitrary bracket value - the roster's own fixed-height thumbnail
            // rows (`DriveGridView`) use `aspect-square`/`aspect-[3/4]` instead,
            // which a bubble of unpredictable width would stretch unpredictably.
            className="h-28 w-full object-cover"
            loading="lazy"
          />
        </button>
      )}
      <Badge
        variant={media.truncated ? 'warning' : 'secondary'}
        appearance="light"
        size="sm"
        // AC-1845: "amber when truncated" - the shared `warning` variant already
        // IS this tone (`--color-yellow-*`); this class names it literally so a
        // reader (and this screen's own test) can tell the two chip states apart
        // without needing the design-token indirection.
        className={media.truncated ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300' : undefined}
      >
        Read {readCount} {readCount === 1 ? 'item' : 'items'}
      </Badge>
    </div>
  );
}
