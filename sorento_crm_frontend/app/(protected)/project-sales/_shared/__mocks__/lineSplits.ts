/**
 * OI-LINE-SPLIT fixture: the GET `/project-sales/sales-orders/{pso}/line-splits` payload for
 * SO417636 (mock case A, `documentation/mockups/OI-LINE-SPLIT/index.html`), in the contract
 * shape of `PLAN-oi-line-split-16oct.md` "API". The dialog tests read from here so a test
 * cannot pass against a shape the service never returns.
 *
 * CSK14B at BRW-BB: line 10 (301 on 24 Aug), line 20 (417 on 20 Oct, AutoCount now 14 on
 * 20 Sep), line 30 (192 on 20 Nov, now 632), line 40 (504 on 20 Dec, now 649 on 2 Jan).
 * Line 20 parks 236 and 167 on line 30; line 40 parks 37 on line 30. CSK11A: line 50.
 * Plan on line 30 = its own 192 + 236 + 167 + 37 = 632 (explained);
 * plan on line 40 = 467 against AutoCount 649 (left 182).
 */
export const PSO_ID = 'pso-417636';

export const LINE_SPLITS_SO417636 = {
  pso_id: PSO_ID,
  so_number: 'SO417636',
  customer: 'GLOBAL INGRESS SDN BHD (PROJECT)',
  project: '405U + 397U PANGSAPURI, ISKANDAR PUTERI',
  lines: [
    {
      line_id: 'line-10', line_no: 10, item_code: 'CSK14B', location: 'BRW-BB',
      base_qty: 301, base_date: '2026-08-24', now_qty: 301, now_date: '2026-08-24',
      pending_change: false, delivered_qty: 0, splits: [],
    },
    {
      line_id: 'line-20', line_no: 20, item_code: 'CSK14B', location: 'BRW-BB',
      base_qty: 417, base_date: '2026-10-20', now_qty: 14, now_date: '2026-09-20',
      pending_change: true, delivered_qty: 0,
      splits: [
        { id: 'sp-20-14', qty: 14, delivery_date: '2026-09-20', is_cancel: false,
          sits_on_line_id: 'line-20', remark: 'Under pre-order' },
        { id: 'sp-20-236', qty: 236, delivery_date: '2026-11-20', is_cancel: false,
          sits_on_line_id: 'line-30', remark: '' },
        { id: 'sp-20-167', qty: 167, delivery_date: '2029-11-20', is_cancel: false,
          sits_on_line_id: 'line-30', remark: 'Order' },
      ],
    },
    {
      line_id: 'line-30', line_no: 30, item_code: 'CSK14B', location: 'BRW-BB',
      base_qty: 192, base_date: '2026-11-20', now_qty: 632, now_date: '2026-11-20',
      pending_change: true, delivered_qty: 0, splits: [],
    },
    {
      line_id: 'line-40', line_no: 40, item_code: 'CSK14B', location: 'BRW-BB',
      base_qty: 504, base_date: '2026-12-20', now_qty: 649, now_date: '2027-01-02',
      pending_change: true, delivered_qty: 0,
      splits: [
        { id: 'sp-40-37', qty: 37, delivery_date: '2026-11-20', is_cancel: false,
          sits_on_line_id: 'line-30', remark: 'Order' },
        { id: 'sp-40-467', qty: 467, delivery_date: '2027-01-02', is_cancel: false,
          sits_on_line_id: 'line-40', remark: 'Order' },
      ],
    },
    {
      line_id: 'line-50', line_no: 50, item_code: 'CSK11A', location: 'BRW-BB',
      base_qty: 335, base_date: '2027-01-02', now_qty: 335, now_date: '2027-01-02',
      pending_change: false, delivered_qty: 0, splits: [],
    },
  ],
};
