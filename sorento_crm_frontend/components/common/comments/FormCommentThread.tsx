'use client';

import { Badge } from '@/components/ui/badge';
import { formatDateTime } from '@/lib/helpers';
import type { FormComment } from '@/types/formComments';
import {
  CommentAvatar,
  CommentComposer,
  CommentsSection,
} from './CommentParts';

/**
 * The comment thread on a form submission, shared by the CRM detail page (staff) and the
 * contact portal (submitter). Add-only: a posted comment has already gone out by WhatsApp or
 * email, so there is no edit or delete. Oldest first.
 */
export function FormCommentThread({
  comments,
  isLoading,
  authorName,
  readOnly,
  pending,
  emptyHint,
  className,
  onAdd,
}: {
  comments: FormComment[];
  isLoading: boolean;
  authorName: string;
  /** Voided forms keep their thread visible but take no new comments. */
  readOnly: boolean;
  pending: boolean;
  emptyHint: string;
  className?: string;
  onAdd: (body: string) => Promise<void>;
}) {
  return (
    <CommentsSection
      count={comments.length}
      isLoading={isLoading}
      isEmpty={comments.length === 0}
      emptyHint={emptyHint}
      className={className}
      composer={
        readOnly ? null : (
          <CommentComposer
            authorName={authorName}
            placeholder="Write a comment"
            submitLabel="Comment"
            pending={pending}
            onSubmit={onAdd}
          />
        )
      }
    >
      <div className="flex flex-col divide-y">
        {comments.map((comment) => (
          <div key={comment.id} className="flex items-start gap-3 py-3">
            <CommentAvatar name={comment.authorName} />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
                <span className="font-medium">{comment.authorName}</span>
                {comment.authorKind === 'submitter' ? (
                  <Badge variant="outline" size="xs">
                    Submitter
                  </Badge>
                ) : null}
                <span className="text-xs text-muted-foreground">
                  {formatDateTime(comment.createdAt)}
                </span>
              </div>
              <p className="whitespace-pre-wrap break-words text-sm">
                {comment.body}
              </p>
            </div>
          </div>
        ))}
      </div>
    </CommentsSection>
  );
}
