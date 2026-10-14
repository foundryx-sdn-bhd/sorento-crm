'use client';

import * as React from 'react';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import { cn } from '@/lib/utils';
// The shared products `/select` mapper. Its name says "variant" because that screen needed
// it first; the endpoint and the shape are the generic ones.
import { getProductsForVariantSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import type { POVersionLine } from '../../../_shared/types/poIntake.types';
import { sameDisplayText } from '@/lib/product-display';

/**
 * Our code, hiding inside theirs, as a search term.
 *
 * `BUI-HB-SRTWB7055` is `SRTWB7055` with the customer's prefix bolted on, so typing the whole
 * printed code into a product search returns nothing and the picker opens on "No products
 * match". The backend resolver already knows this (`_code_candidates`: the code plus every
 * dash-suffix of it), and the shortest suffix still worth searching is the one to seed with,
 * because a search is a substring match and the shortest candidate casts the widest net.
 * Four characters is the same floor the backend's own fuzzy probe uses, and it is what keeps
 * the trailing `-RL` of `BUI-HB-SRTWC8613-RL` from being mistaken for the code.
 */
export function productSearchSeed(customerCode: string | null): string {
  const cleaned = (customerCode ?? '').trim();
  if (!cleaned) return '';
  const parts = cleaned.split('-');
  for (let start = parts.length - 1; start >= 1; start -= 1) {
    const suffix = parts.slice(start).join('-');
    if (suffix.length >= 4) return suffix;
  }
  return cleaned;
}

/**
 * The products this PO actually orders, in the order the document lists them.
 *
 * A schedule column has to end up on a line of the PO it is checked against, so the whole
 * product catalogue is the wrong list to choose from: it offers thousands of items, all but
 * a couple of dozen of which can only produce a column that does not reconcile. Lines the
 * resolver never matched to a product carry no id to pick, and the same product ordered on
 * two lines is one choice, not two.
 */
export function poProductOptions(lines: POVersionLine[]): SearchableSelectOption[] {
  const seen = new Set<string>();
  const options: SearchableSelectOption[] = [];
  for (const line of lines) {
    const productId = line.resolved_product_id;
    if (!productId || seen.has(productId)) continue;
    seen.add(productId);
    options.push({
      value: productId,
      label: line.resolved_product_code ?? line.stock_code_raw ?? `Line ${line.line_no}`,
      description: line.description_raw ?? undefined,
    });
  }
  return options;
}

/** A product code sits in a description as one token of letters and digits, e.g. `SRTW8613-RL`. */
const CODE_TOKEN = /^(?=.*\d)(?=.*[a-z])[a-z0-9]+(?:-[a-z0-9]+)*$/i;

/** Our own brand leads most of our descriptions and tells a reviewer nothing. */
const HOUSE_BRAND = /^sorento$/i;

/**
 * A description as a short name: its first phrase and its size (W1, owner hand test 26 Sep,
 * "too long already").
 *
 * A PO line's description is a spec sheet run together with dashes ("SORENTO SRTW8613-RL ONE
 * PIECE WC - SET- Washdown ... - Size : 700 x 380 x 730mm- S-Trap ..."), five lines tall in a
 * picker. What tells two options apart is the first phrase and the size, so that is the name;
 * the brand and the code are dropped because the code is already printed beside it. The full
 * description stays on the option, so search still matches it and hovering shows it.
 */
export function productShortName(description: string | null | undefined): string {
  const text = (description ?? '').replace(/\s+/g, ' ').trim();
  if (!text) return '';
  // A phrase ends at a dash with a space on either side of it; `One-Piece` and `SRTW8613-RL`
  // are single words and keep theirs.
  const phrases = text.split(/\s+-\s*|-\s+/).map((part) => part.trim()).filter(Boolean);
  const words = (phrases[0] ?? '')
    .split(' ')
    .filter((word) => !HOUSE_BRAND.test(word) && !CODE_TOKEN.test(word));
  let name = words.join(' ');
  if (name && name === name.toUpperCase()) {
    // A shouted spec line reads as a sentence; two-letter words (WC) are initialisms.
    name = name
      .toLowerCase()
      .split(' ')
      .map((word) => (word.replace(/[^a-z]/g, '').length <= 2 ? word.toUpperCase() : word))
      .join(' ');
    name = name.charAt(0).toUpperCase() + name.slice(1);
  }
  const size = phrases
    .map((phrase) => /^size\s*:\s*(.+)$/i.exec(phrase)?.[1])
    .find(Boolean)
    ?.replace(/\s*mm$/i, '');
  return [name || phrases[0], size].filter(Boolean).join(' ');
}

/**
 * Wide enough for a code and its short name on one line (24rem), wider than the Flag popover
 * it opens from, and never wider than the screen: the menu is portalled, and the select caps it
 * at the space Radix measured.
 */
const MENU_CLASS = 'min-w-[280px] w-[max(var(--radix-popper-anchor-width),24rem)]';

/**
 * One line per option: the code, then the short name, clipped with an ellipsis if even that
 * does not fit. The whole description is the row's hover title.
 */
export function renderProductOption(option: SearchableSelectOption) {
  const name = productShortName(option.description);
  return (
    <div className="flex min-w-0 flex-1 items-baseline gap-2" title={option.description}>
      <span className="shrink-0 font-medium">{option.label}</span>
      {name && !sameDisplayText(option.label, name) ? (
        <span className="truncate text-xs text-muted-foreground">{name}</span>
      ) : null}
    </div>
  );
}

/**
 * Every word typed has to appear somewhere in the option, which is the same rule
 * `SearchableSelect` applies to a static list. Keeping the two the same means a PO list and a
 * catalogue list answer a search identically.
 */
function optionMatches(option: SearchableSelectOption, tokens: string[]): boolean {
  if (tokens.length === 0) return true;
  const haystack = `${option.label} ${option.description ?? ''}`.toLowerCase();
  return tokens.every((token) => haystack.includes(token));
}

/**
 * Identifies, or corrects, the product a schedule column means.
 *
 * Offered in three places for one reason: the reviewer has to be able to fix a column from
 * wherever they read that it is broken. In the matrix it sits in the column header, on a
 * phone inside the card, and in the reconciliation list beside the sentence that named the
 * problem, which is where most people meet it first.
 */
export function DeliveryScheduleProductPicker({
  idPrefix,
  columnIndex,
  customerCode,
  action = 'Pick the product',
  variant = 'field',
  poOptions,
  disabled,
  onPick,
}: {
  /**
   * The matrix, the phone view and the reconciliation list are all mounted at once (they
   * differ by breakpoint or by section, not by state), so the element id has to say which
   * one this is or they collide in one document.
   */
  idPrefix: string;
  columnIndex: number;
  customerCode: string | null;
  /**
   * What pressing this does, in the user's words. A column with no product at all is being
   * picked for the first time; a column that resolved to the wrong product is being changed.
   */
  action?: string;
  /** `field` is a select box; `compact` is a small text button for a place with no room. */
  variant?: 'field' | 'compact';
  /**
   * The products the PO this schedule was checked against actually orders.
   *
   * The ONLY list to choose from when there is one. Empty or absent (a schedule checked
   * against no PO version, or a PO whose lines resolved to nothing) is the one case that
   * searches the catalogue, because then it is the only list left.
   */
  poOptions?: SearchableSelectOption[];
  disabled?: boolean;
  onPick: (productId: string) => void;
}) {
  const hasPoOptions = Boolean(poOptions && poOptions.length > 0);

  const fetchCatalogue = React.useCallback(async (query: string) => {
    const rows = await getProductsForVariantSelect(query || undefined);
    return rows.map((row) => ({
      value: row.id,
      label: row.product_code,
      description: row.product_name,
    }));
  }, []);

  /**
   * A PO list is the WHOLE list. The catalogue is never consulted beside it.
   *
   * It was, at first: a search matching no PO line fell through to the catalogue so that an
   * item genuinely missing from the PO could still be picked. What that produced on screen is
   * the thing it was meant to avoid - type two letters, and thousands of catalogue items are
   * back, indistinguishable from the PO's own. A column that names a product the PO does not
   * order is not fixed by pointing it at another product the PO does not order either; it is
   * fixed by amending the PO, which the gear menu opens.
   */
  const fetchProducts = React.useCallback(
    async (query: string) => {
      const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
      if (poOptions && poOptions.length > 0)
        return poOptions.filter((option) => optionMatches(option, tokens));
      return fetchCatalogue(query);
    },
    [fetchCatalogue, poOptions],
  );

  const emptyMessage = hasPoOptions ? 'No PO line matches' : 'No products match';

  const id = `${idPrefix}-column-product-${columnIndex}`;
  const name = customerCode
    ? `${action} for ${customerCode}`
    : `${action} for column ${columnIndex + 1}`;
  /**
   * A PO list opens whole, never narrowed.
   *
   * The seed exists because a catalogue of thousands is unusable unnarrowed, and it is the
   * wrong move against a list of forty-odd: the printed code often appears nowhere on the PO,
   * which is precisely why the column did not reconcile, and the picker would open on an
   * empty list with the reviewer having to clear the box to see the lines they came to
   * choose from. Catalogue mode keeps the seed, where it earns its place.
   */
  const seed = hasPoOptions ? '' : productSearchSeed(customerCode);

  if (variant === 'compact') {
    return (
      <SearchableSelect
        value=""
        onChange={onPick}
        disabled={disabled}
        fetchOptions={fetchProducts}
        initialQuery={seed}
        emptyMessage={emptyMessage}
        renderOption={renderProductOption}
        className={MENU_CLASS}
        renderTrigger={({ disabled: isDisabled }) => (
          <button
            type="button"
            id={id}
            disabled={isDisabled}
            aria-label={name}
            className={cn(
              'rounded-sm text-start text-[11px] font-normal text-primary underline-offset-2',
              'hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary',
              'disabled:cursor-not-allowed disabled:text-muted-foreground',
            )}
          >
            {action}
          </button>
        )}
      />
    );
  }

  return (
    <>
      <label htmlFor={id} className="sr-only">
        {name}
      </label>
      <SearchableSelect
        id={id}
        value=""
        onChange={onPick}
        size="sm"
        disabled={disabled}
        fetchOptions={fetchProducts}
        initialQuery={seed}
        placeholder={action}
        emptyMessage={emptyMessage}
        renderOption={renderProductOption}
        triggerClassName="w-full"
        className={MENU_CLASS}
      />
    </>
  );
}
