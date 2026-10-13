'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import {
  addFormComment,
  editFormComment,
  listFormComments,
} from '@/services/formCommentsService';
import type { FormCommentKind } from '@/types/formComments';

export const FORM_COMMENTS_KEY = 'form-comments';

export function useFormCommentsQuery(kind: FormCommentKind, id: string) {
  return useQuery({
    queryKey: [FORM_COMMENTS_KEY, kind, id],
    queryFn: () => listFormComments(kind, id),
    enabled: Boolean(id),
  });
}

/**
 * Post a staff comment. The comment is saved before the WhatsApp is queued, so a channel
 * problem is a warning about the notification, never a lost comment.
 */
export function useAddFormComment(kind: FormCommentKind, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      body,
      files,
      parentId,
    }: {
      body: string;
      files: File[];
      parentId?: string;
    }) => addFormComment(kind, id, body, files, parentId),
    onSuccess: ({ delivery }) => {
      queryClient.invalidateQueries({
        queryKey: [FORM_COMMENTS_KEY, kind, id],
      });
      if (delivery === 'no_whatsapp') {
        toast.warning(
          'Comment posted. The submitter has no WhatsApp contact, so they were not notified.',
        );
      } else if (delivery === 'failed') {
        toast.warning(
          'Comment posted, but the WhatsApp to the submitter could not be sent.',
        );
      } else {
        toast.success('Comment posted');
      }
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to post the comment'),
  });
}

/** Edit a comment in place; no notification goes out for an edit. */
export function useEditFormComment(kind: FormCommentKind, id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ commentId, body }: { commentId: string; body: string }) =>
      editFormComment(kind, id, commentId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: [FORM_COMMENTS_KEY, kind, id],
      });
      toast.success('Comment saved');
    },
    onError: (error: Error) =>
      toast.error(error.message || 'Failed to save the comment'),
  });
}
