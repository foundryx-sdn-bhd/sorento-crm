import type { Idea, IdeaTransition } from '@/types/ideas';

/** The transition a row's "next move" takes: its advance edge. */
export function advanceTransition(idea: Idea): IdeaTransition | null {
  return idea.transitions.find((t) => t.id === idea.advanceTransitionId) ?? null;
}

/** The transition Restore takes out of an archived status: the advance edge, else the first. */
export function restoreTransition(idea: Idea): IdeaTransition | null {
  return advanceTransition(idea) ?? idea.transitions[0] ?? null;
}
