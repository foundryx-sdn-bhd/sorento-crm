/**
 * OI-LINE-SPLIT: the whole-SO split view's wire shapes
 * (`GET|PUT /api/v1/project-sales/sales-orders/{pso_id}/line-splits`,
 * `documentation/plans/scm/PLAN-oi-line-split-16oct.md` "API"). Quantities are JSON numbers.
 * The server owns `base_qty` / `base_date`; the client never sends them.
 */

/** One stored split of a line. `sits_on_line_id` null = a new line not in AutoCount yet. */
export interface LineSplit {
  id: string;
  qty: number;
  delivery_date: string | null;
  is_cancel: boolean;
  sits_on_line_id: string | null;
  remark: string;
}

/** One open SO line: its base (what the splits add up to), AutoCount now, and its splits. */
export interface LineSplitLine {
  line_id: string;
  line_no: number | null;
  item_code: string;
  location: string;
  base_qty: number;
  base_date: string | null;
  now_qty: number;
  now_date: string | null;
  /** The line carries a pending AutoCount change (it is inside Confirm). */
  pending_change: boolean;
  /** The saved splits still act on Confirm; false once AutoCount moved on from their base. */
  acting?: boolean;
  /**
   * `acting` (Confirm applies the splits), `applied` (the Confirm that applied them has run;
   * the plan stays the line's record) or `stale` (AutoCount moved on: base is the line's
   * current one and the view warns "AutoCount changed").
   */
  split_state?: 'acting' | 'applied' | 'stale';
  delivered_qty: number;
  splits: LineSplit[];
}

export interface LineSplitsPayload {
  pso_id: string;
  so_number: string | null;
  customer: string | null;
  project: string | null;
  lines: LineSplitLine[];
}

/** What Save sends for one split (no id: the server replaces a line's splits whole). */
export interface LineSplitInput {
  qty: number;
  delivery_date: string | null;
  is_cancel: boolean;
  sits_on_line_id: string | null;
  remark: string;
}

/** PUT body: every line of the order, an unsplit line as an empty list. */
export interface LineSplitsSaveBody {
  lines: { line_id: string; splits: LineSplitInput[] }[];
}
