import { User } from './user';

// Models
export interface SystemLog {
  id: string;
  event: string;
  userId: string;
  createdAt: Date;
  entityId?: string | null;
  entityType?: string | null;
  description?: string | null;
  ipAddress?: string | null;
  user?: User;
  meta?: JSON;
}

export interface SystemSetting {
  id: string;
  name: string;
  logo?: string | null;
  /**
   * The photograph behind the sign-in card, already signed by the backend so an <img>
   * can load it. Null means none is set and the sign-in page draws its designed default.
   */
  signinBackground?: string | null;
  active: boolean;
  address?: string | null;
  websiteURL?: string | null;
  supportEmail?: string | null;
  supportPhone?: string | null;
  language: string;
  timezone: string;
  currency: string;
  currencyFormat: string;

  defaultProductSupplierId?: string | null;
  defaultProductStandardLeadTimeDays?: number;

  /**
   * The unit a product gets when nobody states one - a product import row with no UOM
   * cell, and any other create that leaves it out.
   *
   * A SETTING rather than a constant because the constant was wrong: the code falls back
   * to `EA`, an older fallback took whatever unit the database happened to return first,
   * and 11,415 products were stamped `L` by it. The admin names the right one here and a
   * re-import fixes them; a backfill script would have to guess the same thing this
   * setting lets somebody state.
   */
  defaultUomId?: string | null;
  /** The code of the unit above, resolved by the backend - never a bare UUID on screen. */
  defaultUomCode?: string | null;

  /**
   * `PLAN-oi-request-cs-reserve.md` section 6c (F1): the reserve dialog's own
   * configurable default Location - owner words, "list all the site pool with this BRW
   * (configurable as default)". Null means no configured default; the dialog then
   * falls back to the row's own site pool (R3).
   */
  oiReserveDefaultPoolWarehouseId?: string | null;

  /** Takeover cooldown window in seconds (0 = instant takeover). */
  takeoverCooldownSeconds?: number;
  /** Global default undo grace window for form-SLA actions, in seconds. */
  formSlaGraceSeconds?: number;

  /**
   * The two grace windows a deferred record action waits out (D16).
   *
   * The product asks for no confirmation: a delete or a status change parks on the
   * server and the countdown is the way back, so its LENGTH is the only thing left
   * to tune. Destructive covers every `<entity>.delete`; reversible covers the rest.
   */
  deferredDeleteSeconds?: number;
  deferredActionSeconds?: number;

  /**
   * How many days an untouched price tag collection waits before the sweep
   * closes it (r9 D10). 0 turns the sweep off; 7 is the shipped default.
   */
  priceTagAutoCollectDays?: number;

  /** Days a chatbot file link keeps opening (1..3650). Null = never expires. */
  chatbotFileLinkExpiryDays?: number | null;

  /**
   * SCM front planning: which grain new plans are DECIDED at (AC-F01).
   *
   * Admin policy, not a per-run selector. It is stamped onto each run when the run
   * is created, so changing it affects runs created afterwards and never relabels
   * or unlocks an existing one.
   */
  planGrain?: 'product' | 'location';

  /**
   * Local-supplier Buy routing (PLAN-local-buy-routing-toggle.md), off by default.
   * Off means the whole local rule does not run: no pill, every Buy reaches Order
   * Inquiries and counts toward reorder demand.
   */
  localBuyRoutingEnabled?: boolean;

  /** Sample requests (SR-CORE): submit needs a registered project / a quotation. Off by default. */
  sampleRequestRequireProject?: boolean;

  /**
   * "Borrow from another order" (PLAN-borrow-location-only-8oct.md D1), off by default.
   * Off borrows a location's stock and orders it back on the borrowing line.
   */
  borrowFromOrderEnabled?: boolean;

  /** System default approver for procurement "Send for approval" (skips chooser when set). */
  purchaseRequestDefaultApproverUserId?: string | null;
  purchaseRequestDefaultApproverName?: string | null;
  purchaseRequestDefaultApproverEmail?: string | null;
  sponsorshipFormDefaultApproverUserId?: string | null;
  sponsorshipFormDefaultApproverName?: string | null;
  sponsorshipFormDefaultApproverEmail?: string | null;

  /** When set, used for attachment upload webhooks; otherwise backend may use N8N_WEBHOOK_URL env. */
  n8nAttachmentWebhookUrl?: string | null;
  /** Webhook for CRM Chat Records replies (Respond.io-shaped payload for n8n). */
  n8nCrmChatOutboundWebhookUrl?: string | null;
  /** Webhook for public rejected stock inquiry revise requests. */
  n8nStockInquiryReviseWebhookUrl?: string | null;
  n8nCloseConvoWebhookUrl?: string | null;

  socialFacebook?: string | null;
  socialTwitter?: string | null;
  socialInstagram?: string | null;
  socialLinkedIn?: string | null;
  socialPinterest?: string | null;
  socialYoutube?: string | null;

  notifyStockEmail: boolean;
  notifyStockWeb: boolean;
  notifyStockThreshold: number;
  notifyStockRoleIds: string[];

  notifyNewOrderEmail: boolean;
  notifyNewOrderWeb: boolean;
  notifyNewOrderRoleIds: string[];

  notifyOrderStatusUpdateEmail: boolean;
  notifyOrderStatusUpdateWeb: boolean;
  notifyOrderStatusUpdateRoleIds: string[];

  notifyPaymentFailureEmail: boolean;
  notifyPaymentFailureWeb: boolean;
  notifyPaymentFailureRoleIds: string[];

  notifySystemErrorFailureEmail: boolean;
  notifySystemErrorWeb: boolean;
  notifySystemErrorRoleIds: string[];

  // Complaint <-> DO auto-fulfilment: comma list of Complaint-team tiers (e.g. "1,2")
  // that receive the replacement-DO-delivered notification.
  complaintDoDeliveredNotifyTiers: string;

  // Form handling-lock ("I'm handling this"): the source_entity_types the per-form lock
  // is enabled for (e.g. ["complaint", "stock_inquiry"]). Empty = lock off everywhere.
  handlingLockEnabledTypes: string[];

  // Price tag packages (PLAN-price-tag-combos D2): the product class labels whose
  // lines are warned about when they reach marketing without their catalogue
  // package. Defaults to Bathroom Furniture + Kitchen Sink. Empty = warn nobody.
  priceTagGuardedClasses: string[];

  // Portal submission revisions (UAC A1). `portalRevisionsEnabled` is the kill
  // switch and applies to every form type regardless of its own config row;
  // `portalMaxRevisions` is the fallback cap a type inherits when its own
  // `max_revisions` is NULL.
  portalRevisionsEnabled: boolean;
  portalMaxRevisions: number;

  // System Health observability (daily digest + immediate anomaly alerts).
  /** Send the daily system-health digest (in-app + email) to the notify roles. */
  healthDigestEnabled: boolean;
  /** Send off-cycle anomaly alerts (n8n down, task failed/overdue, integration spike). */
  healthAlertsEnabled: boolean;
  /** Roles that receive the digest + alerts. */
  healthNotifyRoleIds: string[];
  /** Individual users that receive the digest + alerts (unioned with role members). */
  healthNotifyUserIds: string[];
  /** Failed-integration count within the window that trips an immediate alert. */
  healthIntegrationFailThreshold: number;
  /** Expected minimum daily audit-event volume; 0 disables the low-volume warning. */
  healthAuditVolumeFloor: number;

  /** WhatsApp round-trip SLA: the latency target the chosen percentile is held to. */
  chatLatencyTargetSeconds: number;
  /** Which computed percentile alerts (50 / 95 / 99). p50 and p95 are always shown. */
  chatLatencyPercentile: number;
  /** Per-turn hard ceiling = target x this. Fires on ONE bad turn, no minimum sample. */
  chatLatencyCeilingMultiplier: number;
  /** Minutes an incoming message may sit unanswered before alerting. */
  chatLatencyNoReplyMinutes: number;
  /** Below this many turns no percentile is claimed, so the window stays quiet. */
  chatLatencyMinSample: number;

  smtp?: {
    smtp_host?: string | null;
    smtp_port?: string | null;
    smtp_secure?: boolean;
    smtp_username?: string | null;
    smtp_from?: string | null;
    smtp_password?: null; // never returned
  } | null;
}
