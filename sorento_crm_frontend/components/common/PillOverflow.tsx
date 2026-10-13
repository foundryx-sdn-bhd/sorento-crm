'use client';

import * as React from 'react';
import {
  Popover,
  PopoverContent,
  PopoverPortal,
  PopoverTrigger,
} from '@/components/ui/popover';
import { cn } from '@/lib/utils';

/**
 * One pill's tone. The six hues `_shared/components/SupplyBar.tsx` already paints a supply
 * bar with (`_shared/lib/supplyVocabulary.ts` COLOURS): own is emerald, the shared/site pool
 * is sky, any borrow is amber, Buy is rose, incoming/SPO is violet. This lives in
 * `components/common`, which does not import a project-sales feature module, so the same six
 * hues are RESTATED below rather than imported - reused, never invented (R-J).
 */
export type PillTone = 'neutral' | 'pool' | 'own' | 'borrow' | 'buy' | 'spo';

export interface PillItem {
  key: string;
  label: string;
  tone?: PillTone;
}

const TONE_CLASS: Record<PillTone, string> = {
  neutral: 'bg-muted text-muted-foreground',
  pool: 'bg-sky-100 text-sky-700',
  own: 'bg-emerald-100 text-emerald-700',
  borrow: 'bg-amber-100 text-amber-700',
  buy: 'bg-rose-100 text-rose-700',
  spo: 'bg-violet-100 text-violet-700',
};

/** The gap between pills, in px - kept as one number so the fit math and the `gap-1` class
 * that draws it can never disagree. */
const GAP_PX = 4;

/**
 * A row of pills that folds whatever does not fit into one "+N" pill (S3b, R-J: "the cell
 * shows as many as its width fits and folds the rest into a '+N' pill; clicking any pill,
 * '+N' included, opens the composition").
 *
 * NO FIXED COUNT. A `ResizeObserver` on the row's own container reacts to the DataGrid column
 * being dragged wider live (`columnsResizable`), the same way `useHorizontalOverflow` reacts to
 * a scroll container's size - the count of pills shown is read off the container's actual
 * width every time it changes, never off a breakpoint or a guess.
 *
 * MEASURED, NOT GUESSED. A hidden row renders every pill once, off-screen but laid out, so
 * `offsetWidth` is the real rendered width in this font at this size - not an estimate that
 * drifts the moment a label gets longer. The visible row and the measuring row share the same
 * pill markup for that reason.
 *
 * A QUANTITY IS NEVER CUT (S3b fix). Pill 0 always renders at its own natural width - never
 * capped below its content to leave room for "+N" beside it. Later pills fold to keep "+N" on
 * the same line; only when pill 0 alone leaves no room does "+N" wrap to a second line
 * (`flex-wrap` on the visible row). Pill 0's text is never the thing that gives.
 *
 * ONE POPOVER FOR THE WHOLE ROW. Every pill, "+N" included, is a plain, unlabelled trigger for
 * the SAME popover (`renderPopover` renders every item, not only the folded ones) - the
 * captain's rule is "clicking any pill ... opens the composition", not "each pill opens its
 * own fact". A single controlled `Popover` wraps the row so any pill's click reaches it.
 *
 * THE CLICK NEVER REACHES THE ROW BEHIND IT. A DataGrid row expands on click and, on the
 * board grid, the whole cell is itself a `<button>` (`FulfilmentBoardMatrix`); a pill's click
 * would otherwise open BOTH the popover and the row/cell underneath it. The outer wrapper
 * stops the click after the inner trigger has already toggled the popover.
 *
 * Pills are `role="button"` `<span>`s, never `<button>`, on purpose: the board grid nests this
 * component INSIDE a `<button>` cell, and a `<button>` cannot legally contain another one (the
 * grid's own annotation comment documents the same rule for a table). One shape, everywhere,
 * rather than a button variant for two call sites and a span variant for the third.
 */
export function PillOverflow({
  items,
  renderPopover,
  ariaLabel,
  className,
  testId,
}: {
  items: PillItem[];
  renderPopover: (items: PillItem[]) => React.ReactNode;
  ariaLabel: string;
  className?: string;
  /**
   * Placed on the VISIBLE pill row only, never on the hidden measuring row beside it - the
   * two necessarily repeat each other's label text (that is how the measurement works), so a
   * plain `getByText` finds both. Scope a query with `within(getByTestId(testId))` to reach
   * only what a person can see.
   */
  testId?: string;
}) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const pillRefs = React.useRef<(HTMLSpanElement | null)[]>([]);
  const overflowRef = React.useRef<HTMLSpanElement | null>(null);
  const [open, setOpen] = React.useState(false);
  const [visibleCount, setVisibleCount] = React.useState(items.length);

  const recompute = React.useCallback(() => {
    const container = containerRef.current;
    if (!container || items.length === 0) return;
    const width = container.clientWidth;
    const pillWidths = pillRefs.current.map((el) => el?.offsetWidth ?? 0);

    let used = 0;
    let count = 0;
    for (let i = 0; i < pillWidths.length; i += 1) {
      const withThis = used + (count > 0 ? GAP_PX : 0) + pillWidths[i];
      // The first pill always shows, WHOLE (AC-3b.4, S3b fix: a quantity is never
      // truncated) - even at a width narrower than the pill itself. Room for "+N" is made
      // below by folding LATER pills, never by capping pill 0's own text.
      if (count === 0 || withThis <= width) {
        used = withThis;
        count += 1;
      } else {
        break;
      }
    }
    // Something folds, so "+N" needs room on the same line: drop pills from the end until it
    // fits, but never pill 0 (its own width is never given up, per the rule above).
    if (count < pillWidths.length) {
      const overflowWidth = overflowRef.current?.offsetWidth ?? 0;
      while (count > 1 && used + GAP_PX + overflowWidth > width) {
        count -= 1;
        used -= GAP_PX + pillWidths[count];
      }
    }
    setVisibleCount(count);
  }, [items.length]);

  // Content-based signature, not the `items` array itself (nit, code review round 3 batch
  // 2): every call site builds `items` fresh each render, so depending on the array
  // re-measured the DOM on every unrelated re-render, not only when a pill's key/label or
  // the count actually changed. Joins the two fields `recompute` cares about into a string,
  // which the dependency array below compares by VALUE the way a primitive does.
  const itemsSignature = items
    .map((item) => `${item.key} ${item.label}`)
    .join('');

  React.useLayoutEffect(() => {
    pillRefs.current = pillRefs.current.slice(0, items.length);
    recompute();
  }, [itemsSignature, items.length, recompute]);

  React.useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => recompute());
    observer.observe(container);
    return () => observer.disconnect();
  }, [recompute]);

  if (items.length === 0) return null;

  const overflowCount = items.length - visibleCount;

  return (
    <div
      className={cn('relative min-w-0', className)}
      // Stopped here, ONE ancestor above the trigger below, and ONLY when the click actually
      // landed ON A PILL: a native click on a pill has already reached the trigger and
      // toggled the popover by the time bubbling gets this far, and stopping unconditionally
      // swallowed a click on the row's own padding too - at a width narrow enough that the
      // strip fills the cell, that was most of the cell's own click target (the board grid's
      // whole-cell button never opened). A click that missed every pill is left to bubble on
      // to whatever this strip sits inside.
      onClick={(event) => {
        if ((event.target as HTMLElement).closest('[role="button"]')) {
          event.stopPropagation();
        }
      }}
    >
      {/* The hidden measuring row: every pill, laid out in the same markup, off-screen.
          `tabIndex` is never set on these - `aria-hidden` alone does not remove a focusable
          element from the tab order, and a phantom stop here would be reachable but invisible. */}
      <div
        aria-hidden
        className="pointer-events-none absolute start-0 top-0 flex items-center gap-1 overflow-hidden opacity-0"
        style={{ height: 0 }}
      >
        {items.map((item, index) => (
          <Pill
            key={item.key}
            label={item.label}
            tone={item.tone}
            measureRef={(el) => {
              pillRefs.current[index] = el;
            }}
          />
        ))}
        {/* The widest "+N" this row can show, so the fit above can leave room for it. */}
        <Pill
          label={`+${items.length}`}
          tone="neutral"
          measureRef={(el) => {
            overflowRef.current = el;
          }}
        />
      </div>

      <Popover open={open} onOpenChange={setOpen}>
        {/* asChild: Radix attaches its own click-to-toggle handler straight to this div, so a
            native click on any pill inside it - mouse or the synthetic one keyboard activation
            dispatches - opens the SAME popover without this component tracking `open` itself
            beyond what `Popover` already does. */}
        <PopoverTrigger asChild>
          <div
            ref={containerRef}
            role="group"
            aria-label={ariaLabel}
            data-testid={testId}
            // `flex-wrap` (S3b fix): pill 0 never has its text capped to make room for
            // "+N" - when the two cannot sit on one line, "+N" wraps to its own line
            // instead. `overflow-hidden` + `min-w-0` are the Phase 1 hit-test fix, kept:
            // they stop an oversized pill 0 from bleeding into whatever sits after this
            // strip (the cell's info/trail icon buttons), they just no longer do it by
            // shrinking the pill's own text below its content.
            className="flex min-w-0 flex-wrap items-center gap-1 overflow-hidden"
          >
            {items.slice(0, visibleCount).map((item) => (
              <Pill
                key={item.key}
                label={item.label}
                tone={item.tone}
                interactive
              />
            ))}
            {overflowCount > 0 && (
              <Pill label={`+${overflowCount}`} tone="neutral" interactive />
            )}
          </div>
        </PopoverTrigger>
        <PopoverPortal>
          <PopoverContent
            align="start"
            collisionPadding={8}
            data-testid={testId ? `${testId}-popover` : undefined}
            className="w-[320px] max-w-[92vw] p-0"
            // Portalled out of whatever dialog or scroll container holds this cell so it is
            // never clipped, which puts it outside that dialog's focus scope: taking focus on
            // open reads to a Dialog as focus leaving and closes it (the same reason
            // `BoardTrailPopover` prevents it). Read-only content, so it does not need the
            // focus.
            onOpenAutoFocus={(event) => event.preventDefault()}
            // The portal moves the content to the document root in the DOM, but a React
            // event still bubbles up the COMPONENT tree: a click on a listed item reached
            // the DataGrid row's own handler and opened the row (`rowHref` / `onRowClick`)
            // behind the popover. The wrapper above only stops clicks that land on a pill,
            // so the content stops its own here, the way the other "+N" popovers do.
            onClick={(event) => event.stopPropagation()}
          >
            <div className="max-h-[60vh] overflow-auto p-3 text-xs">
              {renderPopover(items)}
            </div>
          </PopoverContent>
        </PopoverPortal>
      </Popover>
    </div>
  );
}

/**
 * The plain popover body a label-only cell wants: every item on its own line. The People
 * pages (Roles, Access types), the role list's Permissions and the other label-list cells
 * pass this as `renderPopover` so the same fold reads the same way on every list; a cell
 * whose items carry more than a label (a link, a tone legend) renders its own.
 */
export function PillOverflowList({ items }: { items: PillItem[] }) {
  return (
    <ul className="flex flex-col gap-1 text-sm">
      {items.map((item) => (
        <li key={item.key}>{item.label}</li>
      ))}
    </ul>
  );
}

/**
 * One pill. `interactive` pills are keyboard-reachable and dispatch a real `click()` of
 * themselves on Enter/Space, which then bubbles to the `PopoverTrigger` above exactly like a
 * mouse click would - one activation path for both, rather than a second one only keyboard
 * users take. The hidden measuring copies pass neither `interactive` nor an activation path:
 * they exist to be measured, not pressed.
 */
function Pill({
  label,
  tone = 'neutral',
  interactive = false,
  measureRef,
}: {
  label: string;
  tone?: PillTone;
  interactive?: boolean;
  measureRef?: React.Ref<HTMLSpanElement>;
}) {
  return (
    <span
      ref={measureRef}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      onKeyDown={
        interactive
          ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                event.currentTarget.click();
              }
            }
          : undefined
      }
      className={cn(
        'inline-flex h-5 shrink-0 items-center truncate rounded-full px-1.5 text-2xs font-medium tabular-nums',
        interactive &&
          'cursor-pointer outline-hidden focus-visible:ring-2 focus-visible:ring-ring',
        TONE_CLASS[tone],
      )}
    >
      {label}
    </span>
  );
}
