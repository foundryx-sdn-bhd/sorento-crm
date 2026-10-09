/** The portal form kinds that carry a comment thread. */
export type FormCommentKind =
  | 'complaint'
  | 'stock_inquiry'
  | 'purchase_request'
  | 'sponsorship_form';

export interface FormComment {
  id: string;
  body: string;
  authorName: string;
  authorKind: 'staff' | 'submitter';
  createdAt: string;
}

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
