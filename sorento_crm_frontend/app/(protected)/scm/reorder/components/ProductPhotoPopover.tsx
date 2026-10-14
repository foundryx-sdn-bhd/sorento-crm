'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Image as ImageIcon } from 'lucide-react';
import { Popover, PopoverContent, PopoverPortal, PopoverTrigger } from '@/components/ui/popover';
import { Skeleton } from '@/components/ui/skeleton';
import { useProductPhoto } from '../hooks/useReorderRun';
import { distinctName } from '@/lib/product-display';

/**
 * What the product actually looks like, on the row the buyer is deciding.
 *
 * > "as IT I do not know what a product looks like"
 *
 * A plan row is a code and a name, and a buyer who does not handle the goods cannot tell
 * SRTWCY8840 from SRTWCY8850 from either. The picture is the catalogue's, governed by
 * Dealer Kit -> Brochure images, so the plan screen and the catalogue can never show a
 * different picture of the same product.
 *
 * Two fetches, deliberately. The run-wide map (`onOpen`, once) says only WHICH rows have a
 * photo, so the icon can dim without signing anything; the URL is signed here, per product,
 * on the popover that asked for it.
 */
export type ProductPhotoStatus = 'idle' | 'loading' | 'ready' | 'error';

/**
 * The fetch lives in here, and this only mounts once the popover is OPEN.
 *
 * The same reason the demand drill does it: a query subscription per row, for a panel nobody
 * opened, is hundreds of them on a full plan.
 */
function ProductPhotoBody({
  runId,
  productId,
  sku,
  productName,
}: {
  runId: string | null;
  productId: string | null;
  sku: string;
  productName?: string | null;
}) {
  const { data, isError } = useProductPhoto(runId, productId, true);
  // A signed URL that expired between the fetch and the render comes back 403, and the
  // browser reports it only through the image's own error event.
  const [broken, setBroken] = useState(false);
  // The failure belonged to the URL that produced it. A re-signed one is a new attempt, so
  // without this the buyer keeps reading "Failed to load the photo" over a photo that now
  // loads - the exact case this flag exists to cover (an expiry) is the case that refreshes
  // the URL.
  useEffect(() => setBroken(false), [data?.url]);

  // An answer we HOLD beats an error about fetching it again: react-query keeps the last
  // good response through a failed background refetch, so checking `isError` first turned a
  // true statement about the product ("nobody has given it a photo") into a false one ("we
  // could not load its photo") on a blip the buyer never saw. Same order the run-wide map
  // uses in `usePlanLines`.
  if (data) {
    if (data.url && !broken) {
      return (
        <div className="space-y-2">
          {/* M6-07: a fixed aspect-[4/3] box, the same one the loading
              Skeleton below reserves, so the popover does not resize the
              instant the photo finishes loading. */}
          <div className="aspect-[4/3] w-full overflow-hidden rounded bg-muted/20">
            {/* A plain <img>: the src is a signed, expiring S3/R2 URL, so next/image would
                need every storage host whitelisted and would re-proxy a link that is
                already thumbnail-sized and short-lived. */}
            <img
              src={data.url}
              alt={productName || sku}
              onError={() => setBroken(true)}
              className="size-full object-contain"
            />
          </div>
          {/* Most products have never been nominated, so this is the picture the catalogue
              falls back to. Saying where the choice is made keeps the loop open. */}
          {data.is_primary ? null : (
            <Link
              href="/dealer-kit/brochure-images"
              className="block text-2xs text-primary hover:underline"
            >
              Choose the primary photo in Dealer Kit -&gt; Brochure images
            </Link>
          )}
        </div>
      );
    }
    if (data.url) {
      return <p className="text-2xs text-muted-foreground">Failed to load the photo.</p>;
    }
    // Reached with a LIT icon too, and that is deliberate. The run-wide map says a live
    // image row exists; this call says the row is servable, and the backend signs strictly,
    // so a photo it cannot sign comes back as no photo. Signability is a property of the
    // storage backend rather than a column, so the map cannot know it without buying a
    // signature per row. This state therefore reads identically whichever way the buyer got
    // here, and a lit icon opening onto it is not an error. Do NOT "fix" one side to match
    // the other.
    return (
      <div className="space-y-1">
        <p className="text-xs">No primary photo yet</p>
        <Link href="/dealer-kit/brochure-images" className="text-2xs text-primary hover:underline">
          Choose one in Dealer Kit -&gt; Brochure images
        </Link>
      </div>
    );
  }
  if (isError) {
    return <p className="text-2xs text-muted-foreground">Failed to load the photo.</p>;
  }
  return (
    <Skeleton className="aspect-[4/3] w-full" data-testid="product-photo-skeleton" />
  );
}

export function ProductPhotoPopover({
  runId = null,
  productId = null,
  sku,
  productName,
  hasPhoto = false,
  status = 'idle',
  onOpen,
  label = 'Product photo',
}: {
  runId?: string | null;
  productId?: string | null;
  sku: string;
  productName?: string | null;
  /** Whether the run-wide map says this product has a photo at all. */
  hasPhoto?: boolean;
  /** Where the run's map is: not asked for yet, in flight, loaded, or failed. */
  status?: ProductPhotoStatus;
  /** Fired on the first open, so the caller can start the run-wide map fetch. */
  onOpen?: () => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  // Dimmed only once we KNOW: before the map lands, "no photo" is not yet a fact about this
  // product, and a dimmed icon would tell the buyer something we have not checked.
  const missing = status === 'ready' && !hasPhoto;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next) onOpen?.();
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={label}
          title={label}
          data-testid="product-photo-trigger"
          className={`inline-flex size-5 items-center justify-center rounded-sm transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
            missing ? 'text-muted-foreground/50' : 'text-muted-foreground/70'
          }`}
          onClick={(e) => e.stopPropagation()}
        >
          <ImageIcon className="size-3.5" aria-hidden />
        </button>
      </PopoverTrigger>
      <PopoverPortal>
        {/* 92vw so the panel still fits a phone, where 336px would run off the screen. */}
        <PopoverContent align="start" collisionPadding={8} className="w-[336px] max-w-[92vw] p-0">
          <div className="border-b px-3 py-2">
            <div className="truncate text-xs font-semibold" title={sku}>
              {sku}
            </div>
            {distinctName(sku, productName) ? (
              <div className="truncate text-2xs text-muted-foreground" title={productName ?? undefined}>
                {productName}
              </div>
            ) : null}
          </div>
          <div className="p-3">
            <ProductPhotoBody
              runId={runId}
              productId={productId}
              sku={sku}
              productName={productName}
            />
          </div>
        </PopoverContent>
      </PopoverPortal>
    </Popover>
  );
}

export default ProductPhotoPopover;
