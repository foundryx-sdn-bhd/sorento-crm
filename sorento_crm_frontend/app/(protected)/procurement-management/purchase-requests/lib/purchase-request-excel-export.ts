/**
 * Export purchase request / sponsorship form to Excel in the document formats.
 * Uses xlsx (see lib/excel-utils.ts).
 */

import { withRevisionSuffix } from '@/lib/document-number';
import type { FormRevisionEntry } from '@/components/common/RevisionTimeline';
import {
  appendedRevisionEntries,
  latestRevisionEntry,
  revisionDocumentNumber,
  revisionExportFilename,
  revisionInfoRows,
  revisionSheetName,
} from '@/lib/revision-export';

import type { PurchaseRequest } from '../types/purchaseRequest.types';
import { revisionEntryToPurchaseRequest } from './revisionEntryToPurchaseRequest';

let XLSX: typeof import('xlsx') | null = null;

async function getXLSX() {
  if (typeof window === 'undefined') {
    throw new Error('Excel export can only be used in the browser');
  }
  if (!XLSX) {
    XLSX = await import('xlsx');
  }
  return XLSX;
}

function formatDateCell(value: string | null | undefined): string {
  if (value == null || value === '') return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const day = d.getDate();
  const month = d.getMonth() + 1;
  const year = d.getFullYear();
  return `${day}/${month}/${year}`;
}

function formatDateShort(value: string | null | undefined): string {
  if (value == null || value === '') return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const day = d.getDate();
  const month = d.getMonth() + 1;
  const year = String(d.getFullYear()).slice(-2);
  return `${day}/${month}/${year}`;
}

function formatDateForSponsorship(value: string | null | undefined): string {
  if (value == null || value === '') return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${d.getDate()}-${months[d.getMonth()]}-${d.getFullYear()}`;
}

function str(value: unknown): string {
  if (value == null) return '';
  return String(value);
}

/**
 * Number as the screens show it: the stored value plus the derived `-R{n}`
 * (UAC N4/N5). The sheet is a read-only rendering of the document, so nothing
 * here can travel back into `request_number` - only the editable input on the
 * form is required to stay bare.
 */
function displayNumber(request: PurchaseRequest): string {
  return withRevisionSuffix(request.request_number, request.revision_no) ?? '';
}

/**
 * The Purchase Request document as a row array.
 *
 * Split out from the writer so a revision sheet is built from THIS layout rather
 * than a second copy of it, and so the rows are assertable without writing a
 * file.
 */
export function buildPurchaseRequestAoa(
  request: PurchaseRequest,
  // `sales_type` stores a code (`cash_sales`); the screen shows "Cash Sales" via
  // LookupBoundLabel. The caller has the resolved options, so it passes the label
  // in - the sheet must not disagree with the screen.
  salesTypeLabel?: string | null,
): (string | number)[][] {
  const lines = request.lines ?? [];
  // Single submission date shown at the top "Date"; no separate footer date.
  const requestDate = formatDateShort(request.submitted_at);
  const expectedDelivery = str(request.expected_delivery_date)
    ? formatDateCell(request.expected_delivery_date)
    : str(request.expected_delivery_date);
  const expectedPO = request.expected_po_date_text ?? (request.expected_po_date ? formatDateCell(request.expected_po_date) : '');
  const approvedAt = request.approved_at
    ? formatDateCell(request.approved_at)
    : '';

  const aoa: (string | number)[][] = [
    ['Purchase Request'],
    ['Purchase request number:', displayNumber(request), 'Date:', requestDate],
    [],
    ['Customer Name:', str(request.customer_name)],
    ['PIC:', str(request.pic)],
    ['Project Title:', str(request.project_title)],
    ['Purpose:', str(request.purpose)],
    ['Sales Type:', salesTypeLabel ?? str(request.sales_type)],
    ['Expected Date of Delivery:', expectedDelivery, 'Expected date to receive PO:', expectedPO],
    [],
    ['#', 'Item Code', 'Qty', 'Remark'],
  ];

  // Emit exactly the line items - no blank padding rows.
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    aoa.push([
      i + 1,
      line ? str(line.item_code) : '',
      line != null && line.quantity != null ? Number(line.quantity) : '',
      line ? str(line.remark) : '',
    ]);
  }

  aoa.push(
    [],
    ['Requested by:', str(request.requested_by)],
    ['Approved by:', str(request.approved_by), 'Date:', approvedAt],
  );

  return aoa;
}

const PURCHASE_REQUEST_COL_WIDTHS = [{ wch: 22 }, { wch: 24 }, { wch: 12 }, { wch: 40 }];
const SPONSORSHIP_COL_WIDTHS = [
  { wch: 14 },
  { wch: 20 },
  { wch: 8 },
  { wch: 10 },
  { wch: 12 },
  { wch: 40 },
];

/**
 * Export a purchase request to Excel in the Purchase Request form format:
 * Purchase request number, Date, Customer, Project, Purpose, Expected dates,
 * line items table (#, Item Code, Qty, Remark), Requested by, Approved by.
 */
export async function exportPurchaseRequestToExcel(
  request: PurchaseRequest,
  salesTypeLabel?: string | null,
): Promise<void> {
  const xlsx = await getXLSX();
  const ws = xlsx.utils.aoa_to_sheet(buildPurchaseRequestAoa(request, salesTypeLabel));
  ws['!cols'] = PURCHASE_REQUEST_COL_WIDTHS;
  const wb = xlsx.utils.book_new();
  xlsx.utils.book_append_sheet(wb, ws, 'Purchase Request');
  const filename = `Purchase_Request_${displayNumber(request) || request.id}.xlsx`;
  xlsx.writeFile(wb, filename);
}

const SORENTO_HEADER = [
  'SORENTO SDN BHD',
  'No 5, Jalan Astana 2/KU2, Bandar Bukit Raja, 41050 Klang, Selangor, Malaysia.',
  'Tel: +603-3082 9778, Fax: +603-30829278.',
];

/**
 * The Project Sales Sponsorship Form document as a row array (see
 * `buildPurchaseRequestAoa` for why this is split out).
 */
export function buildSponsorshipFormAoa(
  request: PurchaseRequest,
  salesTypeLabel?: string | null,
): (string | number)[][] {
  const lines = request.lines ?? [];
  // Single submission date shown at the top "Date"; no separate footer date.
  const requestDate = formatDateForSponsorship(request.submitted_at);
  const deliveryDate = formatDateForSponsorship(request.expected_delivery_date);
  const approvedAt = request.approved_at
    ? formatDateForSponsorship(request.approved_at)
    : '';
  const tpv =
    str(request.total_project_value_text) ||
    (request.total_project_value != null ? str(request.total_project_value) : '');

  const aoa: (string | number)[][] = [
    [SORENTO_HEADER[0]],
    [SORENTO_HEADER[1]],
    [SORENTO_HEADER[2]],
    [],
    ['Project Sales Sponsorship Form'],
    [],
    ['Sponsorship form number:', displayNumber(request), 'Date:', requestDate],
    [],
    ['Customer Name:', str(request.customer_name)],
    ['PIC:', str(request.pic)],
    ['Delivery Address:', str(request.delivery_address)],
    ['Project Title:', str(request.project_title)],
    ['Total Project Value:', tpv],
    ['Sponsor Subject:', str(request.sponsor_subject)],
    ['Sales Type:', salesTypeLabel ?? str(request.sales_type)],
    ['Date of Delivery:', deliveryDate],
    [],
    ['NO.', 'Item Code', 'Qty', 'U/P', 'Total', 'Remark'],
  ];

  let grandTotal = 0;
  // Emit exactly the line items - no blank padding rows.
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const qty = line != null && line.quantity != null ? Number(line.quantity) : 0;
    const unitPrice = line != null && line.unit_price != null ? Number(line.unit_price) : 0;
    const lineTotal =
      line != null && line.total != null
        ? Number(line.total)
        : qty * unitPrice;
    grandTotal += lineTotal;
    aoa.push([
      i + 1,
      line ? str(line.item_code) : '',
      line != null && line.quantity != null ? Number(line.quantity) : '',
      unitPrice,
      lineTotal.toFixed(2),
      line ? str(line.remark) : '',
    ]);
  }

  aoa.push(
    ['', '', '', 'Grand Total:', grandTotal.toFixed(2), ''],
    [],
    ['Requested by:', str(request.requested_by)],
    ['Approved by:', str(request.approved_by), 'Date:', approvedAt],
  );

  return aoa;
}

/**
 * Export a sponsorship form to Excel in the Project Sales Sponsorship Form format:
 * Company header, Sponsorship form number, Date, Customer, Delivery Address, Project Title,
 * Total Project Value, Sponsor Subject, Sales Type, Date of Delivery, line items (NO., Item Code, Qty, U/P, Total), Grand Total, Requested/Approved by.
 */
export async function exportSponsorshipFormToExcel(
  request: PurchaseRequest,
  salesTypeLabel?: string | null,
): Promise<void> {
  const xlsx = await getXLSX();
  const ws = xlsx.utils.aoa_to_sheet(buildSponsorshipFormAoa(request, salesTypeLabel));
  ws['!cols'] = SPONSORSHIP_COL_WIDTHS;
  const wb = xlsx.utils.book_new();
  xlsx.utils.book_append_sheet(wb, ws, 'Sponsorship Form');
  const filename = `Sponsorship_Form_${displayNumber(request) || request.id}.xlsx`;
  xlsx.writeFile(wb, filename);
}

function isSponsorship(request: PurchaseRequest): boolean {
  return request.request_type === 'sponsorship_form';
}

/** The document for this type, as rows. Both carry the sales type (SF-SALES-TYPE). */
export function buildPurchaseRequestOrSponsorshipAoa(
  request: PurchaseRequest,
  salesTypeLabel?: string | null,
): (string | number)[][] {
  return isSponsorship(request)
    ? buildSponsorshipFormAoa(request, salesTypeLabel)
    : buildPurchaseRequestAoa(request, salesTypeLabel);
}

/**
 * Export based on request_type: purchase_request -> Purchase Request format, sponsorship_form -> Sponsorship Form format.
 */
export async function exportPurchaseRequestOrSponsorshipToExcel(
  request: PurchaseRequest,
  salesTypeLabel?: string | null,
): Promise<void> {
  if (isSponsorship(request)) {
    await exportSponsorshipFormToExcel(request, salesTypeLabel);
  } else {
    await exportPurchaseRequestToExcel(request, salesTypeLabel);
  }
}

/** Row index of the document title, which the revision block follows. The
 *  sponsorship form opens with the three letterhead lines and a blank. */
function titleRowIndex(request: PurchaseRequest): number {
  return isSponsorship(request) ? 4 : 0;
}

/**
 * The same document, filled in from ONE stored revision (round 6, 6.3).
 *
 * The layout is the type's own builder unchanged - only the values differ, and
 * they all come from the snapshot (see `revisionEntryToPurchaseRequest`). The
 * block under the title says which version the sheet is: without it a page of
 * superseded values is indistinguishable from the current form.
 */
export function buildPurchaseRequestRevisionAoa(
  entry: FormRevisionEntry,
  live: PurchaseRequest,
  salesTypeLabel?: string | null,
): (string | number)[][] {
  const adapted = revisionEntryToPurchaseRequest(entry, live);
  return withVersionBlock(
    buildPurchaseRequestOrSponsorshipAoa(adapted, salesTypeLabel),
    adapted,
    entry,
  );
}

/**
 * The version block under the document title: which version these rows are, why
 * it changed, when it was sent.
 *
 * One inserter for both users of it - a revision sheet, and the CURRENT sheet of
 * an include-revisions workbook (whose newest entry no longer gets a sheet of its
 * own). `entry` null leaves the rows exactly as the document builder produced
 * them.
 */
function withVersionBlock(
  aoa: (string | number)[][],
  request: PurchaseRequest,
  entry: FormRevisionEntry | null,
): (string | number)[][] {
  if (!entry) return aoa;
  const cut = titleRowIndex(request) + 1;
  const head = aoa.slice(0, cut);
  const tail = aoa.slice(cut);
  // The sponsorship form already carries a blank row under its title; do not add
  // a second one.
  const separator = (tail[0]?.length ?? 0) === 0 ? [] : [[]];
  return [...head, [], ...revisionInfoRows(entry), ...separator, ...tail];
}

/**
 * One superseded version as its own workbook (round 6, 6.3).
 *
 * Named after THAT version's own document number, exactly as the PDF of the
 * same revision is - not after the record's current number with a second
 * revision marker appended.
 */
export async function exportPurchaseRequestOrSponsorshipRevisionToExcel(
  entry: FormRevisionEntry,
  live: PurchaseRequest,
  salesTypeLabel?: string | null,
): Promise<void> {
  const xlsx = await getXLSX();
  const sponsorship = isSponsorship(live);
  const ws = xlsx.utils.aoa_to_sheet(
    buildPurchaseRequestRevisionAoa(entry, live, salesTypeLabel),
  );
  ws['!cols'] = sponsorship ? SPONSORSHIP_COL_WIDTHS : PURCHASE_REQUEST_COL_WIDTHS;
  const wb = xlsx.utils.book_new();
  xlsx.utils.book_append_sheet(wb, ws, sponsorship ? 'Sponsorship Form' : 'Purchase Request');
  const stem = sponsorship ? 'Sponsorship_Form' : 'Purchase_Request';
  const filename = revisionExportFilename(
    stem,
    entry,
    revisionDocumentNumber(entry, 'request_number', live.request_number) ?? live.id,
  );
  xlsx.writeFile(wb, filename);
}

/** `sales_type` code -> the label the screen shows, from the lookup options the
 *  caller already holds. One resolver so a revision sheet reads its OWN code's
 *  label, not the live record's. */
export type SalesTypeLabelResolver = (code?: string | null) => string | null;

export function createSalesTypeLabelResolver(
  options?: { value: string; label: string }[] | null,
): SalesTypeLabelResolver {
  return (code) => {
    const wanted = (code ?? '').trim().toLowerCase();
    if (!wanted) return null;
    return (
      (options ?? []).find((option) => option.value.toLowerCase() === wanted)?.label ??
      (code ?? null)
    );
  };
}

/**
 * The current form, then every EARLIER version newest first, one sheet each
 * (round 6, 6.4; corrected round 7).
 *
 * Sheet 1 is byte-for-byte the export this page has always produced, plus the
 * version block naming the newest entry when there is one. That entry gets no
 * sheet of its own: it is the version sheet 1 shows, so a sheet for it repeated
 * the same form with the office fields blanked. Same rule as the PDF's
 * `appended_revision_entries`, from the same shared helper.
 *
 * A submission with no lineage yet is silently just that sheet.
 */
export async function exportPurchaseRequestOrSponsorshipWithRevisionsToExcel(
  request: PurchaseRequest,
  entries: FormRevisionEntry[] | null | undefined,
  resolveSalesTypeLabel?: SalesTypeLabelResolver,
): Promise<void> {
  const xlsx = await getXLSX();
  const sponsorship = isSponsorship(request);
  const colWidths = sponsorship ? SPONSORSHIP_COL_WIDTHS : PURCHASE_REQUEST_COL_WIDTHS;
  const resolve = resolveSalesTypeLabel ?? (() => null);

  const wb = xlsx.utils.book_new();
  const current = xlsx.utils.aoa_to_sheet(
    withVersionBlock(
      buildPurchaseRequestOrSponsorshipAoa(request, resolve(request.sales_type)),
      request,
      latestRevisionEntry(entries),
    ),
  );
  current['!cols'] = colWidths;
  const currentSheetName = sponsorship ? 'Sponsorship Form' : 'Purchase Request';
  const taken = new Set<string>([currentSheetName]);
  xlsx.utils.book_append_sheet(wb, current, currentSheetName);

  for (const entry of appendedRevisionEntries(entries)) {
    const code = (entry.snapshot?.sales_type as string | null | undefined) ?? request.sales_type;
    const ws = xlsx.utils.aoa_to_sheet(
      buildPurchaseRequestRevisionAoa(entry, request, resolve(code)),
    );
    ws['!cols'] = colWidths;
    xlsx.utils.book_append_sheet(wb, ws, revisionSheetName(entry, taken));
  }

  const stem = sponsorship ? 'Sponsorship_Form' : 'Purchase_Request';
  xlsx.writeFile(wb, `${stem}_${displayNumber(request) || request.id}.xlsx`);
}
