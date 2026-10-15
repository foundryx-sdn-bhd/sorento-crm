import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import type { SalesOrderLine } from '../../../types/scm.types';
import { codeWithName } from '@/lib/product-display';

/**
 * The line-edit draft, and the one rule that reads it from outside the component.
 *
 * ITS OWN MODULE, deliberately. These used to live in `SalesOrderDetail.tsx`, and exporting
 * a value that is not a React component from a component file takes that file OUT of React
 * Fast Refresh: Next then does a FULL PAGE RELOAD for any edit to it, in Next's own words -
 *
 *   "Fast Refresh will perform a full reload when you edit a file that's imported by modules
 *    outside of the React rendering tree. You might have a file which exports a React
 *    component but also exports a value that is imported by a non-React component file.
 *    Consider migrating the non-React component export to a separate file and importing it
 *    into both files."
 *
 * Measured in the browser on 13 September 2026: with the resolver exported from the
 * component file, one edit under the running dev server wiped an OPEN edit session - the
 * page came back in view mode with the route's own query string, so Remove line and Save
 * landed on a tree that no longer had a session, sent nothing, and said nothing. Splitting
 * the module restores the state-preserving refresh.
 */
export type LineDraft = {
  sku: string;
  qty_ordered: string;
  warehouse_code: string;
  required_date: string;
  uom: string;
  unit_price: string;
  discount: string;
  /**
   * The product option the person actually PICKED, kept whole.
   *
   * The select resolves its own trigger label out of the page it last fetched, and that page
   * is refetched per open and thrown away on close - so a picked product read fine for a
   * moment and then fell back to "Select product" the instant the popover shut. Holding the
   * option here means the cell can say what was chosen without asking the server again.
   */
  picked_product?: SearchableSelectOption | null;
};

/**
 * The option the Product select shows for a line whose product is not on the page the
 * server just returned - which is most of them, against a 22,000-row catalogue.
 *
 * TWO SOURCES, in this order. What the person PICKED in this session, kept on the draft -
 * because the select's own label comes from `asyncOptions`, a per-open fetch that is
 * discarded when the popover closes, so a picked product reverted to "Select product" the
 * moment it shut (measured in the browser twice, 13 September 2026). Then the line's OWN
 * product as it loaded, for the untouched case. Nothing when the draft names a product
 * neither source can label: a stale row label over somebody else's SKU would be worse than
 * the placeholder.
 *
 * Pure, so it can be read on its own: the defect it exists to stop is invisible in jsdom,
 * where the fetch resolves after the popover has already closed.
 */
export function productFallbackFor(
  row: Pick<SalesOrderLine, 'sku' | 'product_name'>,
  draft: Pick<LineDraft, 'sku' | 'picked_product'> | undefined,
): SearchableSelectOption | undefined {
  const sku = draft?.sku ?? row.sku;
  const picked = draft?.picked_product;
  if (picked && picked.value === sku) return picked;
  if (!row.sku || sku !== row.sku) return undefined;
  return {
    value: row.sku,
    label: codeWithName(row.sku, row.product_name, ' · '),
  };
}
