'use client';

import { useContext, useEffect } from 'react';
import { SessionContext } from 'next-auth/react';
import { Trash2 } from 'lucide-react';
import LoadErrorState from '@/components/common/LoadErrorState';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import {
  FORM_COMMENTS_KEY,
  useAddFormComment,
  useEditFormComment,
  useFormCommentsQuery,
} from '@/hooks/useFormComments';
import {
  FORM_COMMENT_MAX_FILES,
  type FormCommentKind,
} from '@/types/formComments';
import { CommentThreadView } from './CommentThreadView';
import { useDeletingIds } from './useDeletingIds';

/** Staff delete: the server-deferred countdown in place of the button (D7), never a dialog. */
function FormCommentDelete({
  kind,
  entityId,
  commentId,
  onActive,
}: {
  kind: FormCommentKind;
  entityId: string;
  commentId: string;
  onActive: (id: string, active: boolean) => void;
}) {
  const removal = useDeferredAction({
    actionKey: 'form_comment.delete',
    entityType: 'form_comment',
    entityId: commentId,
    verb: 'Deleting',
    subject: 'Comment',
    surface: 'inline',
    successMessage: 'Comment deleted',
    invalidateKeys: [[FORM_COMMENTS_KEY, kind, entityId]],
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
 * Staff side of a form submission's comment thread (CRM detail pages): the thread shared with
 * ideation, read as one story - the staff reply first, comments oldest first, the composer last.
 */
export function FormComments({
  kind,
  entityId,
  readOnly = false,
}: {
  kind: FormCommentKind;
  entityId: string;
  readOnly?: boolean;
}) {
  // The session is only needed for the composer avatar's name; read the context directly so
  // the thread also renders where no SessionProvider wraps the page (detail-page tests).
  const session = useContext(SessionContext)?.data;
  const { data, isLoading, isError, error, refetch, isFetching } =
    useFormCommentsQuery(kind, entityId);
  const add = useAddFormComment(kind, entityId);
  const edit = useEditFormComment(kind, entityId);
  const { isDeleting, report } = useDeletingIds();

  if (isError) {
    return (
      <Card>
        <CardContent>
          <LoadErrorState
            title="Comments could not be loaded"
            message={error instanceof Error ? error.message : undefined}
            onRetry={() => void refetch()}
            retrying={isFetching}
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
          comments={data ?? []}
          isLoading={isLoading}
          authorName={session?.user?.name ?? ''}
          composerPosition="bottom"
          readOnly={readOnly}
          maxFiles={FORM_COMMENT_MAX_FILES}
          pending={add.isPending}
          editPending={edit.isPending}
          isDeleting={isDeleting}
          emptyHint="Comments between the submitter and the team appear here."
          previewItem={(a) => ({
            id: a.attachmentId,
            name: a.filename,
            url: '',
            downloadUrl: `/api/v1/resource-management/attachments/${a.attachmentId}/download`,
            sizeBytes: a.size,
          })}
          onAdd={async (body, files, parentId) => {
            await add.mutateAsync({ body, files, parentId });
          }}
          onEdit={async (commentId, body) => {
            await edit.mutateAsync({ commentId, body });
          }}
          renderDelete={(c) => (
            <FormCommentDelete
              kind={kind}
              entityId={entityId}
              commentId={c.id}
              onActive={report}
            />
          )}
        />
      </CardContent>
    </Card>
  );
}
