/**
 * GRN-PULL-CRM (AC-GP-50/52/60): the goods-receive-notes entity in the pull's pure seams -
 * the Excel view columns (the "DETAIL LISTING" sheet's order), the compare rows' only-in label
 * split (the same `DOCNO|ITEM|LOCATION` the DO compare sends) and the permission slug.
 */
import { describe, it, expect } from 'vitest';
import { GOODS_RECEIVE_NOTE_COLUMNS } from './PullExcelViewTab';
import { buildCompareRows, fieldLabel } from './compareRows';
import {
  AUTOCOUNT_PULL_PERMISSION,
  isDocumentEntity,
} from '../types/autocountPull.types';

describe('goods receive notes pull seams', () => {
  it('Excel view columns follow the Detail Listing', () => {
    expect(GOODS_RECEIVE_NOTE_COLUMNS.map((c) => c.id)).toEqual([
      'doc_no', 'doc_date', 'creditor_code', 'creditor_name', 'from_doc_no', 'item_code',
      'description', 'location', 'qty', 'uom',
    ]);
    for (const column of GOODS_RECEIVE_NOTE_COLUMNS) expect(column.size).toBeGreaterThan(0);
  });

  it('splits a lines only-in label into doc, item and location, and keeps headers whole', () => {
    const result = {
      summary: { total: 0, matched: 0, different: 0 },
      differences: [
        { item_code: 'P1', doc_no: 'GR-1', location: 'BRW', field: 'source_doc', excel: 'SPO-1', pull: 'SPO-2' },
      ],
      only_in_excel: ['GR-2026/10-0006|SRTWT167|BRW-BB'],
      only_in_pull: [],
    };
    const rows = buildCompareRows(result as never, 'goods_receive_notes', 'lines');
    expect(rows[0].field).toBe('Source PO / SPO');
    expect(rows[1]).toMatchObject({
      doc_no: 'GR-2026/10-0006', item_code: 'SRTWT167', location: 'BRW-BB',
      field: 'Only in your Excel', source: 'Lines',
    });
    const headers = buildCompareRows(
      { ...result, differences: [], only_in_excel: [], only_in_pull: ['GR-2026/10-0006'] } as never,
      'goods_receive_notes',
      'headers',
    );
    expect(headers[0]).toMatchObject({ doc_no: 'GR-2026/10-0006', item_code: '', field: 'Only in AutoCount' });
    expect(fieldLabel('creditor_code')).toBe('Creditor Code');
  });

  it('is a document entity gated by its own slug', () => {
    expect(isDocumentEntity('goods_receive_notes')).toBe(true);
    expect(isDocumentEntity('products')).toBe(false);
    expect(AUTOCOUNT_PULL_PERMISSION.goods_receive_notes).toBe('procurement.grn.autocount_pull');
  });
});
