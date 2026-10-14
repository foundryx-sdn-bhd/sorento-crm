'use client';

import * as React from 'react';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { getProductsForLineSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import {
  InlineLineTable,
  type InlineDraft,
  type InlineLineColumn,
} from '../../../_shared/components/InlineLineTable';
import {
  useSeriesProductMutations,
  useSeriesProductRowMutations,
  useSeriesProductRows,
} from '../../../_shared/hooks/useProjects';
import type { SeriesProductRow } from '../../../_shared/types/project.types';

/**
 * The client's sheet, as a table: what this series sells, for how much, and how far it may be
 * discounted.
 *
 * Excel-shaped on purpose - add a row, then type across it - which is the editing model the
 * client asked for on every line-item table. `InlineLineTable` is the same component the
 * quotation lines use, so the two screens cannot drift into behaving differently.
 *
 * **The floor is not computed here.** `derived_floor` arrives from the server, from the same
 * function the pricing engine refuses a save with. Recomputing `price * (1 - pct/100)` in the
 * browser would be a second implementation of the number a refusal is argued from, and the two
 * would eventually disagree.
 */
/** Malaysia is the only market this system prices in, and a settings lookup for one value
 *  would be a configuration nobody ever changes standing between the reader and the number. */
const CURRENCY = 'RM';

export function SeriesProductsTable({ seriesId }: { seriesId: string }) {
  const rows = useSeriesProductRows(seriesId);
  const { setPricing, remove } = useSeriesProductRowMutations(seriesId);
  const { importCodes } = useSeriesProductMutations();
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
  } = useDebouncedSearch();

  const fetchProducts = React.useCallback(async (query: string) => {
    const products = await getProductsForLineSelect(query || undefined);
    return products.map((product) => ({
      value: product.id,
      label: product.product_code,
      description: product.product_name,
    }));
  }, []);

  const columns = React.useMemo<InlineLineColumn<SeriesProductRow>[]>(
    () => [
      {
        key: 'product_id',
        header: 'Product',
        width: 200,
        kind: 'searchable-select',
        placeholder: 'Pick a product',
        fetchOptions: fetchProducts,
        resolveSelected: (row) =>
          row?.product_id
            ? {
                value: row.product_id,
                label: row.product_code ?? '',
                description: row.product_name ?? undefined,
              }
            : undefined,
        // Picking the product answers the description too, so nobody retypes the catalogue.
        onOptionSelected: (option) => ({
          product_name: option?.description ?? '',
        }),
      },
      {
        key: 'product_name',
        header: 'Description',
        width: 280,
        kind: 'derived',
        derive: (draft) => (
          <span className="block truncate" title={draft.product_name || ''}>
            {draft.product_name || '-'}
          </span>
        ),
      },
      {
        key: 'selling_price',
        header: 'Selling price',
        width: 150,
        kind: 'number',
        align: 'end',
        prefix: CURRENCY,
        // Left blank on purpose where the sheet said nothing: 56 of the client's 151 codes
        // carry no price, and a 0 there would read as "free".
        placeholder: '-',
      },
      {
        key: 'max_discount_pct',
        header: 'Max discount %',
        width: 130,
        kind: 'number',
        align: 'end',
        prefix: '%',
        placeholder: '-',
      },
      {
        key: 'derived_floor',
        header: 'Floor',
        width: 130,
        kind: 'derived',
        align: 'end',
        // Carries the unit for the same reason the editable cells do: this is the number a
        // refusal is argued from, and a bare `95.00` beside a bare `5.00` percentage is one
        // glance away from being read as the wrong kind of quantity.
        derive: (_draft, _index, row) =>
          row?.derived_floor ? (
            <span className="tabular-nums">{`${CURRENCY} ${row.derived_floor}`}</span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
      },
    ],
    [fetchProducts],
  );

  const toDraft = React.useCallback(
    (row: SeriesProductRow): InlineDraft => ({
      product_id: row.product_id ?? '',
      product_name: row.product_name ?? '',
      selling_price: row.selling_price ?? '',
      max_discount_pct: row.max_discount_pct ?? '',
      derived_floor: row.derived_floor ?? '',
    }),
    [],
  );

  const emptyDraft = React.useCallback(
    (): InlineDraft => ({
      product_id: '',
      product_name: '',
      selling_price: '',
      max_discount_pct: '',
      derived_floor: '',
    }),
    [],
  );

  /** Blank cell -> null, which CLEARS. On this screen somebody emptied it deliberately. */
  const money = (value: string) => (value.trim() === '' ? null : value.trim());

  const all = React.useMemo(() => rows.data ?? [], [rows.data]);

  /**
   * Filtered in the browser, over the whole set.
   *
   * The series is 92 products today and its ceiling is a sheet somebody types by hand, so
   * a server round trip per keystroke would buy nothing and cost a spinner. Code AND name,
   * because the admin arrives from their spreadsheet knowing one or the other.
   *
   * The browser's own Ctrl-F is not a substitute: it finds only what is scrolled into the
   * DOM, so searching a long series for a code the table has not drawn yet answers 0/0 for
   * a product that is plainly there.
   */
  const visible = React.useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return all;
    return all.filter(
      (row) =>
        (row.product_code ?? '').toLowerCase().includes(needle) ||
        (row.product_name ?? '').toLowerCase().includes(needle),
    );
  }, [all, search]);

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <ListSearchInput
          value={searchInput}
          onChange={setSearchInput}
          placeholder="Search products"
          aria-label="Search products in this series"
          className="w-full sm:max-w-xs"
        />
        <p className="text-xs text-muted-foreground" role="status">
          {search.trim()
            ? `${visible.length} of ${all.length} products`
            : `${all.length} ${all.length === 1 ? 'product' : 'products'}`}
        </p>
      </div>

      <InlineLineTable<SeriesProductRow>
        rows={visible}
        getRowId={(row) => row.product_id}
        canDeleteRow={(row) => !row.in_scope}
        columns={columns}
        toDraft={toDraft}
        emptyDraft={emptyDraft}
        isLoading={rows.isLoading}
        addLabel="Add a product"
        emptyHint={
          search.trim()
            ? `No product here matches "${search.trim()}".`
            : 'No products yet. Add one, or load the sheet above.'
        }
        describeRow={(row, index) => row?.product_code || `row ${index + 1}`}
        validateRow={(draft) => {
          const errors: Record<string, string> = {};
          if (!draft.product_id) errors.product_id = 'Pick a product';
          const pct = Number(draft.max_discount_pct);
          if (draft.max_discount_pct && (Number.isNaN(pct) || pct < 0 || pct > 100)) {
            errors.max_discount_pct = 'A percentage between 0 and 100';
          }
          const price = Number(draft.selling_price);
          if (draft.selling_price && (Number.isNaN(price) || price < 0)) {
            errors.selling_price = 'A price of zero or more';
          }
          return errors;
        }}
        onCreate={async (draft) => {
          // Two steps because nominating and pricing are two different writes: the first is
          // the S18 importer (which is also what the sheet upload calls), the second sets the
          // money. Doing it this way keeps ONE way for a product to join a series.
          const product = await getProductsForLineSelect(undefined).then((options) =>
            options.find((candidate) => candidate.id === draft.product_id),
          );
          await importCodes.mutateAsync({
            id: seriesId,
            body: { codes: [product?.product_code ?? draft.product_id], mode: 'append' },
          });
          if (draft.selling_price || draft.max_discount_pct) {
            await setPricing.mutateAsync({
              productId: draft.product_id,
              body: {
                selling_price: money(draft.selling_price),
                max_discount_pct: money(draft.max_discount_pct),
              },
            });
          }
        }}
        onUpdate={async (row, draft) => {
          await setPricing.mutateAsync({
            productId: row.product_id,
            body: {
              selling_price: money(draft.selling_price),
              max_discount_pct: money(draft.max_discount_pct),
            },
          });
        }}
        onDelete={async (row) => {
          await remove.mutateAsync(row.product_id);
        }}
        deleteDescription={(row) =>
          `Remove ${row.product_code ?? 'this product'} from the series? This action cannot be undone.`
        }
      />
    </div>
  );
}
