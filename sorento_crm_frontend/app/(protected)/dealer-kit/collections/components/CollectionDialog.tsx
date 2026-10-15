'use client';

import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from '@/lib/toast';
import { Package, Pencil } from 'lucide-react';

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
import { ScrollArea } from '@/components/ui/scroll-area';
import { Skeleton } from '@/components/ui/skeleton';
import {
  EMPTY_SELECTION,
  ProductPickerDialog,
  type ProductSelection,
} from '../../components/ProductPickerDialog';
import {
  createCollection,
  resolveCollection,
  updateCollection,
} from '../../services/catalogueService';
import type { CollectionSummary } from '@/lib/dealer-kit/types';
import { distinctName } from '@/lib/product-display';

/**
 * A collection, as a record you can open.
 *
 * **What a collection is for**, since it is a fair question and the answer does
 * not belong on the screen: it is the binding between a place in a document and
 * a live set of products. A printed row in a brochure names a collection, never
 * a price list, so the same published page quotes a dealer and a consumer
 * different figures and picks up a product added to the set afterwards. The
 * user guide says this; the UI does not explain itself.
 *
 * A LIBRARY collection is one meant to be reused: bind it to three pages and one
 * edit reaches all three. The unnamed page-scoped ones the editor creates when
 * somebody picks products inside a block are not records anybody manages, which
 * is why they are not in this list.
 */
export function CollectionDialog({
  open,
  onOpenChange,
  collection,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Null creates a new library collection. */
  collection: CollectionSummary | null;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [selection, setSelection] = useState<ProductSelection>(EMPTY_SELECTION);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setName(collection?.name ?? '');
    setSelection(EMPTY_SELECTION);
  }, [open, collection]);

  /**
   * What this collection resolves to RIGHT NOW.
   *
   * Not the pins, the members: a rule earns its keep by picking up products
   * added after it was written, so "what is in it" is a question only the
   * server can answer and it changes without anybody editing this record.
   */
  const { data: resolved, isLoading: resolving } = useQuery({
    queryKey: ['dealer-kit', 'resolve-collection', collection?.id],
    queryFn: () => resolveCollection(collection!.id),
    enabled: open && Boolean(collection?.id),
  });

  const save = async () => {
    if (!name.trim()) return;
    setSaving(true);
    try {
      const payload = {
        scope: 'library' as const,
        name: name.trim(),
        conditions: (selection.conditions ?? null) as Record<string, unknown> | null,
        pinnedProductIds: selection.pinnedProductIds,
        excludedProductIds: selection.excludedProductIds,
      };
      if (collection) {
        await updateCollection(collection.id, payload);
      } else {
        await createCollection(payload);
      }
      await queryClient.invalidateQueries({ queryKey: ['dealer-kit', 'collections'] });
      await queryClient.invalidateQueries({
        queryKey: ['dealer-kit', 'resolve-collection', collection?.id],
      });
      toast.success(collection ? 'Collection updated' : 'Collection created');
      onOpenChange(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not save this collection.');
    } finally {
      setSaving(false);
    }
  };

  const chosenCount =
    selection.pinnedProductIds.length > 0 || selection.conditions
      ? selection.pinnedProductIds.length
      : null;

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg" data-dk-collection-dialog>
          <DialogHeader>
            <DialogTitle>{collection ? 'Edit collection' : 'New collection'}</DialogTitle>
            <DialogDescription>
              A named set of products a catalogue can bind to. Bind it to several pages and one
              edit reaches all of them.
            </DialogDescription>
          </DialogHeader>

          <div className="flex flex-col gap-6">
            <div className="flex flex-col gap-2">
              <Label htmlFor="dk-collection-name" className="text-xs">
                Name
              </Label>
              <Input
                id="dk-collection-name"
                value={name}
                placeholder="Bathroom best sellers"
                onChange={(event) => setName(event.target.value)}
              />
            </div>

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <Label className="text-xs">Products</Label>
                <Button variant="outline" size="sm" onClick={() => setPickerOpen(true)}>
                  <Pencil className="size-3.5" />
                  {chosenCount === null ? 'Choose products' : 'Change'}
                </Button>
              </div>

              {/*
                What it holds now, not what was pinned. A rule picks up products
                added after it was written, so this is the only honest answer
                and it comes from the same resolver the published page uses.
              */}
              {collection && resolving && <Skeleton className="h-24 w-full" />}

              {collection && !resolving && (
                <div className="rounded-md border border-border" data-dk-collection-members>
                  <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                    <Package className="size-4 text-muted-foreground" aria-hidden />
                    <span className="text-sm">
                      {resolved?.tiles.length ?? 0} product
                      {(resolved?.tiles.length ?? 0) === 1 ? '' : 's'} right now
                    </span>
                  </div>
                  <ScrollArea className="max-h-40">
                    {(resolved?.tiles.length ?? 0) === 0 ? (
                      <p className="p-3 text-xs text-muted-foreground">
                        Nothing matches yet. Choose products, or write a rule.
                      </p>
                    ) : (
                      <ul className="flex flex-col divide-y divide-border">
                        {resolved?.tiles.map((tile) => (
                          <li
                            key={tile.productId}
                            className="flex items-center justify-between gap-2 px-3 py-1.5"
                          >
                            <span className="truncate font-mono text-xs">{tile.productCode}</span>
                            {distinctName(tile.productCode, tile.productName) ? (
                              <span className="truncate text-xs text-muted-foreground">
                                {tile.productName}
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </ScrollArea>
                </div>
              )}

              {!collection && (
                <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                  {chosenCount === null
                    ? 'Nothing chosen yet.'
                    : `${chosenCount} product${chosenCount === 1 ? '' : 's'} picked by hand.`}
                </p>
              )}
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button disabled={!name.trim() || saving} onClick={save}>
              {saving ? 'Saving' : 'Save collection'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* The same picker the page editor uses. A second way to choose products
          would be a second idea of what choosing means. */}
      <ProductPickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        value={selection}
        onSave={setSelection}
      />
    </>
  );
}
