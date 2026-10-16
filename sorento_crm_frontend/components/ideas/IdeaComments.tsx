'use client';

import { useEffect, useMemo } from 'react';
import { useSession } from 'next-auth/react';
import { Trash2 } from 'lucide-react';
import {
  CommentThreadView,
  type ThreadComment,
} from '@/components/common/comments/CommentThreadView';
import { useDeletingIds } from '@/components/common/comments/useDeletingIds';
import { Button } from '@/components/ui/button';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import {
  IDEAS_KEY,
  useIdeaCommentsQuery,
  useIdeaMutations,
} from '@/hooks/useIdeas';

/** Delete is the server-deferred countdown in place of the button (D7), never a dialog. */
export function IdeaCommentDelete({
  ideaId,
  commentId,
  onActive,
}: {
  ideaId: string;
  commentId: string;
  onActive: (id: string, active: boolean) => void;
}) {
  const removal = useDeferredAction({
    actionKey: 'idea_comment.delete',
    entityType: 'idea_comment',
    entityId: commentId,
    verb: 'Deleting',
    subject: 'Comment',
    surface: 'inline',
    successMessage: 'Comment deleted',
    payload: { idea_id: ideaId },
    invalidateKeys: [IDEAS_KEY],
  });
  const active = Boolean(removal.countdown) || removal.isPending;
  useEffect(() => {
    onActive(commentId, active);
    return () => onActive(commentId, false);
  }, [commentId, active, onActive]);
  if (removal.countdown) return <div className="pt-1">{removal.countdown}</div>;
  return (
    <Button
      type="button"
      variant="dim"
      size="sm"
      className="h-7 gap-1 px-1.5"
      onClick={() => removal.start()}
    >
      <Trash2 className="size-3.5" />
      Delete
    </Button>
  );
}

/**
 * Comments under the idea's Details, on the thread shared with form submissions: oldest first,
 * one reply level, "edited" tag, a "Comment deleted" placeholder when a deleted comment still
 * has replies. A merged child's comments are frozen.
 */
export function IdeaComments({
  ideaId,
  frozen,
}: {
  ideaId: string;
  frozen: boolean;
}) {
  const { data: session } = useSession();
  const { data: comments, isLoading } = useIdeaCommentsQuery(ideaId);
  const { addComment, editComment } = useIdeaMutations();
  const { isDeleting, report } = useDeletingIds();

  const thread = useMemo<ThreadComment[]>(
    () =>
      (comments ?? []).map((c) => ({
        ...c,
        canReply: true,
      })),
    [comments],
  );

  return (
    <CommentThreadView
      comments={thread}
      isLoading={isLoading}
      authorName={session?.user?.name ?? ''}
      composerPosition="top"
      readOnly={frozen}
      emptyHint="Comments on this idea appear here."
      pending={addComment.isPending}
      editPending={editComment.isPending}
      isDeleting={isDeleting}
      onAdd={async (body, _files, parentId) => {
        await addComment.mutateAsync({ id: ideaId, body, parentId });
      }}
      onEdit={async (commentId, body) => {
        await editComment.mutateAsync({ id: ideaId, commentId, body });
      }}
      renderDelete={(c) => (
        <IdeaCommentDelete
          ideaId={ideaId}
          commentId={c.id}
          onActive={report}
        />
      )}
    />
  );
}
