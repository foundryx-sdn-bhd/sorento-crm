/**
 * The portal form kinds and their labels - one list, read by both sides.
 *
 * The portal reads it to build its landing dropdown and its submission pages.
 * The CRM's Contact Access Types screen reads it to offer an admin the kinds it
 * may grant (D61b). They lived in `app/(auth)/portal/lib/portal-client.ts` until
 * the admin screen needed them; that module re-exports every name below, so the
 * portal's imports are unchanged and neither side can label a kind differently
 * from the other.
 *
 * Deliberately free of imports: an admin page under `(protected)` has no
 * business pulling in the portal's token storage and fetch helpers.
 */

export type PortalSubmissionKind =
  | 'complaint'
  | 'stock_inquiry'
  | 'purchase_request'
  | 'sponsorship_form';

/** Canonical kind list - single source for route guards, tab lists, labels. */
export const SUBMISSION_KINDS: readonly PortalSubmissionKind[] = [
  'complaint',
  'stock_inquiry',
  'purchase_request',
  'sponsorship_form',
] as const;

export function isSubmissionKind(
  value: string | null | undefined,
): value is PortalSubmissionKind {
  return (SUBMISSION_KINDS as readonly string[]).includes(value ?? '');
}

/**
 * Every form the landing dropdown can offer. Price Tag Request has a page of
 * its own (not part of `SUBMISSION_KINDS`, which is what the generic `[type]`
 * route guards read to decide what the shared submission pages may render),
 * but joins this list so it shows up beside the four legacy kinds.
 *
 * All five are gated the same way: only shown to a contact whose
 * `visible_form_types` (server-resolved: base kinds + market segment grants,
 * minus overrides) includes them (D2/D3). There is no "always on" kind and no
 * "gated" subset any more - PLAN-portal-forms-market-segment D6.
 */
export const GATED_FORM_TYPE = 'price_tag_request' as const;

/**
 * Sales Opportunity (plan S2, section 16) is grantable the same way `price_tag_request` is,
 * and since fix lane round 2 (owner ruling 27 Sep, F1) it is one kind in the landing's kind
 * selector exactly like Price Tag Request: same selector, count, search, filter, sort, list
 * and grid, and New button. Its pages stay its own (`app/(auth)/portal/sales_opportunity/`),
 * as Price Tag Request's do; neither is a `SUBMISSION_KINDS` entry, so the generic `[type]`
 * submission pages never render either.
 */
export const SALES_OPPORTUNITY_KIND = 'sales_opportunity' as const;

/**
 * Customer asks (chatbot stock ask v2, fix round 5, owner 29 Sep): the stock asks of the
 * customers assigned to the contact's linked sales agent. One kind in the selector like
 * Price Tag Request, switched per contact the same way (default off), and only offered by
 * the server to a contact linked to a sales agent. No form and no pages of its own: the
 * landing renders `CustomerAsksList` as its body, and there is no New button.
 */
export const CUSTOMER_ASKS_KIND = 'customer_asks' as const;

/**
 * Conversation (lane SALES-CONVO, owner 30 Sep): the WhatsApp conversations of the customers
 * assigned to the contact's linked sales agent, read-only. Switched per contact exactly like
 * Customer asks (default off, agents only, resolved server-side). No form, no pages, no New
 * button: the landing renders `ConversationList` as its body.
 */
export const CONVERSATION_KIND = 'conversation' as const;

/**
 * Sample Request (lane SR-CORE): a consignment requisition for samples to a project site.
 * Grantable per contact exactly like `price_tag_request`; its pages are its own
 * (`app/(auth)/portal/c/[slug]/sample_request/`).
 */
export const SAMPLE_REQUEST_KIND = 'sample_request' as const;

export type PortalLandingKind =
  | PortalSubmissionKind
  | typeof GATED_FORM_TYPE
  | typeof SAMPLE_REQUEST_KIND
  | typeof SALES_OPPORTUNITY_KIND
  | typeof CUSTOMER_ASKS_KIND
  | typeof CONVERSATION_KIND;

/** Every kind an access type may be granted, in the order the admin sees them. */
export const LANDING_KINDS: readonly PortalLandingKind[] = [
  ...SUBMISSION_KINDS,
  SAMPLE_REQUEST_KIND,
  GATED_FORM_TYPE,
  SALES_OPPORTUNITY_KIND,
  CUSTOMER_ASKS_KIND,
  CONVERSATION_KIND,
] as const;

/**
 * Every contact already gets `SUBMISSION_KINDS` (the base default,
 * PLAN-portal-forms-market-segment D3) - a market segment can only grant more
 * on top of that base, never take a base kind away. This is what the Market
 * Segments admin's "Additional portal forms" field offers (AC-M2); the next
 * opt-in kind joins `LANDING_KINDS` above and appears here automatically.
 */
export const ADDITIONAL_LANDING_KINDS: readonly PortalLandingKind[] = LANDING_KINDS.filter(
  (k) => !isSubmissionKind(k),
);

export function isLandingKind(
  value: string | null | undefined,
): value is PortalLandingKind {
  return (LANDING_KINDS as readonly string[]).includes(value ?? '');
}

export const SUBMISSION_LABELS: Record<PortalSubmissionKind, string> = {
  complaint: 'Complaint',
  stock_inquiry: 'Stock Inquiry',
  purchase_request: 'Purchase Request',
  sponsorship_form: 'Sponsorship Form',
};

/** Every kind the landing can list, legacy or gated (D45). */
export const LANDING_LABELS: Record<PortalLandingKind, string> = {
  ...SUBMISSION_LABELS,
  price_tag_request: 'Price Tag Request',
  sample_request: 'Sample Request',
  sales_opportunity: 'Sales Opportunity',
  customer_asks: 'Customer asks',
  conversation: 'Conversation',
};

/** The label for any kind, falling back to the raw code for one we do not know. */
export function portalFormKindLabel(kind: string): string {
  return LANDING_LABELS[kind as PortalLandingKind] ?? kind;
}
