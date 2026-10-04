import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  addComment,
  createIdea,
  editComment,
  getBoard,
  getIdea,
  getMergedChildren,
  listComments,
  listIdeas,
  mergeIdeas,
  moveIdeaToStatus,
  promoteIdea,
  reorderIdeas,
  restoreIdea,
  unmergeIdea,
  updateIdea,
  uploadAttachment,
  voteIdea,
} from '@/services/ideasService';
import type { IdeaListParams } from '@/types/ideas';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';

export const IDEAS_KEY = ['ideas'] as const;

export function useIdeasQuery(params: IdeaListParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: [...IDEAS_KEY, 'list', params.query ?? '', params.status ?? '', params.mine ?? false],
    queryFn: () => listIdeas(params),
  });
}

export function useIdeaQuery(id: string | null) {
  return useQuery({
    queryKey: [...IDEAS_KEY, 'detail', id],
    queryFn: () => getIdea(id as string),
    enabled: !!id,
    retry: 1,
  });
}

export function useIdeaBoardQuery() {
  return useQuery({ queryKey: [...IDEAS_KEY, 'board'], queryFn: getBoard });
}

export function useIdeaMergedQuery(id: string | null) {
  return useQuery({
    queryKey: [...IDEAS_KEY, 'merged', id],
    queryFn: () => getMergedChildren(id as string),
    enabled: !!id,
  });
}

export function useIdeaCommentsQuery(id: string | null) {
  return useQuery({
    queryKey: [...IDEAS_KEY, 'comments', id],
    queryFn: () => listComments(id as string),
    enabled: !!id,
  });
}

/**
 * Every Ideas write. Each one invalidates the whole `['ideas']` family (list, board, detail,
 * comments are all small) and toasts; errors toast the server's message.
 */
export function useIdeaMutations() {
  const queryClient = useQueryClient();
  const refresh = () => queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
  const onError = (error: Error) =>
    toast.error(error.message || 'Something went wrong');

  const vote = useMutation({
    mutationFn: (id: string) => voteIdea(id),
    onSuccess: refresh,
    onError,
  });

  const create = useMutation({
    mutationFn: createIdea,
    onSuccess: () => {
      refresh();
      toast.success('Idea captured');
    },
    // The idea may exist even when a file did not attach, so the list refreshes either way.
    onError: (error: Error) => {
      refresh();
      onError(error);
    },
  });

  const update = useMutation({
    mutationFn: ({
      id,
      ...input
    }: { id: string } & Parameters<typeof updateIdea>[1]) =>
      updateIdea(id, input),
    onSuccess: () => {
      refresh();
      toast.success('Idea saved');
    },
    onError,
  });

  const move = useMutation({
    mutationFn: ({ id, toStatusId }: { id: string; toStatusId: string }) =>
      moveIdeaToStatus(id, toStatusId),
    onSuccess: (idea) => {
      refresh();
      toast.success(`Moved to ${idea.statusLabel}`);
    },
    onError: (error: Error) => {
      // The board's optimistic card has to snap back, so the failed move refetches too.
      refresh();
      onError(error);
    },
  });

  const restore = useMutation({
    mutationFn: ({ id, toStatusId }: { id: string; toStatusId: string }) =>
      restoreIdea(id, toStatusId),
    onSuccess: () => {
      refresh();
      toast.success('Idea restored');
    },
    onError,
  });

  const reorder = useMutation({
    mutationFn: (orderedIds: string[]) => reorderIdeas(orderedIds),
    onSuccess: refresh,
    onError: (error: Error) => {
      refresh();
      onError(error);
    },
  });

  const merge = useMutation({
    mutationFn: mergeIdeas,
    onSuccess: () => {
      refresh();
      toast.success('Ideas merged');
    },
    onError,
  });

  const unmerge = useMutation({
    mutationFn: (id: string) => unmergeIdea(id),
    onSuccess: () => {
      refresh();
      toast.success('Idea unmerged');
    },
    onError,
  });

  const promote = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      promoteIdea(id, { title }),
    onSuccess: (created) => {
      refresh();
      toast.success(
        created?.title
          ? `Business requirement created: ${created.title}`
          : 'Business requirement created',
      );
    },
    // ss's own 403 wording reaches the user as the toast.
    onError,
  });

  const upload = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) =>
      uploadAttachment(id, file),
    onSuccess: refresh,
    onError,
  });

  const addIdeaComment = useMutation({
    mutationFn: ({
      id,
      body,
      parentId,
    }: {
      id: string;
      body: string;
      parentId?: string | null;
    }) => addComment(id, { body, parentId }),
    onSuccess: refresh,
    onError,
  });

  const editIdeaComment = useMutation({
    mutationFn: ({
      id,
      commentId,
      body,
    }: {
      id: string;
      commentId: string;
      body: string;
    }) => editComment(id, commentId, body),
    onSuccess: refresh,
    onError,
  });

  return {
    vote,
    create,
    update,
    move,
    restore,
    reorder,
    merge,
    unmerge,
    promote,
    upload,
    addComment: addIdeaComment,
    editComment: editIdeaComment,
  };
}
