'use client';

import { parseWhatsAppText } from '@/lib/whatsappText';
import { splitHighlightSegments } from '@/lib/textHighlight';

/**
 * A bot reply rendered the way the handset renders it: WhatsApp `*bold*`,
 * `_italic_`, `~strike~` and fenced monospace applied, bare URLs clickable.
 * The parse is `lib/whatsappText.ts` (the same one the Conversations inbox
 * uses); this is only the React half. Plain React text nodes throughout, so
 * literal markup in a reply stays text.
 *
 * `term` marks a search hit inside whatever style its segment ended up with.
 * Markup is parsed first and the hit split second, so a term inside a `*bold*`
 * run never cuts the pair in half and leaves the asterisks on screen.
 */
export function WhatsAppText({ text, term = '' }: { text: string; term?: string }) {
  return (
    <>
      {parseWhatsAppText(text).map((segment, i) => {
        const marked = <Highlighted text={segment.text} term={term} />;

        if (segment.code) {
          return (
            <code
              key={i}
              className="block whitespace-pre-wrap rounded bg-black/5 px-1.5 py-1 font-mono text-xs dark:bg-white/10"
            >
              {marked}
            </code>
          );
        }

        let node: React.ReactNode = marked;
        if (segment.strike) node = <s>{node}</s>;
        if (segment.italic) node = <em>{node}</em>;
        if (segment.bold)
          node = <strong className="font-semibold">{node}</strong>;

        if (segment.href) {
          return (
            <a
              key={i}
              href={segment.href}
              target="_blank"
              rel="noopener noreferrer"
              className="break-all underline underline-offset-2 hover:opacity-80"
            >
              {node}
            </a>
          );
        }
        return <span key={i}>{node}</span>;
      })}
    </>
  );
}

function Highlighted({ text, term }: { text: string; term: string }) {
  if (!term.trim()) return <>{text}</>;
  return (
    <>
      {splitHighlightSegments(text, term).map((segment, i) =>
        segment.match ? (
          <mark key={i} className="rounded-sm bg-amber-200 text-inherit">
            {segment.text}
          </mark>
        ) : (
          <span key={i}>{segment.text}</span>
        ),
      )}
    </>
  );
}
