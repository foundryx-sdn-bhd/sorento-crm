'use client';

/**
 * Choosing which photo of a product goes in a brochure (S7.0).
 *
 * **Why this screen exists.** `product_attachments.is_primary` has always been
 * the flag that decides a catalogue tile's photo - `product_images.py` already
 * orders by it - and it is false on every one of the 1,087 photo rows behind the
 * flyer's products. So a tile shows whichever row happened to be linked first.
 * `SRTWC286-SH` has 31 linked images, among them a blank page and two other
 * products' photographs. There is nothing to fix in the renderer; somebody has
 * to say which picture is the product.
 *
 * **Nothing is GUESSED on the user's behalf.** A filename matching the product
 * code would identify the right image for 509 of 535 products, and that is
 * deliberately not used: a wrong photo is a wrong product in front of a
 * customer, and the same wrong photo fed to a mesh generator is that plus a
 * bill.
 *
 * A product with exactly ONE candidate is a different case, and it used to be
 * treated the same. There is no choice to get wrong: the renderer already falls
 * back to the first linked photo, so recording it changes nothing a reader sees.
 * 509 of those products meant five hundred clicks that could each only go one
 * way. They are now taken automatically and shown as taken.
 *
 * **The standard DataGrid, like every other list in the system.** The
 * candidates used to be laid out under every product on the page, and then in a
 * hand-rolled list with its own Previous/Next buttons at the bottom. Both were
 * this screen inventing a list the system already has: same header, same column
 * controls, same pager as the users list. The pictures open per row, and the
 * dialog walks the same rows in the same order.
 */

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
} from '@tanstack/react-table';

import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardTable } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { TriangleAlert, Check, ImageOff, Images } from 'lucide-react';

import {
  BROCHURE_IMAGE_PAGE_SIZE,
  PROMOTION_PAGE_SIZE,
  type BrochureImageRow,
} from '../../services/brochureImageService';
import {
  useAdoptSingleBrochureImages,
  useBrochureImagePromotionOptions,
  useBrochureImagesQuery,
  useSetBrochureImage,
} from '../hooks/useBrochureImages';
import { BrochureImageDialog } from './BrochureImageDialog';
import { distinctName } from '@/lib/product-display';

export function BrochureImagePicker() {
  const [promotionId, setPromotionId] = useState('');
  const [onlyUnset, setOnlyUnset] = useState(true);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
    isSettling: searchSettling,
  } = useDebouncedSearch();
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: BROCHURE_IMAGE_PAGE_SIZE,
  });

  const promotionOptions = useBrochureImagePromotionOptions();

  // Any change of filter is a different worklist, so page 1 is where it starts.
  useEffect(() => {
    setPagination((current) => ({ ...current, pageIndex: 0 }));
  }, [promotionId, onlyUnset, search]);

  const query = useBrochureImagesQuery({
    promotionId,
    onlyUnset,
    query: search,
    page: pagination.pageIndex + 1,
    limit: pagination.pageSize,
  });

  const setImage = useSetBrochureImage();
  const adoptSingle = useAdoptSingleBrochureImages();
  /** Which row of THIS page the dialog is showing, or null when it is closed. */
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  /**
   * What the user just clicked, held until the refetch confirms it.
   *
   * Without this the tile shows nothing chosen for as long as the round trip
   * takes, which reads as "the click did not register" and invites a second
   * click on a different candidate. It is dropped again if the save fails, so
   * the mark can never outlive the answer it claims to record.
   */
  const [pendingChoice, setPendingChoice] = useState<Record<string, string>>({});

  const rows = useMemo(
    () =>
      (query.data?.items ?? []).map((row) => ({
        ...row,
        chosenAttachmentId: pendingChoice[row.productId] ?? row.chosenAttachmentId,
      })),
    [query.data, pendingChoice],
  );

  /*
    Products on this page whose photo is not in doubt, answered without asking.

    Keyed on the ids so a refetch of the same page does not re-fire, and the
    server call is idempotent besides.
  */
  const singleCandidateIds = useMemo(
    () =>
      (query.data?.items ?? [])
        .filter((row) => row.candidates.length === 1 && !row.chosenAttachmentId)
        .map((row) => row.productId),
    [query.data],
  );
  const adoptKey = singleCandidateIds.join(',');
  const adoptMutate = adoptSingle.mutate;
  useEffect(() => {
    if (!adoptKey) return;
    adoptMutate(adoptKey.split(','));
  }, [adoptKey, adoptMutate]);

  const total = query.data?.total ?? 0;
  const remaining = query.data?.remaining ?? 0;
  // Nothing in this filter has a photo to pick from. Distinct from "you have
  // finished": the work here is a photo shoot, not a click, and the screen used
  // to say "11390 of 11390 still to choose" over a page of products with nothing
  // under any of them - true, and indistinguishable from a failed load.
  const nothingToChooseFrom = query.isSuccess && total > 0 && (query.data?.choosable ?? 0) === 0;
  const withoutCandidates = rows.filter((row) => row.candidates.length === 0).length;
  // `shown`, not `total`: `total` counts everything matching the filter, while
  // the "only products still without an image" switch means only `shown` of them
  // can appear. Paging over `total` offers pages that hold nothing - a
  // 998-product promotion with 900 answered would advertise 40 pages for 4.
  const listable = query.data?.shown ?? 0;

  const choose = (productId: string, attachmentId: string, isChosen: boolean) => {
    // Idempotent, not a toggle: clicking the chosen one again leaves it chosen
    // rather than putting the product back to having no image at all.
    if (isChosen) return;
    setPendingChoice((current) => ({ ...current, [productId]: attachmentId }));
    setImage.mutate(
      { productId, attachmentId },
      {
        // A failed save leaves the product unanswered, and an error toast is
        // read once and gone while the mark stays on screen: keeping it would
        // tell the user this product is done for the rest of their session.
        // Dropping the pending entry falls the row back to the cached page,
        // which is the last thing the server actually reported.
        onError: () =>
          setPendingChoice((current) => {
            // Keyed on the attempt, not just the product. If the user has since
            // clicked another candidate, that later choice is what is on screen
            // and this failure is not entitled to clear it.
            if (current[productId] !== attachmentId) return current;
            const next = { ...current };
            delete next[productId];
            return next;
          }),
      },
    );
  };

  const columns = useMemo<ColumnDef<BrochureImageRow>[]>(
    () => [
      {
        id: 'image',
        header: '',
        size: 64,
        enableSorting: false,
        enableResizing: false,
        meta: { headerTitle: 'Image' },
        cell: ({ row }) => {
          const chosen = row.original.candidates.find(
            (candidate) => candidate.attachmentId === row.original.chosenAttachmentId,
          );
          return (
            // Small on purpose: this is a list somebody scans by CODE, and the
            // thumbnail is confirmation rather than the thing being read.
            <div className="relative size-10 overflow-hidden rounded-md bg-muted">
              {chosen?.url ? (
                // A plain img, not next/image: the src is a signed URL on a
                // storage host that changes per request, which the optimiser
                // cannot cache anyway.
                <img src={chosen.url} alt={chosen.filename} className="size-full object-cover" />
              ) : (
                <div className="flex size-full items-center justify-center text-muted-foreground">
                  <ImageOff className="size-4" />
                </div>
              )}
            </div>
          );
        },
      },
      {
        accessorKey: 'productCode',
        header: ({ column }) => <DataGridColumnHeader title="Product" column={column} />,
        size: 240,
        minSize: 140,
        enableSorting: false,
        meta: { headerTitle: 'Product' },
        cell: ({ row }) => (
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate font-mono text-sm" title={row.original.productCode}>
              {row.original.productCode}
            </span>
            {row.original.chosenAttachmentId && (
              <Badge variant="success" appearance="light" size="sm">
                {/* Said out loud. A product that answered itself must not look
                    like one somebody reviewed. */}
                {row.original.candidates.length === 1 ? 'only photo' : 'chosen'}
              </Badge>
            )}
          </div>
        ),
      },
      {
        accessorKey: 'productName',
        header: ({ column }) => <DataGridColumnHeader title="Name" column={column} />,
        size: 340,
        minSize: 160,
        enableSorting: false,
        meta: { headerTitle: 'Name' },
        cell: ({ row }) => (
          <span
            className="block truncate text-sm text-muted-foreground"
            title={distinctName(row.original.productCode, row.original.productName) ?? undefined}
          >
            {distinctName(row.original.productCode, row.original.productName)}
          </span>
        ),
      },
      {
        id: 'candidates',
        header: ({ column }) => <DataGridColumnHeader title="Photos" column={column} />,
        size: 110,
        minSize: 90,
        enableSorting: false,
        meta: { headerTitle: 'Photos' },
        cell: ({ row }) => (
          <span className="text-sm text-muted-foreground">
            {row.original.candidates.length === 0 ? 'no photo' : row.original.candidates.length}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '',
        size: 130,
        enableSorting: false,
        enableResizing: false,
        meta: { headerTitle: 'Choose' },
        cell: ({ row }) => (
          <Button
            variant="outline"
            size="sm"
            disabled={row.original.candidates.length === 0}
            onClick={() => setOpenIndex(row.index)}
            data-dk-bi-open={row.original.productCode}
            aria-label={`Choose a photo for ${row.original.productCode}`}
          >
            <Images className="size-4" />
            {row.original.chosenAttachmentId ? 'Change' : 'Choose'}
          </Button>
        ),
      },
    ],
    [],
  );

  const table = useReactTable({
    columns,
    data: rows,
    pageCount: Math.max(1, Math.ceil(listable / pagination.pageSize)),
    getRowId: (row) => row.productId,
    state: { pagination },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    // The server pages and filters; the grid renders only what it is handed.
    manualPagination: true,
    manualFiltering: true,
    columnResizeMode: 'onChange',
    enableColumnResizing: true,
  });

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardContent className="flex flex-col gap-4 py-5 lg:flex-row lg:items-end lg:justify-between">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-end">
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor="dk-bi-promotion" className="text-xs text-muted-foreground">
                Promotion
              </Label>
              <SearchableSelect
                id="dk-bi-promotion"
                value={promotionId}
                onChange={setPromotionId}
                // Server-searched and paged: there are hundreds of promotions,
                // so a static option list would cap the filter at one page and
                // hide the rest without saying so.
                fetchOptions={promotionOptions.fetchOptions}
                paginated
                pageSize={PROMOTION_PAGE_SIZE}
                selectedOption={promotionOptions.optionFor(promotionId)}
                placeholder="Every product"
                clearable
                className="w-full sm:w-72"
              />
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor="dk-bi-search" className="text-xs text-muted-foreground">
                Product
              </Label>
              <ListSearchInput
                id="dk-bi-search"
                value={searchInput}
                onChange={setSearchInput}
                isSettling={isSearchInFlight(searchSettling, query.isFetching, search)}
                placeholder="Code or name"
                aria-label="Product code or name"
                className="w-full sm:w-56"
              />
            </div>
            <div className="flex items-center gap-2 pb-1.5">
              <Switch id="dk-bi-only-unset" checked={onlyUnset} onCheckedChange={setOnlyUnset} />
              <Label htmlFor="dk-bi-only-unset" className="text-sm">
                Only products still without an image
              </Label>
            </div>
          </div>

          <div className="text-sm text-muted-foreground" data-dk-bi-remaining>
            {/* "11390 of 11390 still to choose" is a promise of work that cannot
                be done: there is nothing to choose BETWEEN. Says what is true
                instead, and the empty state below says what to do about it. */}
            {nothingToChooseFrom
              ? `${total} products, none with a photo yet`
              : `${remaining} of ${total} still to choose`}
          </div>
        </CardContent>
      </Card>

      {/* Suppressed when nothing anywhere has a photo: "25 products on this page
          have no photo" is a page-scoped restatement of what the empty state
          below already says about all 11,390, and reads as a small local
          problem rather than the state of the catalogue. */}
      {withoutCandidates > 0 && !nothingToChooseFrom && (
        <Alert variant="warning" appearance="light">
          <AlertIcon>
            <TriangleAlert />
          </AlertIcon>
          {/* Scoped to the page in the wording, because that is all it counts.
              Unqualified, page 1 of the flyer reads "25 products have no photo"
              when the figure across the filter is 465. */}
          <AlertTitle>
            {withoutCandidates === 1
              ? '1 product on this page has'
              : `${withoutCandidates} products on this page have`}{' '}
            no photo to choose from. Attach a photo first.
          </AlertTitle>
        </Alert>
      )}

      {query.isError && (
        <Alert variant="destructive" appearance="light" data-dk-bi-error>
          <AlertIcon>
            <TriangleAlert />
          </AlertIcon>
          <AlertTitle>
            {(query.error as Error)?.message || 'The product list could not be loaded.'}
            <Button variant="inverse" size="sm" className="ms-3" onClick={() => query.refetch()}>
              Try again
            </Button>
          </AlertTitle>
        </Alert>
      )}

      {nothingToChooseFrom && (
        <Card data-dk-bi-no-photos>
          <CardContent className="flex flex-col items-center gap-2 py-14 text-center">
            <ImageOff className="size-8 text-muted-foreground" />
            <div className="text-base font-medium">No product here has a photo yet</div>
            {/* One line and the CTA. The longer version explained what this
                screen is FOR, which is a feature explanation in the UI. */}
            <p className="max-w-md text-sm text-muted-foreground">
              Attach photos to the products first, then come back and choose.
            </p>
            <Button variant="outline" size="sm" className="mt-2" asChild>
              <Link href="/resource-management/attachment-directories">Go to Files</Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {!query.isError && !nothingToChooseFrom && (
        <DataGrid
          table={table}
          recordCount={listable}
          isLoading={query.isPending}
          isPlaceholderData={query.isPlaceholderData}
          tableLayout={{ width: 'fixed', columnsResizable: true }}
          // Prefixed with the permission that guards the screen, so saved column
          // widths survive the route moving.
          listingKey="master_data.product_attachments.view::brochure-images"
          emptyMessage={
            <div className="py-8 text-center" data-dk-bi-empty>
              <Check className="mx-auto size-8 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium text-foreground">
                Every product here has a brochure image
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                Turn off the filter above to review or change one.
              </p>
            </div>
          }
        >
          <Card>
            <CardTable>
              <DataGridTable />
            </CardTable>
            <CardFooter>
              <DataGridPagination />
            </CardFooter>
          </Card>
        </DataGrid>
      )}

      <BrochureImageDialog
        rows={rows}
        index={openIndex}
        onIndexChange={setOpenIndex}
        onChoose={(productId, attachmentId, isChosen) => {
          choose(productId, attachmentId, isChosen);
          // Straight on to the next product. Choosing a photo is a sitting, and
          // making somebody close and reopen between each one is most of the
          // work. The last row stays put rather than closing, so a final choice
          // can still be corrected.
          if (!isChosen) {
            setOpenIndex((current) =>
              current !== null && current < rows.length - 1 ? current + 1 : current,
            );
          }
        }}
      />
    </div>
  );
}
