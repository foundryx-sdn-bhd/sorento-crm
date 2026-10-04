'use client';

/**
 * Internal-note composer with an @mention typeahead (UAC AC-L1).
 *
 * Deliberately a separate surface from `SharedConversationComposer`: a note is
 * not a message. It has no 24h window, no template fallback, no attachments and
 * no send path to the contact - wiring those into the message composer would put
 * one keystroke between "internal note" and "message the customer". The amber
 * treatment is the same signal, made visual.
 *
 * Mentions are stored as CRM user ids; the body keeps the readable
 * "@Display Name" inline so the note still reads correctly wherever it is shown
 * (including Respond's inbox, where mapped users become {{@user.<id>}}).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2, StickyNote } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { getUserLookup, type UserLookupItem } from '@/services/userSelectService';

interface InternalCommentComposerProps {
  /** Writes the note. Rejects with an Error whose message is shown by the caller. */
  onSubmit: (payload: { body: string; mentionedUserIds: string[] }) => Promise<unknown>;
  disabled?: boolean;
  disabledMessage?: string;
  placeholder?: string;
}

/** Mentions the author actually picked, kept so the ids survive editing. */
interface PickedMention {
  id: string;
  name: string;
}

const MENTION_DEBOUNCE_MS = 200;
/** Longest partial name we keep looking up before assuming it is prose. */
const MAX_MENTION_QUERY = 30;

function displayNameOf(user: UserLookupItem): string {
  return (user.name || '').trim();
}

/**
 * The "@..." fragment being typed immediately before the caret, or null.
 *
 * A mention only starts at the beginning of the text or after whitespace, so an
 * email address in the note never opens the typeahead. Spaces are allowed
 * INSIDE the fragment because real names have them ("@Team Lead"), bounded by
 * `MAX_MENTION_QUERY` so a whole paragraph after a stray "@" is not a query.
 */
export function activeMentionFragment(
  text: string,
  caret: number,
): { start: number; query: string } | null {
  const upToCaret = text.slice(0, caret);
  const at = upToCaret.lastIndexOf('@');
  if (at < 0) return null;
  const before = at === 0 ? '' : upToCaret[at - 1];
  if (before && !/\s/.test(before)) return null;
  const query = upToCaret.slice(at + 1);
  if (query.includes('\n')) return null;
  if (query.length > MAX_MENTION_QUERY) return null;
  return { start: at, query };
}

/**
 * The suggestion's name with the typed fragment in bold. "zhi" legitimately
 * matches both "Chong Zhi Xiu" and "Tay Zhi Yang"; showing WHERE it matched is
 * what tells the reader to keep typing rather than pick the first row.
 */
function MatchedName({ name, query }: { name: string; query: string }) {
  const q = query.trim();
  if (!q) return <>{name}</>;
  const at = name.toLowerCase().indexOf(q.toLowerCase());
  if (at < 0) return <>{name}</>;
  return (
    <>
      {name.slice(0, at)}
      <strong className="font-semibold">{name.slice(at, at + q.length)}</strong>
      {name.slice(at + q.length)}
    </>
  );
}

export default function InternalCommentComposer({
  onSubmit,
  disabled = false,
  disabledMessage = 'Notes are not available on this ticket.',
  placeholder = 'Write an internal note. Type @ to mention someone.',
}: InternalCommentComposerProps) {
  const [body, setBody] = useState('');
  const [picked, setPicked] = useState<PickedMention[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fragment, setFragment] = useState<{ start: number; query: string } | null>(null);
  const [debouncedQuery, setDebouncedQuery] = useState<string | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (fragment === null) {
      setDebouncedQuery(null);
      return;
    }
    const handle = setTimeout(() => setDebouncedQuery(fragment.query), MENTION_DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [fragment]);

  // The shared people lookup: open to every signed-in staff user (never-stuck L10).
  const {
    data: candidates = [],
    isFetching,
    isError: lookupFailed,
    error: lookupError,
  } = useQuery({
    queryKey: ['comment-mention-users', debouncedQuery],
    queryFn: () => getUserLookup({ query: debouncedQuery || undefined }),
    enabled: debouncedQuery !== null,
    staleTime: 30_000,
  });

  const suggestions = useMemo(() => candidates.slice(0, 8), [candidates]);
  // How many matches the 8-row window hides. Without the footer this renders,
  // a reader assumes the list IS everyone and scrolls for a name that sorts
  // past the window instead of typing it (it cost a real mention).
  const hiddenCount = Math.max(0, candidates.length - suggestions.length);
  // A failed lookup stays OPEN with its reason (same as the snippet picker):
  // silently closing reads as "@ does not work here".
  const typeaheadOpen =
    fragment !== null && (isFetching || lookupFailed || suggestions.length > 0);

  useEffect(() => {
    setActiveIndex(0);
  }, [debouncedQuery]);

  const syncFragment = useCallback((value: string, caret: number) => {
    setFragment(activeMentionFragment(value, caret));
  }, []);

  const handleChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = event.target.value;
    setBody(value);
    if (error) setError(null);
    syncFragment(value, event.target.selectionStart ?? value.length);
  };

  const insertMention = useCallback(
    (user: UserLookupItem) => {
      const name = displayNameOf(user);
      if (!fragment || !name) return;
      const caret = textareaRef.current?.selectionStart ?? body.length;
      const next = `${body.slice(0, fragment.start)}@${name} ${body.slice(caret)}`;
      setBody(next);
      setPicked((prev) =>
        prev.some((m) => m.id === user.id) ? prev : [...prev, { id: user.id, name }],
      );
      setFragment(null);
      setDebouncedQuery(null);
      const nextCaret = fragment.start + name.length + 2;
      queueMicrotask(() => {
        const node = textareaRef.current;
        node?.focus();
        node?.setSelectionRange?.(nextCaret, nextCaret);
      });
    },
    [body, fragment],
  );

  // Only the mentions still spelled out in the body count: deleting "@Team Lead"
  // must not keep notifying them.
  const mentionedUserIds = useMemo(
    () => picked.filter((m) => body.includes(`@${m.name}`)).map((m) => m.id),
    [picked, body],
  );

  const canSubmit = body.trim().length > 0 && !saving && !disabled;

  const submit = async () => {
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      await onSubmit({ body: body.trim(), mentionedUserIds });
      setBody('');
      setPicked([]);
      setFragment(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add the note');
    } finally {
      setSaving(false);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (typeaheadOpen && suggestions.length > 0) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        setActiveIndex((i) => (i + 1) % suggestions.length);
        return;
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault();
        setActiveIndex((i) => (i - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        insertMention(suggestions[activeIndex]);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        setFragment(null);
        return;
      }
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      void submit();
    }
  };

  if (disabled) {
    return <p className="text-xs text-muted-foreground">{disabledMessage}</p>;
  }

  return (
    <div className="space-y-2" data-testid="internal-comment-composer">
      <div className="relative rounded-md border border-amber-300 bg-amber-50 p-2 dark:border-amber-700 dark:bg-amber-950/40">
        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-amber-800 dark:text-amber-300">
          <StickyNote className="size-3.5" />
          Internal note
        </div>
        <Textarea
          ref={textareaRef}
          data-testid="internal-comment-input"
          placeholder={placeholder}
          value={body}
          rows={3}
          disabled={saving}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onClick={(e) =>
            syncFragment(
              (e.target as HTMLTextAreaElement).value,
              (e.target as HTMLTextAreaElement).selectionStart ?? 0,
            )
          }
          className="resize-none bg-background"
        />

        {typeaheadOpen && (
          <div
            data-testid="mention-typeahead"
            role="listbox"
            // Opens UPWARD, like the snippet picker: this composer sits at the
            // bottom of the drawer, and downward is off the screen at 375px.
            className="absolute inset-x-2 bottom-full z-20 mb-1 max-h-48 overflow-y-auto rounded-md border bg-popover p-1 shadow-md"
          >
            {isFetching && suggestions.length === 0 && (
              <div className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" />
                Searching people
              </div>
            )}
            {!isFetching && lookupFailed && (
              <div
                className="px-2 py-1.5 text-xs text-destructive"
                data-testid="mention-typeahead-error"
              >
                {lookupError instanceof Error ? lookupError.message : 'Failed to load people'}
              </div>
            )}
            {suggestions.map((user, index) => (
              <button
                key={user.id}
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                // onMouseDown, not onClick: the textarea's blur would otherwise
                // close the typeahead before the click lands.
                onMouseDown={(e) => {
                  e.preventDefault();
                  insertMention(user);
                }}
                className={`block w-full truncate rounded px-2 py-1.5 text-left text-sm hover:bg-accent ${
                  index === activeIndex ? 'bg-accent' : ''
                }`}
                title={displayNameOf(user)}
              >
                <MatchedName name={displayNameOf(user)} query={debouncedQuery ?? ''} />
              </button>
            ))}
            {hiddenCount > 0 && (
              <div
                className="px-2 py-1.5 text-xs text-muted-foreground"
                data-testid="mention-typeahead-more"
              >
                {hiddenCount} more. Keep typing to narrow.
              </div>
            )}
          </div>
        )}
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}

      <div className="flex justify-end">
        <Button
          type="button"
          size="sm"
          disabled={!canSubmit}
          onClick={() => void submit()}
          className="bg-amber-600 text-white hover:bg-amber-700"
        >
          {saving ? 'Adding…' : 'Add note'}
        </Button>
      </div>
    </div>
  );
}
