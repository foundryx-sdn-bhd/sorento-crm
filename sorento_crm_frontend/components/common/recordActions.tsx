'use client';

/**
 * One action set per entity, shown in two places (D15).
 *
 * An entity declares its actions once, in
 * `app/(protected)/<module>/<entity>/actions.tsx`, as
 * `use<Entity>Actions(record) -> { actions, dialogs }`. The record page renders
 * them in the gear (`DetailActions`) and the list row renders the SAME array, in
 * the same order, in its "..." menu (`RowActionsMenu`), so an action can never be
 * reachable from one surface and missing from the other.
 *
 * Permissions are resolved once, inside the definition hook: an action the user
 * may not run is simply not in the array. That is why there is no `permission`
 * field here - nothing downstream could act on it, and a second check is a second
 * place to get it wrong.
 *
 * `dialogs` is the node the definition hook needs mounted (an impersonation
 * prompt, a form). Both surfaces render it, so a list row and a record page open
 * the very same one. A destructive action has no dialog to mount since S6: it
 * parks a pending action and hands its countdown back as `pending`, which the
 * record page passes to `DetailActions` and a list row leaves to the toast.
 */

import { useEffect, useState, type ReactElement, type ReactNode } from 'react';
import { Check, type LucideIcon } from 'lucide-react';
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu';

/** How long the tick + confirm label sit before a confirmable item closes the menu itself. */
const CONFIRM_TIMEOUT_MS = 1500;

export interface RecordAction {
  /** Stable identity for the item, e.g. `user.impersonate`. */
  key: string;
  label: string;
  icon?: LucideIcon;
  /** `destructive` items are rendered last, after a separator, in red. */
  kind?: 'secondary' | 'destructive';
  disabled?: boolean;
  /**
   * Why a disabled item cannot run right now ("Sent plans are cancelled, not deleted"),
   * shown as the item's `title` (a hover tooltip) and `aria-description` (S1). Optional so
   * every existing consumer that never set it renders exactly as before.
   */
  disabledReason?: string;
  run: () => void | boolean | Promise<boolean | void>;
  /**
   * When set, selecting this item does not close the menu the instant it is
   * clicked. It runs `run`, and unless the result is explicitly `false` shows a
   * check mark and this label for ~1.5s before closing (S7-05's tick, adapted
   * for a menu item - a menu that closes on select erases a confirmation before
   * anyone sees it, which is exactly what left "Copy link" with no feedback at
   * all). `run` resolving to `false` skips the tick and closes immediately; the
   * caller surfaces that failure itself (a toast, same as any other clipboard
   * refusal - see `useCopyToClipboard`).
   */
  confirmLabel?: string;
  /** A second, muted line under the label (where a download lands, why it is unavailable). */
  description?: string;
}

/** What a `use<Entity>Actions` hook returns. */
export interface RecordActionSet {
  actions: RecordAction[];
  /** Mount this wherever the actions are rendered. */
  dialogs?: ReactNode;
  /**
   * The countdown of an action parked on this record (D7, S6-06), for the record
   * page to pass to `DetailActions` as `pendingAction`. Null while nothing is
   * parked, and null on a list row, where the countdown travels to a toast.
   */
  pending?: ReactNode;
}

/** Secondary items first, then a separator, then the destructive ones. */
export function orderRecordActions(actions: RecordAction[]): {
  secondary: RecordAction[];
  destructive: RecordAction[];
} {
  return {
    secondary: actions.filter((a) => a.kind !== 'destructive'),
    destructive: actions.filter((a) => a.kind === 'destructive'),
  };
}

/**
 * One item. A plain action fires and closes the menu, exactly as before. A
 * confirmable one (`confirmLabel` set) holds the menu open, swaps its icon for
 * a check mark and its label for `confirmLabel`, then closes itself after
 * `CONFIRM_TIMEOUT_MS` - the tick IS the confirmation, so nothing needs a toast
 * to say a copy landed.
 */
function RecordActionItem({
  action,
  onRequestClose,
}: {
  action: RecordAction;
  onRequestClose: () => void;
  /**
   * Unused here - `action.kind` is what actually drives the rendered item's
   * colour. It exists so `DetailActionsMenu`'s `orderChildren` (which moves a
   * destructive CHILD ELEMENT last by reading `props.variant` off it) can still
   * see the same signal on a caller that splices `recordActionItems` straight
   * into its own raw menu, instead of going through `RecordActionMenuItems`.
   */
  variant?: 'destructive';
}) {
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    if (!confirmed) return;
    const timer = setTimeout(() => {
      setConfirmed(false);
      onRequestClose();
    }, CONFIRM_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [confirmed, onRequestClose]);

  const handleSelect = (event: Event) => {
    if (!action.confirmLabel) {
      action.run();
      return;
    }
    // Keep the menu open: `run` (e.g. a clipboard write) has to resolve, and the
    // tick that reports it has nowhere to render once the menu is gone.
    event.preventDefault();
    Promise.resolve(action.run()).then((result) => {
      if (result === false) {
        onRequestClose();
        return;
      }
      setConfirmed(true);
    });
  };

  const Icon = confirmed ? Check : action.icon;

  return (
    <DropdownMenuItem
      key={action.key}
      variant={action.kind === 'destructive' ? 'destructive' : undefined}
      disabled={action.disabled}
      title={action.disabledReason}
      aria-description={action.disabledReason}
      onSelect={handleSelect}
    >
      {Icon && <Icon className="size-4" />}
      {confirmed ? (
        action.confirmLabel
      ) : action.description ? (
        <span className="min-w-0">
          {action.label}
          <span className="block text-xs text-muted-foreground">{action.description}</span>
        </span>
      ) : (
        action.label
      )}
    </DropdownMenuItem>
  );
}

/**
 * The items as an ARRAY, not a fragment.
 *
 * A gear whose other items are a workflow (the SLA tracking one, say) splices
 * these in among its own children, and `DetailActionsMenu` can only see a
 * destructive item, and move it last, if the array is flattened into its
 * children rather than hidden inside one fragment.
 */
export function recordActionItems(
  actions: RecordAction[],
  // Optional: a caller splicing these into its own raw DropdownMenu (the
  // workflow gears) has no `confirmLabel` actions today, so it has nothing to
  // close. Only `RecordActionMenuItems` (below) needs to wire this up for real.
  onRequestClose: () => void = () => {},
): ReactElement[] {
  return actions.map((action) => (
    <RecordActionItem
      key={action.key}
      action={action}
      onRequestClose={onRequestClose}
      variant={action.kind === 'destructive' ? 'destructive' : undefined}
    />
  ));
}

/** The menu items themselves, so the gear and the row "..." render one list. */
export function RecordActionMenuItems({
  actions,
  onRequestClose,
}: {
  actions: RecordAction[];
  /** Closes the menu that owns these items - the confirmable ones need it once their tick times out. */
  onRequestClose: () => void;
}) {
  const { secondary, destructive } = orderRecordActions(actions);

  return (
    <>
      {recordActionItems(secondary, onRequestClose)}
      {secondary.length > 0 && destructive.length > 0 && <DropdownMenuSeparator />}
      {recordActionItems(destructive, onRequestClose)}
    </>
  );
}
