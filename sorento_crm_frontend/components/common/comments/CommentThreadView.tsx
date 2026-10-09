'use client';

import { useMemo, useState, type ReactNode } from 'react';
import { CornerDownRight, Paperclip, Pencil } from 'lucide-react';
import AttachmentPreviewModal, {
  type AttachmentPreviewItem,
} from '@/components/common/AttachmentPreviewModal';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { formatDateTime } from '@/lib/helpers';
import { cn } from '@/lib/utils';
import {
  CommentAvatar,
  CommentComposer,
  CommentsSection,
} from './CommentParts';

/** One entry of a thread, in ideation's shape plus what form threads add. */
export interface ThreadComment {
  id: string;
  /** `response` is the form's staff reply shown as the first entry; it takes no actions. */
  kind?: 'comment' | 'response';
  parentId: string | null;
  body: string;
  authorName: string;
  /** Drives the "Submitter" badge; absent on ideation. */
  authorKind?: 'staff' | 'submitter';
  /** Set when an admin viewing as the contact posted it on the contact's behalf. */
  postedByName?: string | null;
  createdAt: string | null;
  editedAt: string | null;
  deleted: boolean;
  canEdit: boolean;
  canDelete: boolean;
  canReply: boolean;
  attachments?: ThreadAttachment[];
}

export interface ThreadAttachment {
  attachmentId: string;
  filename: string;
  size: number | null;
  contentType: string | null;
}

type FetchBytes = (item: AttachmentPreviewItem) => Promise<Response>;

export interface CommentThreadViewProps {
  comments: ThreadComment[];
  isLoading: boolean;
  authorName: string;
  /** Ideation keeps the composer on top; a form thread reads as one story, composer last. */
  composerPosition: 'top' | 'bottom';
  /** No composer and no Reply (a frozen idea, a voided form). */
  readOnly: boolean;
  emptyHint: string;
  className?: string;
  /** Files per comment; omit for text only (ideation). */
  maxFiles?: number;
  pending?: boolean;
  /** True while an edit is being saved. */
  editPending?: boolean;
  onAdd: (body: string, files: File[], parentId?: string) => Promise<void>;
  onEdit: (id: string, body: string) => Promise<void>;
  /** The host's delete control (a server-deferred countdown), rendered where Delete goes. */
  renderDelete?: (comment: ThreadComment) => ReactNode;
  /** True while that comment's delete countdown runs: the row dims. */
  isDeleting?: (comment: ThreadComment) => boolean;
  previewItem?: (attachment: ThreadAttachment) => AttachmentPreviewItem;
  fetchBytes?: FetchBytes;
}

/**
 * The comment thread shared by ideation and form submissions: oldest first, one reply level,
 * an "edited" tag, a "Comment deleted" placeholder when a deleted comment still has replies.
 * Delete belongs to the host (a server-deferred countdown, never a dialog).
 */
export function CommentThreadView({
  comments,
  isLoading,
  authorName,
  composerPosition,
  readOnly,
  emptyHint,
  className,
  maxFiles,
  pending,
  editPending,
  onAdd,
  onEdit,
  renderDelete,
  isDeleting,
  previewItem,
  fetchBytes,
}: CommentThreadViewProps) {
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [preview, setPreview] = useState<{
    items: AttachmentPreviewItem[];
    index: number;
  } | null>(null);

  const { threads, count } = useMemo(() => {
    const top = comments.filter((c) => !c.parentId);
    return {
      // A deleted comment stays only as the placeholder its replies hang from.
      threads: top
        .map((c) => ({
          root: c,
          replies: comments.filter((r) => r.parentId === c.id && !r.deleted),
        }))
        .filter(({ root, replies }) => !root.deleted || replies.length > 0),
      count: comments.filter((c) => !c.deleted && c.kind !== 'response').length,
    };
  }, [comments]);

  const composer = readOnly ? null : (
    <CommentComposer
      authorName={authorName}
      placeholder="Write a comment"
      submitLabel="Comment"
      pending={pending && replyTo === null}
      maxFiles={maxFiles}
      onSubmit={(body, files) => onAdd(body, files)}
    />
  );

  const renderItem = (
    comment: ThreadComment,
    rootId: string,
    small?: boolean,
  ) => {
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
    if (editing === comment.id) {
      return (
        <div className="py-3">
          <CommentComposer
            authorName={comment.authorName}
            placeholder="Edit comment"
            submitLabel="Save"
            initial={comment.body}
            pending={editPending}
            small={small}
            autoFocus
            onCancel={() => setEditing(null)}
            onSubmit={async (body) => {
              await onEdit(comment.id, body);
              setEditing(null);
            }}
          />
        </div>
      );
    }
    const isResponse = comment.kind === 'response';
    const attachments = comment.attachments ?? [];
    const countingDown = Boolean(isDeleting?.(comment));
    const canReply = !readOnly && !isResponse && !countingDown && comment.canReply;
    const canEdit = !readOnly && !isResponse && !countingDown && comment.canEdit;
    const canDelete = !readOnly && !isResponse && comment.canDelete;
    return (
      <div
        className={cn(
          'flex items-start gap-3 py-3',
          countingDown && 'opacity-60',
        )}
      >
        <CommentAvatar name={comment.authorName} small={small} />
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-sm">
            <span className="font-medium">{comment.authorName}</span>
            {comment.authorKind === 'submitter' ? (
              <Badge variant="outline" size="xs">
                Submitter
              </Badge>
            ) : null}
            {comment.createdAt ? (
              <span className="text-xs text-muted-foreground">
                {formatDateTime(comment.createdAt)}
              </span>
            ) : null}
            {comment.editedAt ? (
              <Badge variant="outline" size="xs">
                edited
              </Badge>
            ) : null}
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
          {attachments.length > 0 && previewItem ? (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {attachments.map((attachment, index) => (
                <Button
                  key={attachment.attachmentId}
                  type="button"
                  variant="outline"
                  size="sm"
                  className="h-7 max-w-full gap-1 px-2"
                  title={attachment.filename}
                  onClick={() =>
                    setPreview({ items: attachments.map(previewItem), index })
                  }
                >
                  <Paperclip className="size-3.5 shrink-0" />
                  <span className="truncate">{attachment.filename}</span>
                </Button>
              ))}
            </div>
          ) : null}
          {canReply || canEdit || canDelete ? (
            <div className="flex flex-wrap items-center gap-1 pt-0.5">
              {canReply ? (
                <Button
                  type="button"
                  variant="dim"
                  size="sm"
                  className="h-7 gap-1 px-1.5"
                  onClick={() => setReplyTo(rootId)}
                >
                  <CornerDownRight className="size-3.5" />
                  Reply
                </Button>
              ) : null}
              {canEdit ? (
                <Button
                  type="button"
                  variant="dim"
                  size="sm"
                  className="h-7 gap-1 px-1.5"
                  onClick={() => setEditing(comment.id)}
                >
                  <Pencil className="size-3.5" />
                  Edit
                </Button>
              ) : null}
              {canDelete && renderDelete ? renderDelete(comment) : null}
            </div>
          ) : null}
        </div>
      </div>
    );
  };

  return (
    <>
      <CommentsSection
        count={count}
        isLoading={isLoading}
        isEmpty={threads.length === 0}
        emptyHint={emptyHint}
        className={className}
        composer={composerPosition === 'top' ? composer : null}
        footer={composerPosition === 'bottom' ? composer : null}
      >
        <div className="flex flex-col divide-y">
          {threads.map(({ root, replies }) => (
            <div key={root.id}>
              {renderItem(root, root.id)}
              {replies.length > 0 || replyTo === root.id ? (
                <div className="ms-4 border-s ps-4 sm:ms-11">
                  {replies.map((reply) => (
                    <div key={reply.id}>{renderItem(reply, root.id, true)}</div>
                  ))}
                  {replyTo === root.id ? (
                    <div className="pb-3">
                      <CommentComposer
                        authorName={authorName}
                        placeholder="Write a reply"
                        submitLabel="Reply"
                        pending={pending}
                        maxFiles={maxFiles}
                        small
                        autoFocus
                        onCancel={() => setReplyTo(null)}
                        onSubmit={async (body, files) => {
                          await onAdd(body, files, root.id);
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
      {previewItem ? (
        <AttachmentPreviewModal
          open={preview !== null}
          onOpenChange={(open) => {
            if (!open) setPreview(null);
          }}
          items={preview?.items ?? []}
          startIndex={preview?.index ?? 0}
          fetchBytes={fetchBytes}
        />
      ) : null}
    </>
  );
}
