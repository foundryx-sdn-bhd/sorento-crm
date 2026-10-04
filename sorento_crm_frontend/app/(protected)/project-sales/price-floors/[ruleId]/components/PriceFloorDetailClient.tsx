'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { MoveLeft } from 'lucide-react';
import { toast } from '@/lib/toast';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import { ConfirmDeleteDialog } from '@/components/common/ConfirmDeleteDialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { getProductsForLineSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import { useProductCategorySelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-product-category-select-query';
import { usePriceFloorMutations, usePriceFloors } from '../../../_shared/hooks/useProjects';
import type { FloorMode, PriceFloorRule } from '../../../_shared/types/project.types';

const NEW = 'new';

/**
 * One price floor, on its own page.
 *
 * The LEVEL is not a field: it is implied by which target is set, exactly as the model does
 * it (`product_id` set = product level, `category_id` set = category, neither = system). A
 * stored level would be a second source of truth for something the keys already say.
 */
export function PriceFloorDetailClient({ ruleId }: { ruleId: string }) {
  const router = useRouter();
  const isNew = ruleId === NEW;
  const floors = usePriceFloors();
  const { upsert, remove } = usePriceFloorMutations();
  const categories = useProductCategorySelectQuery();

  const row: PriceFloorRule | null = React.useMemo(
    () => (floors.data ?? []).find((item) => item.id === ruleId) ?? null,
    [floors.data, ruleId],
  );

  const [scope, setScope] = React.useState<'product' | 'category' | 'system'>('system');
  const [productId, setProductId] = React.useState('');
  const [categoryId, setCategoryId] = React.useState('');
  const [mode, setMode] = React.useState<FloorMode>('percent');
  const [value, setValue] = React.useState('');
  const [notes, setNotes] = React.useState('');
  const [isActive, setIsActive] = React.useState(true);
  const [seeded, setSeeded] = React.useState(false);
  const [confirmingDelete, setConfirmingDelete] = React.useState(false);

  React.useEffect(() => {
    if (isNew || seeded || !row) return;
    setScope(row.product_id ? 'product' : row.category_id ? 'category' : 'system');
    setProductId(row.product_id ?? '');
    setCategoryId(row.category_id ?? '');
    setMode(row.mode);
    setValue(row.value ?? '');
    setNotes(row.notes ?? '');
    setIsActive(row.is_active);
    setSeeded(true);
  }, [isNew, row, seeded]);

  const fetchProducts = React.useCallback(async (query: string) => {
    const products = await getProductsForLineSelect(query || undefined);
    return products.map((product) => ({
      value: product.id,
      label: product.product_code,
      description: product.product_name,
    }));
  }, []);

  const categoryOptions = React.useMemo(
    () =>
      (categories.data ?? []).map((category) => ({
        value: category.id,
        label: category.category_name,
        description: category.category_code,
      })),
    [categories.data],
  );

  const save = async () => {
    if (!value.trim()) {
      toast.error('The floor needs a value');
      return;
    }
    if (scope === 'product' && !productId) {
      toast.error('Pick the product this floor applies to');
      return;
    }
    if (scope === 'category' && !categoryId) {
      toast.error('Pick the category this floor applies to');
      return;
    }
    await upsert.mutateAsync({
      mode,
      value: value.trim(),
      product_id: scope === 'product' ? productId : null,
      category_id: scope === 'category' ? categoryId : null,
      notes: notes.trim() || null,
      is_active: isActive,
    });
    router.push('/project-sales/price-floors');
  };

  return (
    <Container>
      <PageHeader
        title={isNew ? 'New price floor' : 'Price floor'}
        actions={
          <>
            {!isNew && row && (
              <Button variant="outline" onClick={() => setConfirmingDelete(true)}>
                Delete
              </Button>
            )}
            <Button asChild variant="outline">
              <Link href="/project-sales/price-floors">
                <MoveLeft /> Back to price floors
              </Link>
            </Button>
          </>
        }
      />

      <Card>
        <CardContent className="space-y-5 pt-5">
          {floors.isLoading && !isNew ? (
            <div className="space-y-3">
              <Skeleton className="h-9 w-full max-w-md" />
              <Skeleton className="h-9 w-full max-w-md" />
            </div>
          ) : (
            <>
              <div className="grid gap-5 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Applies to</Label>
                  <SearchableSelect
                    value={scope}
                    onChange={(next) => setScope(next as typeof scope)}
                    options={[
                      { value: 'system', label: 'Everything' },
                      { value: 'category', label: 'A category' },
                      { value: 'product', label: 'A product' },
                    ]}
                  />
                </div>

                {scope === 'product' && (
                  <div className="space-y-2">
                    <Label>Product</Label>
                    <SearchableSelect
                      value={productId}
                      onChange={setProductId}
                      fetchOptions={fetchProducts}
                      selectedOption={
                        row?.product_id && row.product_code
                          ? { value: row.product_id, label: row.product_code }
                          : undefined
                      }
                      placeholder="Pick a product"
                    />
                  </div>
                )}

                {scope === 'category' && (
                  <div className="space-y-2">
                    <Label>Category</Label>
                    <SearchableSelect
                      value={categoryId}
                      onChange={setCategoryId}
                      loadError={categories.error}
                      onRetry={() => void categories.refetch()}
                      options={categoryOptions}
                      placeholder="Pick a category"
                    />
                  </div>
                )}
              </div>

              <div className="grid gap-5 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Mode</Label>
                  <SearchableSelect
                    value={mode}
                    onChange={(next) => setMode(next as FloorMode)}
                    options={[
                      { value: 'percent', label: 'Percent of list price' },
                      { value: 'absolute', label: 'Ringgit amount' },
                    ]}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="floor-value">
                    {mode === 'percent' ? 'Percent' : 'Amount (RM)'}
                  </Label>
                  <Input
                    id="floor-value"
                    inputMode="decimal"
                    value={value}
                    onChange={(event) => setValue(event.target.value)}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="floor-notes">Notes</Label>
                <Textarea
                  id="floor-notes"
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  rows={2}
                />
              </div>

              <div className="flex items-center gap-2">
                <Checkbox
                  id="floor-active"
                  checked={isActive}
                  onCheckedChange={(next) => setIsActive(Boolean(next))}
                />
                <Label htmlFor="floor-active">Active</Label>
              </div>

              <div className="flex flex-wrap justify-end gap-2 pt-1">
                <Button
                  variant="outline"
                  onClick={() => router.push('/project-sales/price-floors')}
                >
                  Cancel
                </Button>
                <Button onClick={() => void save()} disabled={upsert.isPending}>
                  {isNew ? 'Create floor' : 'Save changes'}
                </Button>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {row && (
        <ConfirmDeleteDialog
          open={confirmingDelete}
          onOpenChange={setConfirmingDelete}
          description="Delete this price floor? This action cannot be undone."
          onDelete={async () => {
            await remove.mutateAsync(row.id);
            router.push('/project-sales/price-floors');
          }}
        />
      )}
    </Container>
  );
}
