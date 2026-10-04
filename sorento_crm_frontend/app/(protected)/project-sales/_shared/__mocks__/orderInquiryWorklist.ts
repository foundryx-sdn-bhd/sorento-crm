/**
 * Phase 1 fixture for the cross-project order inquiry.
 *
 * Shaped off the spreadsheet purchasing works from today (`JAN - DEC 2026 ORDER.xlsx`,
 * sheets `JAN 26`, `2.1.26`, ...): a row that traces to a placed purchase order carries
 * a supplier and a PO number, and one that does not carries neither, because on their
 * sheet a blank in those two columns is what "not placed yet" looks like.
 *
 * Kept after the backend landed because the component tests read from it: one shape for
 * the prototype and the tests means a test cannot pass against a row the screen never saw.
 */
import type {
  OrderInquiryWorklistRow,
  OrderInquiryWorklistSummary,
  UnplaceAllPreview,
} from '../types/orderInquiry.types';

export const MOCK_WORKLIST_ROWS: OrderInquiryWorklistRow[] = [
  {
    id: 'row-1',
    inquiry_no: 'OI-000101',
    so_date: '2025-12-30',
    so_number: 'SO385126',
    item_code: 'SRTWB5400',
    product_name: 'Wall hung basin 5400',
    qty: '35',
    delivery_date: '2026-01-02',
    project_customer: 'EXACO / CURVO RESIDENCE @ SETAPAK',
    customer_name: 'EXACO',
    project_title: 'Curvo Residence @ Setapak',
    supplier: 'DAFUYUAN',
    supplier_id: 'sup-1',
    po_number: '202601-S0015',
    po_id: 'po-1',
    location: 'BRW-BB',
    agent_code: 'SEAN I',
    agent_label: 'Sean',
    state: 'actioned',
    raised_at: '2026-01-02T09:15:00',
    raised_by_name: 'CONTACT W',
    verb: 'ORDER',
    note: null,
    taken_from_po: '35',
    remaining_open: '0',
    linked_qty: '35',
    links: [
      {
        id: 'link-1',
        kind: 'po',
        document: '202601-S0015',
        line_label: 'L3',
        qty: '35',
        location: 'BRW-BB',
        issue_date: '2026-01-02',
        expected_date: '2026-01-20',
        tier: 1,
        auto: true,
        po_id: 'po-1',
      },
    ],
    project_id: null,
    project_sales_order_id: 'pso-1',
    core_sales_order_id: 'so-385126',
    is_adopted: true,
  },
  {
    id: 'row-2',
    inquiry_no: 'OI-000102',
    so_date: '2026-01-08',
    so_number: 'SO386461',
    item_code: 'SRTWC8605-SC-RL',
    product_name: 'Close coupled WC 8605',
    qty: '85',
    delivery_date: '2026-01-19',
    project_customer: 'OPTAD / ENG CONSTRUCTION / SEPANG',
    customer_name: 'OPTAD',
    project_title: 'Eng Construction / Sepang',
    supplier: null,
    supplier_id: null,
    po_number: null,
    location: null,
    agent_code: null,
    agent_label: null,
    state: 'raised',
    raised_at: '2026-01-08T11:02:00',
    raised_by_name: 'CONTACT AA Tan',
    verb: 'ORDER',
    note: null,
    taken_from_po: '0',
    remaining_open: '85',
    linked_qty: '0',
    links: [],
    project_id: null,
    project_sales_order_id: 'pso-2',
    core_sales_order_id: 'so-386461',
    is_adopted: true,
  },
  {
    id: 'row-3',
    inquiry_no: 'OI-000103',
    so_date: '2025-08-15',
    so_number: 'SO363150',
    item_code: 'SRTWT107',
    product_name: 'Wall hung WC 107',
    qty: '91',
    delivery_date: '2026-03-02',
    project_customer: 'KNUSFORD / EKO TITIWANGSA / KL',
    customer_name: 'KNUSFORD',
    project_title: 'Eko Titiwangsa / KL',
    supplier: 'CHAOSHENG',
    supplier_id: 'sup-2',
    po_number: '202601-S0044',
    agent_code: 'LCL',
    agent_label: 'Lee',
    // Partly linked across two lines of one purchase order (AC-I5/AC-I6): the row keeps
    // its full 91 and 60 of it sits on 202601-S0044, so 31 is still demand.
    state: 'partly_linked',
    raised_at: '2026-01-02T09:15:00',
    raised_by_name: 'CONTACT W',
    verb: 'ORDER',
    note: null,
    taken_from_po: '60',
    remaining_open: '31',
    linked_qty: '60',
    links: [
      {
        id: 'link-2',
        kind: 'po',
        document: '202601-S0044',
        line_label: 'L3',
        qty: '40',
        location: 'BRW-BB',
        issue_date: '2026-01-04',
        expected_date: '2026-02-10',
        tier: 1,
        auto: true,
        po_id: 'po-2',
      },
      {
        id: 'link-3',
        kind: 'po',
        document: '202601-S0044',
        line_label: 'L7',
        qty: '20',
        location: 'BRW',
        issue_date: '2026-01-04',
        expected_date: '2026-02-24',
        tier: 3,
        auto: true,
        po_id: 'po-2',
      },
    ],
    project_id: 'proj-9',
    project_sales_order_id: 'pso-3',
    core_sales_order_id: null,
    is_adopted: false,
  },
  {
    id: 'row-4',
    inquiry_no: null,
    so_date: null,
    so_number: 'PSO-000412',
    item_code: 'BT012-CR',
    product_name: 'Bath trap chrome',
    qty: '6',
    delivery_date: null,
    project_customer: 'BUIMACO / INTA BINA / SDB @ GOMBAK',
    customer_name: 'BUIMACO',
    project_title: 'Inta Bina / SDB @ Gombak',
    supplier: null,
    supplier_id: null,
    po_number: null,
    state: 'cancelled',
    raised_at: '2026-01-08T11:02:00',
    raised_by_name: null,
    verb: 'CANCEL_BALANCE',
    note: 'Linked 12, new need 6',
    project_id: 'proj-4',
    project_sales_order_id: 'pso-4',
    core_sales_order_id: null,
    is_adopted: false,
  },
  {
    // An ORDER BACK row: the ONE verb whose links may name an SPO allocation (part 2
    // section 4b), and CS cited the document on the form, so the walk tried it first.
    id: 'row-5',
    inquiry_no: 'OI-000104',
    so_date: '2026-08-12',
    so_number: 'SO381895',
    item_code: 'SRTWCY7405-PJ',
    product_name: 'Wall hung WC 7405',
    qty: '10',
    delivery_date: null,
    project_customer: 'YOTU BUILDER / LOT 2752',
    customer_name: 'YOTU BUILDER',
    project_title: 'Lot 2752',
    supplier: null,
    supplier_id: null,
    po_number: 'SPO-2026/08-0061',
    location: 'BRW-IB',
    agent_code: 'CONTACT N',
    agent_label: 'CONTACT N',
    state: 'placed',
    raised_at: '2026-08-12T16:10:00',
    raised_by_name: 'Cyndi Ong',
    verb: 'ORDER_BACK',
    note: null,
    taken_from_po: '10',
    remaining_open: '0',
    cited_document: 'SPO-2026/08-0061',
    linked_qty: '10',
    links: [
      {
        id: 'link-4',
        kind: 'spo',
        document: 'SPO-2026/08-0061',
        line_label: null,
        qty: '10',
        location: 'BRW-IB',
        issue_date: '2026-08-12',
        expected_date: '2026-08-01',
        tier: 1,
        auto: true,
        po_id: null,
      },
    ],
    project_id: null,
    project_sales_order_id: 'pso-5',
    core_sales_order_id: 'so-381895',
    is_adopted: true,
  },
  {
    // `PLAN-oi-replan-received-links.md`, S1/S3: OI-000477 line 1 verbatim - the SPO that
    // shipped 158 to BRW-IR closed and received on 19 Jan 2026, so a replan could not
    // carry this row forward. It keeps its OLD qty/date/link as history (`redirected_to_
    // pool: true`) and contributes nothing to any card; the fresh need is row-7 below.
    // `PLAN-oi-cancelled-line-used-confirm.md`, AC-CL-2b: also `line_cancelled: true`, so
    // this is the one fixture row that shows BOTH pills together.
    id: 'row-6',
    inquiry_no: 'OI-000477',
    so_date: '2025-11-20',
    so_number: 'SO314593',
    item_code: 'B2154-NL',
    product_name: null,
    qty: '182',
    delivery_date: '2026-06-01',
    project_customer: 'PTL Supplies / BRW-IR',
    customer_name: 'PTL Supplies',
    project_title: 'BRW-IR',
    supplier: null,
    supplier_id: null,
    po_number: null,
    location: 'BRW-IR',
    agent_code: null,
    agent_label: null,
    state: 'partly_linked',
    raised_at: '2025-11-20T09:00:00',
    raised_by_name: 'CONTACT W',
    verb: 'ORDER',
    // AC-RL-10's own note shape.
    note: 'SPO-2026/01-0143 received 19 Jan 2026 in full, goods are BRW-IR stock, released at revision 4',
    // S3, AC-RL-16: a redirected row's own flow figures read as 0 - what still flows to
    // reorder planning is the fresh row (row-7), never this one's history.
    taken_from_po: '0',
    remaining_open: '0',
    linked_qty: '158',
    links: [
      {
        id: 'link-5',
        kind: 'spo',
        document: 'SPO-2026/01-0143',
        qty: '158',
        location: 'BRW-IR',
        issue_date: '2026-01-19',
        expected_date: '2026-01-19',
        tier: 1,
        auto: true,
        po_id: null,
        received: true,
        received_qty: '158',
      },
    ],
    project_id: null,
    project_sales_order_id: 'pso-6',
    core_sales_order_id: 'so-314593',
    is_adopted: true,
    redirected_to_pool: true,
    line_cancelled: true,
  },
  {
    // The fresh Buy the redirect raised (S2, backend - shown here only as what row-6's
    // replan hands the worklist): the full 220 need, the new 1 Mar 2027 date, no links -
    // a plain buy, exactly as AC-RL-30 reads it.
    id: 'row-7',
    inquiry_no: 'OI-000477',
    so_date: '2025-11-20',
    so_number: 'SO314593',
    item_code: 'B2154-NL',
    product_name: null,
    qty: '220',
    delivery_date: '2027-03-01',
    project_customer: 'PTL Supplies / BRW-IR',
    customer_name: 'PTL Supplies',
    project_title: 'BRW-IR',
    supplier: null,
    supplier_id: null,
    po_number: null,
    location: null,
    agent_code: null,
    agent_label: null,
    state: 'raised',
    raised_at: '2026-09-16T10:00:00',
    raised_by_name: 'CONTACT W',
    verb: 'ORDER',
    note: null,
    taken_from_po: '220',
    remaining_open: '220',
    linked_qty: '0',
    links: [],
    project_id: null,
    project_sales_order_id: 'pso-6',
    core_sales_order_id: 'so-314593',
    is_adopted: true,
  },
];

export const MOCK_WORKLIST_SUMMARY: OrderInquiryWorklistSummary = {
  // `by_month`/`suppliers`/`projects`/`raised_by` below are left exactly as the first
  // five rows computed them - row-6/row-7 (S1/S3 fixtures, `PLAN-oi-replan-received-
  // links.md`) are additive facts about the received/redirected marks, not a rebuild of
  // every axis this fixture drives; `total_rows`/`total_qty`/`by_state`/`kinds` DO count
  // them, since those are exactly what AC-RL-04 and AC-RL-16 are about.
  total_rows: 7,
  total_qty: '629',
  by_state: { raised: 2, partly_linked: 2, actioned: 1, cancelled: 1, placed: 1, total: 7 },
  by_month: [
    { month: '2026-01', label: 'JAN 26', rows: 2, qty: '120' },
    { month: '2026-03', label: 'MAR 26', rows: 1, qty: '91' },
  ],
  suppliers: [
    { id: 'sup-2', label: 'CHAOSHENG', rows: 1 },
    { id: 'sup-1', label: 'DAFUYUAN', rows: 1 },
  ],
  projects: [
    { id: 'proj-4', label: 'SDB @ Gombak', rows: 1 },
    { id: 'proj-9', label: 'Eko Titiwangsa', rows: 1 },
  ],
  // Only the people who actually raised one of these rows - the "Raised by" filter's
  // whole list, and the reason it is not a picker over every user in the company.
  raised_by: [
    { id: 'user-cindy', label: 'CONTACT W', rows: 2 },
    { id: 'user-contact-aa', label: 'CONTACT AA Tan', rows: 1 },
  ],
  // The three cards (AC-I11), over these same rows: row 5's SPO link 10; row 1's
  // 35 and row 3's 40 + 20 on purchase orders; row 2's whole 85 and row 3's remaining
  // 31 plus row-7's fresh 220 still to buy. The CANCELLED row 4 contributes nothing at
  // all, and the REDIRECTED row-6 contributes nothing either (AC-RL-04, AC-RL-16) - its
  // 182 and its own 158-of-it SPO link are both history now, so the three cards add to
  // 336 + 95 + 10 = 441 rather than to `total_qty` 629 (which still counts row-6's 182
  // as demand history plus row-7's fresh 220, since `total_qty` is every visible row).
  kinds: { spo: '10', po: '95', buy: '336' },
};

/**
 * `GET .../order-inquiries/unplace-all-preview` (the captain, 20-21 Aug): the confirm
 * dialog's own count, resolved server-side against the CURRENT worklist scope - one
 * product when the filters happen to narrow to it (name attached), every placed row when
 * they do not (name absent).
 */
export const MOCK_UNPLACE_ALL_PREVIEW_SCOPED: UnplaceAllPreview = {
  count: 3,
  product_code: 'SRTWC8605-SC-RL',
  product_name: 'Close coupled WC 8605',
};

/** No single product across the matching set - the dialog names only the count. */
export const MOCK_UNPLACE_ALL_PREVIEW_UNSCOPED: UnplaceAllPreview = {
  count: 7,
  product_code: null,
  product_name: null,
};

/** Nothing to unplace - the toolbar action stays disabled. */
export const MOCK_UNPLACE_ALL_PREVIEW_EMPTY: UnplaceAllPreview = {
  count: 0,
  product_code: null,
  product_name: null,
};
