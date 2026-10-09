'use client';

import { ReactNode } from 'react';
import { LoaderCircleIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';

export interface FormDialogScaffoldProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: ReactNode;
  onSubmit: (e: React.FormEvent) => void;
  submitLabel?: string;
  cancelLabel?: string;
  isPending?: boolean;
  error?: string | null;
  /** Screen-reader-only dialog description (Radix requires one, or an explicit
   * `aria-describedby={undefined}`, per every `DialogContent`). Falls back to the
   * title so a caller that names nothing more specific never trips the "Missing
   * Description" console warning. */
  description?: string;
  /** Id of an element inside the form that describes it (read out on focus). */
  formDescribedBy?: string;
}

/**
 * Standard form modal scaffold per ADR: Cancel (outline) left, Save/Create (primary) right.
 */
export function FormDialogScaffold({
  open,
  onOpenChange,
  title,
  children,
  onSubmit,
  submitLabel = 'Save',
  cancelLabel = 'Cancel',
  isPending = false,
  error,
  description,
  formDescribedBy,
}: FormDialogScaffoldProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className="sr-only">{description ?? title}</DialogDescription>
        </DialogHeader>
        <form onSubmit={onSubmit} className="space-y-4" aria-describedby={formDescribedBy}>
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          {children}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              {cancelLabel}
            </Button>
            <Button type="submit" disabled={isPending}>
              {isPending && <LoaderCircleIcon className="animate-spin me-2 size-4" />}
              {submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
