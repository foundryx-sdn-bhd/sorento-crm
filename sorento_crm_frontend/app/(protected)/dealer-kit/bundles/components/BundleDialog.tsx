'use client';

import { useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { Plus, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { createBundle } from '../../services/catalogueService';
import { PICKER_PAGE_SIZE, listPickerProducts } from '../../services/productPickerService';
import { sameDisplayText } from '@/lib/product-display';

/**
 * Building a bundle: a name, one price, and the products it contains.
 *
 * The price is the BUNDLE's, not a sum. What each component is worth on an
 * invoice is worked out by allocating that price pro-rata across them, which is
 * why nothing here asks for per-component figures - they would immediately
 * disagree with the total.
 *
 * Availability is never asked for either: it is derived from the components
 * every time the bundle is read, so a discontinued part takes the bundle out of
 * stock without anyone editing it.
 */
export function BundleDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [price, setPrice] = useState<number | null>(null);
  const [components, setComponents] = useState<{ productId: string; quantity: number | null }[]>([
    { productId: '', quantity: 1 },
  ]);
  const [saving, setSaving] = useState(false);
  // Numbers are checked when Create bundle is pressed; afterwards the messages follow the fields.
  const [checked, setChecked] = useState(false);

  /**
   * Labels for products we have already seen in a dropdown.
   *
   * The picker is server-searched, so the page holding a chosen product is
   * usually gone by the time the row re-renders. Without this the field falls
   * back to showing an id, which is exactly what the UI must never show.
   */
  const seenProducts = useRef(new Map<string, { code: string; name: string }>());

  const usable = components.filter((component) => component.productId);
  const canSave = Boolean(name.trim()) && usable.length > 0;
  const priceError = checked ? numberFieldError(price, { min: 0 }) : undefined;
  const quantityError = (component: { productId: string; quantity: number | null }) =>
    checked && component.productId ? numberFieldError(component.quantity, { min: 1 }) : undefined;

  const reset = () => {
    setName('');
    setPrice(null);
    setChecked(false);
    setComponents([{ productId: '', quantity: 1 }]);
  };

  const save = async () => {
    if (!canSave) return;
    setChecked(true);
    if (
      numberFieldError(price, { min: 0 }) ||
      usable.some((component) => numberFieldError(component.quantity, { min: 1 }))
    ) {
      return;
    }
    setSaving(true);
    try {
      await createBundle(
        name.trim(),
        String(parseNumberDraft(price)),
        usable.map((component) => ({
          productId: component.productId,
          quantity: parseNumberDraft(component.quantity) as number,
        })),
      );
      await queryClient.invalidateQueries({ queryKey: ['dealer-kit', 'bundles'] });
      toast.success('Bundle created');
      reset();
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not save this bundle.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>New bundle</DialogTitle>
          <DialogDescription>
            A bundle is a set of products sold together under one price. The price is split
            across the products automatically when one is ordered.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2">
          <Label htmlFor="dk-bundle-name" className="text-xs">
            Name
          </Label>
          <Input
            id="dk-bundle-name"
            value={name}
            placeholder="Kitchen starter pack"
            onChange={(event) => setName(event.target.value)}
          />
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="dk-bundle-price" className="text-xs">
            Bundle price
          </Label>
          <NumberInput
            id="dk-bundle-price"
            min={0}
            step="0.01"
            value={price}
            placeholder="1800.00"
            onValueChange={setPrice}
            error={priceError}
          />
        </div>

        <div className="flex flex-col gap-2">
          <p className="text-xs font-medium text-foreground">Products in this bundle</p>

          {components.map((component, index) => (
            <div key={index} className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div className="min-w-0 flex-1">
                <SearchableSelect
                  id={`dk-bundle-component-${index}`}
                  value={component.productId}
                  onChange={(value) =>
                    setComponents((current) =>
                      current.map((row, position) =>
                        position === index ? { ...row, productId: value } : row,
                      ),
                    )
                  }
                  // Server-searched and paged: a static option list capped
                  // this picker at one page of a 22,000-product catalogue, so
                  // most products could not be found at all.
                  fetchOptions={async (query, pageIndex) => {
                    const rows = await listPickerProducts(query, pageIndex);
                    for (const product of rows) {
                      seenProducts.current.set(product.id, {
                        code: product.code,
                        name: product.name,
                      });
                    }
                    return rows.map((product) => ({
                      value: product.id,
                      label: product.code,
                      description: product.name,
                    }));
                  }}
                  paginated
                  pageSize={PICKER_PAGE_SIZE}
                  selectedOption={(() => {
                    const seen = seenProducts.current.get(component.productId);
                    return seen
                      ? {
                          value: component.productId,
                          label: seen.code,
                          description: seen.name,
                        }
                      : undefined;
                  })()}
                  renderOption={(option) => (
                    <span className="min-w-0">
                      <span className="block truncate font-mono text-xs">{option.label}</span>
                      {option.description && !sameDisplayText(option.label, option.description) ? (
                        <span className="block truncate text-xs text-muted-foreground">
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                  )}
                  placeholder="Pick a product"
                />
              </div>

              <div className="sm:w-24">
                <NumberInput
                  min={1}
                  aria-label={`Quantity for product ${index + 1}`}
                  value={component.quantity}
                  error={quantityError(component)}
                  onValueChange={(quantity) =>
                    setComponents((current) =>
                      current.map((row, position) =>
                        position === index ? { ...row, quantity } : row,
                      ),
                    )
                  }
                />
              </div>

              <Button
                variant="ghost"
                size="sm"
                aria-label={`Remove product ${index + 1}`}
                disabled={components.length === 1}
                onClick={() =>
                  setComponents((current) =>
                    current.filter((_row, position) => position !== index),
                  )
                }
              >
                <X className="size-4" />
              </Button>
            </div>
          ))}

          <Button
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() =>
              setComponents((current) => [...current, { productId: '', quantity: 1 }])
            }
          >
            <Plus className="size-4" />
            Add product
          </Button>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canSave || saving} onClick={save}>
            {saving ? 'Saving' : 'Create bundle'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
