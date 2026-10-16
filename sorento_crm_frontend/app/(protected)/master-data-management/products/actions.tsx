'use client';

/**
 * The Products action set (D15): Delete.
 *
 * Edit is the record page's primary button and the list's row click opens the
 * record, so neither belongs in this menu (D15). The list's old Duplicate icon
 * is not here because it was never implemented - it logged to the console.
 *
 * Delete asks nothing (D7). It parks the deletion on the server for ten seconds
 * and the countdown takes over the primary button, or the toast over the list, so
 * the way back is Cancel rather than a dialog the reader has to read first.
 */

import { Trash2 } from 'lucide-react';
import type { RecordAction, RecordActionSet } from '@/components/common/recordActions';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { useHasPermission } from '@/hooks/usePermissions';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import type { ProductListItem } from './types/product.types';
import { distinctName } from '@/lib/product-display';

export interface UseProductActionsOptions {
  /** Where to go once the record is gone (the record page returns to the list). */
  onDeleted?: () => void;
  /**
   * The record page shows the countdown in place of its primary button; a list
   * row has nowhere to put one, so it travels to a toast (S6-06, S6-07).
   */
  surface?: 'inline' | 'toast';
}

export function useProductActions(
  product: ProductListItem | undefined | null,
  { onDeleted, surface = 'inline' }: UseProductActionsOptions = {},
): RecordActionSet {
  const canDelete = useHasPermission('master_data.products.delete');

  const deletion = useDeferredAction({
    actionKey: 'product.delete',
    entityType: 'product',
    entityId: product?.id,
    verb: 'Deleting',
    subject: product
      ? distinctName(product.product_code, product.product_name)
        ? `${product.product_name} (${product.product_code})`
        : product.product_code
      : '',
    surface,
    watchFromMount: surface === 'inline',
    successMessage: 'Product deleted',
    invalidateKeys: [['products']],
    onCommitted: onDeleted,
  });

  const actions: RecordAction[] = [];
  if (!product) return { actions, dialogs: null, pending: null };

  if (canDelete) {
    actions.push({
      key: 'product.delete',
      label: 'Delete product',
      icon: Trash2,
      kind: 'destructive',
      disabled: deletion.isPending,
      run: deletion.start,
    });
  }

  return { actions, dialogs: null, pending: deletion.countdown };
}

/** The list row's "..." cell - the same items the record page's gear shows. */
export function ProductRowActions({ product }: { product: ProductListItem }) {
  const { actions } = useProductActions(product, { surface: 'toast' });

  if (actions.length === 0) return null;

  return <RowActionsMenu actions={actions} ariaLabel="product" />;
}
