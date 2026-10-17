'use client';

import * as React from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { StockStatusPanel } from '@/components/stock-status/StockStatusPanel';
import { searchStockStatusProducts } from '@/components/stock-status/stockStatusService';
import { useStockStatus } from '@/components/stock-status/useStockStatus';

/**
 * Inventory > Stock status: pick any item and read AutoCount's Available Stock Status for it.
 * The item lives in the URL (`?product_id=`), so an embedded panel's "Open full page" lands
 * here on the same item and a link can be shared.
 */
export default function StockStatusPageClient() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const productId = params.get('product_id') ?? '';
  // The panel's own read (same key, so no second request): names the picked item when the page
  // was opened by link, so the picker never shows a bare id.
  const picked = useStockStatus(productId || null, null);
  const selectedOption = picked.data
    ? { value: picked.data.product_id, label: picked.data.item_code, description: picked.data.description ?? undefined }
    : undefined;

  const pick = (value: string) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set('product_id', value);
    else next.delete('product_id');
    router.replace(`${pathname}${next.toString() ? `?${next.toString()}` : ''}`);
  };

  return (
    <div className="flex flex-col gap-4 pb-8">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Item</span>
        <SearchableSelect
          value={productId}
          onChange={pick}
          fetchOptions={searchStockStatusProducts}
          selectedOption={selectedOption}
          paginated
          clearable
          placeholder="Search an item code"
          triggerClassName="w-full sm:w-96"
        />
      </div>
      {productId ? (
        <StockStatusPanel productId={productId} />
      ) : (
        <div className="rounded-md border px-4 py-8 text-center">
          <p className="font-medium">Pick an item</p>
          <p className="text-sm text-muted-foreground">Every location, its orders and what is coming in.</p>
        </div>
      )}
    </div>
  );
}
