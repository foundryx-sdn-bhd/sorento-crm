'use client';

import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { Textarea } from '@/components/ui/textarea';
import { cn } from '@/lib/utils';
import { FindBar, isFindChord } from './FindBar';
import { useFindController } from './useFindController';

type TextareaProps = React.ComponentProps<typeof Textarea>;

// Computed-style props copied from the textarea onto the highlight backdrop so
// its text lays out identically (font, wrapping, padding).
const METRIC_PROPS = [
  'fontFamily',
  'fontSize',
  'fontWeight',
  'fontStyle',
  'lineHeight',
  'letterSpacing',
  'textTransform',
  'wordSpacing',
  'textIndent',
  'tabSize',
  'paddingTop',
  'paddingRight',
  'paddingBottom',
  'paddingLeft',
] as const;

/**
 * A textarea with a built-in find widget (Cmd/Ctrl+F while focused). Matches are
 * highlighted via a backdrop overlay positioned exactly behind the textarea
 * (the textarea's background is transparent, so the backdrop's coloured match
 * rectangles show through under the real editable text). Shows a match count and
 * next/previous navigation. Mirrors the n8n prompt-input find.
 */
export function SearchableTextarea({
  value,
  className,
  containerClassName,
  onScroll,
  ...rest
}: TextareaProps & { value: string; containerClassName?: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const backdropRef = useRef<HTMLDivElement>(null);
  const textValue = typeof value === 'string' ? value : String(value ?? '');
  const find = useFindController(textValue);
  const { open, matches, activeIndex, jumpSeq, openFind, close, next, prev } = find;
  const textValueRef = useRef(textValue);
  textValueRef.current = textValue;

  // Overlay the backdrop exactly on the textarea's padding box (excludes border
  // + scrollbar, so wrapping matches even once a vertical scrollbar appears).
  const syncMetrics = useCallback(() => {
    const ta = ref.current;
    const bd = backdropRef.current;
    if (!ta || !bd) return;
    const cs = window.getComputedStyle(ta);
    for (const p of METRIC_PROPS) {
      bd.style[p] = cs[p];
    }
    bd.style.boxSizing = 'border-box';
    bd.style.border = 'none';
    bd.style.top = cs.borderTopWidth;
    bd.style.left = cs.borderLeftWidth;
    bd.style.width = `${ta.clientWidth}px`;
    bd.style.height = `${ta.clientHeight}px`;
    bd.scrollTop = ta.scrollTop;
    bd.scrollLeft = ta.scrollLeft;
  }, []);

  const syncScroll = useCallback(() => {
    const ta = ref.current;
    const bd = backdropRef.current;
    if (ta && bd) {
      bd.scrollTop = ta.scrollTop;
      bd.scrollLeft = ta.scrollLeft;
    }
  }, []);

  useLayoutEffect(syncMetrics);

  useEffect(() => {
    const ta = ref.current;
    if (!ta || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => syncMetrics());
    ro.observe(ta);
    return () => ro.disconnect();
  }, [syncMetrics]);

  // Select + scroll to the active match on an explicit find navigation only
  // (jumpSeq). Keying this on the text or the match list re-selected the active
  // match on every keystroke, so typing while the bar was open replaced that
  // match instead of editing at the caret (owner bug, 30 Sep 2026).
  const latest = useRef({ matches, activeIndex, open });
  latest.current = { matches, activeIndex, open };
  useEffect(() => {
    const { matches: ms, activeIndex: idx, open: isOpen } = latest.current;
    const el = ref.current;
    if (!el || !isOpen || idx < 0 || !ms[idx]) return;
    const { start, end } = ms[idx];
    try {
      el.setSelectionRange(start, end);
    } catch {
      /* selection can throw on detached nodes - ignore */
    }
    // Measure the active <mark> in the backdrop: it lays out exactly like the
    // textarea, so its offsetTop accounts for wrapped lines (counting '\n' did
    // not, and long wrapped prompt lines scrolled to the wrong place).
    const mark = backdropRef.current?.querySelector<HTMLElement>('mark[data-active="true"]');
    const target = mark
      ? mark.offsetTop
      : (textValueRef.current.slice(0, start).split('\n').length - 1) *
        (parseFloat(window.getComputedStyle(el).lineHeight) || 16);
    el.scrollTop = Math.max(0, target - el.clientHeight / 2);
    syncScroll();
  }, [jumpSeq, syncScroll]);

  // Highlighted segments (only computed while the find bar is open with a query).
  const segments = useMemo(() => {
    if (!open || matches.length === 0) return null;
    const out: { text: string; match: number }[] = [];
    let cursor = 0;
    matches.forEach((m, i) => {
      if (m.start > cursor) out.push({ text: textValue.slice(cursor, m.start), match: -1 });
      out.push({ text: textValue.slice(m.start, m.end), match: i });
      cursor = m.end;
    });
    if (cursor < textValue.length) out.push({ text: textValue.slice(cursor), match: -1 });
    return out;
  }, [open, matches, textValue]);

  return (
    <div
      className={cn('relative', containerClassName)}
      onKeyDown={(e) => {
        if (isFindChord(e)) {
          e.preventDefault();
          openFind();
        } else if (e.key === 'Escape' && open) {
          e.preventDefault();
          close();
          ref.current?.focus();
        } else if (open && e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
          e.preventDefault();
          if (e.shiftKey) prev();
          else next();
        }
      }}
    >
      <FindBar controller={find} onDismiss={() => ref.current?.focus()} />
      {/* Highlight backdrop - same text metrics as the textarea, text hidden,
          only the <mark> rectangles are visible under the transparent textarea. */}
      <div
        ref={backdropRef}
        aria-hidden
        className="pointer-events-none absolute overflow-hidden whitespace-pre-wrap break-words text-transparent"
      >
        {segments?.map((seg, i) =>
          seg.match === -1 ? (
            <Fragment key={i}>{seg.text}</Fragment>
          ) : (
            <mark
              key={i}
              data-active={seg.match === activeIndex ? 'true' : undefined}
              className={cn(
                'rounded-sm text-transparent',
                seg.match === activeIndex ? 'bg-primary/40' : 'bg-warning/40',
              )}
            >
              {seg.text}
            </mark>
          ),
        )}
      </div>
      <Textarea
        ref={ref}
        value={value}
        onScroll={(e) => {
          syncScroll();
          onScroll?.(e);
        }}
        className={cn('relative bg-transparent', className)}
        {...rest}
      />
    </div>
  );
}
