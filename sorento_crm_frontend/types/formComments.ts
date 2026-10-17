/** The portal form kinds that carry a comment thread. */
export type FormCommentKind =
  | 'complaint'
  | 'stock_inquiry'
  | 'purchase_request'
  | 'sponsorship_form'
  | 'sample_request';

export interface FormCommentAttachment {
  attachmentId: string;
  filename: string;
  size: number | null;
  contentType: string | null;
}

export interface FormComment {
  id: string;
  /** `response` is the form's staff reply, served as the thread's first entry. */
  kind: 'comment' | 'response';
  parentId: string | null;
  body: string;
  authorName: string;
  authorKind: 'staff' | 'submitter';
  /** Set when an admin viewing as the contact posted it on the contact's behalf. */
  postedByName: string | null;
  createdAt: string | null;
  editedAt: string | null;
  deleted: boolean;
  canEdit: boolean;
  canDelete: boolean;
  canReply: boolean;
  attachments: FormCommentAttachment[];
}

/** Server ceiling on files per comment. */
export const FORM_COMMENT_MAX_FILES = 5;

/** What happened to the notification after the comment was saved. */
export type FormCommentDelivery =
  | 'queued'
  | 'no_whatsapp'
  | 'no_recipients'
  | 'failed';

export interface FormCommentPostResult {
  comment: FormComment;
  delivery: FormCommentDelivery;
}
