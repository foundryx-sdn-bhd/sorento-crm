'use client';

import { useSession } from 'next-auth/react';
import { Card, CardContent } from '@/components/ui/card';
import {
  useAddFormComment,
  useFormCommentsQuery,
} from '@/hooks/useFormComments';
import type { FormCommentKind } from '@/types/formComments';
import { FormCommentThread } from './FormCommentThread';

/** Staff side of a form submission's comment thread (CRM detail pages). */
export function FormComments({
  kind,
  entityId,
  readOnly = false,
}: {
  kind: FormCommentKind;
  entityId: string;
  readOnly?: boolean;
}) {
  const { data: session } = useSession();
  const { data, isLoading } = useFormCommentsQuery(kind, entityId);
  const add = useAddFormComment(kind, entityId);

  return (
    <Card>
      <CardContent>
        <FormCommentThread
          className="border-t-0 pt-0"
          comments={data ?? []}
          isLoading={isLoading}
          authorName={session?.user?.name ?? ''}
          readOnly={readOnly}
          pending={add.isPending}
          emptyHint="Comments between the submitter and the team appear here."
          previewItem={(a) => ({
            id: a.attachmentId,
            name: a.filename,
            url: '',
            downloadUrl: `/api/v1/resource-management/attachments/${a.attachmentId}/download`,
            sizeBytes: a.size,
          })}
          onAdd={async (body, files) => {
            await add.mutateAsync({ body, files });
          }}
        />
      </CardContent>
    </Card>
  );
}
