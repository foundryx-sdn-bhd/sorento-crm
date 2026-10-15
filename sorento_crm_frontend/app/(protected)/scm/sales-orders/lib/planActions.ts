import { CalendarRange } from 'lucide-react';
import type { ToolbarAction } from '@/components/ui/data-grid-list-toolbar';

/**
 * Who may open the fulfilment planning board. Same gate the board's own page carries -
 * exported so the record page's own "Plan" primary (S2) reads the identical constant
 * rather than a second copy of the permission slug.
 */
export const PLAN_PERMISSION = 'projects.projects.view';

/**
 * "Plan selected (N)" - the sales-order list's way onto the fulfilment board.
 *
 * It is the toolbar's own primary button (the owner's ruling, 22 Sep 2026), and NOT in the
 * bulk strip. The strip only exists once rows are ticked, so an action that lives there
 * cannot be found by someone who has not already guessed it is there - and when it refused
 * (over the board's bound) it refused as a greyed-out button whose reason was a hover away,
 * which is indistinguishable from a dead click.
 *
 * The one thing this list starts is taking a set of orders to the board, so that is the CTA:
 * always present (while the caller holds the permission), always counting what is selected,
 * and stating its own refusal on itself via a tooltip.
 *
 * There is no plan ENTITY behind it. The fulfilment board is a URL
 * (`/project-sales/fulfilment-planning?orders=SO1,SO2`) and stays one, so this action
 * navigates and saves nothing: nothing is created by pressing it, and the same link can be
 * kept or sent.
 *
 * Two refusals, and they say different things:
 *
 * * with nothing ticked, and over the board's own bound, the item is DISABLED with the
 *   reason on it rather than hidden - the user is entitled to know the action exists and
 *   what it wants from them;
 * * without the permission the board's page requires, nothing renders at all. Offering a
 *   door that answers 403 is worse than not offering it.
 */
export interface PlanActionState {
  selectedCount: number;
  /** Whether the caller holds the permission the board's own page is gated on. */
  canPlan: boolean;
  /** The board's `MAX_BOARD_SELECTION`. */
  max: number;
  /** What the list holds, plural, for the refusal ("Tick the consignments ..."). */
  noun?: string;
}

export interface PlanActionHandlers {
  onPlan: () => void;
}

export function buildPlanActions(
  state: PlanActionState,
  handlers: PlanActionHandlers,
): ToolbarAction[] {
  if (!state.canPlan) return [];
  const nothingPicked = state.selectedCount < 1;
  const tooMany = state.selectedCount > state.max;
  return [
    {
      key: 'plan-selected',
      // The count is in the label, not only in the refusal: the menu is opened away from
      // the ticked rows, so it has to say how many are going.
      label: `Plan selected (${state.selectedCount})`,
      icon: CalendarRange,
      onClick: handlers.onPlan,
      disabled: nothingPicked || tooMany,
      disabledReason: nothingPicked
        ? `Tick the ${state.noun ?? 'sales orders'} to plan first.`
        : tooMany
          ? `The planning board takes up to ${state.max} orders at a time. ` +
            `${state.selectedCount} are selected.`
          : undefined,
    },
  ];
}
