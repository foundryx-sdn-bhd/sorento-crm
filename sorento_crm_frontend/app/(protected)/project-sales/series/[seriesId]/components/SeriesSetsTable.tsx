'use client';

import * as React from 'react';
import { getProductSets } from '@/app/(protected)/master-data-management/product-sets/services/productSetService';
import {
  InlineLineTable,
  type InlineDraft,
  type InlineLineColumn,
} from '../../../_shared/components/InlineLineTable';
import {
  useSeriesSetRowMutations,
  useSeriesSetRows,
} from '../../../_shared/hooks/useProjects';
import type { SeriesSetRow } from '../../../_shared/types/project.types';

/**
 * What this series sells each product SET for, and how far it may be discounted: the twin of
 * `SeriesProductsTable`, for the sets a quotation can carry as one line.
 *
 * A set is standard in a series only by a row here (it has no category), so adding the row is
 * what makes it standard. `derived_floor` arrives from the server, from the function the pricing
 * engine enforces with; it is never recomputed in the browser.
 */
const CURRENCY = 'RM';

export function SeriesSetsTable({ seriesId }: { seriesId: string }) {
  const rows = useSeriesSetRows(seriesId);
  const { setPricing, remove } = useSeriesSetRowMutations(seriesId);

  const fetchSets = React.useCallback(async (query: string) => {
    const sets = await getProductSets({
      pageIndex: 0,
      pageSize: 50,
      sorting: [],
      searchQuery: query,
    });
    return (sets.data ?? [])
      .filter((set) => set.is_active)
      .map((set) => ({
        value: set.id,
        label: set.set_code,
        description: set.name,
        searchText: `${set.set_code} ${set.name}`,
      }));
  }, []);

  const columns = React.useMemo<InlineLineColumn<SeriesSetRow>[]>(
    () => [
      {
        key: 'product_set_id',
        header: 'Set',
        width: 190,
        kind: 'searchable-select',
        placeholder: 'Pick a set',
        fetchOptions: fetchSets,
        resolveSelected: (row) =>
          row?.product_set_id
            ? { value: row.product_set_id, label: row.set_code ?? '' }
            : undefined,
        onOptionSelected: (option, draft) => ({
          ...draft,
          set_name: option?.description ?? '',
        }),
      },
      {
        key: 'set_name',
        header: 'Name',
        width: 220,
        kind: 'derived',
        derive: (draft) => (
          <span className="block truncate" title={draft.set_name || ''}>
            {draft.set_name || '-'}
          </span>
        ),
      },
      {
        key: 'part_codes',
        header: 'Parts',
        width: 260,
        kind: 'derived',
        derive: (_draft, _index, row) => {
          const text = (row?.part_codes ?? []).join(' + ');
          return (
            <span className="block truncate" title={text || undefined}>
              {text || '-'}
            </span>
          );
        },
      },
      {
        key: 'selling_price',
        header: 'Selling price',
        width: 150,
        kind: 'number',
        align: 'end',
        prefix: CURRENCY,
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
        derive: (_draft, _index, row) =>
          row?.derived_floor ? (
            <span className="tabular-nums">{`${CURRENCY} ${row.derived_floor}`}</span>
          ) : (
            <span className="text-muted-foreground">-</span>
          ),
      },
    ],
    [fetchSets],
  );

  const toDraft = React.useCallback(
    (row: SeriesSetRow): InlineDraft => ({
      product_set_id: row.product_set_id ?? '',
      set_name: row.set_name ?? '',
      selling_price: row.selling_price ?? '',
      max_discount_pct: row.max_discount_pct ?? '',
      derived_floor: row.derived_floor ?? '',
    }),
    [],
  );

  const emptyDraft = React.useCallback(
    (): InlineDraft => ({
      product_set_id: '',
      set_name: '',
      selling_price: '',
      max_discount_pct: '',
      derived_floor: '',
    }),
    [],
  );

  /** Blank cell -> null, which CLEARS: on this screen somebody emptied it deliberately. */
  const money = (value: string) => (value.trim() === '' ? null : value.trim());

  return (
    <InlineLineTable<SeriesSetRow>
      rows={rows.data ?? []}
      getRowId={(row) => row.product_set_id}
      columns={columns}
      toDraft={toDraft}
      emptyDraft={emptyDraft}
      isLoading={rows.isLoading}
      addLabel="Add a set"
      emptyHint="No sets yet."
      describeRow={(row, index) => row?.set_code || `row ${index + 1}`}
      validateRow={(draft) => {
        const errors: Record<string, string> = {};
        if (!draft.product_set_id) errors.product_set_id = 'Pick a set';
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
        // The PATCH is an upsert: it adds the set to the series and prices it in one write.
        await setPricing.mutateAsync({
          productSetId: draft.product_set_id,
          body: {
            selling_price: money(draft.selling_price),
            max_discount_pct: money(draft.max_discount_pct),
          },
        });
      }}
      onUpdate={async (row, draft) => {
        await setPricing.mutateAsync({
          productSetId: row.product_set_id,
          body: {
            selling_price: money(draft.selling_price),
            max_discount_pct: money(draft.max_discount_pct),
          },
        });
      }}
      onDelete={async (row) => {
        await remove.mutateAsync(row.product_set_id);
      }}
      deleteDescription={(row) =>
        `Remove ${row.set_code ?? 'this set'} from the series? This action cannot be undone.`
      }
    />
  );
}
