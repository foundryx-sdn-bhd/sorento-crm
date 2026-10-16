'use client';

import { useMemo } from 'react';
import { useQueries, useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import type { SearchableSelectOption } from '@/components/common/SearchableSelect';
import { STOCK_DETAIL_KEY } from './useFulfilmentPlanning';
import { getStockDetail } from '../services/fulfilmentPlanningService';

/**
 * `PLAN-oi-request-cs-reserve.md` section 6c F1/F2 (AC-RS-53/AC-RS-55, review round 2
 * ruling): the reserve dialog's own "Location" options and its "Reserved" default, ONE
 * resolution shared by both so they can never read two different answers for the same
 * row.
 *
 * Round 2 supersedes round 1's own group-suffix reading entirely - owner words: "list
 * all the site pool with this BRW (configurable as default)". Options are now EVERY
 * pool `stock-detail?group=pools` lists for the row's own product (the SAME axis the
 * fulfilment board itself reads, unchanged - F1's own ruling), never a group-of-code
 * resolution keyed off the row's own location. Default is the configured
 * `system_settings.oi_reserve_default_pool_warehouse_id` when it names one of those
 * pools, else the row's own SITE pool (the pool whose code matches the row's own
 * location's site prefix), else the first pool offered.
 */

function siteCodeOf(code: string | null | undefined): string | null {
  if (!code) return null;
  const cut = code.indexOf('-');
  return cut < 0 ? code : code.slice(0, cut);
}

export interface ReserveRowOptionsEntry {
  key: string;
  productId: string | null;
  /** The row's own stock location code (`BRW-NTC`), or null when the row states none -
   * used only to pick the FALLBACK default (its own site pool) when no configured
   * default applies. */
  location: string | null;
}

export interface ReserveRowOptionsResult {
  isLoading: boolean;
  defaultWarehouseId: string | null;
  options: SearchableSelectOption[];
  /** `available_qty` at every pool this row's own product carries - the Reserved
   * default recomputes from whichever one is chosen (AC-RS-28/AC-RS-55). */
  availableQtyByWarehouseId: Record<string, number>;
}

const EMPTY_RESULT: ReserveRowOptionsResult = {
  isLoading: false,
  defaultWarehouseId: null,
  options: [],
  availableQtyByWarehouseId: {},
};

/**
 * The owner-configured default pool (F1). Read through the narrow `/settings/
 * app-config` projection (`AppConfigResponse`), NOT the full settings blob - the
 * blob is gated on `user_management.settings.view`, which Eling (CS, the one
 * `projects.order_inquiries.reserve` holder this default exists for) does not hold,
 * so reading it there meant the configured default never reached her at all. The
 * same reasoning `useCurrencyFormat.ts` already carries for `currency_format`.
 *
 * S3 (reviewer round): `apiFetch` rewrites this whole `/api/user-management/*` prefix
 * straight to FastAPI's `/api/v1/user-management/...` (`lib/api.ts`'s own
 * `businessApiRoutes` table) - it never reaches a Next.js `app/api/...` route at all,
 * so there was never a missing proxy route to add. `app/api/user-management/settings/
 * app-config/route.ts` (which claimed to be that fix) is deleted; it never once ran.
 */
function useConfiguredDefaultPoolId(): string | null {
  const { data } = useQuery({
    queryKey: ['oi-reserve-default-pool-setting'],
    queryFn: async (): Promise<string | null> => {
      const response = await apiFetch('/api/user-management/settings/app-config');
      if (!response.ok) return null;
      try {
        const body = await response.json();
        const id = body?.oi_reserve_default_pool_warehouse_id;
        return typeof id === 'string' && id ? id : null;
      } catch {
        return null;
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
  return data ?? null;
}

export function useReserveRowOptions(
  entries: ReserveRowOptionsEntry[],
): Record<string, ReserveRowOptionsResult> {
  const configuredDefaultPoolId = useConfiguredDefaultPoolId();

  // One query per DISTINCT product, mapped back to every entry that names it: six rows of
  // one product used to register six identical query keys, which `QueriesObserver` flags as
  // duplicates and keeps notifying on (a freeze in the Request CS to reserve dialog).
  const productIds = useMemo(
    () => [...new Set(entries.map((entry) => entry.productId).filter((id): id is string => Boolean(id)))],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [entries.map((entry) => entry.productId ?? '').join('|')],
  );
  const poolsQueries = useQueries({
    queries: productIds.map((productId) => ({
      queryKey: [STOCK_DETAIL_KEY, productId, 'pools', 'reserve-row-options'],
      queryFn: () => getStockDetail(productId, null, [], 'pools'),
    })),
  });

  // Stable while nothing underneath changed, so a caller's unrelated state change (a tickbox)
  // does not hand its grid a fresh record every render.
  const dataSignature = poolsQueries
    .map((query) => `${query.dataUpdatedAt}:${query.isLoading ? 1 : 0}`)
    .join('|');
  const entriesSignature = entries
    .map((entry) => `${entry.key}~${entry.productId ?? ''}~${entry.location ?? ''}`)
    .join('|');
  return useMemo(() => {
    const queryByProduct = new Map(productIds.map((id, index) => [id, poolsQueries[index]]));
    const result: Record<string, ReserveRowOptionsResult> = {};
    entries.forEach((entry) => {
      if (!entry.productId) {
        result[entry.key] = EMPTY_RESULT;
        return;
      }
      const query = queryByProduct.get(entry.productId);
      const poolLocations = query?.data?.locations ?? [];

      const options: SearchableSelectOption[] = [];
      const availableQtyByWarehouseId: Record<string, number> = {};
      for (const loc of poolLocations) {
        if (!loc.warehouse_id) continue;
        const available = loc.available_qty != null ? Number(loc.available_qty) : null;
        if (available != null) availableQtyByWarehouseId[loc.warehouse_id] = available;
        options.push({
          value: loc.warehouse_id,
          label: available != null ? `${loc.location ?? ''}  available ${available}` : (loc.location ?? ''),
        });
      }

      const siteCode = siteCodeOf(entry.location);
      const rowSitePoolId =
        poolLocations.find((loc) => loc.location === siteCode)?.warehouse_id ?? null;
      const configuredIsOffered =
        configuredDefaultPoolId != null &&
        options.some((option) => option.value === configuredDefaultPoolId);

      result[entry.key] = {
        isLoading: Boolean(query?.isLoading),
        defaultWarehouseId:
          (configuredIsOffered ? configuredDefaultPoolId : null) ??
          rowSitePoolId ??
          options[0]?.value ??
          null,
        options,
        availableQtyByWarehouseId,
      };
    });
    return result;
    // `entries` and `poolsQueries` are rebuilt every render; their signatures are what matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entriesSignature, dataSignature, configuredDefaultPoolId]);
}
