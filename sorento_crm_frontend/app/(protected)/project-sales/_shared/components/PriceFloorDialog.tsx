'use client';

import * as React from 'react';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useProductCategorySelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-product-category-select-query';
// The shared products `/select` mapper. Its name says "variant" because that screen
// needed it first; the endpoint and the shape are the generic ones.
import { getProductsForVariantSelect } from '@/app/(protected)/master-data-management/products/services/productService';
import { usePriceFloorMutations } from '../hooks/useProjects';
import type {
  FloorMode,
  FloorTargetLevel,
  PriceFloorRule,
} from '../types/project.types';

type Level = 'product' | 'category' | 'system';

/**
 * A target the caller has already chosen: the product whose Pricing tab is open, or the
 * category being edited. The picker is replaced by its NAME, because there is nothing to
 * pick and a select showing one option is a decision the user does not have to make.
 */
export interface PriceFloorLockedTarget {
  level: FloorTargetLevel;
  id: string;
  /** Product code or category name. Never an id. */
  label: string;
}

const LEVELS: { value: Level; label: string; hint: string }[] = [
  {
    value: 'product',
    label: 'One product',
    hint: 'Beats every other rule for that product.',
  },
  {
    value: 'category',
    label: 'A category',
    hint: 'Applies to that category, and to anything under it that has no rule of its own.',
  },
  {
    value: 'system',
    label: 'Company default',
    hint: 'The last word, used when nothing more specific matches. Only one exists.',
  },
];

/**
 * Set the floor for one product, one category, or the whole company.
 *
 * A rule is one per target, not one per attempt: saving a category floor that already
 * exists edits it rather than creating a second, competing rule. That is why this is a
 * single dialog for both add and edit, and why the level cannot be changed on an existing
 * rule -- changing it would mean moving the rule to a different target, which is two
 * separate decisions (delete one, create another) pretending to be one.
 *
 * A percent rule needs a list price to mean anything. Products with no list price fall
 * through to the next level down, which is why an absolute company default is a useful
 * backstop even when the percent rules look complete.
 *
 * `lockedTarget` is the same dialog opened from somewhere that already knows the target:
 * the product's Pricing tab, or the category editor. It is a MODE of this component
 * rather than a second dialog, so the copy, the percent-versus-fixed toggle and the
 * over-100% refusal cannot drift between the two places a floor gets set.
 */
export function PriceFloorDialog({
  rule,
  lockedTarget,
  onDone,
}: {
  rule: PriceFloorRule | null;
  lockedTarget?: PriceFloorLockedTarget;
  onDone: () => void;
}) {
  const { upsert } = usePriceFloorMutations();
  const categories = useProductCategorySelectQuery();

  const [level, setLevel] = React.useState<Level>(
    lockedTarget
      ? lockedTarget.level
      : rule
        ? rule.level === 'product'
          ? 'product'
          : rule.level === 'category'
            ? 'category'
            : 'system'
        : 'system',
  );
  const [productId, setProductId] = React.useState(
    lockedTarget?.level === 'product' ? lockedTarget.id : (rule?.product_id ?? ''),
  );
  const [categoryId, setCategoryId] = React.useState(
    lockedTarget?.level === 'category' ? lockedTarget.id : (rule?.category_id ?? ''),
  );
  const [mode, setMode] = React.useState<FloorMode>(rule?.mode ?? 'percent');
  const [value, setValue] = React.useState(rule?.value ?? '');
  const [notes, setNotes] = React.useState(rule?.notes ?? '');
  const [isActive, setIsActive] = React.useState(rule?.is_active ?? true);

  const isEdit = Boolean(rule);
  const numeric = Number(value);
  const percentTooHigh = mode === 'percent' && numeric > 100;
  const blocked =
    !value.trim() ||
    Number.isNaN(numeric) ||
    numeric < 0 ||
    percentTooHigh ||
    (level === 'product' && !productId) ||
    (level === 'category' && !categoryId);

  const fetchProducts = React.useCallback(async (query: string) => {
    const rows = await getProductsForVariantSelect(query || undefined);
    return rows.map((row) => ({
      value: row.id,
      label: row.product_code,
      description: row.product_name,
    }));
  }, []);

  const selectedProduct = React.useMemo(
    () =>
      rule?.product_id
        ? { value: rule.product_id, label: rule.product_code ?? 'Selected product' }
        : undefined,
    [rule],
  );

  return (
    <Dialog open onOpenChange={(next) => !next && onDone()}>
      <DialogContent className="max-h-[92vh] w-full max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle>
            {lockedTarget
              ? `${isEdit ? 'Change' : 'Set'} the floor for ${lockedTarget.label}`
              : isEdit
                ? 'Edit price floor'
                : 'Add a price floor'}
          </DialogTitle>
          <DialogDescription>
            {/* This used to end "It is never blocked", which was true until the approval gate
                landed. Quoting below a floor is still never refused - what is refused is ISSUING
                that quotation to the customer before a manager approves it. Saying the old thing
                here would have a salesperson meet the block with no idea a floor caused it. */}
            A quoted price under the floor is still saved and flagged. What it stops is sending:
            the quotation cannot be issued to the customer until a manager approves it.
          </DialogDescription>
        </DialogHeader>

        <form
          onSubmit={async (event) => {
            event.preventDefault();
            await upsert.mutateAsync({
              mode,
              value: value.trim(),
              product_id: level === 'product' ? productId : null,
              category_id: level === 'category' ? categoryId : null,
              notes: notes.trim() || null,
              is_active: isActive,
            });
            onDone();
          }}
        >
          <DialogBody className="max-h-[65vh] space-y-4 overflow-y-auto">
            {lockedTarget ? (
              <div className="space-y-1.5">
                <Label>Applies to</Label>
                <div className="rounded-lg border border-border px-3 py-2">
                  <p className="min-w-0 break-words text-sm font-medium">
                    {lockedTarget.label}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {lockedTarget.level === 'product'
                      ? 'This product only. Beats every other rule for it.'
                      : 'This category, and anything under it that has no rule of its own.'}
                  </p>
                </div>
              </div>
            ) : (
            <div className="space-y-2">
              <Label>Applies to</Label>
              {LEVELS.map((option) => (
                <label
                  key={option.value}
                  className={
                    isEdit && option.value !== level
                      ? 'flex cursor-not-allowed items-start gap-2.5 rounded-lg border border-border px-3 py-2 opacity-40'
                      : 'flex cursor-pointer items-start gap-2.5 rounded-lg border border-border px-3 py-2 has-[:checked]:border-primary has-[:checked]:bg-primary/5'
                  }
                >
                  <input
                    type="radio"
                    name="floor-level"
                    className="mt-0.5"
                    value={option.value}
                    checked={level === option.value}
                    disabled={isEdit}
                    onChange={() => setLevel(option.value)}
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{option.label}</span>
                    <span className="block text-xs text-muted-foreground">
                      {option.hint}
                    </span>
                  </span>
                </label>
              ))}
              {isEdit && (
                <p className="text-xs text-muted-foreground">
                  To move this floor to a different target, delete it and add a new one.
                </p>
              )}
            </div>
            )}

            {!lockedTarget && level === 'product' && (
              <div className="space-y-1.5">
                <Label htmlFor="floor-product">
                  Product <span className="text-destructive">*</span>
                </Label>
                <SearchableSelect
                  id="floor-product"
                  value={productId}
                  onChange={setProductId}
                  fetchOptions={fetchProducts}
                  selectedOption={selectedProduct}
                  disabled={isEdit}
                  placeholder="Search by code or name"
                  emptyMessage="No products match"
                />
              </div>
            )}

            {!lockedTarget && level === 'category' && (
              <div className="space-y-1.5">
                <Label htmlFor="floor-category">
                  Category <span className="text-destructive">*</span>
                </Label>
                <SearchableSelect
                  id="floor-category"
                  value={categoryId}
                  onChange={setCategoryId}
                  disabled={isEdit}
                  loadError={categories.error}
                  onRetry={() => void categories.refetch()}
                  options={(categories.data ?? []).map((row) => ({
                    value: row.id,
                    label: row.category_name,
                    description: row.category_code,
                  }))}
                  placeholder="Select a category"
                  emptyMessage="No categories found"
                />
              </div>
            )}

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="floor-mode">Expressed as</Label>
                <SearchableSelect
                  id="floor-mode"
                  value={mode}
                  onChange={(next) => setMode(next as FloorMode)}
                  options={[
                    {
                      value: 'percent',
                      label: 'Percent of list',
                      description: 'Needs the product to carry a list price',
                    },
                    {
                      value: 'absolute',
                      label: 'Fixed amount',
                      description: 'Ignores the list price entirely',
                    },
                  ]}
                  placeholder="Select"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="floor-value">
                  {mode === 'percent' ? 'Minimum percent of list' : 'Minimum price (RM)'}{' '}
                  <span className="text-destructive">*</span>
                </Label>
                <Input
                  id="floor-value"
                  type="number"
                  step="0.01"
                  min="0"
                  max={mode === 'percent' ? '100' : undefined}
                  value={value}
                  onChange={(event) => setValue(event.target.value)}
                  placeholder={mode === 'percent' ? '70' : '150.00'}
                  required
                />
                {percentTooHigh && (
                  <p className="text-xs text-destructive">
                    A floor above 100% of list would flag every sale at list price.
                  </p>
                )}
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="floor-notes">Notes</Label>
              <Textarea
                id="floor-notes"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={2}
                placeholder="Who set this, and when it should be revisited"
              />
            </div>

            <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2.5">
              <div className="min-w-0">
                <Label htmlFor="floor-active" className="text-sm">
                  Active
                </Label>
                <p className="text-xs text-muted-foreground">
                  An inactive rule is skipped, and the next level down decides instead.
                </p>
              </div>
              <Switch id="floor-active" checked={isActive} onCheckedChange={setIsActive} />
            </div>
          </DialogBody>

          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button type="button" variant="outline" onClick={onDone}>
              Cancel
            </Button>
            <Button type="submit" disabled={blocked || upsert.isPending}>
              {isEdit ? 'Save floor' : 'Add floor'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
