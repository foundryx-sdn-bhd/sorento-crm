'use client';

import { useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
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
import { useDeleteOrderLine } from '../hooks/useOrders';
import type { OrderLine } from '../types/order.types';
import { codeWithName } from '@/lib/product-display';

export interface OrderLineDeleteDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orderId: string;
  line: OrderLine;
  onSuccess?: () => void;
}

export default function OrderLineDeleteDialog({
  open,
  onOpenChange,
  orderId,
  line,
  onSuccess,
}: OrderLineDeleteDialogProps) {
  const deleteMutation = useDeleteOrderLine();
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await deleteMutation.mutateAsync({ orderId, lineId: line.id });
      onOpenChange(false);
      onSuccess?.();
    } catch {
      // Error is handled by the mutation hook
    } finally {
      setIsDeleting(false);
    }
  };

  const displayLabel = line.product
    ? codeWithName(line.product.product_code ?? '', line.product.product_name)
    : line.product_id;

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Remove delivery order line</AlertDialogTitle>
          <AlertDialogDescription>
            Remove line <strong className="text-foreground">{displayLabel}</strong>? This cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleDelete}
            disabled={isDeleting}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {isDeleting ? (
              <>
                <LoaderCircleIcon className="size-4 animate-spin" />
                Removing...
              </>
            ) : (
              'Remove'
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
