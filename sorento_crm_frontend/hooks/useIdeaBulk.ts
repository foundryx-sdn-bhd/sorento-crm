'use client';

import { useCallback } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { advanceTransition, restoreTransition } from '@/lib/ideaTransitions';
import { IDEAS_KEY } from '@/hooks/useIdeas';
import {
  mergeIdeas,
  moveIdeaToStatus,
  promoteIdeas,
  restoreIdea,
  unmergeIdea,
} from '@/services/ideasService';
import type { Idea } from '@/types/ideas';

const titleOf = (idea: Idea) => idea.title ?? idea.problem;
const noun = (n: number) => `${n} idea${n === 1 ? '' : 's'}`;

/**
 * The Ideas list's bulk writes that are NOT deferred (Archive and Delete are server pending
 * actions): one service call per row, one closing toast for the batch, then the list refetches.
 * A partial failure says how many failed; `done` always runs so the caller can drop the selection.
 */
export function useIdeaBulk(done: () => void) {
  const queryClient = useQueryClient();

  const perRow = useCallback(
    async (
      rows: Idea[],
      call: (row: Idea) => Promise<unknown>,
      past: string,
    ) => {
      const results = await Promise.allSettled(rows.map(call));
      const failed = results.filter((r) => r.status === 'rejected');
      if (failed.length === 0) {
        toast.success(`${noun(rows.length)} ${past}.`);
      } else {
        const reason = (failed[0] as PromiseRejectedResult).reason;
        toast.error(
          `${failed.length} of ${rows.length} failed${reason instanceof Error && reason.message ? `: ${reason.message}` : '.'}`,
        );
      }
      done();
      await queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
    },
    [done, queryClient],
  );

  const advance = useCallback(
    (rows: Idea[]) =>
      perRow(
        rows,
        (row) => {
          const next = advanceTransition(row);
          return next
            ? moveIdeaToStatus(row.id, next.toStatusId)
            : Promise.reject(new Error('No next stage'));
        },
        'moved',
      ),
    [perRow],
  );

  const restore = useCallback(
    (rows: Idea[]) =>
      perRow(
        rows,
        (row) => {
          const next = restoreTransition(row);
          return next
            ? restoreIdea(row.id, next.toStatusId)
            : Promise.reject(new Error('Nothing to restore to'));
        },
        'restored',
      ),
    [perRow],
  );

  const unmerge = useCallback(
    (rows: Idea[]) => perRow(rows, (row) => unmergeIdea(row.id), 'unmerged'),
    [perRow],
  );

  /** One BR from every selected idea, titled after the first one selected. */
  const promote = useCallback(
    async (rows: Idea[]) => {
      try {
        const created = await promoteIdeas({
          ideaIds: rows.map((r) => r.id),
          title: titleOf(rows[0]),
        });
        toast.success(
          created?.title
            ? `Business requirement created: ${created.title}`
            : 'Business requirement created',
        );
      } catch (error) {
        toast.error(
          error instanceof Error
            ? error.message
            : 'Failed to promote the ideas',
        );
      }
      done();
      await queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
    },
    [done, queryClient],
  );

  const merge = useCallback(
    async (survivorId: string, rows: Idea[]) => {
      try {
        await mergeIdeas({ survivorId, ideaIds: rows.map((r) => r.id) });
        toast.success('Ideas merged');
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : 'Failed to merge the ideas',
        );
        throw error;
      }
      done();
      await queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
    },
    [done, queryClient],
  );

  return { advance, restore, unmerge, promote, merge };
}
