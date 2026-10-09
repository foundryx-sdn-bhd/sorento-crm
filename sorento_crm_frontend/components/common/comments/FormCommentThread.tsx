'use client';

import { useState } from 'react';
import { Paperclip } from 'lucide-react';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { formatDateTime } from '@/lib/helpers';
import {
  FORM_COMMENT_MAX_FILES,
  type FormComment,
  type FormCommentAttachment,
} from '@/types/formComments';
import {
  CommentAvatar,
  CommentComposer,
  CommentsSection,
} from './CommentParts';

type FetchBytes = (item: AttachmentPreviewItem) => Promise<Response>;

/**
 * The comment thread on a form submission, shared by the CRM detail page (staff) and the
 * contact portal (submitter). Add-only: a posted comment has already gone out by WhatsApp or
 * email, so there is no edit or delete. Oldest first. A comment may carry files; they open in the
 * shared preview modal, and each side supplies its own byte route.
 */
export function FormCommentThread({
  comments,
  isLoading,
  authorName,
  readOnly,
  pending,
  emptyHint,
  className,
  previewItem,
  fetchBytes,
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
  previewItem: (attachment: FormCommentAttachment) => AttachmentPreviewItem;
  /** Byte reader for the preview modal; the staff default (`apiFetch`) when omitted. */
  fetchBytes?: FetchBytes;
  onAdd: (body: string, files: File[]) => Promise<void>;
}) {
  const [preview, setPreview] = useState<{
    items: AttachmentPreviewItem[];
    index: number;
  } | null>(null);

  return (
    <>
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
              maxFiles={FORM_COMMENT_MAX_FILES}
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
                {comment.postedByName ? (
                  <span className="text-xs text-muted-foreground">
                    Posted by {comment.postedByName} on behalf
                  </span>
                ) : null}
                {comment.body ? (
                  <p className="whitespace-pre-wrap break-words text-sm">
                    {comment.body}
                  </p>
                ) : null}
                {comment.attachments.length > 0 ? (
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    {comment.attachments.map((attachment, index) => (
                      <Button
                        key={attachment.attachmentId}
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-7 max-w-full gap-1 px-2"
                        title={attachment.filename}
                        onClick={() =>
                          setPreview({
                            items: comment.attachments.map(previewItem),
                            index,
                          })
                        }
                      >
                        <Paperclip className="size-3.5 shrink-0" />
                        <span className="truncate">{attachment.filename}</span>
                      </Button>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      </CommentsSection>
      <AttachmentPreviewModal
        open={preview !== null}
        onOpenChange={(open) => {
          if (!open) setPreview(null);
        }}
        items={preview?.items ?? []}
        startIndex={preview?.index ?? 0}
        fetchBytes={fetchBytes}
      />
    </>
  );
}
