/**
 * Shared soft-pastel status pill - the look used by Complaints, applied across
 * Purchase Requests / Sponsorship Forms / Stock Inquiries so every form status
 * pill reads the same. Mirrors the palette in lib/complaint-status.ts.
 *
 * Render: <span className={`${STATUS_PILL_BASE} ${statusPillClass(status)}`}>{label}</span>
 */
const STATUS_PILL_CLASS: Record<string, string> = {
  draft: 'bg-muted text-muted-foreground',
  new: 'bg-muted text-muted-foreground',
  submitted: 'bg-sky-100 text-sky-800',
  pending: 'bg-amber-100 text-amber-800',
  pending_approval: 'bg-amber-100 text-amber-800',
  pending_project_sales: 'bg-amber-100 text-amber-800',
  pending_purchasing: 'bg-amber-100 text-amber-800',
  updated: 'bg-amber-100 text-amber-800',
  // Work that was accepted and is still running (a queued flyer read). Amber
  // like the other "waiting on something" states, so a list reads at a glance
  // as not-finished-yet rather than as an outcome.
  processing: 'bg-amber-100 text-amber-800',
  responded: 'bg-indigo-100 text-indigo-800',
  approved: 'bg-blue-100 text-blue-800',
  // A quotation the customer holds, and what they answered (QUOTE-APPROVAL-UX).
  issued: 'bg-emerald-100 text-emerald-800',
  accepted: 'bg-emerald-100 text-emerald-800',
  changes_requested: 'bg-amber-100 text-amber-800',
  rejected: 'bg-red-100 text-red-800',
  processed_by_cs: 'bg-emerald-100 text-emerald-800',
  resolved: 'bg-emerald-100 text-emerald-800',
  closed: 'bg-slate-200 text-slate-700',
  // A catalogue Edition that went live. Terminal and successful, so it reads
  // like resolved/processed rather than like closed, which is merely over.
  done: 'bg-emerald-100 text-emerald-800',
  // Voided = form was cancelled/annulled after creation. Neutral gray (muted),
  // deliberately NOT red - voiding is administrative, not an error/rejection.
  voided: 'bg-gray-200 text-gray-600',
  // Outbound delivery (supplier notices, and anything else that leaves the building). Added
  // here rather than as a local vocabulary so "failed" is the same red everywhere; falling back
  // to neutral would have rendered a failed send as calmly as a queued one.
  sent: 'bg-emerald-100 text-emerald-800',
  failed: 'bg-red-100 text-red-800',
  // Skipped is an outcome, not an error: nothing could send, and the reason is stated beside
  // it. Gray for the same reason `voided` is gray.
  skipped: 'bg-gray-200 text-gray-600',
  // Certificate register (LIF-3): the two lifecycle statuses plus the DERIVED
  // validity states. Validity is never a status on the row - it just shares the
  // pill palette so the list reads consistently.
  active: 'bg-emerald-100 text-emerald-800',
  archived: 'bg-slate-200 text-slate-700',
  valid: 'bg-emerald-100 text-emerald-800',
  expiring_soon: 'bg-amber-100 text-amber-800',
  expired: 'bg-red-100 text-red-800',
  not_yet_valid: 'bg-sky-100 text-sky-800',
  unknown: 'bg-muted text-muted-foreground',
  // How a covered-product link got there. Human confirmation reads stronger
  // than a machine reading, so `manual` takes the affirmative colour.
  manual: 'bg-blue-100 text-blue-800',
  ai: 'bg-muted text-muted-foreground',
  // Onboarding requests. `link_sent` rather than reusing `sent`: an onboarding
  // link that went out is a WAIT (nobody has filled anything in yet), while
  // outbound delivery's `sent` is a success, and the two must not share a
  // colour. The onboarding surfaces map their `sent` status onto this code.
  //
  // Violet, deliberately NOT the sky that `submitted` wears. Those two carry
  // the queue's whole reading: sky means the batch is back with us and wants a
  // decision, violet means it is still out with the requester and there is
  // nothing to do. Painting both sky made the queue answer neither.
  link_sent: 'bg-violet-100 text-violet-800',
  in_review: 'bg-amber-100 text-amber-800',
  // `processing` is deliberately absent: the entry above already means "accepted
  // and still running", which is exactly what a queued provisioning job is.
  completed: 'bg-emerald-100 text-emerald-800',
  // Finished, but not cleanly: amber for the same reason a partial import is
  // amber - somebody still has to look at it.
  partially_completed: 'bg-amber-100 text-amber-800',
  cancelled: 'bg-gray-200 text-gray-600',
  // Product specifications. A spec row's status, plus whether the product can be
  // found by describing it. Every colour above is reused: a spec status is not a
  // new kind of state, it is the same three-way "machine said / person said /
  // somebody must look" the rest of the system already renders.
  derived: 'bg-muted text-muted-foreground',
  needs_review: 'bg-amber-100 text-amber-800',
  // A person's own word, so it takes the same affirmative blue as `manual`.
  authored: 'bg-blue-100 text-blue-800',
  findable: 'bg-emerald-100 text-emerald-800',
  // Neutral, not red: a product nothing can find yet is unfinished, not broken.
  not_findable: 'bg-gray-200 text-gray-600',
  // Verification (PR 3). Registered here with no call site on purpose, so the
  // slice that builds the verification screen cannot invent a second palette for
  // states this one already named.
  verified: 'bg-emerald-100 text-emerald-800',
  needs_reverify: 'bg-amber-100 text-amber-800',
  unverified: 'bg-muted text-muted-foreground',
};

/** Base classes for the pill chip (shape + sizing); pair with statusPillClass(). */
export const STATUS_PILL_BASE =
  'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold capitalize';

/** Pastel pill colour for a status. Accepts raw codes or display labels
 * ("Pending approval" -> pending_approval). Falls back to neutral. */
export function statusPillClass(status?: string | null): string {
  const s = (status ?? '').trim().toLowerCase().replace(/\s+/g, '_');
  return STATUS_PILL_CLASS[s] ?? 'bg-muted text-muted-foreground';
}

/**
 * The same palette, `text-*` only - for an icon-only control (a toolbar button, a row
 * icon) that reads the status by colour but carries no pill background of its own.
 * Reviewer round (S6, `PLAN-oi-request-cs-reserve.md`): an OI row's own Reserve
 * icon-button used to hardcode `text-amber-600`/`text-emerald-600` locally rather than
 * reading this ONE source of truth, so it could silently drift from every pill that
 * already reads `pending`/`done` this way (`OrderInquiryVerbPill.tsx`'s own
 * `ReservePill`).
 */
export function statusTextClass(status?: string | null): string {
  const classes = statusPillClass(status).split(' ');
  return classes.find((cls) => cls.startsWith('text-')) ?? 'text-muted-foreground';
}
