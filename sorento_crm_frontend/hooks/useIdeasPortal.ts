import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import { IDEAS_KEY } from '@/hooks/useIdeas';
import {
  getActivity,
  getChangelog,
  getPortalIdea,
  getRoadmap,
  getVoters,
  likePortalComment,
  listFeedback,
  listPortalComments,
  postPortalComment,
  setPortalHidden,
  setPortalNote,
  togglePortalVote,
} from '@/services/ideasPortalService';
import type { PortalFeedbackParams } from '@/types/ideasPortal';

/** Under the Ideas family, so a comment delete or any Ideas write refreshes the portal too. */
export const PORTAL_KEY = [...IDEAS_KEY, 'portal'] as const;

export function usePortalRoadmapQuery(enabled = true) {
  return useQuery({ queryKey: [...PORTAL_KEY, 'roadmap'], queryFn: () => getRoadmap(), enabled });
}

export function usePortalFeedbackQuery(params: PortalFeedbackParams, enabled = true) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [
      ...PORTAL_KEY,
      'feedback',
      params.q ?? '',
      params.sort ?? '',
      params.mine ?? false,
      params.page ?? 1,
      params.pageSize ?? 0,
    ],
    queryFn: () => listFeedback(params),
    enabled,
  });
}

export function usePortalChangelogQuery(enabled = true) {
  return useQuery({ queryKey: [...PORTAL_KEY, 'changelog'], queryFn: getChangelog, enabled });
}

export function usePortalIdeaQuery(id: string) {
  return useQuery({ queryKey: [...PORTAL_KEY, 'idea', id], queryFn: () => getPortalIdea(id), retry: 1 });
}

export function usePortalVotersQuery(id: string) {
  return useQuery({ queryKey: [...PORTAL_KEY, 'voters', id], queryFn: () => getVoters(id) });
}

export function usePortalActivityQuery(id: string) {
  return useQuery({ queryKey: [...PORTAL_KEY, 'activity', id], queryFn: () => getActivity(id) });
}

export function usePortalCommentsQuery(id: string) {
  return useQuery({ queryKey: [...PORTAL_KEY, 'comments', id], queryFn: () => listPortalComments(id) });
}

/** Every portal write: refreshes the `['ideas', 'portal']` family; errors toast the server's message. */
export function usePortalMutations() {
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: PORTAL_KEY });
  const onError = (error: Error) => toast.error(error.message || 'Something went wrong');

  const vote = useMutation({ mutationFn: (id: string) => togglePortalVote(id), onSuccess: refresh, onError });

  const comment = useMutation({
    mutationFn: ({ id, body, parentId }: { id: string; body: string; parentId?: string | null }) =>
      postPortalComment(id, { body, parentId }),
    onSuccess: refresh,
    onError,
  });

  const like = useMutation({
    mutationFn: ({ id, commentId }: { id: string; commentId: string }) => likePortalComment(id, commentId),
    onSuccess: refresh,
    onError,
  });

  const hidden = useMutation({
    mutationFn: ({ id, hidden: value }: { id: string; hidden: boolean }) => setPortalHidden(id, value),
    onSuccess: refresh,
    onError,
  });

  const note = useMutation({
    mutationFn: ({ id, note: value }: { id: string; note: string }) => setPortalNote(id, value),
    onSuccess: () => {
      refresh();
      toast.success('Note saved');
    },
    onError,
  });

  return { vote, comment, like, hidden, note };
}
