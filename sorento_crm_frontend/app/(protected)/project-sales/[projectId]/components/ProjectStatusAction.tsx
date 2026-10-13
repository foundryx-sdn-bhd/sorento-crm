'use client';

import { Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type {
  Status,
  StatusGraph,
  StatusTransition,
} from '@/app/(protected)/system-management/status-graphs/types/statusGraph.types';

export type StatusMove = {
  transitionId: string;
  toStatusId: string;
  /** What the admin called this edge on the status graph, e.g. "PO received". */
  label: string;
  /** The rung it lands on, e.g. "PO Received". */
  toLabel: string;
  /** The rung's stable key, e.g. "specified", for behaviour that must survive a relabel. */
  toKey: string;
  /** Terminal rungs (Lost, Dormant) are exits, never the happy path. */
  toIsTerminal: boolean;
  /** Lands on a LATER rung than the one we are on. A correction is not the happy path. */
  isForward: boolean;
};

/**
 * The forward move, and everything else.
 *
 * The happy path is the first move that ADVANCES the funnel: non-terminal, and landing on a
 * rung later than the current one. It used to be merely the first non-terminal move in the
 * admin's sort order, and every seeded edge carries `sort_order` 0, so the winner was
 * whichever row Postgres happened to return first. On a Registered project that was "Back to
 * identified" - the header offered undoing the registration as the obvious next step, with
 * "Spec in" demoted into the gear menu.
 *
 * Ranking on the TARGET rung's position needs no new data and cannot be got wrong by an
 * admin who leaves sort orders at zero. Backward moves are real and stay available, behind
 * the gear with the exits, because a correction should be deliberate.
 *
 * A funnel whose only remaining moves are terminal or backward still gets a primary button:
 * "there is a next step but we are hiding it" is worse than naming the exit.
 */
export function splitStatusMoves(moves: StatusMove[]): {
  primary: StatusMove | null;
  secondary: StatusMove[];
} {
  if (moves.length === 0) return { primary: null, secondary: [] };
  const forward =
    // Advance along the funnel.
    moves.find((move) => !move.toIsTerminal && move.isForward) ??
    // A FORWARD terminal rung is still the next step. Won, Accepted and Delivered are ends,
    // and naming one beats offering a correction: with "Accepted" terminal and "Back to draft"
    // open, preferring merely-non-terminal made "Back to draft" the primary button on an issued
    // quotation, which is the same failure as the sort_order one below in a new disguise.
    moves.find((move) => move.isForward) ??
    // Only then a backward correction, because at this point there is nothing forward left.
    moves.find((move) => !move.toIsTerminal) ??
    moves[0];
  return {
    primary: forward,
    secondary: moves.filter((move) => move.transitionId !== forward.transitionId),
  };
}

/**
 * The moves a person may fire from where this project stands.
 *
 * Mirrors `available_transitions` in the backend status service, which is the authority:
 * manual edges only (an automatic edge belongs to the engine, so offering it as a button
 * would let a user bypass its conditions), never out of a final rung, never into a
 * deactivated one. Ordered by the edge's own sort order, then by where it lands, so the
 * forward move an admin put first is the one the header offers first.
 *
 * Nothing here invents a lifecycle: an install whose funnel was reshaped in the status
 * graph editor gets its own moves, and a rung with no outgoing edge offers no action at
 * all rather than a button the server would reject.
 */
export function availableStatusMoves(
  graph: StatusGraph | null | undefined,
  fromStatusId?: string | null,
): StatusMove[] {
  if (!graph || !fromStatusId) return [];
  const byId = new Map<string, Status>(graph.statuses.map((status) => [status.id, status]));
  const current = byId.get(fromStatusId);
  if (!current || current.is_terminal) return [];

  return graph.transitions
    .filter(
      (transition) =>
        transition.from_status_id === fromStatusId && transition.trigger_mode === 'manual',
    )
    .map((transition) => ({ transition, target: byId.get(transition.to_status_id) }))
    .filter(
      (pair): pair is { transition: StatusTransition; target: Status } =>
        Boolean(pair.target?.is_active),
    )
    .sort(
      (a, b) =>
        a.transition.sort_order - b.transition.sort_order ||
        a.target.sort_order - b.target.sort_order,
    )
    .map(({ transition, target }) => ({
      transitionId: transition.id,
      toStatusId: target.id,
      label: transition.label,
      toLabel: target.label,
      toKey: target.key,
      toIsTerminal: Boolean(target.is_terminal),
      isForward: target.sort_order > current.sort_order,
    }));
}

/**
 * The project's one primary action: its next step, named.
 *
 * It used to open a dialog listing every legal move - "Register with developer", "PO
 * received", "Mark lost", "Mark dormant" - which makes the commonest action a decision
 * between four, and puts marking a pursuit LOST one careless click from advancing it. The
 * header now fires the forward move directly. Exits live behind the gear (see
 * `splitStatusMoves`), so choosing one is deliberate.
 */
export function ProjectStatusAction({
  moves,
  onMove,
  isPending = false,
}: {
  moves: StatusMove[];
  onMove: (toStatusId: string) => void;
  isPending?: boolean;
}) {
  const { primary } = splitStatusMoves(moves);
  if (!primary) return null;

  return (
    <Button type="button" disabled={isPending} onClick={() => onMove(primary.toStatusId)}>
      {isPending && <Loader2 className="size-4 animate-spin" aria-hidden />}
      {primary.label}
    </Button>
  );
}
