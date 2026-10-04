'use client';

import { useMemo, useState } from 'react';
import { useSession } from 'next-auth/react';
import {
  CornerDownRight,
  LoaderCircleIcon,
  Pencil,
  Trash2,
} from 'lucide-react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import {
  IDEAS_KEY,
  useIdeaCommentsQuery,
  useIdeaMutations,
} from '@/hooks/useIdeas';
import { formatDateTime } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import type { IdeaComment } from '@/types/ideas';

function initials(name: string): string {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase())
      .join('') || '?'
  );
}

function CommentAvatar({ name, small }: { name: string; small?: boolean }) {
  return (
    <Avatar className={small ? 'size-6' : 'size-8'}>
      <AvatarFallback className="text-[0.6875rem] font-medium">
        {initials(name)}
      </AvatarFallback>
    </Avatar>
  );
}

interface ComposerProps {
  authorName: string;
  placeholder: string;
  submitLabel: string;
  initial?: string;
  pending?: boolean;
  small?: boolean;
  autoFocus?: boolean;
  onSubmit: (body: string) => Promise<void>;
  onCancel?: () => void;
}

function Composer({
  authorName,
  placeholder,
  submitLabel,
  initial = '',
  pending,
  small,
  autoFocus,
  onSubmit,
  onCancel,
}: ComposerProps) {
  const [body, setBody] = useState(initial);
  const canSubmit = body.trim().length > 0 && !pending;

  const submit = async () => {
    if (!canSubmit) return;
    try {
      await onSubmit(body);
      setBody('');
    } catch {
      // The hook toasted the reason; what was typed stays.
    }
  };

  return (
    <div className="flex items-start gap-3">
      <CommentAvatar name={authorName} small={small} />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <Textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={placeholder}
          aria-label={placeholder}
          rows={small ? 2 : 3}
          autoFocus={autoFocus}
        />
        <div className="flex justify-end gap-2">
          {onCancel ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onCancel}
            >
              Cancel
            </Button>
          ) : null}
          <Button
            type="button"
            variant="primary"
            size="sm"
            disabled={!canSubmit}
            onClick={submit}
          >
            {pending ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : null}
            {submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}

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
    <section
      aria-label="Comments"
      className="flex flex-col gap-3 border-t pt-5"
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        Comments
        <Badge variant="secondary" size="sm" shape="circle">
          {count}
        </Badge>
      </h3>

      {frozen ? null : (
        <Composer
          authorName={me}
          placeholder="Write a comment"
          submitLabel="Comment"
          pending={addComment.isPending && replyTo === null}
          onSubmit={async (body) => {
            await addComment.mutateAsync({ id: ideaId, body });
          }}
        />
      )}

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : threads.length === 0 ? (
        <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed py-6 text-center">
          <span className="text-sm font-medium">No comments yet</span>
          <span className="text-sm text-muted-foreground">
            Comments on this idea appear here.
          </span>
        </div>
      ) : (
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
      )}
    </section>
  );
}
