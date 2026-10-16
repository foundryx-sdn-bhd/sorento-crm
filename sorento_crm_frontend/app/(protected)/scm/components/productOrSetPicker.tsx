'use client';

import * as React from 'react';
import { Badge } from '@/components/ui/badge';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import { getProducts } from '@/app/(protected)/master-data-management/products/services/productService';
import { getProductSets } from '@/app/(protected)/master-data-management/product-sets/services/productSetService';
import { codeWithName } from '@/lib/product-display';

/**
 * One list holding products AND product sets, for every place a person answers "what does
 * this supplier's code mean" (F12, R20).
 *
 * The supplier sells the whole WC. `CWC605-RL` is our SET - pedestal plus cistern - and no
 * product carries that code, so a picker that could only offer products could not express
 * the true answer and the operator's only options were the wrong half or Dismiss.
 *
 * Shared by the unmatched-code queue's inline select and the proforma detail's Match dialog,
 * because the two answer the same question and a list that differed between them would make
 * the same code answerable one way on one screen and another way on the other.
 *
 * SERVER-SEARCHED and paginated on the product side: the product master is tens of thousands
 * of rows, and a dropdown holding one cached page silently hides the item the operator is
 * looking for - the mistake this codebase has made twice. Sets are asked for on the FIRST
 * page only: there are two orders of magnitude fewer of them, so one page is the whole
 * answer, and repeating them under every "Load more" would be noise.
 */

/** What a set's option value is prefixed with, so one string can carry either kind. */
export const SET_OPTION_PREFIX = 'set:';

const PRODUCT_PAGE_SIZE = 50;
const SET_PAGE_SIZE = 20;

/** Which of the two an option value names, in the shape the alias endpoint takes. */
export function aliasTargetFor(
  value: string,
): { product_id: string } | { product_set_id: string } {
  return value.startsWith(SET_OPTION_PREFIX)
    ? { product_set_id: value.slice(SET_OPTION_PREFIX.length) }
    : { product_id: value };
}

export function isSetOption(value: string): boolean {
  return value.startsWith(SET_OPTION_PREFIX);
}

/** One product, as a caller that has to fill a row from the pick needs it. */
export interface PickedProductFacts {
  id: string;
  code: string;
  uom: string;
}

export async function fetchProductOrSetOptions(
  query: string,
  pageIndex: number,
  /**
   * The product rows behind this page, for a caller that fills OTHER cells from the pick:
   * the proforma invoice writes an added line's item code and UOM from the product chosen,
   * and an option value alone cannot say either. A third argument, so `SearchableSelect`'s
   * own two-argument call is unaffected.
   */
  onProductsLoaded?: (products: PickedProductFacts[]) => void,
): Promise<SearchableSelectOption[]> {
  const products = await getProducts({
    pageIndex,
    pageSize: PRODUCT_PAGE_SIZE,
    sorting: [],
    searchQuery: query,
    status: 'active',
  });
  onProductsLoaded?.(
    (products.data ?? []).map((p) => ({
      id: p.id,
      code: p.product_code,
      uom: p.base_uom?.uom_code ?? '',
    })),
  );
  const productOptions: SearchableSelectOption[] = (products.data ?? []).map((p) => ({
    value: p.id,
    label: codeWithName(p.product_code, p.product_name),
    searchText: `${p.product_code} ${p.product_name}`,
    code: p.product_code,
  }));

  if (pageIndex > 0) return productOptions;

  // Best-effort: a reader without the product-set permission still gets the product half
  // rather than an empty picker, which is the state that makes a code unanswerable.
  let setOptions: SearchableSelectOption[] = [];
  try {
    const sets = await getProductSets({
      pageIndex: 0,
      pageSize: SET_PAGE_SIZE,
      sorting: [],
      searchQuery: query,
    });
    setOptions = (sets.data ?? []).map((s) => ({
      value: `${SET_OPTION_PREFIX}${s.id}`,
      label: codeWithName(s.set_code, s.name),
      searchText: `${s.set_code} ${s.name}`,
      description: 'Set',
      code: s.set_code,
    }));
  } catch {
    setOptions = [];
  }

  // Sets first: they are the rarer and more specific answer, and a code spelled as a set
  // code is almost never also a product code.
  return [...setOptions, ...productOptions];
}

/** The option body: a set wears a badge, because picking one binds a different thing. */
export function renderProductOrSetOption(opt: SearchableSelectOption): React.ReactNode {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="min-w-0 break-words">{opt.label}</span>
      {isSetOption(opt.value) ? (
        <Badge variant="secondary" appearance="light" size="sm" className="shrink-0">
          Set
        </Badge>
      ) : null}
    </span>
  );
}
