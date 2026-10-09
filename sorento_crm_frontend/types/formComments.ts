/** The portal form kinds that carry a comment thread. */
export type FormCommentKind =
  | 'complaint'
  | 'stock_inquiry'
  | 'purchase_request'
  | 'sponsorship_form';

export interface FormCommentAttachment {
  attachmentId: string;
  filename: string;
  size: number | null;
  contentType: string | null;
}

export interface FormComment {
  id: string;
  body: string;
  authorName: string;
  authorKind: 'staff' | 'submitter';
  /** Set when an admin viewing as the contact posted it on the contact's behalf. */
  postedByName: string | null;
  createdAt: string;
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
