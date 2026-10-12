'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import {
  ColumnDef,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  PaginationState,
  RowSelectionState,
  SortingState,
  useReactTable,
  VisibilityState,
} from '@tanstack/react-table';
import {
  Plus,
  Trash2,
  Upload,
  Download,
  SlidersHorizontal,
  MessageSquare,
  CloudDownload,
} from 'lucide-react';
import { formatDateInMalaysia, formatDateTimeInMalaysia } from '@/lib/helpers';
import { Badge, BadgeDot } from '@/components/ui/badge';
import { ProductTypeBadge } from './ProductTypeBadge';
import { Button } from '@/components/ui/button';
import { Card, CardFooter, CardHeader, CardTable } from '@/components/ui/card';
import { DataGrid } from '@/components/ui/data-grid';
import { DataGridColumnHeader } from '@/components/ui/data-grid-column-header';
import { DataGridListToolbar } from '@/components/ui/data-grid-list-toolbar';
import { buildSelectColumn, selectedRowIds } from '@/components/ui/data-grid-select-column';
import { DataGridPagination } from '@/components/ui/data-grid-pagination';
import { DataGridTable } from '@/components/ui/data-grid-table';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { Label } from '@/components/ui/label';
import { DateRangePicker, parseIsoDate } from '@/components/ui/date-range-picker';
import { useProductFilters } from '../hooks/useProductFilters';
import { useProductCategorySelectQuery } from '../../shared/hooks/use-product-category-select-query';
import { useBrandSelectQuery } from '../../shared/hooks/use-brand-select-query';
import { CHAT_SEARCH_LABEL, chatSearchState, type ProductListItem } from '../types/product.types';
import { bulkImportProducts, validateProductsImport } from '../services/productService';
import ProductBulkChatSearchDialog from './ProductBulkChatSearchDialog';
import { UploadCostPriceAction } from './UploadCostPriceAction';
import { TemplateUploadDialog } from '@/components/template/TemplateUploadDialog';
import { useImportJobDrawer } from '@/components/upload-activity';
import { ListQueryFilterDialog } from '@/components/list/ListQueryFilterDialog';
import type { ListQueryFilterGroup } from '@/lib/list-query/listQueryService';
import {
  buildDetailSearch,
  decodeAdvancedFilter,
  encodeAdvancedFilter,
} from '@/lib/listNavQuery';
import { ProductRowActions } from '../actions';
import { useDeferredBulkAction } from '@/hooks/useDeferredBulkAction';
import { pendingEntityKey, usePendingEntityKeys } from '@/lib/pending-entity-store';
import {
  fetchProductsPage,
  productsListQueryKey,
  type ProductsListParams,
} from '../lib/listQuery';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { generateExcelFile } from '@/lib/excel-utils';
import type { ColumnOption } from '@/lib/excel-utils';
import { useListStateFromUrl } from '@/hooks/useListStateFromUrl';
import { useResetPageOnFilterChange } from '@/hooks/useResetPageOnFilterChange';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import { useAutocountPullAction } from '@/app/(protected)/system-management/import-jobs/autocount-pull/hooks/useAutocountPull';

const PRODUCT_IMPORT_COLUMNS: ColumnOption[] = [
  { key: 'Item Code', label: 'Item Code', selected: true },
  { key: 'Description', label: 'Description', selected: true },
  { key: 'Desc 2', label: 'Desc 2', selected: true },
  { key: 'Item Group', label: 'Item Group', selected: true },
  { key: 'Item Brand', label: 'Item Brand', selected: true },
  { key: 'Price', label: 'Price', selected: true },
  { key: 'Is Active', label: 'Is Active', selected: true },
];

// Named here rather than left to `Intl`, whose `en-GB` short form spells
// September "Sept" (see `boardChangeAnnotations.ts`'s own `SHORT_MONTHS`) -
// the UAC's own wording is the 3-letter form ("Discontinued: 1 Sep 26 ...").
const SHORT_MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/** `2026-09-26` -> `26 Sep 26` (chip), the shared `DateRangePicker`'s own ISO
 *  parser reused rather than a second one (same shape as StockDebtClient's
 *  `formatDateChip`, minus its `Intl` month name). */
function formatDateChip(value: string): string {
  const date = parseIsoDate(value);
  if (!date) return value;
  const year = String(date.getFullYear()).slice(-2);
  return `${date.getDate()} ${SHORT_MONTHS[date.getMonth()]} ${year}`;
}

const ProductsList = () => {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();
  // Hard refresh = clean slate: don't restore persisted search/filters from the URL
  // (those params only exist to restore state when navigating BACK from a detail page).
  const isReloadRef = useRef<boolean | null>(null);
  if (isReloadRef.current === null) {
    const nav =
      typeof performance !== 'undefined'
        ? (performance.getEntriesByType('navigation')[0] as
            | PerformanceNavigationTiming
            | undefined)
        : undefined;
    isReloadRef.current = nav?.type === 'reload';
  }
  const queryClient = useQueryClient();
  const { notifyImportQueued } = useImportJobDrawer();
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 50,
  });
  const [sorting, setSorting] = useState<SortingState>([
    { id: 'created_at', desc: true },
  ]);
  const {
    value: searchInputValue,
    setValue: setSearchInputValue,
    debouncedValue: searchQuery,
    isSettling: searchSettling,
    reset: resetSearchQuery,
  } = useDebouncedSearch();
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedBrand, setSelectedBrand] = useState<string | null>(null);
  const [selectedStatus, setSelectedStatus] = useState<string | null>('all');
  const [selectedVariantFilter, setSelectedVariantFilter] = useState<
    'base' | 'variant' | 'all'
  >('all');
  // "Discontinued at" range (issue #1287), YYYY-MM-DD or null (`DateRangePicker`'s
  // own contract).
  const [discontinuedFrom, setDiscontinuedFrom] = useState<string | null>(null);
  const [discontinuedTo, setDiscontinuedTo] = useState<string | null>(null);

  const {
    setCategoryId,
    setBrandId,
    setStatus,
    setSearch,
    clearFilters,
    hasActiveFilters,
  } = useProductFilters();

  // A hard refresh is a clean slate: the persisted params only exist to restore
  // state when coming BACK from a detail page, so they are stripped rather than
  // read. A "products discontinued" deep link survives it - that is an explicit
  // navigation intent, and dropping its brand slice would widen the list to
  // products the recipient was never notified about.
  useEffect(() => {
    if (!isReloadRef.current) return;
    isReloadRef.current = false;
    if (!searchParams.toString()) return;
    const batch = searchParams.get('discontinued_batch_id');
    const batchBrands = searchParams.get('brand_id');
    if (batch) {
      const kept = new URLSearchParams({ discontinued_batch_id: batch });
      if (batchBrands) kept.set('brand_id', batchBrands);
      router.replace(`${pathname}?${kept.toString()}`);
    } else {
      router.replace(pathname);
    }
  }, [searchParams, router, pathname]);


  const [bulkChatSearchDialogOpen, setBulkChatSearchDialogOpen] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [advancedFilter, setAdvancedFilter] = useState<ListQueryFilterGroup | null>(null);
  const [advancedFilterDialogOpen, setAdvancedFilterDialogOpen] = useState(false);

  // AutoCount pull - one more secondary action beside Import (PLAN-autocount-pull-review.md).
  const autocountPull = useAutocountPullAction('products', 'master_data.products.autocount_pull');

  // Back hands the list its own query string back, and the pager keeps rewriting
  // it, so the list reads it (S3-01). One hook, every list.
  useListStateFromUrl(
    (state) => {
      setPagination({ pageIndex: state.pageIndex, pageSize: state.pageSize });
      setSorting(state.sorting);
      resetSearchQuery(state.searchQuery);
      setSearch(state.searchQuery || undefined);
      const category = state.filters.category_id ?? null;
      setSelectedCategory(category);
      setCategoryId(category ?? undefined);
      // A multi-brand deep link (brand_id=a,b) has no single-select equivalent, so
      // the dropdown stays on "All brands" while the param still filters the grid.
      const brand = state.filters.brand_id ?? null;
      if (!brand || !brand.includes(',')) {
        setSelectedBrand(brand);
        setBrandId(brand ?? undefined);
      }
      const status = state.filters.status ?? 'all';
      setSelectedStatus(status);
      setStatus(status === 'active' ? true : status === 'inactive' ? false : undefined);
      const variant = state.filters.variant_filter;
      setSelectedVariantFilter(variant === 'base' || variant === 'variant' ? variant : 'all');
      setDiscontinuedFrom(state.filters.discontinued_from ?? null);
      setDiscontinuedTo(state.filters.discontinued_to ?? null);
      setAdvancedFilter(
        decodeAdvancedFilter<ListQueryFilterGroup>(state.filters.advFilter),
      );
    },
    { enabled: !isReloadRef.current },
  );

  const {
    data: categories,
    error: categoriesError,
    refetch: refetchCategories,
  } = useProductCategorySelectQuery();
  const {
    data: brands,
    error: brandsError,
    refetch: refetchBrands,
  } = useBrandSelectQuery();

  // Deep link from a "products discontinued" notification - restrict the list to
  // exactly the products reported in that batch.
  const discontinuedBatchId = searchParams.get('discontinued_batch_id') || undefined;
  // ...and, for a recipient scoped to specific brands, to their slice of it. The
  // param is passed through verbatim (comma-separated ids filter with IN).
  const discontinuedBrandIds = discontinuedBatchId
    ? searchParams.get('brand_id') || undefined
    : undefined;
  const effectiveBrandId =
    selectedBrand && selectedBrand !== 'all' ? selectedBrand : discontinuedBrandIds;

  // The list query, built through the shared key + fetch so the detail page's
  // pager reads THIS cache entry instead of asking the server again.
  const listParams = useMemo<ProductsListParams>(
    () => ({
      pageIndex: pagination.pageIndex,
      pageSize: pagination.pageSize,
      sorting,
      searchQuery,
      category_id:
        selectedCategory && selectedCategory !== 'all' ? selectedCategory : undefined,
      brand_id: effectiveBrandId,
      status: selectedStatus ?? 'all',
      variant_filter: selectedVariantFilter,
      discontinued_batch_id: discontinuedBatchId,
      discontinued_brand_ids: discontinuedBrandIds,
      discontinued_from: discontinuedFrom ?? undefined,
      discontinued_to: discontinuedTo ?? undefined,
      advancedFilter: advancedFilter ?? undefined,
    }),
    [
      pagination,
      sorting,
      searchQuery,
      selectedCategory,
      effectiveBrandId,
      selectedStatus,
      selectedVariantFilter,
      discontinuedBatchId,
      discontinuedBrandIds,
      discontinuedFrom,
      discontinuedTo,
      advancedFilter,
    ],
  );

  const { data, isLoading, isPlaceholderData, isFetching, isError, error } = useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: productsListQueryKey(listParams),
    queryFn: () => fetchProductsPage(listParams),
    gcTime: 1000 * 60 * 60, // 60 minutes
    refetchOnReconnect: false,
    retry: 1,
  });

  const handleCategorySelection = (categoryId: string) => {
    setSelectedCategory(categoryId);
    setCategoryId(categoryId !== 'all' ? categoryId : undefined);
    setPagination({ ...pagination, pageIndex: 0 });
  };

  const handleBrandSelection = (brandId: string) => {
    setSelectedBrand(brandId);
    setBrandId(brandId !== 'all' ? brandId : undefined);
    setPagination({ ...pagination, pageIndex: 0 });
  };

  const handleStatusSelection = (status: string) => {
    setSelectedStatus(status);
    setStatus(status === 'active' ? true : status === 'inactive' ? false : undefined);
    setPagination({ ...pagination, pageIndex: 0 });
  };

  const handleVariantFilterSelection = (value: string) => {
    setSelectedVariantFilter(value as 'base' | 'variant' | 'all');
    setPagination({ ...pagination, pageIndex: 0 });
  };

  const handleDiscontinuedRangeChange = (next: { from: string | null; to: string | null }) => {
    setDiscontinuedFrom(next.from);
    setDiscontinuedTo(next.to);
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  };

  const handleClearDiscontinuedRange = () => {
    setDiscontinuedFrom(null);
    setDiscontinuedTo(null);
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  };

  const handleClearFilters = () => {
    clearFilters();
    setSelectedCategory(null);
    setSelectedBrand(null);
    setSelectedStatus('all');
    setSelectedVariantFilter('all');
    setDiscontinuedFrom(null);
    setDiscontinuedTo(null);
    setAdvancedFilter(null);
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  };

  // Page one when a filter CHANGES, never on mount - the mount run used to stamp
  // page 1 over the page `useListStateFromUrl` had just restored from the URL.
  useResetPageOnFilterChange(setPagination, [advancedFilter, searchQuery]);

  // Carry the active list query (search/sort + category/brand/status/discontinued
  // batch filters) into the detail URL so the detail page's prev/next pager walks
  // the same filtered+sorted set. Uses the SAME param names as the list GET
  // (via buildDataGridParams) so the neighbours hook forwards them verbatim.
  const buildProductDetailUrl = (productId: string) => {
    const qs = buildDetailSearch(
      {
        pageIndex: pagination.pageIndex,
        pageSize: pagination.pageSize,
        sorting,
        searchQuery,
      },
      {
        category_id: listParams.category_id,
        brand_id: listParams.brand_id,
        status:
          listParams.status && listParams.status !== 'all' ? listParams.status : undefined,
        variant_filter:
          selectedVariantFilter !== 'all' ? selectedVariantFilter : undefined,
        discontinued_batch_id: discontinuedBatchId,
        discontinued_from: discontinuedFrom ?? undefined,
        discontinued_to: discontinuedTo ?? undefined,
        advFilter: encodeAdvancedFilter(advancedFilter),
      },
    );
    return `/master-data-management/products/${productId}${qs ? `?${qs}` : ''}`;
  };

  // The whole row opens the record; the filters the grid does not know about
  // ride in this query string, and the pager rebuilds the list's key from both.
  const rowHref = (row: ProductListItem) => buildProductDetailUrl(row.id);

  // A product whose deletion is counting down stays on the list, dimmed, until the
  // window lapses - the toast holds the Cancel, and this says which row it is for.
  const pendingKeys = usePendingEntityKeys();
  const rowPending = (row: ProductListItem) =>
    pendingKeys.has(pendingEntityKey('product', row.id));

  // Delete selected asks nothing either (D7): one action per selected row, ONE
  // countdown over them, one Cancel that withdraws the lot. Every selected row dims
  // through the same `rowPending` a single delete uses.
  const bulkDeletion = useDeferredBulkAction({
    actionKey: 'product.delete',
    entityType: 'product',
    describe: (count) => `${count} product${count === 1 ? '' : 's'}`,
    invalidateKeys: [['products']],
    onStarted: () => setRowSelection({}),
  });

  const columns = useMemo<ColumnDef<ProductListItem>[]>(
    () => [
      buildSelectColumn<ProductListItem>(),
      {
        accessorKey: 'product_code',
        id: 'product_code',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Product Code"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          return (
            <div className="font-medium text-sm">{row.original.product_code}</div>
          );
        },
        size: 150,
        meta: {
          headerTitle: 'Product Code',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
        enableSorting: true,
        enableHiding: false,
      },
      {
        accessorKey: 'product_name',
        id: 'product_name',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Product Name"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          return (
            <div className="font-medium text-sm">
              {row.original.product_name}
            </div>
          );
        },
        size: 300,
        meta: {
          headerTitle: 'Product Name',
          skeleton: <Skeleton className="h-4 w-40" />,
        },
        enableSorting: true,
        enableHiding: false,
      },
      {
        accessorKey: 'is_variant',
        id: 'variant_type',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Type"
            visibility={true}
            column={column}
          />
        ),
        size: 100,
        cell: ({ row }) => (
          <ProductTypeBadge isVariant={row.original.is_variant} />
        ),
        meta: {
          headerTitle: 'Type',
          skeleton: <Skeleton className="w-14 h-7" />,
        },
        enableSorting: false,
        enableHiding: true,
      },
      {
        accessorKey: 'variant_of',
        id: 'variant_of',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Variant of"
            visibility={true}
            column={column}
          />
        ),
        size: 160,
        cell: ({ row }) => {
          const parent = row.original.variant_of;
          if (!parent) {
            return <span className="text-muted-foreground"> - </span>;
          }
          return (
            <div
              className="truncate text-sm"
              title={`${parent.product_code} - ${parent.product_name}`}
            >
              {parent.product_code}
            </div>
          );
        },
        meta: {
          headerTitle: 'Variant of',
          skeleton: <Skeleton className="h-4 w-24" />,
        },
        enableSorting: false,
        enableHiding: true,
      },
      {
        accessorKey: 'variant_child_count',
        id: 'variant_child_count',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Variants"
            visibility={true}
            column={column}
          />
        ),
        size: 90,
        cell: ({ row }) => (
          <div className="text-sm">{row.original.variant_child_count ?? 0}</div>
        ),
        meta: {
          headerTitle: 'Variants',
          skeleton: <Skeleton className="h-4 w-8" />,
        },
        enableSorting: false,
        enableHiding: true,
      },
      {
        accessorKey: 'category_name',
        id: 'category_name',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Category"
            visibility={true}
            column={column}
          />
        ),
        size: 150,
        cell: ({ row }) => {
          const categoryName = row.original.category_name || row.original.category?.category_name;
          return categoryName ? (
            <Badge variant="secondary">{categoryName}</Badge>
          ) : (
            '-'
          );
        },
        meta: {
          headerTitle: 'Category',
          skeleton: <Skeleton className="w-28 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'brand_name',
        id: 'brand_name',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Brand"
            visibility={true}
            column={column}
          />
        ),
        size: 150,
        cell: ({ row }) => {
          const brandName = row.original.brand_name || row.original.brand?.brand_name;
          return brandName ? (
            <Badge variant="outline">{brandName}</Badge>
          ) : (
            '-'
          );
        },
        meta: {
          headerTitle: 'Brand',
          skeleton: <Skeleton className="w-28 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'list_price',
        id: 'list_price',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="List Price"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const price = row.original.list_price;
          return (
            <div className="text-sm">
              {new Intl.NumberFormat('en-US', {
                style: 'currency',
                currency: 'MYR',
              }).format(price)}
            </div>
          );
        },
        size: 120,
        meta: {
          headerTitle: 'List Price',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'is_active',
        id: 'is_active',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Status"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const isActive = row.original.is_active;
          return (
            <Badge
              variant={isActive ? 'success' : 'secondary'}
              appearance="light"
            >
              <BadgeDot />
              {isActive ? 'Active' : 'Inactive'}
            </Badge>
          );
        },
        size: 100,
        meta: {
          headerTitle: 'Status',
          skeleton: <Skeleton className="w-14 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'is_discontinued',
        id: 'is_discontinued',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Discontinued"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const isDiscontinued = row.original.is_discontinued;
          return (
            <Badge
              variant={isDiscontinued ? 'destructive' : 'secondary'}
              appearance="light"
            >
              <BadgeDot />
              {isDiscontinued ? 'Discontinued' : 'Available'}
            </Badge>
          );
        },
        size: 130,
        meta: {
          headerTitle: 'Discontinued',
          skeleton: <Skeleton className="w-20 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'discontinued_at',
        id: 'discontinued_at',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Discontinued at"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const value = row.original.discontinued_at;
          return <div className="text-sm">{value ? formatDateInMalaysia(value) : ''}</div>;
        },
        size: 140,
        meta: {
          headerTitle: 'Discontinued at',
          skeleton: <Skeleton className="h-4 w-20" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'is_searchable',
        id: 'is_searchable',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Chat Search"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => {
          const state = chatSearchState(row.original);
          return (
            <Badge
              variant={state === 'shown' ? 'success' : 'destructive'}
              appearance="light"
            >
              <BadgeDot />
              {CHAT_SEARCH_LABEL[state]}
            </Badge>
          );
        },
        size: 140,
        meta: {
          headerTitle: 'Chat Search',
          skeleton: <Skeleton className="w-16 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        // S5 (reorder-feedback-9sep.md, 9 Sep 2026): optional, hidden by default -
        // reachable via the Columns control for a buyer auditing which products
        // never enter a reorder run.
        accessorKey: 'exclude_from_planning',
        id: 'exclude_from_planning',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Reorder Planning"
            visibility={true}
            column={column}
          />
        ),
        cell: ({ row }) => (
          <Badge
            variant={row.original.exclude_from_planning ? 'destructive' : 'secondary'}
            appearance="light"
          >
            <BadgeDot />
            {row.original.exclude_from_planning ? 'Excluded' : 'Included'}
          </Badge>
        ),
        size: 140,
        meta: {
          headerTitle: 'Reorder Planning',
          skeleton: <Skeleton className="w-16 h-7" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'created_at',
        id: 'created_at',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Created"
            visibility={true}
            column={column}
          />
        ),
        cell: (info) => {
          const v = info.getValue() as string | null | undefined;
          return v ? formatDateTimeInMalaysia(v) : '-';
        },
        size: 180,
        meta: {
          headerTitle: 'Created',
          skeleton: <Skeleton className="h-4 w-32" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'updated_at',
        id: 'updated_at',
        header: ({ column }) => (
          <DataGridColumnHeader
            title="Updated"
            visibility={true}
            column={column}
          />
        ),
        cell: (info) => {
          const val = info.getValue() as string | null | undefined;
          return val ? formatDateTimeInMalaysia(val) : '-';
        },
        size: 180,
        meta: {
          headerTitle: 'Updated',
          skeleton: <Skeleton className="h-4 w-32" />,
        },
        enableSorting: true,
        enableHiding: true,
      },
      {
        accessorKey: 'actions',
        header: '',
        cell: ({ row }) => <ProductRowActions product={row.original} />,
        meta: {
          skeleton: <Skeleton className="size-4" />,
        },
        size: 60,
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
      },
    ],
    [],
  );

  const [columnOrder, setColumnOrder] = useState<string[]>(
    columns.map((column) => column.id as string),
  );
  // S5: "Reorder Planning" starts hidden - reachable via the Columns control, same
  // shape the SPO document Lines tab already uses for its own optional columns.
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    exclude_from_planning: false,
  });

  const table = useReactTable({
    columns,
    data: data?.data || [],
    pageCount: Math.ceil((data?.pagination.total || 0) / pagination.pageSize),
    getRowId: (row: ProductListItem) => row.id,
    state: {
      pagination,
      sorting,
      columnOrder,
      columnVisibility,
      rowSelection,
    },
    columnResizeMode: 'onChange',
    onColumnOrderChange: setColumnOrder,
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: setPagination,
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableRowSelection: true,
  });

  // The search box drives the server query itself (via useDebouncedSearch), so
  // this only has to keep the OTHER consumer of the term in step: the chat-search
  // filter panel. The page reset rides with every other filter, above.
  useEffect(() => {
    setSearch(searchQuery || undefined);
  }, [searchQuery]);

  const simpleFiltersActiveCount =
    (selectedCategory && selectedCategory !== 'all' ? 1 : 0) +
    (selectedBrand && selectedBrand !== 'all' ? 1 : 0) +
    (selectedStatus && selectedStatus !== 'all' ? 1 : 0) +
    (selectedVariantFilter && selectedVariantFilter !== 'all' ? 1 : 0) +
    (discontinuedFrom || discontinuedTo ? 1 : 0);

  // "Discontinued: 1 Sep 26 - 26 Sep 26" both ends, "from X" / "to Y" one end.
  const discontinuedChipLabel =
    discontinuedFrom && discontinuedTo
      ? `Discontinued: ${formatDateChip(discontinuedFrom)} - ${formatDateChip(discontinuedTo)}`
      : discontinuedFrom
        ? `Discontinued: from ${formatDateChip(discontinuedFrom)}`
        : discontinuedTo
          ? `Discontinued: to ${formatDateChip(discontinuedTo)}`
          : null;

  const getExportPayload = () => ({
    filter: advancedFilter ?? undefined,
    quick_search: searchQuery || undefined,
    category_id: selectedCategory && selectedCategory !== 'all' ? selectedCategory : undefined,
    brand_id: selectedBrand && selectedBrand !== 'all' ? selectedBrand : undefined,
    product_status: selectedStatus && selectedStatus !== 'all' ? selectedStatus : undefined,
    discontinued_from: discontinuedFrom ?? undefined,
    discontinued_to: discontinuedTo ?? undefined,
  });

  const openCreateProduct = () => router.push('/master-data-management/products/new');

  return (
    <DataGrid
      table={table}
      recordCount={data?.pagination.total || 0}
      isLoading={isLoading}
      isPlaceholderData={isPlaceholderData}
      rowHref={rowHref}
      rowPending={rowPending}
      tableLayout={{
        columnsResizable: true,
        columnsPinnable: true,
        columnsMovable: true,
        columnsVisibility: true,
      }}
      tableClassNames={{
        edgeCell: 'px-5',
      }}
    >
      <Card>
        <CardHeader className="block">
          {discontinuedBatchId && (
            <div className="mb-3 flex items-center justify-between gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-200">
              <span>Showing only the products from a recent “products discontinued” notification.</span>
              <Button variant="ghost" size="sm" onClick={() => router.replace(pathname)}>
                Clear filter
              </Button>
            </div>
          )}
          <DataGridListToolbar
            table={table}
            searchSlot={
              <ListSearchInput
                value={searchInputValue}
                onChange={setSearchInputValue}
                isSettling={isSearchInFlight(searchSettling, isFetching, searchQuery)}
                placeholder="Search products..."
                className="w-64"
              />
            }
            filters={{
              kind: 'custom',
              active: simpleFiltersActiveCount > 0,
              activeCount: simpleFiltersActiveCount,
              activeSummary: discontinuedChipLabel
                ? { label: discontinuedChipLabel, onClear: handleClearDiscontinuedRange }
                : undefined,
              content: (
                <div className="space-y-3">
                  <SearchableSelect
                    value={selectedCategory || 'all'}
                    onChange={handleCategorySelection}
                    placeholder="Category"
                    loadError={categoriesError}
                    onRetry={() => void refetchCategories()}
                    options={[
                      { value: 'all', label: 'All categories' },
                      ...(categories ?? []).map((cat) => ({
                        value: cat.id,
                        label: cat.category_name,
                      })),
                    ]}
                  />
                  <SearchableSelect
                    value={selectedBrand || 'all'}
                    onChange={handleBrandSelection}
                    placeholder="Brand"
                    loadError={brandsError}
                    onRetry={() => void refetchBrands()}
                    options={[
                      { value: 'all', label: 'All brands' },
                      ...(brands ?? []).map((brand) => ({
                        value: brand.id,
                        label: brand.brand_name,
                      })),
                    ]}
                  />
                  <SearchableSelect
                    value={selectedStatus || 'all'}
                    onChange={handleStatusSelection}
                    placeholder="Status"
                    options={[
                      { value: 'all', label: 'All status' },
                      { value: 'active', label: 'Active' },
                      { value: 'inactive', label: 'Inactive' },
                    ]}
                  />
                  <SearchableSelect
                    value={selectedVariantFilter}
                    onChange={handleVariantFilterSelection}
                    placeholder="Variant"
                    options={[
                      { value: 'all', label: 'All products' },
                      { value: 'base', label: 'Base only' },
                      { value: 'variant', label: 'Variants only' },
                    ]}
                  />
                  <div className="space-y-1.5">
                    <Label className="text-xs text-muted-foreground">Discontinued between</Label>
                    <DateRangePicker
                      from={discontinuedFrom}
                      to={discontinuedTo}
                      onChange={handleDiscontinuedRangeChange}
                      aria-label="Discontinued between"
                    />
                  </div>
                  {(hasActiveFilters || simpleFiltersActiveCount > 0) && (
                    <Button variant="outline" size="sm" onClick={handleClearFilters} className="w-full">
                      Clear Filters
                    </Button>
                  )}
                </div>
              ),
            }}
            exportConfig={{
              kind: 'listQuery',
              resourceKey: 'products',
              filename: 'products_export.xlsx',
              getPayload: getExportPayload,
            }}
            primaryAction={<UploadCostPriceAction />}
            secondaryActions={[
              {
                key: 'create-product',
                label: 'Create product',
                icon: Plus,
                onClick: openCreateProduct,
              },
              {
                key: 'advanced-filter',
                label: 'Advanced filters',
                icon: SlidersHorizontal,
                onClick: () => setAdvancedFilterDialogOpen(true),
              },
              {
                key: 'template',
                label: 'Download template',
                icon: Download,
                onClick: () =>
                  generateExcelFile([{}], PRODUCT_IMPORT_COLUMNS, 'products_import_template.xlsx'),
              },
              {
                key: 'import',
                label: 'Import',
                icon: Upload,
                onClick: () => setUploadDialogOpen(true),
                dataGuideTarget: 'master-data.products.upload-button',
              },
              ...(autocountPull.visible
                ? [
                    {
                      key: 'autocount-pull',
                      label: autocountPull.label,
                      icon: CloudDownload,
                      // Zero-arg on purpose: the toolbar passes the click event to `onClick`,
                      // and a pull takes an optional scope object, not an event.
                      onClick: () => void autocountPull.onSelect(),
                    },
                  ]
                : []),
            ]}
            bulkActions={[
              {
                key: 'chat-search',
                label: 'Chat search',
                icon: MessageSquare,
                onClick: () => setBulkChatSearchDialogOpen(true),
              },
              {
                key: 'delete',
                label: 'Delete',
                icon: Trash2,
                destructive: true,
                onClick: () =>
                  bulkDeletion.run(selectedRowIds(table).map((id) => ({ id }))),
              },
            ]}
          />
        </CardHeader>
        {isError ? (
          <div className="px-5 pb-2 text-sm text-destructive">
            {error instanceof Error ? error.message : 'Failed to load products'}
          </div>
        ) : null}
        <CardTable>
          <DataGridTable />
        </CardTable>
        <CardFooter>
          <DataGridPagination />
        </CardFooter>
      </Card>
      <TemplateUploadDialog
        open={uploadDialogOpen}
        onOpenChange={setUploadDialogOpen}
        onTest={async (data: Record<string, unknown>[]) => validateProductsImport(data)}
        onUpload={async (data: Record<string, unknown>[]) => {
          await bulkImportProducts(data);
          notifyImportQueued();
          toast.success(
            'Import job queued successfully. Processing in background. Refresh or check Import Jobs for status.',
            {
              duration: 5000,
              action: {
                label: 'View Status',
                onClick: () => router.push('/system-management/import-jobs'),
              },
            },
          );
          queryClient.invalidateQueries({ queryKey: ['products'] });
          queryClient.invalidateQueries({ queryKey: ['import-jobs'] });
        }}
        accept=".xlsx,.xls"
      />
      <ListQueryFilterDialog
        resourceKey="products"
        open={advancedFilterDialogOpen}
        onOpenChange={setAdvancedFilterDialogOpen}
        initialFilter={advancedFilter}
        onApply={setAdvancedFilter}
      />
      <ProductBulkChatSearchDialog
        open={bulkChatSearchDialogOpen}
        onOpenChange={setBulkChatSearchDialogOpen}
        productIds={selectedRowIds(table)}
        onSuccess={() => setRowSelection({})}
      />
    </DataGrid>
  );
};

export default ProductsList;
