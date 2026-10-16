'use client';

import * as React from 'react';
import { DollarSign, Plus } from 'lucide-react';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { formatMyrExact } from '../../../../_shared/lib/money';
import type { QuotationScope } from '../../../../_shared/services/quotationDocumentService';

/**
 * The scope switcher, read as a spreadsheet's sheet tabs: the names in a strip, and the way to
 * add one sitting at the END of that strip rather than as a page action across the screen.
 *
 * The client drew an arrow from the tabs to the far-right "Add a scope" button to make the point:
 * adding a scope is a tab-strip control, the same gesture as adding a sheet in Excel, so it
 * belongs where the tabs are and nowhere else.
 *
 * Money stays OFF the tab labels. The original reason still holds - "this is too much information
 * fatigue la, you can have a $ icon there where on click it shows the amount, but i don't want to
 * show the money and the scope tgt" - so a tab reads as a name and nothing more until the reader
 * asks. What changed is WHERE the asking happens: one $ per tab, answering for that tab in a
 * popover, instead of a single switch that printed the whole row of figures at once.
 *
 * The figure is always the server's own `scope_total`, formatted by the repo's decimal-exact money
 * helper. Never a second arithmetic in the browser, and never `parseFloat` on a money string.
 */
function ScopeValueButton({ scope }: { scope: QuotationScope }) {
  return (
    <Popover>
      <PopoverTrigger
        type="button"
        aria-label={`Show the value of ${scope.scope_label}`}
        title={`Show the value of ${scope.scope_label}`}
        className="me-0.5 inline-flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:text-primary"
      >
        <DollarSign className="size-3.5" aria-hidden />
      </PopoverTrigger>
      {/* Portalled because the strip scrolls (`overflow-x-auto`), and a popover laid out inside a
          scrolling box is clipped by it - positioned correctly, never painted. */}
      <PopoverPortal>
        <PopoverContent align="start" className="w-auto min-w-44 p-3">
          <p className="text-xs uppercase tracking-wide text-muted-foreground">Scope value</p>
          <p className="mt-1 min-w-0 break-words text-sm font-medium">{scope.scope_label}</p>
          <p className="mt-1 text-base font-semibold tabular-nums">
            {formatMyrExact(scope.scope_total)}
          </p>
        </PopoverContent>
      </PopoverPortal>
    </Popover>
  );
}

export function QuotationScopeTabs({
  scopes,
  activeScopeId,
  onSelect,
  canEdit,
  onAddScope,
  onRename,
  onRemove,
  canRemove,
}: {
  scopes: QuotationScope[];
  activeScopeId: string;
  onSelect: (scopeId: string) => void;
  canEdit: boolean;
  onAddScope?: () => void;
  /**
   * The quotation form's own scope handlers. Given, a right-click on a tab offers Rename and
   * Remove scope, exactly as the pen and the Remove scope button do; the read page passes
   * neither, so its tabs have no menu.
   */
  onRename?: (scopeId: string) => void;
  onRemove?: (scopeId: string) => void;
  /** Whether Remove scope may run on this tab (the button's own rule). */
  canRemove?: (scopeId: string) => boolean;
}) {
  const hasMenu = Boolean(onRename || onRemove);
  return (
    <Tabs value={activeScopeId} onValueChange={onSelect} className="min-w-0">
      {/* The strip scrolls inside its own gutter. Without `min-w-0` the flex/grid ancestors
          refuse to shrink below the strip's content and the whole PAGE drags sideways at 375px,
          which is the one failure that makes a phone unusable. */}
      <div className="min-w-0 overflow-x-auto" data-testid="quotation-scope-strip">
        {/* `w-max` so the strip's background runs the full length of the tabs it holds rather
            than stopping at the viewport edge once they overflow. */}
        <TabsList variant="default" className="w-max">
          {scopes.map((scope) => {
            // The chip carries the active styling, not the trigger, so the tab's $ sits INSIDE
            // the selected tab instead of floating beside it. A button cannot be nested in a
            // button, which is why the trigger and the $ are siblings under one chip.
            const chip = (
              <div
                key={scope.id}
                className="flex shrink-0 items-center rounded-md transition-colors has-[[data-state=active]]:bg-background has-[[data-state=active]]:shadow-xs has-[[data-state=active]]:shadow-black/5"
              >
                <TabsTrigger
                  value={scope.id}
                  className="pe-1 shadow-none data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <span className="min-w-0 max-w-[12rem] truncate" title={scope.scope_label}>
                    {scope.scope_label}
                  </span>
                </TabsTrigger>
                <ScopeValueButton scope={scope} />
              </div>
            );
            if (!hasMenu) return chip;
            // Right-click works on the tab it is over, the way a spreadsheet's sheet tab does,
            // so it also opens that tab.
            return (
              <ContextMenu key={scope.id} modal={false}>
                <ContextMenuTrigger asChild onContextMenu={() => onSelect(scope.id)}>
                  {chip}
                </ContextMenuTrigger>
                <ContextMenuContent className="w-44">
                  {onRename && (
                    <ContextMenuItem onSelect={() => onRename(scope.id)}>Rename</ContextMenuItem>
                  )}
                  {onRemove && (
                    <ContextMenuItem
                      variant="destructive"
                      disabled={canRemove ? !canRemove(scope.id) : false}
                      onSelect={() => onRemove(scope.id)}
                    >
                      Remove scope
                    </ContextMenuItem>
                  )}
                </ContextMenuContent>
              </ContextMenu>
            );
          })}

          {canEdit && onAddScope && (
            <button
              type="button"
              onClick={onAddScope}
              aria-label="Add a scope"
              title="Add a scope"
              className="ms-0.5 inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-background hover:text-foreground focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Plus className="size-4" aria-hidden />
            </button>
          )}
        </TabsList>
      </div>
    </Tabs>
  );
}
