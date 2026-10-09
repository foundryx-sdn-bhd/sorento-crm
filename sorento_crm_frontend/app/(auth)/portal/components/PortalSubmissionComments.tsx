'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Trash2 } from 'lucide-react';
import { CommentThreadView } from '@/components/common/comments/CommentThreadView';
import { useDeletingIds } from '@/components/common/comments/useDeletingIds';
import { DeferredCountdown } from '@/components/common/DeferredActionButton';
import LoadErrorState from '@/components/common/LoadErrorState';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { toast } from '@/lib/toast';
import type { PendingAction } from '@/services/pendingActionService';
import { FORM_COMMENT_MAX_FILES, type FormComment } from '@/types/formComments';
import {
  deleteSubmissionComment,
  editSubmissionComment,
  fetchSubmissionComments,
  portalAttachmentDownloadUrl,
  postSubmissionComment,
  type PortalLandingKind,
} from '../lib/portal-client';
import { portalFetchBytes } from '../lib/portal-preview';

/** Same window as a staff hard delete. */
const DELETE_WINDOW_SECONDS = 10;

/**
 * Portal delete: the same in-place countdown with Cancel as the CRM, then the DELETE. The portal
 * has no server-side pending actions for contact tokens, so the window runs in this tab; closing
 * it before the window lapses keeps the comment (crew-ask, round 2).
 */
function PortalCommentDelete({
  commentId,
  onDelete,
  onActive,
}: {
  commentId: string;
  onDelete: (commentId: string) => Promise<void>;
  onActive: (id: string, active: boolean) => void;
}) {
  const [pending, setPending] = useState<PendingAction | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const active = pending !== null;
  useEffect(() => {
    onActive(commentId, active);
    return () => onActive(commentId, false);
  }, [commentId, active, onActive]);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const start = () => {
    const commitAt = new Date(Date.now() + DELETE_WINDOW_SECONDS * 1000);
    setPending({
      id: `local-${commentId}`,
      action_key: 'form_comment.delete',
      entity_type: 'form_comment',
      entity_id: commentId,
      commit_at: commitAt.toISOString(),
      window_seconds: DELETE_WINDOW_SECONDS,
    });
    timer.current = setTimeout(async () => {
      try {
        await onDelete(commentId);
      } finally {
        setPending(null);
      }
    }, DELETE_WINDOW_SECONDS * 1000);
  };

  const cancel = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setPending(null);
  };

  if (pending) {
    return (
      <div className="pt-1">
        <DeferredCountdown
          pending={pending}
          verb="Deleting"
          subject="Comment"
          onCancel={cancel}
        />
      </div>
    );
  }
  return (
    <Button
      type="button"
      variant="dim"
      size="sm"
      className="h-7 gap-1 px-1.5"
      onClick={start}
    >
      <Trash2 className="size-3.5" />
      Delete
    </Button>
  );
}

/**
 * The submitter's side of the form's comment thread (FORM-COMMENTS): the same thread staff see
 * on the CRM detail page, read as one story - the staff reply first, the composer last. A comment
 * emails the form's handler; the reply comes back by WhatsApp and shows here.
 */
export function PortalSubmissionComments({
  kind,
  submissionId,
  authorName,
  readOnly,
}: {
  kind: PortalLandingKind;
  submissionId: string;
  authorName: string;
  readOnly: boolean;
}) {
  const [comments, setComments] = useState<FormComment[]>([]);
  const [loading, setLoading] = useState(true);
  const [posting, setPosting] = useState(false);
  const { isDeleting, report } = useDeletingIds();
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setComments(await fetchSubmissionComments(kind, submissionId));
  }, [kind, submissionId]);

  // A failed load is an inline error with Retry, not a toast: the thread is one card on a
  // longer page, and an empty "No comments yet" would hide that the read failed.
  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      await refresh();
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to load comments.');
    } finally {
      setLoading(false);
    }
  }, [refresh]);

  useEffect(() => {
    void load();
  }, [load]);

  if (loadError) {
    return (
      <Card>
        <CardContent>
          <LoadErrorState
            title="Comments could not be loaded"
            message={loadError}
            onRetry={() => void load()}
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent>
        <CommentThreadView
          className="border-t-0 pt-0"
          comments={comments}
          isLoading={loading}
          authorName={authorName}
          composerPosition="bottom"
          readOnly={readOnly}
          maxFiles={FORM_COMMENT_MAX_FILES}
          pending={posting}
          isDeleting={isDeleting}
          emptyHint="Questions and answers about this form appear here."
          previewItem={(a) => ({
            id: a.attachmentId,
            name: a.filename,
            url: '',
            downloadUrl: portalAttachmentDownloadUrl(a.attachmentId),
            sizeBytes: a.size,
          })}
          fetchBytes={portalFetchBytes}
          onAdd={async (body, files, parentId) => {
            setPosting(true);
            try {
              const { delivery } = await postSubmissionComment(
                kind,
                submissionId,
                body,
                files,
                parentId,
              );
              // Re-read so the reply lands under its parent with the server's flags.
              await refresh();
              if (delivery === 'queued') toast.success('Comment sent');
              else toast.success('Comment saved');
            } catch (e) {
              toast.error(
                e instanceof Error ? e.message : 'Failed to post the comment.',
              );
              // Re-thrown so the composer keeps what was typed.
              throw e;
            } finally {
              setPosting(false);
            }
          }}
          onEdit={async (commentId, body) => {
            try {
              await editSubmissionComment(kind, submissionId, commentId, body);
              await refresh();
              toast.success('Comment saved');
            } catch (e) {
              toast.error(
                e instanceof Error ? e.message : 'Failed to save the comment.',
              );
              throw e;
            }
          }}
          renderDelete={(c) => (
            <PortalCommentDelete
              commentId={c.id}
              onActive={report}
              onDelete={async (commentId) => {
                try {
                  await deleteSubmissionComment(kind, submissionId, commentId);
                  await refresh();
                  toast.success('Comment deleted');
                } catch (e) {
                  toast.error(
                    e instanceof Error
                      ? e.message
                      : 'Failed to delete the comment.',
                  );
                }
              }}
            />
          )}
        />
      </CardContent>
    </Card>
  );
}
