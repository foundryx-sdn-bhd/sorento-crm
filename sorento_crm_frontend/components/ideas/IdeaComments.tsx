'use client';

import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';
import { CornerDownRight, Pencil, Trash2 } from 'lucide-react';
import {
  CommentAvatar,
  CommentComposer as Composer,
  CommentsSection,
} from '@/components/common/comments/CommentParts';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import {
  IDEAS_KEY,
  useIdeaCommentsQuery,
  useIdeaMutations,
} from '@/hooks/useIdeas';
import { formatDateTime } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import type { IdeaComment } from '@/types/ideas';

interface ItemProps {
  ideaId: string;
  comment: IdeaComment;
  small?: boolean;
  /** A merged child's comments are frozen. */
  frozen: boolean;
  onReply: () => void;
}

function CommentItem({ ideaId, comment, small, frozen, onReply }: ItemProps) {
  const { editComment } = useIdeaMutations();
  const [editing, setEditing] = useState(false);
  const removal = useDeferredAction({
    actionKey: 'idea_comment.delete',
    entityType: 'idea_comment',
    entityId: comment.id,
    verb: 'Deleting',
    subject: 'Comment',
    surface: 'inline',
    successMessage: 'Comment deleted',
    payload: { idea_id: ideaId },
    invalidateKeys: [IDEAS_KEY],
  });

  if (comment.deleted) {
    return (
      <div className="flex items-start gap-3 py-3 text-sm text-muted-foreground">
        <Avatar className={small ? 'size-6' : 'size-8'}>
          <AvatarFallback />
        </Avatar>
        <span className="pt-1 italic">Comment deleted</span>
      </div>
    );
  }

  const canEdit = !frozen && comment.canEdit;
  const canDelete = !frozen && comment.canDelete;

  if (editing) {
    return (
      <div className="py-3">
        <Composer
          authorName={comment.authorName}
          placeholder="Edit comment"
          submitLabel="Save"
          initial={comment.body}
          pending={editComment.isPending}
          small={small}
          autoFocus
          onCancel={() => setEditing(false)}
          onSubmit={async (body) => {
            await editComment.mutateAsync({
              id: ideaId,
              commentId: comment.id,
              body,
            });
            setEditing(false);
          }}
        />
      </div>
    );
  }

  return (
    <div
      className={cn(
        'flex items-start gap-3 py-3',
        removal.isPending && 'opacity-60',
      )}
    >
      <CommentAvatar name={comment.authorName} small={small} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
          <span className="font-medium">{comment.authorName}</span>
          <span className="text-xs text-muted-foreground">
            {formatDateTime(comment.createdAt)}
          </span>
          {comment.editedAt ? (
            <Badge variant="outline" size="xs">
              edited
            </Badge>
          ) : null}
        </div>
        <p className="whitespace-pre-wrap break-words text-sm">
          {comment.body}
        </p>
        {removal.countdown ? (
          <div className="pt-1">{removal.countdown}</div>
        ) : frozen ? null : (
          <div className="flex flex-wrap items-center gap-1 pt-0.5">
            <Button
              type="button"
              variant="dim"
              size="sm"
              className="h-7 gap-1 px-1.5"
              onClick={onReply}
            >
              <CornerDownRight className="size-3.5" />
              Reply
            </Button>
            {canEdit ? (
              <Button
                type="button"
                variant="dim"
                size="sm"
                className="h-7 gap-1 px-1.5"
                onClick={() => setEditing(true)}
              >
                <Pencil className="size-3.5" />
                Edit
              </Button>
            ) : null}
            {canDelete ? (
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
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Comments under the idea's Details: oldest first, one reply level, "edited" tag, a "Comment
 * deleted" placeholder when a deleted comment still has replies. Delete is the deferred
 * countdown, in place of the row's actions (D7), not a dialog.
 */
export function IdeaComments({
  ideaId,
  frozen,
}: {
  ideaId: string;
  frozen: boolean;
}) {
  const { data: session } = useSession();
  const me = session?.user?.name ?? '';
  const { data: comments, isLoading } = useIdeaCommentsQuery(ideaId);
  const { addComment } = useIdeaMutations();
  const [replyTo, setReplyTo] = useState<string | null>(null);

  const { threads, count } = useMemo(() => {
    const all = comments ?? [];
    const top = all.filter((c) => !c.parentId);
    return {
      // A deleted comment stays only as the placeholder its replies hang from.
      threads: top
        .map((c) => ({
          root: c,
          replies: all.filter((r) => r.parentId === c.id && !r.deleted),
        }))
        .filter(({ root, replies }) => !root.deleted || replies.length > 0),
      count: all.filter((c) => !c.deleted).length,
    };
  }, [comments]);

  return (
    <CommentsSection
      count={count}
      isLoading={isLoading}
      isEmpty={threads.length === 0}
      emptyHint="Comments on this idea appear here."
      composer={
        frozen ? null : (
          <Composer
            authorName={me}
            placeholder="Write a comment"
            submitLabel="Comment"
            pending={addComment.isPending && replyTo === null}
            onSubmit={async (body) => {
              await addComment.mutateAsync({ id: ideaId, body });
            }}
          />
        )
      }
    >
      <div className="flex flex-col divide-y">
        {threads.map(({ root, replies }) => (
          <div key={root.id}>
            <CommentItem
              ideaId={ideaId}
              comment={root}
              frozen={frozen}
              onReply={() => setReplyTo(root.id)}
            />
            {replies.length > 0 || replyTo === root.id ? (
              <div className="ms-4 border-s ps-4 sm:ms-11">
                {replies.map((reply) => (
                  <CommentItem
                    key={reply.id}
                    ideaId={ideaId}
                    comment={reply}
                    small
                    frozen={frozen}
                    onReply={() => setReplyTo(root.id)}
                  />
                ))}
                {replyTo === root.id ? (
                  <div className="pb-3">
                    <Composer
                      authorName={me}
                      placeholder="Write a reply"
                      submitLabel="Reply"
                      pending={addComment.isPending}
                      small
                      autoFocus
                      onCancel={() => setReplyTo(null)}
                      onSubmit={async (body) => {
                        await addComment.mutateAsync({
                          id: ideaId,
                          body,
                          parentId: root.id,
                        });
                        setReplyTo(null);
                      }}
                    />
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </CommentsSection>
  );
}
