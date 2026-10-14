/**
 * Project Sales types. Mirrors `app/schemas/projects.py`.
 *
 * Two contract details carry meaning that is easy to lose:
 *
 * - `can_edit` is computed by the SERVER from ownership, collaborator rows and the
 *   manage permission. Never re-derive it in the browser: a second implementation of
 *   the ownership rule is a second place for it to disagree with the API, and the API
 *   is the one that decides.
 * - A clash candidate's `similarity` and `blocks` are independent. A perfect title
 *   match on a lost project is 1.0 and still does not block, because re-tendering is
 *   legitimate. Render the list from `blocks`, never from a similarity threshold.
 */

export type ProjectOutcome = 'open' | 'won' | 'lost' | 'dormant';

export type PartyType =
  | 'developer'
  | 'architect'
  | 'main_contractor'
  | 'trading_house'
  | 'consultant';

export type InfluenceLevel = 'high' | 'medium' | 'low';

export interface ProjectParty {
  id: string;
  party_type: PartyType;
  name: string;
  registration_no?: string | null;
  address?: string | null;
  phone?: string | null;
  email?: string | null;
  notes?: string | null;
  customer_id?: string | null;
  customer_name?: string | null;
  is_active: boolean;
  project_count?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectPartyBody {
  party_type: PartyType;
  name: string;
  registration_no?: string | null;
  address?: string | null;
  phone?: string | null;
  email?: string | null;
  notes?: string | null;
  customer_id?: string | null;
  is_active?: boolean;
}

export interface ProjectTemplateRole {
  id: string;
  name: string;
  sort_order: number;
  is_active: boolean;
}

export interface ProjectType {
  id: string;
  name: string;
  code: string;
  description?: string | null;
  /**
   * Property developments infer a delivery window from launch date plus a
   * configurable lag; every other type has to state the window explicitly, so the
   * registration form switches which fields it requires on this flag.
   */
  derives_delivery_from_launch: boolean;
  sort_order: number;
  is_active: boolean;
  template_count?: number | null;
}

export interface ProjectTemplate {
  id: string;
  type_id: string;
  type_name?: string | null;
  name: string;
  description?: string | null;
  is_active: boolean;
  roles: ProjectTemplateRole[];
  has_forked_status_graph: boolean;
}

export interface ProjectTypeBody {
  name: string;
  code: string;
  description?: string | null;
  derives_delivery_from_launch?: boolean;
  sort_order?: number;
  is_active?: boolean;
}

/**
 * `role_names` is the WHOLE role list, not a delta. The server reconciles by name:
 * a name that disappears is deactivated rather than deleted when stakeholders still
 * reference it, so history survives a rename of the template's cast.
 */
export interface ProjectTemplateBody {
  type_id: string;
  name: string;
  description?: string | null;
  is_active?: boolean;
  role_names?: string[];
}

export interface Project {
  id: string;
  project_code: string;
  title: string;
  outcome: ProjectOutcome;
  loss_reason?: string | null;
  /** Project sales admin's filing reference, e.g. PS26-0143. Not an identity: it is
   *  the string written on every piece of paper for this job, and it is how they find
   *  the file. Searchable, and shown beside the project code rather than instead of it. */
  admin_ref?: string | null;

  developer_party_id?: string | null;
  developer_name?: string | null;
  type_id?: string | null;
  type_name?: string | null;
  template_id?: string | null;
  template_name?: string | null;

  status_id?: string | null;
  status_key?: string | null;
  status_label?: string | null;

  owner_user_id?: string | null;
  owner_name?: string | null;

  is_critical: boolean;
  critical_at?: string | null;
  management_support?: string | null;
  management_notes?: string | null;

  registered_company_name?: string | null;
  location?: string | null;
  address?: string | null;
  architect_party_id?: string | null;
  architect_name?: string | null;
  main_contractor_party_id?: string | null;
  main_contractor_name?: string | null;
  estimated_sales_value?: string | null;
  launch_date?: string | null;
  expected_delivery_from?: string | null;
  expected_delivery_to?: string | null;
  brands: string[];
  brand_ids: string[];

  /**
   * Provenance (AC-O10). All null when the project was registered directly, which the
   * detail page states explicitly rather than rendering an empty section.
   */
  lead_id?: string | null;
  lead_code?: string | null;
  lead_source?: string | null;
  lead_created_at?: string | null;
  lead_owner_user_id?: string | null;
  /** The lead's buyer: a project has no customer of its own (owner, 13 Oct). */
  customer_id?: string | null;
  customer_name?: string | null;
  customer_phone?: string | null;
  /** The customer's main contact, which the quotation header's Attn defaults to. */
  customer_attn?: string | null;

  last_meaningful_activity_at?: string | null;
  days_since_last_activity?: number | null;
  /**
   * DERIVED server-side from the earliest open task (AC-N6), never stored. Null with
   * `open_task_count > 0` means there IS open work but none of it is dated.
   */
  next_action_date?: string | null;
  next_action_overdue: boolean;
  open_task_count: number;

  /**
   * Staleness ladder, stamped by the daily sweep (AC-H6): 0 fine, 1 owner nudged, 2 owner
   * warned and management copied, 3 Unattended and open to takeover requests. Read, never
   * recomputed client-side - the sweep is what notified the owner.
   */
  stale_level: number;
  stale_reason?: 'overdue_task' | 'no_activity' | null;
  stale_since?: string | null;
  is_unattended: boolean;
  can_edit: boolean;

  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectRegisterBody {
  title: string;
  developer_party_id?: string | null;
  type_id?: string | null;
  template_id?: string | null;
  owner_user_id?: string | null;
  registered_company_name?: string | null;
  location?: string | null;
  address?: string | null;
  /** Filing reference, e.g. PS26-0143 (D7). Settable on create too, not edit-only. */
  admin_ref?: string | null;
  architect_party_id?: string | null;
  main_contractor_party_id?: string | null;
  estimated_sales_value?: string | null;
  launch_date?: string | null;
  expected_delivery_from?: string | null;
  expected_delivery_to?: string | null;
  brand_ids?: string[];
  /** Which lead this project came from. `null` unlinks (D6); omit to leave alone. */
  lead_id?: string | null;
}

export interface ProjectUpdateBody extends Partial<ProjectRegisterBody> {
  is_critical?: boolean;
  management_support?: string | null;
  management_notes?: string | null;
  loss_reason?: string | null;
}

export interface ClashCandidate {
  project_id: string;
  project_code: string;
  title: string;
  outcome: ProjectOutcome;
  status_label?: string | null;
  owner_user_id?: string | null;
  owner_name?: string | null;
  developer_name?: string | null;
  estimated_sales_value?: string | null;
  brands: string[];
  last_activity_at?: string | null;
  similarity: number;
  blocks: boolean;
}

export interface ClashPreview {
  candidates: ClashCandidate[];
  would_block: boolean;
}

export interface ProjectStakeholder {
  id: string;
  project_id: string;
  person_name: string;
  role_id?: string | null;
  role_name?: string | null;
  party_id?: string | null;
  party_name?: string | null;
  job_title?: string | null;
  phone?: string | null;
  email?: string | null;
  influence?: InfluenceLevel | null;
  is_primary: boolean;
  notes?: string | null;
}

export interface ProjectStakeholderBody {
  person_name: string;
  role_id?: string | null;
  party_id?: string | null;
  job_title?: string | null;
  phone?: string | null;
  email?: string | null;
  influence?: InfluenceLevel | null;
  is_primary?: boolean;
  notes?: string | null;
}

export interface TakeoverRequest {
  id: string;
  project_id: string;
  project_code?: string | null;
  project_title?: string | null;
  kind: 'join' | 'dispute';
  reason: string;
  status: 'pending' | 'approved' | 'rejected';
  requester_user_id: string;
  requester_name?: string | null;
  decided_by?: string | null;
  decided_by_name?: string | null;
  decided_at?: string | null;
  decision_note?: string | null;
  created_at?: string | null;
}

export interface ProjectCollaborator {
  project_id: string;
  user_id: string;
  user_name?: string | null;
  granted_by?: string | null;
  granted_at?: string | null;
}

export interface ProjectListParams {
  query?: string;
  status_id?: string[];
  outcome?: string[];
  owner_user_id?: string[];
  developer_party_id?: string[];
  type_id?: string[];
  brand_id?: string[];
  only_critical?: boolean;
  page?: number;
  limit?: number;
  sort?: string;
  dir?: 'asc' | 'desc';
}

// ------------------------------------------------------------------- tasks

/**
 * A task carries TWO independent axes and the UI must keep them apart:
 * - `task_phase` is the lifecycle position (pursuit = win the work, delivery = do it).
 * - `category` is the work-stream (Spec-in, Sampling, Commercial), free-form per
 *   template, and it is what the Tasks tab groups into collapsible sections.
 *
 * Grouping by phase, or filtering by category, would invert the design.
 */
export type TaskPhase = 'pursuit' | 'delivery';

export type TaskLinkType = 'quotation_version' | 'sample' | 'purchase_order';

export interface ProjectTask {
  id: string;
  project_id: string;
  project_code?: string | null;
  project_title?: string | null;

  name: string;
  description?: string | null;
  task_phase: TaskPhase;
  category?: string | null;

  status_id?: string | null;
  /** Stable identifier. Branch on this, never on `status_label`, which admins rename. */
  status_key?: string | null;
  status_label?: string | null;
  is_open: boolean;

  assignee_user_id?: string | null;
  assignee_name?: string | null;
  escalated_to_user_id?: string | null;
  escalated_to_name?: string | null;
  stuck_reason?: string | null;

  start_date?: string | null;
  due_date?: string | null;
  completed_at?: string | null;
  /** Server-computed, so a client in another timezone cannot disagree about lateness. */
  is_overdue: boolean;
  days_until_due?: number | null;
  sort_order: number;
  source_template_task_id?: string | null;
  linked_entity_type?: TaskLinkType | null;
  linked_entity_id?: string | null;
  can_edit: boolean;

  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectTaskBody {
  name: string;
  description?: string | null;
  task_phase?: TaskPhase;
  category?: string | null;
  assignee_user_id?: string | null;
  start_date?: string | null;
  due_date?: string | null;
  sort_order?: number;
  linked_entity_type?: TaskLinkType | null;
  linked_entity_id?: string | null;
  status_id?: string | null;
}

/**
 * The status move and its required context travel together. Two calls would leave a
 * window where the task is escalated to nobody.
 */
export interface TaskStatusChangeBody {
  to_status_id: string;
  escalated_to_user_id?: string | null;
  stuck_reason?: string | null;
}

export interface ProjectTemplateTask {
  id: string;
  template_id: string;
  name: string;
  description?: string | null;
  task_phase: TaskPhase;
  category?: string | null;
  sort_order: number;
  default_offset_days?: number | null;
  is_active: boolean;
  /** Non-zero means delete is blocked and deactivate is the action (AC-N11). */
  in_use_count: number;
}

export interface ProjectTemplateTaskBody {
  name: string;
  description?: string | null;
  task_phase?: TaskPhase;
  category?: string | null;
  sort_order?: number;
  default_offset_days?: number | null;
  is_active?: boolean;
}

export interface TaskHistoryEntry {
  at: string;
  actor_name?: string | null;
  action: string;
  field?: string | null;
  from_value?: string | null;
  to_value?: string | null;
}

// ------------------------------------------------------------------- leads

/**
 * A lead is a RUMOUR, and every rule in this module follows from that:
 *
 * - It is not exclusive. Two salespeople may record the same sighting, so
 *   `possible_duplicates` is a hint the UI shows and never a block (AC-O3).
 * - `customer_id` is required, because somebody told us (AC-O1).
 * - Ownership locks at QUALIFY, which is where the registration clash check finally
 *   runs (AC-O4). Until then a lead grants nobody anything.
 */
export type LeadOutcome = 'open' | 'qualified' | 'disqualified';

export type LeadSource =
  | 'site_visit'
  | 'architect'
  | 'contractor'
  | 'dealer'
  | 'inbound'
  | 'other';

export interface LeadDuplicateHint {
  lead_id: string;
  lead_code: string;
  owner_name?: string | null;
}

export interface ProjectLead {
  id: string;
  lead_code: string;
  title: string;

  customer_id: string;
  customer_name?: string | null;
  developer_party_id?: string | null;
  developer_name?: string | null;

  source?: LeadSource | null;
  source_detail?: string | null;
  estimated_value?: string | null;
  location?: string | null;
  notes?: string | null;

  status_id?: string | null;
  /** Stable identifier. Branch on this, never on `status_label`, which admins rename. */
  status_key?: string | null;
  status_label?: string | null;
  outcome: LeadOutcome;
  disqualified_reason?: string | null;
  qualified_at?: string | null;

  owner_user_id?: string | null;
  owner_name?: string | null;

  /** One lead may produce several projects (AC-O5), so this is a count, not a flag. */
  project_count: number;
  possible_duplicates: LeadDuplicateHint[];
  can_edit: boolean;

  created_at?: string | null;
  updated_at?: string | null;
}

/** Step 1 of the wizard when the informant has never bought anything. */
export interface LeadNewCustomer {
  customer_name: string;
  customer_code?: string | null;
  email?: string | null;
  phone_number?: string | null;
  registration_number?: string | null;
  notes?: string | null;
}

export interface ProjectLeadBody {
  title: string;
  customer_id?: string | null;
  new_customer?: LeadNewCustomer | null;
  developer_party_id?: string | null;
  source?: LeadSource | null;
  source_detail?: string | null;
  estimated_value?: string | null;
  location?: string | null;
  notes?: string | null;
  owner_user_id?: string | null;
}

/**
 * Every field optional: the lead already knows most of it, and re-asking for what we
 * were told is the re-keying this module exists to remove. `title` is here so a
 * masterplan sighting can be split into one registration per phase.
 */
export interface LeadQualifyBody {
  title?: string | null;
  developer_party_id?: string | null;
  type_id?: string | null;
  template_id?: string | null;
  owner_user_id?: string | null;
  brand_ids?: string[];
  details?: Record<string, unknown> | null;
}

export interface LeadReasonOption {
  value: string;
  label: string;
}

export interface LeadConversionMetrics {
  total: number;
  open: number;
  qualified: number;
  disqualified: number;
  decided: number;
  /** Null rather than 0 when nothing is decided: zero reads as "we convert nothing". */
  conversion_rate?: number | null;
  projects_from_leads: number;
  disqualified_reasons: (LeadReasonOption & { count: number })[];
}

export interface CustomerPortfolio {
  leads: ProjectLead[];
  projects: Project[];
}

export interface LeadListParams {
  query?: string;
  outcome?: string[];
  status_id?: string[];
  owner_user_id?: string[];
  customer_id?: string[];
  source?: string[];
  /** Narrows to where the handshake stands: assigned but unanswered, accepted, declined.
   *  Server-side because "who has not answered yet" is the whole question the acceptance
   *  worklist exists to ask, and filtering one page of results client-side answers it
   *  only for that page. */
  acceptance_state?: string[];
  page?: number;
  limit?: number;
  sort?: string;
  dir?: 'asc' | 'desc';
}

// -------------------------------------------------------------- quotations

/**
 * The version model is defined by what it does NOT have (AC-E3a). There is no
 * `current_version_id` column and no `is_frozen` flag: current is MAX(version_no) and
 * everything below it is frozen. `is_current` on a version and `current_version_id` on
 * a quotation are both SERVER-DERIVED for rendering. Never write them, and never
 * re-derive "frozen" in the browser from anything else.
 */
export type QuotationOutcome = 'open' | 'won' | 'lost';

export type UnitType = 'house_unit' | 'bathroom' | 'facility' | 'common_area';

export type FloorMode = 'percent' | 'absolute';

export type FloorLevel = 'product' | 'category' | 'category_ancestor' | 'system';

export interface ProjectQuotation {
  id: string;
  project_id: string;
  scope_label: string;
  series_id?: string | null;
  series_name?: string | null;
  notes?: string | null;

  outcome: QuotationOutcome;
  loss_reason?: string | null;
  loss_reason_label?: string | null;
  decided_at?: string | null;

  version_count: number;
  current_version_id?: string | null;
  current_version_no?: number | null;
  current_total?: string | null;

  /** Counted on the CURRENT version only: a breach on a superseded version is history. */
  below_floor_count: number;
  non_standard_count: number;
  line_count: number;

  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectQuotationBody {
  scope_label: string;
  series_id?: string | null;
  notes?: string | null;
}

export interface QuotationVersion {
  id: string;
  quotation_id: string;
  version_no: number;
  is_current: boolean;
  /** Sent to a customer. Its rows are what they hold, so they cannot be rewritten. */
  is_issued?: boolean;
  /** Current AND not issued. Gate the editor on this, never on `is_current` alone. */
  is_editable?: boolean;
  frozen_at?: string | null;
  issued_by?: string | null;
  issued_by_name?: string | null;
  issued_on?: string | null;
  total_amount: string;
  notes?: string | null;
  created_at?: string | null;
}

/**
 * Which photograph of the line's product the customer will see (S21).
 *
 * Resolved by the server from `product_attachments.is_primary` - the SAME flag the brochure
 * reads - so the quotation, the brochure and 3D-model generation cannot show three different
 * pictures of one product. The four states are deliberately distinct because the ACTION differs:
 *
 * - `chosen`      the picture, and nothing to do.
 * - `not_chosen`  photographs exist but nobody has said which one is the product. One click.
 * - `no_photos`   there is nothing to choose between. The fix is an upload, not a click.
 * - `off_catalog` no product at all, so there is nothing a flag could point at, ever.
 */
export type QuotationLineImageState =
  | 'chosen'
  | 'not_chosen'
  | 'no_photos'
  | 'off_catalog';

export interface QuotationLineImage {
  state: QuotationLineImageState;
  /** A signed THUMBNAIL. Only ever present on `chosen`. */
  url?: string | null;
  /**
   * The signed ORIGINAL, for the viewer that opens on click. The thumbnail is ~320px and turns
   * to mush as soon as somebody zooms. Both URLs are reused server-side for a window so the
   * browser can actually cache the bytes.
   */
  preview_url?: string | null;
  /** Lets the viewer download through the authenticated attachments route. */
  attachment_id?: string | null;
  filename?: string | null;
  /** Photographs somebody could choose between. Zero on `no_photos` and `off_catalog`. */
  candidate_count: number;
  /** Those photographs, for the picker that sets the line's `image_attachment_id`. */
  candidates?: QuotationLineFileCandidate[];
}

/** One image a line's photo or drawing could be: a signed thumbnail and the original. */
export interface QuotationLineFileCandidate {
  attachment_id: string;
  filename?: string | null;
  url?: string | null;
  preview_url?: string | null;
}

/**
 * The drawing cell: the product's "Technical Specifications" images. `chosen` is a picked or
 * issued id, `default` the product's first drawing by upload.
 */
export interface QuotationLineDrawing {
  state: 'chosen' | 'default' | 'no_drawings' | 'off_catalog';
  url?: string | null;
  preview_url?: string | null;
  attachment_id?: string | null;
  filename?: string | null;
  candidate_count: number;
  candidates?: QuotationLineFileCandidate[];
}

/** The photo and drawing cells for a product on a line not saved yet. */
export interface QuotationLineImages {
  product_image: QuotationLineImage;
  technical_drawing: QuotationLineDrawing;
}

export interface QuotationLine {
  id: string;
  version_id: string;
  product_id?: string | null;
  product_code?: string | null;
  description?: string | null;
  list_price?: string | null;
  image_attachment_id?: string | null;
  /** The picture cell. Absent on a payload from before S21. */
  product_image?: QuotationLineImage | null;
  /** The drawing picked for (or issued with) this line. */
  drawing_attachment_id?: string | null;
  technical_drawing?: QuotationLineDrawing | null;

  unit_price: string;
  quantity: string;
  uom?: string | null;
  unit_type?: UnitType | null;
  line_total: string;
  /** Quoted at a rate and PRINTED, but contributing nothing to any total (AC-C2). */
  is_rate_only?: boolean;

  /** The printed document's own columns (AC-C1). */
  item_label?: string | null;
  /** The response's name for what the request calls `brand_snapshot`. */
  brand?: string | null;
  technical_spec?: string | null;
  complete_set?: string | null;
  /** Set on the line that OPENS a section, so a band cannot orphan itself (AC-C3). */
  band_label?: string | null;

  is_non_standard: boolean;
  /** The floor in force WHEN PRICED (AC-E7). A later policy change never rewrites it. */
  floor_value_applied?: string | null;
  floor_level_applied?: FloorLevel | null;
  is_below_floor: boolean;

  sort_order: number;
  notes?: string | null;
}

export interface QuotationLineBody {
  product_id?: string | null;
  description_snapshot?: string | null;
  unit_price?: string;
  quantity?: string;
  uom?: string | null;
  unit_type?: UnitType | null;
  sort_order?: number;
  notes?: string | null;
  image_attachment_id?: string | null;
  drawing_attachment_id?: string | null;

  item_label?: string | null;
  /** The REQUEST's name for the brand. The response calls the same value `brand`. */
  brand_snapshot?: string | null;
  technical_spec?: string | null;
  complete_set?: string | null;
  band_label?: string | null;
  is_rate_only?: boolean;
}

/**
 * One line inside a WHOLE-SET write (S10).
 *
 * `id` is the only thing that tells an existing line from a new one: a line already stored
 * carries the id the API gave it, a new one leaves the field out entirely. `sort_order` is not
 * sent at all - array position is the order.
 */
export interface QuotationLineBulkItem extends Omit<QuotationLineBody, 'sort_order'> {
  id?: string;
}

/**
 * One line as the quotation edit view holds it, before anything has been written.
 *
 * Shared between the line editor that draws it and the document shell that owns it, which is why
 * it is a domain type rather than one component's local shape. `id` is what tells the bulk write
 * an existing line from a new one; a stored line whose id never reaches the request is deleted.
 */
export interface StagedQuotationLine {
  /** The stored line's id, or null for one added in this session. */
  id: string | null;
  /** The row's identity for as long as the session lasts. Minted by the line table. */
  key: string;
  /** The stored line it came from, for the flags and codes only the server can decide. */
  line: QuotationLine | null;
  /** Every field as a string, the way the line table holds a row. */
  draft: Record<string, string>;
  /** Staged for removal: struck through on screen, and gone only once Save runs. */
  removed: boolean;
}

export interface QuotationOutcomeBody {
  outcome: QuotationOutcome;
  loss_reason?: string | null;
}

export interface ProjectSeries {
  id: string;
  name: string;
  brand_id?: string | null;
  brand_name?: string | null;
  description?: string | null;
  is_active: boolean;
  category_ids: string[];
  category_names: string[];
  /** Nominated plus every descendant: what the non-standard check compares against. */
  covered_category_count: number;
  /** Products nominated BY NAME, independently of any category (S18). */
  product_count: number;
  /** Those products' codes. Codes, not ids: no UUID reaches the screen. */
  product_codes: string[];
  quotation_count: number;
}

/**
 * One product on a series, with what this series sells it for (T2).
 *
 * Both money fields are optional and a `null` means the series has NOT said - never zero.
 * Of the client's 151 codes, 95 carry a price and 56 a percentage, so an empty cell is the
 * common case and has to read as "not stated".
 */
export interface SeriesProductRow {
  product_id: string;
  product_code?: string | null;
  product_name?: string | null;
  /** What this series sells it for. The sheet's DEVELOPERS column. */
  selling_price?: string | null;
  /** A PERCENT: `6` means 6%. The sheet's DISTRIBUTORS column. */
  max_discount_pct?: string | null;
  /**
   * `selling_price` less `max_discount_pct`, computed on the SERVER and null unless both are
   * set. Never recalculated here: the pricing engine enforces with the same function, and a
   * second implementation in the browser could disagree with the one that blocks a save.
   */
  derived_floor?: string | null;
}

/** A list of product codes to load onto a series (S18). */
export interface SeriesProductImportBody {
  codes: string[];
  /** append = add to what the series lists. replace = make it say exactly this. */
  mode: 'append' | 'replace';
}

/**
 * What the import did, and - the half that matters - what it could not do.
 *
 * `unmatched_codes` is never empty by accident: the client's own sheet quotes base codes
 * the catalogue only stocks as suffixed variants, so a real list can miss a third of its
 * rows. Reporting a smaller success number instead would hide that.
 */
export interface SeriesProductImportResult {
  series_id: string;
  series_name: string;
  mode: 'append' | 'replace';
  submitted: number;
  unique_codes: number;
  matched_codes: number;
  added: number;
  already_present: number;
  removed: number;
  product_count: number;
  unmatched_codes: string[];
}

/**
 * A queued sheet load. No counts on purpose: the upload answers before a single code has
 * been read, and a zero would be indistinguishable from a sheet that matched nothing.
 */
export interface SeriesProductImportJob {
  job_id: string;
  series_id: string;
  mode: 'append' | 'replace';
}

/** One poll of a background import. `result` is the finished report, or null until then. */
export interface ImportJobStatus {
  job_id: string;
  status: string;
  progress: {
    total: number;
    processed: number;
    successful: number;
    failed: number;
    skipped: number;
    percentage: number;
  } | null;
  result: SeriesProductImportResult | null;
  error: string | null;
}

/**
 * Job states that mean the work is over, one way or the other.
 *
 * `str(JobStatus.FAILED)` on the server is `'JobStatus.FAILED'`, not `'failed'` - RQ's
 * status is a `(str, Enum)` whose `__str__` is the Enum's - so both spellings are accepted
 * rather than trusting a serialiser we do not own. A poll loop that never recognises the
 * terminal state polls until the tab closes.
 */
export const IMPORT_JOB_DONE = ['completed', 'finished'];
export const IMPORT_JOB_DEAD = ['failed', 'cancelled', 'canceled', 'stopped'];

export function importJobPhase(status: string): 'running' | 'done' | 'dead' {
  const value = (status || '').split('.').pop()!.toLowerCase();
  if (IMPORT_JOB_DONE.includes(value)) return 'done';
  if (IMPORT_JOB_DEAD.includes(value)) return 'dead';
  return 'running';
}

/**
 * The two guardrail answers for one DRAFT line, judged now (not at save).
 *
 * `floor_value` rides along so a breach can be argued with, the same fields the saved
 * line carries in `floor_value_applied` / `floor_level_applied`.
 */
export interface QuotationLineVerdict {
  is_non_standard: boolean;
  is_below_floor: boolean;
  floor_value: string | null;
  floor_level: string | null;
}

/** What re-asking the guardrails changed on one version (S19). */
export interface QuotationRecomputeResult {
  version_id: string;
  version_no: number;
  quotation_id: string;
  scope_label?: string | null;
  line_count: number;
  changed_count: number;
  now_non_standard: number;
  no_longer_non_standard: number;
  now_below_floor: number;
  no_longer_below_floor: number;
  floor_changed: number;
  /** Lines naming a product this company's catalogue does not carry. */
  unresolved_products: number;
  /** The lines that moved, named the way a person names them. */
  changed_lines: string[];
}

export interface ProjectSeriesBody {
  name: string;
  brand_id?: string | null;
  description?: string | null;
  is_active?: boolean;
  category_ids?: string[];
}

export interface PriceFloorRule {
  id: string;
  product_id?: string | null;
  product_code?: string | null;
  category_id?: string | null;
  category_name?: string | null;
  mode: FloorMode;
  value: string;
  notes?: string | null;
  is_active: boolean;
  /** Derived from which key is set, never stored. */
  level: FloorLevel;
}

export interface PriceFloorRuleBody {
  mode: FloorMode;
  value: string;
  product_id?: string | null;
  category_id?: string | null;
  notes?: string | null;
  is_active?: boolean;
}

/** Which single thing a floor is being read or written for. */
export type FloorTargetLevel = 'product' | 'category';

export interface EffectiveFloorSource {
  rule_id: string;
  level: FloorLevel;
  mode: FloorMode;
  /** The rule as configured: a percentage, or a ringgit amount. */
  value: string;
  /**
   * The floor in ringgit, when it can be computed. Null for a percentage read against a
   * category, which has no list price to apply it to.
   */
  amount?: string | null;
  /** Product code, category name, or "Company default". Never an id. */
  source_label: string;
}

/**
 * What governs ONE product or ONE category.
 *
 * `own_rule` and `effective` answer different questions and the UI needs both: the first
 * says whether this target carries a floor of its own (so whether clearing it is even
 * possible), the second says what actually applies once inheritance is walked. A product
 * with no rule of its own is still governed by a floor, and showing nothing there would
 * be a lie by omission.
 */
export interface EffectivePriceFloor {
  target_level: FloorTargetLevel;
  target_id: string;
  target_label: string;
  list_price?: string | null;
  own_rule?: PriceFloorRule | null;
  effective?: EffectiveFloorSource | null;
}

/**
 * S4. A sample binds to a VERSION, a PO binds to the version the contractor was last
 * shown. Both `is_version_current` and the PO's mismatch counts are server-derived --
 * the browser must not re-derive either, or it will eventually disagree with the server
 * about what was compared against what.
 */
export type PoSource = 'contractor_direct' | 'trading_house';

export interface ProjectSample {
  id: string;
  project_id: string;
  quotation_version_id: string;
  quotation_id?: string | null;
  scope_label?: string | null;
  version_no?: number | null;
  /** False = the version was superseded AFTER this sample went out. */
  is_version_current: boolean;
  submitted_on?: string | null;
  submitted_by?: string | null;
  submitted_by_name?: string | null;
  developer_feedback?: string | null;
  salesperson_notes?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectSampleBody {
  quotation_version_id: string;
  submitted_on?: string | null;
  developer_feedback?: string | null;
  salesperson_notes?: string | null;
}

export interface ProjectPurchaseOrder {
  id: string;
  project_id: string;
  quotation_version_id?: string | null;
  quotation_id?: string | null;
  scope_label?: string | null;
  version_no?: number | null;

  po_source: PoSource;
  issuing_party_id?: string | null;
  issuing_party_name?: string | null;
  po_number: string;
  po_date?: string | null;
  po_amount?: string | null;
  notes?: string | null;

  line_count: number;
  line_total: string;
  /** What still stands between this PO and its sales orders. Sent by the server, since
   *  deriving it here would mean loading every version and schedule of every PO. */
  status?: string | null;
  po_confirmed?: boolean;
  schedule_confirmed?: boolean;
  /**
   * Sales orders already published out of this PO. Editing the PO is still allowed - a
   * correction is the normal case - but the edit view says this first, because those orders
   * do not follow the change.
   */
  published_sales_order_count?: number;
  model_mismatch_count: number;
  price_mismatch_count: number;

  /** AC-F9a: erosion since v1 as a NUMBER, not a flag. Null percent = no baseline. */
  v1_total?: string | null;
  drift_delta?: string | null;
  drift_percent?: string | null;

  /** True only on the create response that actually moved the funnel (AC-F10). */
  status_moved_to_po_received?: boolean;

  created_at?: string | null;
  updated_at?: string | null;
}

export interface ProjectPurchaseOrderBody {
  po_number: string;
  po_source: PoSource;
  quotation_version_id?: string | null;
  issuing_party_id?: string | null;
  po_date?: string | null;
  po_amount?: string | null;
  notes?: string | null;
}

export interface PurchaseOrderLine {
  id: string;
  po_id: string;
  product_id?: string | null;
  product_code?: string | null;
  description?: string | null;
  unit_price: string;
  quantity: string;
  uom?: string | null;
  line_total: string;
  /** What the bound version said WHEN THE PO WAS CHECKED. */
  quoted_unit_price?: string | null;
  model_mismatch: boolean;
  price_mismatch: boolean;
  sort_order: number;
  notes?: string | null;
}

export interface PurchaseOrderLineBody {
  product_id?: string | null;
  product_code?: string | null;
  description?: string | null;
  unit_price?: string;
  quantity?: string;
  uom?: string | null;
  sort_order?: number;
  notes?: string | null;
}

/**
 * One line inside a whole-PO save. `id` on a line already stored, absent on a new one.
 *
 * `sort_order` is deliberately not part of it: the array's order IS the order, so a client
 * never computes one.
 */
export interface PurchaseOrderLineBulkItem extends Omit<PurchaseOrderLineBody, 'sort_order'> {
  id?: string;
}

/**
 * The body of the PO edit view's one Save: the header fields that changed, plus the FULL line
 * set when the lines were touched at all.
 *
 * The sharp edge is worth stating where it is typed: a stored line whose id is missing from
 * `lines` is DELETED, so send everything on screen and never only what changed. Leaving
 * `lines` out entirely is how a header-only save (the record-a-PO modal) leaves them alone.
 */
export interface ProjectPurchaseOrderSaveBody extends Partial<ProjectPurchaseOrderBody> {
  lines?: PurchaseOrderLineBulkItem[];
}

/**
 * One PO line as the edit view holds it, before anything has been written.
 *
 * Shared between the line editor that draws it and the detail shell that owns it, which is why
 * it is a domain type rather than one component's local shape. Same shape as the quotation's
 * `StagedQuotationLine`, for the same reasons.
 */
export interface StagedPurchaseOrderLine {
  /** The stored line's id, or null for one added in this session. */
  id: string | null;
  /** The row's identity for as long as the session lasts. Minted by the line table. */
  key: string;
  /** The stored line it came from, for the flags only the server can decide. */
  line: PurchaseOrderLine | null;
  /** Every field as a string, the way the line table holds a row. */
  draft: Record<string, string>;
  /** Staged for removal: struck through on screen, and gone only once Save runs. */
  removed: boolean;
}

/** A sponsorship form linked to this project (AC-F3). Read-only here: procurement owns it. */
export interface ProjectSponsorship {
  id: string;
  request_number?: string | null;
  request_date?: string | null;
  status?: string | null;
  approval_status?: string | null;
  customer_name?: string | null;
  project_title?: string | null;
  sponsor_subject?: string | null;
  sponsor_subject_other?: string | null;
  total_project_value?: string | null;
  purpose?: string | null;
}

export interface SponsorshipYearTotal {
  year: number;
  total: string;
  form_count: number;
}

export interface SponsorshipRollup {
  project_id: string;
  total: string;
  form_count: number;
  by_year: SponsorshipYearTotal[];
}

/**
 * S5a. Three numbers, never blended (AC-I1). There is no `total` field on purpose: a single
 * figure mixing a banked PO with a 10%-probability rumour is the number every spreadsheet
 * produces and nobody can act on.
 */
export interface ForecastBand {
  pipeline: string;
  weighted: string;
  committed: string;
}

export interface ForecastYearRow extends ForecastBand {
  year: number;
}

export interface ProjectForecast extends ForecastBand {
  project_count: number;
  by_year: ForecastYearRow[];
  /** Projects with no derivable delivery year: reported, never dropped. */
  undated: ForecastBand;
}

export interface ProjectConversion {
  won: number;
  lost: number;
  decided: number;
  open: number;
  /** Null with nothing decided. 0% would claim we lose everything. */
  rate?: string | null;
}

export interface LossReasonCount {
  reason: string;
  label: string;
  count: number;
}

export interface SalespersonRow extends ForecastBand {
  owner_user_id?: string | null;
  owner_name?: string | null;
  project_count: number;
}

export interface SponsorshipConversion {
  sponsored_projects: number;
  converted_projects: number;
  rate?: string | null;
  sponsored_spend: string;
}

export interface ProjectDashboard {
  forecast: ProjectForecast;
  conversion: ProjectConversion;
  loss_reasons: LossReasonCount[];
  by_salesperson: SalespersonRow[];
  sponsorship: SponsorshipConversion;
  delivery_lag_months: number;
}
