'use client';

import { useCallback, useEffect, useState } from 'react';
import { FormCommentThread } from '@/components/common/comments/FormCommentThread';
import { Card, CardContent } from '@/components/ui/card';
import { toast } from '@/lib/toast';
import type { FormComment } from '@/types/formComments';
import {
  fetchSubmissionComments,
  portalAttachmentDownloadUrl,
  postSubmissionComment,
  type PortalLandingKind,
} from '../lib/portal-client';
import { portalFetchBytes } from '../lib/portal-preview';

/**
 * The submitter's side of the form's comment thread (FORM-COMMENTS): the same thread staff see
 * on the CRM detail page. A comment emails the form's handler; the reply comes back by WhatsApp
 * and shows here.
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

  const load = useCallback(async () => {
    try {
      setComments(await fetchSubmissionComments(kind, submissionId));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Failed to load comments.');
    } finally {
      setLoading(false);
    }
  }, [kind, submissionId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card>
      <CardContent>
        <FormCommentThread
          className="border-t-0 pt-0"
          comments={comments}
          isLoading={loading}
          authorName={authorName}
          readOnly={readOnly}
          pending={posting}
          emptyHint="Questions and answers about this form appear here."
          previewItem={(a) => ({
            id: a.attachmentId,
            name: a.filename,
            url: '',
            downloadUrl: portalAttachmentDownloadUrl(a.attachmentId),
            sizeBytes: a.size,
          })}
          fetchBytes={portalFetchBytes}
          onAdd={async (body, files) => {
            setPosting(true);
            try {
              const { comment, delivery } = await postSubmissionComment(
                kind,
                submissionId,
                body,
                files,
              );
              setComments((prev) => [...prev, comment]);
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
        />
      </CardContent>
    </Card>
  );
}
