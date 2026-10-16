'use client';

import { useEffect } from 'react';
import { ChevronLeft, ChevronRight, Maximize2, Minimize2 } from 'lucide-react';

import { Button } from '@/components/ui/button';

/**
 * Give a canvas the whole window.
 *
 * The page builder and the room designer are the two screens where the app
 * chrome actively costs you: the top bar, the sidebar, the breadcrumb and the
 * page heading together take roughly 200px of height and 280px of width, and
 * both of those are canvas. Everywhere else in the CRM that chrome is how you
 * navigate; here you are working inside one screen for minutes at a time.
 *
 * Implemented by taking the panel OUT of the page flow rather than by hiding the
 * chrome in place. Hiding the shell means reaching across the app to toggle
 * layout state that every other screen shares, and the first regression lands on
 * a page that has nothing to do with the Dealer Kit. A fixed overlay is local,
 * reversible, and cannot leak.
 *
 * Escape exits, because anything that covers the whole window and traps you is
 * worse than the chrome it removed.
 */
export function FocusToggle({
  active,
  onToggle,
  label = 'canvas',
  className,
  iconClassName = 'size-4',
}: {
  active: boolean;
  onToggle: (next: boolean) => void;
  label?: string;
  /**
   * A host whose own action bar runs smaller than the others (S6) - the
   * request designer's is `h-7 text-xs` throughout - can match its own
   * buttons this way, without changing what every OTHER host already relies
   * on: the default stays `size="sm"` (`h-7 text-xs` already) for the room
   * designer, the page editor and the template page.
   */
  className?: string;
  /** Same idea, for the icon - default matches the other three hosts. */
  iconClassName?: string;
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      aria-pressed={active}
      aria-label={active ? `Exit full screen ${label}` : `Full screen ${label}`}
      onClick={() => onToggle(!active)}
      className={className}
    >
      {active ? (
        <Minimize2 className={iconClassName} />
      ) : (
        <Maximize2 className={iconClassName} />
      )}
      {active ? 'Exit full screen' : 'Full screen'}
    </Button>
  );
}

export function FocusShell({
  active,
  onExit,
  keepMounted = false,
  children,
}: {
  active: boolean;
  onExit: () => void;
  /**
   * Keep one wrapper element in both states, so toggling never remounts the
   * children (a text editor keeps its caret, scroll and open Find). Inactive,
   * the wrapper is `display: contents`; a parent that spaces its children
   * with `space-y-*` should use `gap-*` instead, which still reaches them.
   */
  keepMounted?: boolean;
  children: React.ReactNode;
}) {
  useEffect(() => {
    if (!active) return undefined;

    const onKey = (event: KeyboardEvent) => {
      // An Escape something inside already used (closing a Find bar, a
      // popover, a dialog) is not also a request to leave full screen.
      if (event.key === 'Escape' && !event.defaultPrevented) onExit();
    };
    document.addEventListener('keydown', onKey);

    // The page behind must not scroll while an overlay covers it, or a stray
    // wheel event moves content the user cannot see.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [active, onExit]);

  if (!active && !keepMounted) return <>{children}</>;

  // The same element in both states when `keepMounted`, and the children in
  // the same slot, so React reconciles instead of remounting.
  return (
    <div
      className={
        active ? 'fixed inset-0 z-50 flex flex-col gap-3 overflow-auto bg-background p-3' : 'contents'
      }
      data-dk-focus-mode={active ? true : undefined}
    >
      {/*
        Say how to get out, on the screen, once.
        A mode that covers the entire window and relies on the user guessing a
        key is a trap; the hint costs one line and removes the guess. Bottom
        right, because top right is where the toolbar lives and a hint that
        covers Save is worse than no hint.
      */}
      {active ? (
        <div className="pointer-events-none absolute bottom-3 end-3 z-10 rounded-md bg-foreground/80 px-2 py-1 text-xs text-background shadow-sm">
          Press Esc to exit full screen
        </div>
      ) : null}
      {children}
    </div>
  );
}

/**
 * A panel that can be folded away.
 *
 * Full screen gives the canvas the window; the side panels then take a third of
 * it straight back. Folding them is what actually makes the room or the page
 * big, and it has to be reversible in one click from the same place.
 */
export function CollapsiblePanel({
  title,
  collapsed,
  onToggle,
  side,
  enabled = true,
  children,
}: {
  title: string;
  collapsed: boolean;
  onToggle: (next: boolean) => void;
  /** Which edge it lives on, so the chevron points the right way. */
  side: 'start' | 'end';
  /**
   * Folding is offered at all. Outside full screen the page scrolls anyway, so
   * a fold control would only add a second copy of the panel's own title.
   */
  enabled?: boolean;
  children: React.ReactNode;
}) {
  if (!enabled) return <>{children}</>;

  if (collapsed) {
    return (
      <div className="shrink-0">
        <Button
          variant="outline"
          size="sm"
          aria-label={`Show ${title}`}
          aria-expanded={false}
          title={`Show ${title}`}
          className="h-9 w-9 p-0"
          onClick={() => onToggle(false)}
        >
          {side === 'start' ? (
            <ChevronRight className="size-4" />
          ) : (
            <ChevronLeft className="size-4" />
          )}
        </Button>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-muted-foreground">{title}</span>
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Hide ${title}`}
          aria-expanded
          title={`Hide ${title}`}
          className="h-7 w-7 p-0"
          onClick={() => onToggle(true)}
        >
          {side === 'start' ? (
            <ChevronLeft className="size-4" />
          ) : (
            <ChevronRight className="size-4" />
          )}
        </Button>
      </div>
      {children}
    </div>
  );
}
