'use client';

import { useEffect, useLayoutEffect, useRef } from 'react';
import { zodResolver } from '@hookform/resolvers/zod';
import { useForm } from 'react-hook-form';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  Banknote,
  Factory,
  Info,
  ListChecks,
  LoaderCircleIcon,
  Paperclip,
  Save,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
  FormDescription,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Switch } from '@/components/ui/switch';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { toast } from '@/lib/toast';
import { useHasPermission } from '@/hooks/usePermissions';
import { useCreateProduct, useUpdateProduct, useProduct } from '../hooks/useProducts';
import { ProductSchema, type ProductSchemaType } from '../forms/product-schema';
import type { Product, ProductFormData } from '../types/product.types';
import { useCategory } from '../../product-categories/hooks/useProductCategories';
import { useProductCategorySelectQuery } from '../../shared/hooks/use-product-category-select-query';
import { useBrandSelectQuery } from '../../shared/hooks/use-brand-select-query';
import { useUOMSelectQuery } from '../../shared/hooks/use-uom-select-query';
// The floor is project-sales pricing POLICY, not a product column, so the panel and its
// rules live with the rest of that policy and are only surfaced here.
import { PriceFloorPanel } from '@/app/(protected)/project-sales/_shared/components/PriceFloorPanel';
import ProductSuppliersSection from './ProductSuppliersSection';
import ProductAttachmentsTab from './ProductAttachmentsTab';
import ListPager from '@/components/common/ListPager';
import { productsPagerQuery } from '../lib/listQuery';

/** The tab values `?tab=` may name, so a stray query string cannot land on an empty panel. */
const PRODUCT_FORM_TABS = [
  'basic',
  'pricing',
  'specifications',
  'suppliers',
  'attachments',
];

interface ProductFormProps {
  productId?: string;
  /** When provided, used as sole source for form reset (avoids cache/timing issues) */
  initialProduct?: Product | null;
  onSuccess?: () => void;
}

export default function ProductForm({ productId, initialProduct, onSuccess }: ProductFormProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isEditMode = !!productId;
  const { data: productFromQuery } = useProduct(productId || null);
  /** Use initialProduct when passed (from Edit page); otherwise use query result */
  const product = initialProduct ?? productFromQuery;
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
  const { data: uoms, error: uomsError, refetch: refetchUoms } = useUOMSelectQuery();
  const createMutation = useCreateProduct();
  const updateMutation = useUpdateProduct();
  const canViewChatbotLimits = useHasPermission('master_data.chatbot_stock_limits.view');
  const canEditChatbotLimits = useHasPermission('master_data.chatbot_stock_limits.edit');
  const canViewSuppliers = useHasPermission('procurement.product_suppliers.view');

  const navigationBasePath = '/master-data-management/products';

  const form = useForm<ProductSchemaType>({
    resolver: zodResolver(ProductSchema),
    defaultValues: {
      product_code: '',
      product_name: '',
      description: '',
      category_id: '',
      brand_id: null,
      barcode: '',
      item_type: null,
      is_active: true,
      is_searchable: true,
      exclude_from_planning: false,
      list_price: 0,
      cost_price: null,
      invoice_price: null,
      weight: null,
      dimensions_length: null,
      dimensions_width: null,
      dimensions_height: null,
      warranty_months: null,
      has_serial_tracking: false,
      has_batch_tracking: false,
      reorder_level: 10,
      reorder_quantity: 50,
      chatbot_max_qty: null,
      chatbot_eta_offset_days: null,
      base_uom_id: '',
    },
    mode: 'onTouched',
  });

  // The category the form currently has selected, refetched in full (the select
  // list only carries code/name) so the two chatbot fields below can show its
  // X / Y as a placeholder when the product's own value is empty.
  const watchedCategoryId = form.watch('category_id');
  const { data: selectedCategoryForChatbotLimits } = useCategory(watchedCategoryId || null);

  // Track which product we've initialized so we don't reset on every render.
  // Using a ref instead of state avoids Strict Mode timing issues (state updates can be
  // lost when Strict Mode unmounts before the effect runs).
  const lastInitializedProductIdRef = useRef<string | null>(null);

  // Reset init guard when productId changes or on unmount (fixes Strict Mode: ref must
  // be cleared on unmount so reset runs again after remount).
  useEffect(() => {
    lastInitializedProductIdRef.current = null;
    return () => {
      lastInitializedProductIdRef.current = null;
    };
  }, [productId]);

  // Load product data when editing - use useLayoutEffect so reset runs before paint,
  // avoiding any flash of empty Category/Brand. Runs synchronously (no Strict Mode cancel).
  useLayoutEffect(() => {
    if (!product || !isEditMode || lastInitializedProductIdRef.current === product.id) return;

    const categoryId = product.category_id
      ? String(product.category_id)
      : product.category?.id
        ? String(product.category.id)
        : '';
    const brandId = product.brand_id
      ? String(product.brand_id)
      : product.brand?.id
        ? String(product.brand.id)
        : null;
    const uomId = String(product.base_uom_id || '');

    form.reset({
      product_code: product.product_code,
      product_name: product.product_name,
      description: product.description || '',
      category_id: categoryId,
      brand_id: brandId,
      barcode: product.barcode || '',
      item_type: product.item_type || null,
      is_active: product.is_active,
      is_searchable: product.is_searchable ?? true,
      exclude_from_planning: product.exclude_from_planning ?? false,
      list_price: product.list_price,
      cost_price: product.cost_price || null,
      invoice_price: product.invoice_price || null,
      weight: product.weight || null,
      dimensions_length: product.dimensions_length || null,
      dimensions_width: product.dimensions_width || null,
      dimensions_height: product.dimensions_height || null,
      warranty_months: product.warranty_months || null,
      has_serial_tracking: product.has_serial_tracking,
      has_batch_tracking: product.has_batch_tracking,
      reorder_level: product.reorder_level,
      reorder_quantity: product.reorder_quantity,
      chatbot_max_qty: product.chatbot_max_qty ?? null,
      chatbot_eta_offset_days: product.chatbot_eta_offset_days ?? null,
      base_uom_id: uomId,
    });
    lastInitializedProductIdRef.current = product.id;
  }, [product, isEditMode, form]);

  // Auto-save draft to localStorage every 30 seconds
  useEffect(() => {
    const interval = setInterval(() => {
      if (form.formState.isDirty) {
        const formData = form.getValues();
        localStorage.setItem('product-form-draft', JSON.stringify(formData));
      }
    }, 30000);

    return () => clearInterval(interval);
  }, [form]);

  const onSubmit = async (data: ProductSchemaType) => {
    try {
      // For update: send null for cleared optional fields (backend needs explicit null to set DB to NULL).
      // For create: omit optional fields when null (undefined).
      const toOptionalNumber = (v: number | string | null | undefined, forUpdate: boolean): number | null | undefined => {
        if (v != null && v !== '') {
          const n = typeof v === 'number' ? v : Number(v);
          if (!Number.isNaN(n)) return n;
        }
        return forUpdate ? null : undefined;
      };

      const formData: ProductFormData = {
        product_code: data.product_code,
        product_name: data.product_name,
        description: data.description || undefined,
        category_id: data.category_id,
        brand_id: data.brand_id ?? (isEditMode ? null : undefined),
        barcode: data.barcode ? data.barcode : isEditMode ? null : undefined,
        base_uom_id: data.base_uom_id,
        list_price: typeof data.list_price === 'number' ? data.list_price : Number(data.list_price),
        cost_price: toOptionalNumber(data.cost_price, isEditMode),
        invoice_price: toOptionalNumber(data.invoice_price, isEditMode),
        weight: toOptionalNumber(data.weight, isEditMode),
        dimensions_length: toOptionalNumber(data.dimensions_length, isEditMode),
        dimensions_width: toOptionalNumber(data.dimensions_width, isEditMode),
        dimensions_height: toOptionalNumber(data.dimensions_height, isEditMode),
        warranty_months: toOptionalNumber(data.warranty_months, isEditMode),
        has_serial_tracking: data.has_serial_tracking,
        has_batch_tracking: data.has_batch_tracking,
        reorder_level: data.reorder_level,
        reorder_quantity: data.reorder_quantity,
        chatbot_max_qty: toOptionalNumber(data.chatbot_max_qty, isEditMode),
        chatbot_eta_offset_days: toOptionalNumber(data.chatbot_eta_offset_days, isEditMode),
        item_type: data.item_type ?? (isEditMode ? null : undefined),
        is_active: data.is_active,
        is_searchable: data.is_searchable,
        exclude_from_planning: data.exclude_from_planning,
      };

      if (isEditMode && productId) {
        await updateMutation.mutateAsync({ id: productId, data: formData });
      } else {
        await createMutation.mutateAsync(formData);
      }
      localStorage.removeItem('product-form-draft');
      if (onSuccess) {
        onSuccess();
      } else {
        router.push('/master-data-management/products');
      }
    } catch (error) {
      // Error is handled by the mutation hook
      console.error('Product form submission error:', error);
    }
  };

  // In edit mode, wait for product only. Category, Brand, UOM are fetched here and use
  // product.category / product.brand / product.base_uom as display fallback (same as UOM).
  if (isEditMode && product == null) {
    return (
      <div className="flex items-center justify-center p-8">
        <LoaderCircleIcon className="size-6 animate-spin" />
      </div>
    );
  }

  const isLoading = createMutation.isPending || updateMutation.isPending;

  return (
    <Form {...form}>
      <form
        onSubmit={form.handleSubmit(onSubmit, (errors) => {
          const first = Object.values(errors)[0]?.message;
          if (first) toast.error(first);
        })}
        className="space-y-6"
      >
        {isEditMode && productId && (
          <div className="flex justify-end">
            <ListPager
              {...productsPagerQuery}
              detailPath={navigationBasePath}
              currentId={productId}
              ariaLabel="product"
              hrefFor={(id, search) =>
                `${navigationBasePath}/${id}/edit${search ? `?${search}` : ''}`
              }
            />
          </div>
        )}
        {/* `?tab=` so another screen can send somebody to the right tab rather than to
            Basic Information with instructions. The quotation's "No photo chosen" cell links
            straight to `?tab=attachments`, which is where the product photo is chosen. Unknown
            values fall back to the first tab rather than rendering nothing. */}
        <Tabs defaultValue={PRODUCT_FORM_TABS.includes(searchParams.get('tab') ?? '')
            ? (searchParams.get('tab') as string)
            : 'basic'} className="w-full">
          {/* The strip scrolls (S1), so it needs no grid: five equal columns at
              375 gave each tab 66px and every label overlapped its neighbour. */}
          <TabsList className="mb-5">
            <TabsTrigger value="basic">
              <Info />
              <span>Basic Information</span>
            </TabsTrigger>
            <TabsTrigger value="pricing">
              <Banknote />
              <span>Pricing</span>
            </TabsTrigger>
            <TabsTrigger value="specifications">
              <ListChecks />
              <span>Specifications</span>
            </TabsTrigger>
            {canViewSuppliers ? (
              <TabsTrigger value="suppliers">
                <Factory />
                <span>Suppliers</span>
              </TabsTrigger>
            ) : null}
            <TabsTrigger value="attachments">
              <Paperclip />
              <span>Attachments</span>
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: Basic Information */}
          <TabsContent value="basic">
            <Card>
              <CardHeader>
                <CardTitle>Basic Information</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <FormField
                  control={form.control}
                  name="product_code"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Product Code *</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="PROD-001"
                          {...field}
                          disabled={isEditMode}
                        />
                      </FormControl>
                      <FormDescription>
                        Unique product identifier (alphanumeric, dashes, underscores only)
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="product_name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Product Name *</FormLabel>
                      <FormControl>
                        <Input placeholder="Enter product name" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="description"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Description</FormLabel>
                      <FormControl>
                        <Textarea
                          placeholder="Enter product description"
                          {...field}
                          value={field.value || ''}
                          rows={4}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <FormField
                    control={form.control}
                    name="category_id"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Category *</FormLabel>
                        <FormControl>
                          <SearchableSelect
                            value={field.value || ''}
                            onChange={field.onChange}
                            placeholder="Search category..."
                            emptyMessage="No category found."
                            loadError={categoriesError}
                            onRetry={() => void refetchCategories()}
                            options={[
                              ...(categories ?? []).map((cat) => ({
                                value: cat.id,
                                label: cat.category_code,
                                searchText: `${cat.category_code} ${cat.category_name ?? ''}`,
                              })),
                              // Keep the product's saved category selectable even when it is
                              // missing from the list (inactive/filtered), so editing doesn't
                              // silently blank a required field.
                              ...(product?.category &&
                              !categories?.some((c) => c.id === product.category?.id)
                                ? [
                                    {
                                      value: product.category.id,
                                      label: product.category.category_code,
                                      searchText: `${product.category.category_code} ${product.category.category_name ?? ''}`,
                                    },
                                  ]
                                : []),
                            ]}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="brand_id"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Brand</FormLabel>
                        <FormControl>
                          <SearchableSelect
                            // Brand is optional, so '__none__' stands in for null the same way
                            // the other nullable selects in this form do.
                            value={field.value || '__none__'}
                            onChange={(v) => field.onChange(v === '__none__' ? null : v)}
                            placeholder="Search brand..."
                            emptyMessage="No brand found."
                            loadError={brandsError}
                            onRetry={() => void refetchBrands()}
                            options={[
                              { value: '__none__', label: 'None' },
                              ...(brands ?? []).map((brand) => ({
                                value: brand.id,
                                label: brand.brand_code,
                                searchText: `${brand.brand_code} ${brand.brand_name ?? ''}`,
                              })),
                              ...(product?.brand &&
                              !brands?.some((b) => b.id === product.brand?.id)
                                ? [
                                    {
                                      value: product.brand.id,
                                      label: product.brand.brand_code,
                                      searchText: `${product.brand.brand_code} ${product.brand.brand_name ?? ''}`,
                                    },
                                  ]
                                : []),
                            ]}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <FormField
                  control={form.control}
                  name="barcode"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Barcode</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="e.g. 1234567890123"
                          {...field}
                          value={field.value || ''}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <FormField
                    control={form.control}
                    name="item_type"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Item Type</FormLabel>
                        <FormControl>
                          <SearchableSelect
                            value={field.value || '__none__'}
                            onChange={(value) =>
                              field.onChange(value === '__none__' ? null : value)
                            }
                            placeholder="Select item type"
                            options={[
                              { value: '__none__', label: 'None' },
                              { value: 'product', label: 'Product' },
                              { value: 'bundle', label: 'Bundle' },
                              { value: 'service', label: 'Service' },
                              { value: 'other', label: 'Other' },
                            ]}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="is_active"
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                        <div className="space-y-0.5">
                          <FormLabel className="text-base">Active Status</FormLabel>
                          <FormDescription>
                            Enable or disable this product
                          </FormDescription>
                        </div>
                        <FormControl>
                          <Switch
                            checked={field.value}
                            onCheckedChange={field.onChange}
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="is_searchable"
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                        <div className="space-y-0.5">
                          <FormLabel className="text-base">Chat Search</FormLabel>
                          <FormDescription>
                            Allow the chatbot to answer with this product
                          </FormDescription>
                        </div>
                        <FormControl>
                          <Switch
                            checked={field.value}
                            onCheckedChange={field.onChange}
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="exclude_from_planning"
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                        <div className="space-y-0.5">
                          <FormLabel className="text-base">Exclude from reorder planning</FormLabel>
                          <FormDescription>
                            Never appears in a reorder run, even when named directly
                          </FormDescription>
                        </div>
                        <FormControl>
                          <Switch
                            checked={field.value}
                            onCheckedChange={field.onChange}
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />
                </div>
              </CardContent>
            </Card>
          </TabsContent>

          {/* Tab 2: Pricing */}
          <TabsContent value="pricing">
            <Card>
              <CardHeader>
                <CardTitle>Pricing</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <FormField
                  control={form.control}
                  name="list_price"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>List Price *</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          step="0.01"
                          placeholder="0.00"
                          {...field}
                          value={field.value ?? ''}
                          onChange={(e) => {
                            const value = e.target.value;
                            if (value === '') {
                              field.onChange(0);
                            } else {
                              const numValue = parseFloat(value);
                              field.onChange(isNaN(numValue) ? 0 : numValue);
                            }
                          }}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="cost_price"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Cost Price</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          step="0.01"
                          placeholder="0.00"
                          {...field}
                          value={field.value || ''}
                          onChange={(e) => field.onChange(e.target.value ? parseFloat(e.target.value) : null)}
                        />
                      </FormControl>
                      <FormDescription>
                        Internal cost price (hidden from viewers)
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="invoice_price"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Invoice Price</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          step="0.01"
                          placeholder="0.00"
                          {...field}
                          value={field.value || ''}
                          onChange={(e) => field.onChange(e.target.value ? parseFloat(e.target.value) : null)}
                        />
                      </FormControl>
                      <FormDescription>
                        Invoice price (hidden from viewers)
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* Saves on its own, deliberately: a floor is a row in price_floor_rules,
                    not a product column, so folding it into this form's submit would be
                    one button writing two resources with no honest thing to say when the
                    second half failed. */}
                <PriceFloorPanel
                  target={
                    isEditMode && productId && product
                      ? { level: 'product', id: productId, label: product.product_code }
                      : null
                  }
                  disabledReason="Save the product first, then set its floor here."
                />
              </CardContent>
            </Card>
          </TabsContent>

          {/* Tab 3: Specifications */}
          <TabsContent value="specifications">
            <Card>
              <CardHeader>
                <CardTitle>Specifications</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <FormField
                  control={form.control}
                  name="base_uom_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Base Unit of Measure *</FormLabel>
                      <FormControl>
                        <SearchableSelect
                          value={field.value || ''}
                          onChange={field.onChange}
                          placeholder="Select base UOM"
                          loadError={uomsError}
                          onRetry={() => void refetchUoms()}
                          options={[
                            ...(uoms ?? []).map((uom) => ({
                              value: uom.id,
                              label: uom.uom_code,
                            })),
                            // The product's saved UOM may be inactive and therefore absent from
                            // the select list; keep it as an option so editing an existing
                            // product doesn't silently blank its Base UOM.
                            ...(product?.base_uom &&
                            !uoms?.some((uom) => uom.id === product.base_uom?.id)
                              ? [
                                  {
                                    value: product.base_uom.id,
                                    label: product.base_uom.uom_code,
                                  },
                                ]
                              : []),
                          ]}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="weight"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Weight</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          step="0.01"
                          placeholder="0.00"
                          {...field}
                          value={field.value || ''}
                          onChange={(e) => field.onChange(e.target.value ? parseFloat(e.target.value) : null)}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                  <FormField
                    control={form.control}
                    name="dimensions_length"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Length</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            step="0.01"
                            placeholder="0.00"
                            {...field}
                            value={field.value || ''}
                            onChange={(e) => field.onChange(e.target.value ? parseFloat(e.target.value) : null)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="dimensions_width"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Width</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            step="0.01"
                            placeholder="0.00"
                            {...field}
                            value={field.value || ''}
                            onChange={(e) => field.onChange(e.target.value ? parseFloat(e.target.value) : null)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="dimensions_height"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Height</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            step="0.01"
                            placeholder="0.00"
                            {...field}
                            value={field.value || ''}
                            onChange={(e) => field.onChange(e.target.value ? parseFloat(e.target.value) : null)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                <FormField
                  control={form.control}
                  name="warranty_months"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Warranty (Months)</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          placeholder="0"
                          {...field}
                          value={field.value || ''}
                          onChange={(e) => field.onChange(e.target.value ? parseInt(e.target.value) : null)}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <FormField
                    control={form.control}
                    name="has_serial_tracking"
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                        <div className="space-y-0.5">
                          <FormLabel className="text-base">Serial Tracking</FormLabel>
                          <FormDescription>
                            Track individual serial numbers
                          </FormDescription>
                        </div>
                        <FormControl>
                          <Switch
                            checked={field.value}
                            onCheckedChange={field.onChange}
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="has_batch_tracking"
                    render={({ field }) => (
                      <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                        <div className="space-y-0.5">
                          <FormLabel className="text-base">Batch Tracking</FormLabel>
                          <FormDescription>
                            Track batches with expiry dates
                          </FormDescription>
                        </div>
                        <FormControl>
                          <Switch
                            checked={field.value}
                            onCheckedChange={field.onChange}
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <FormField
                    control={form.control}
                    name="reorder_level"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Reorder Level</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            placeholder="10"
                            {...field}
                            onChange={(e) => field.onChange(parseInt(e.target.value) || 0)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    control={form.control}
                    name="reorder_quantity"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Reorder Quantity</FormLabel>
                        <FormControl>
                          <Input
                            type="number"
                            placeholder="50"
                            {...field}
                            onChange={(e) => field.onChange(parseInt(e.target.value) || 0)}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>

                {canViewChatbotLimits && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <FormField
                      control={form.control}
                      name="chatbot_max_qty"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Max quantity (assistant)</FormLabel>
                          <FormControl>
                            <Input
                              type="number"
                              min={0}
                              placeholder={
                                selectedCategoryForChatbotLimits?.chatbot_max_qty != null
                                  ? String(selectedCategoryForChatbotLimits.chatbot_max_qty)
                                  : undefined
                              }
                              disabled={!canEditChatbotLimits}
                              {...field}
                              value={field.value ?? ''}
                              onChange={(e) =>
                                field.onChange(e.target.value ? parseInt(e.target.value, 10) : null)
                              }
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />

                    <FormField
                      control={form.control}
                      name="chatbot_eta_offset_days"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>ETA offset (days)</FormLabel>
                          <FormControl>
                            <Input
                              type="number"
                              min={0}
                              placeholder={
                                selectedCategoryForChatbotLimits?.chatbot_eta_offset_days != null
                                  ? String(selectedCategoryForChatbotLimits.chatbot_eta_offset_days)
                                  : undefined
                              }
                              disabled={!canEditChatbotLimits}
                              {...field}
                              value={field.value ?? ''}
                              onChange={(e) =>
                                field.onChange(e.target.value ? parseInt(e.target.value, 10) : null)
                              }
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>

          {/* Tab 4: Suppliers */}
          {canViewSuppliers ? (
            <TabsContent value="suppliers">
              <ProductSuppliersSection
                productId={productId}
                isEditMode={isEditMode}
              />
            </TabsContent>
          ) : null}

          {/* Tab 5: Attachments */}
          <TabsContent value="attachments">
            <ProductAttachmentsTab productId={productId} isEditMode={isEditMode} />
          </TabsContent>
        </Tabs>

        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              isEditMode && productId
                ? router.push(`/master-data-management/products/${productId}`)
                : router.back()
            }
            disabled={isLoading}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={isLoading}>
            {isLoading ? (
              <>
                <LoaderCircleIcon className="size-4 animate-spin" />
                Saving...
              </>
            ) : (
              <>
                <Save className="size-4" />
                {isEditMode ? 'Update Product' : 'Create Product'}
              </>
            )}
          </Button>
        </div>
      </form>
    </Form>
  );
}
