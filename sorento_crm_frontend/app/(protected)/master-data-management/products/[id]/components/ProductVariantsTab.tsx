'use client';

import { useState } from 'react';
import Link from 'next/link';
import { GitBranch, CornerLeftUp, Plus, Link2Off, RotateCcw, X } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  useSetVariantParent,
  useUnlinkVariant,
  useResetVariantAuto,
} from '../../hooks/useProducts';
import type { ProductVariantRef } from '../../types/product.types';
import ProductVariantPickerDialog from './ProductVariantPickerDialog';
import ProductAlternativesSection from './ProductAlternativesSection';

interface ProductVariantsTabProps {
  /** The current product's id - required for variant curation mutations. */
  productId: string;
  /** The current product's code - used to highlight each child's distinguishing suffix. */
  productCode: string;
  /** Parent product when this product is itself a variant; null for a base product. */
  variantOf?: ProductVariantRef | null;
  /** Direct child variants of this product. */
  variants?: ProductVariantRef[];
  /** True when the variant link is hand-curated (shows Manual badge + Reset action). */
  variantLinkManual?: boolean;
}

const PRODUCT_BASE_PATH = '/master-data-management/products';

/**
 * When the child code starts with the base code, dim the shared prefix and
 * bold the distinguishing suffix (e.g. SRTKT71SS **-BL**). Falls back to the
 * plain code when there is no clean prefix match - kept deliberately simple.
 */
function renderCode(childCode: string, baseCode: string) {
  if (baseCode && childCode.toLowerCase().startsWith(baseCode.toLowerCase())) {
    const prefix = childCode.slice(0, baseCode.length);
    const suffix = childCode.slice(baseCode.length);
    if (suffix) {
      return (
        <>
          <span className="text-muted-foreground">{prefix}</span>
          <span className="font-semibold">{suffix}</span>
        </>
      );
    }
  }
  return <span className="font-medium">{childCode}</span>;
}

export default function ProductVariantsTab({
  productId,
  productCode,
  variantOf,
  variants,
  variantLinkManual,
}: ProductVariantsTabProps) {
  const children = variants ?? [];

  const setParent = useSetVariantParent();
  const unlink = useUnlinkVariant();
  const resetAuto = useResetVariantAuto();

  // Set / change parent
  const [parentPickerOpen, setParentPickerOpen] = useState(false);
  // Add child variant
  const [childPickerOpen, setChildPickerOpen] = useState(false);
  // Confirm: unlink self from parent
  const [unlinkConfirmOpen, setUnlinkConfirmOpen] = useState(false);
  // Confirm: remove a specific child
  const [childToRemove, setChildToRemove] = useState<ProductVariantRef | null>(null);
  // Confirm: reset to auto
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);

  const handleSetParent = (parentId: string) => {
    setParent.mutate(
      { productId, parentId },
      { onSuccess: () => setParentPickerOpen(false) },
    );
  };

  const handleAddChild = (childId: string) => {
    setParent.mutate(
      { productId: childId, parentId: productId },
      { onSuccess: () => setChildPickerOpen(false) },
    );
  };

  const handleUnlinkSelf = () => {
    unlink.mutate(
      { productId, parentId: variantOf?.id },
      { onSuccess: () => setUnlinkConfirmOpen(false) },
    );
  };

  const handleRemoveChild = () => {
    if (!childToRemove) return;
    unlink.mutate(
      { productId: childToRemove.id, parentId: productId },
      { onSuccess: () => setChildToRemove(null) },
    );
  };

  const handleReset = () => {
    resetAuto.mutate(
      { productId },
      { onSuccess: () => setResetConfirmOpen(false) },
    );
  };

  // Exclude self + existing children from the "add variant" picker.
  const childPickerExcludeIds = [productId, ...children.map((c) => c.id)];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Variants</CardTitle>
        <p className="text-sm text-muted-foreground">
          How this product relates to its variant family.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Parent - only meaningful when this product is itself a variant */}
        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="font-semibold">Variant of</h3>
            {variantLinkManual && (
              <Badge variant="warning">
                Manual
              </Badge>
            )}
            <div className="ms-auto flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setParentPickerOpen(true)}
              >
                <CornerLeftUp className="size-4" />
                {variantOf ? 'Change parent' : 'Set parent'}
              </Button>
              {variantOf && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setUnlinkConfirmOpen(true)}
                >
                  <Link2Off className="size-4" />
                  Unlink
                </Button>
              )}
              {variantLinkManual && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setResetConfirmOpen(true)}
                >
                  <RotateCcw className="size-4" />
                  Reset to auto
                </Button>
              )}
            </div>
          </div>
          {variantOf ? (
            <Link
              href={`${PRODUCT_BASE_PATH}/${variantOf.id}`}
              className="flex items-center gap-2 rounded-md border px-3 py-2 hover:bg-muted transition-colors"
            >
              <CornerLeftUp className="size-4 text-muted-foreground shrink-0" />
              <Badge variant="secondary">
                Base
              </Badge>
              <div className="min-w-0 flex-1">
                <div
                  className="font-medium truncate"
                  title={`${variantOf.product_code} - ${variantOf.product_name}`}
                >
                  {variantOf.product_code}
                </div>
                <div className="text-xs text-muted-foreground truncate">
                  {variantOf.product_name}
                </div>
              </div>
            </Link>
          ) : (
            <p className="text-sm text-muted-foreground">
              This is a base product - it is not a variant of another product.
            </p>
          )}
        </div>

        {/* Children - the variant list */}
        <div>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="font-semibold">
              Variants{children.length ? ` (${children.length})` : ''}
            </h3>
            <Button
              variant="outline"
              size="sm"
              className="ms-auto"
              onClick={() => setChildPickerOpen(true)}
            >
              <Plus className="size-4" />
              Add variant
            </Button>
          </div>
          {children.length === 0 ? (
            <div className="rounded-md border border-dashed px-3 py-6 text-center">
              <p className="text-sm text-muted-foreground">
                No variants of this product.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-3"
                onClick={() => setChildPickerOpen(true)}
              >
                <Plus className="size-4" />
                Add variant
              </Button>
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {children.map((child) => (
                <div
                  key={child.id}
                  className="flex items-center gap-2 rounded-md border px-3 py-2 hover:bg-muted transition-colors"
                >
                  <Link
                    href={`${PRODUCT_BASE_PATH}/${child.id}`}
                    className="flex min-w-0 flex-1 items-center gap-2"
                  >
                    <GitBranch className="size-4 text-muted-foreground shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div
                        className="truncate"
                        title={`${child.product_code} - ${child.product_name}`}
                      >
                        {renderCode(child.product_code, productCode)}
                      </div>
                      <div className="text-xs text-muted-foreground truncate">
                        {child.product_name}
                      </div>
                    </div>
                  </Link>
                  <Button
                    mode="icon"
                    variant="ghost"
                    size="sm"
                    className="shrink-0"
                    title="Remove variant"
                    onClick={() => setChildToRemove(child)}
                    aria-label="Remove variant"
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>

        <ProductAlternativesSection productId={productId} />
      </CardContent>

      {/* Set / change parent */}
      <ProductVariantPickerDialog
        open={parentPickerOpen}
        onOpenChange={setParentPickerOpen}
        title={variantOf ? 'Change variant parent' : 'Set variant parent'}
        description="Pick the base product this product should be a variant of."
        confirmLabel={variantOf ? 'Change parent' : 'Set parent'}
        excludeIds={[productId]}
        submitting={setParent.isPending}
        onConfirm={handleSetParent}
      />

      {/* Add child variant */}
      <ProductVariantPickerDialog
        open={childPickerOpen}
        onOpenChange={setChildPickerOpen}
        title="Add variant"
        description="Pick a product to attach as a variant of this product."
        confirmLabel="Add variant"
        excludeIds={childPickerExcludeIds}
        submitting={setParent.isPending}
        onConfirm={handleAddChild}
      />

      {/* Confirm: unlink self from parent */}
      <AlertDialog open={unlinkConfirmOpen} onOpenChange={setUnlinkConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Unlink from parent?</AlertDialogTitle>
            <AlertDialogDescription>
              This product will no longer be a variant of{' '}
              {variantOf?.product_code ?? 'its parent'}. You can re-link it later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={unlink.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={(e) => {
                e.preventDefault();
                handleUnlinkSelf();
              }}
              disabled={unlink.isPending}
            >
              Unlink
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirm: remove a child */}
      <AlertDialog
        open={!!childToRemove}
        onOpenChange={(open) => !open && setChildToRemove(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove variant?</AlertDialogTitle>
            <AlertDialogDescription>
              {childToRemove?.product_code ?? 'This product'} will no longer be a
              variant of this product.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={unlink.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={(e) => {
                e.preventDefault();
                handleRemoveChild();
              }}
              disabled={unlink.isPending}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirm: reset to auto */}
      <AlertDialog open={resetConfirmOpen} onOpenChange={setResetConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset to automatic linking?</AlertDialogTitle>
            <AlertDialogDescription>
              This clears the manual override and re-derives the variant link from
              the product code.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={resetAuto.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(e) => {
                e.preventDefault();
                handleReset();
              }}
              disabled={resetAuto.isPending}
            >
              Reset to auto
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
