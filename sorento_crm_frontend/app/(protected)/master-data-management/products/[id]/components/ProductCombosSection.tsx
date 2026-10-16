'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import { ImagePlus, LoaderCircleIcon, Plus, Trash2, Upload, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { SectionSkeleton } from '@/components/common/SectionSkeleton';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { useHasPermission } from '@/hooks/usePermissions';
import {
  useAddProductComboPart,
  useProductComboDelete,
  useProductComboPartDelete,
  useProductCombos,
  useUpdateProductComboPart,
} from '../../hooks/useProductCombos';
import { useProductComboImage } from '../../hooks/useProductComboImage';
import { getProducts } from '../../services/productService';
import type { ProductComboPartRow, ProductComboRow } from '../../types/productCombo.types';
import { AddComboModal } from './AddComboModal';
import { codeWithName, distinctName } from '@/lib/product-display';

/** Server-searched and PAGED, never one cached page: the catalogue is 22,000 rows. */
const PRODUCT_PAGE_SIZE = 50;

async function fetchProductOptions(
  query: string,
  pageIndex: number,
): Promise<SearchableSelectOption[]> {
  const products = await getProducts({
    pageIndex,
    pageSize: PRODUCT_PAGE_SIZE,
    sorting: [],
    searchQuery: query,
    status: 'active',
  });
  return (products.data ?? []).map((product) => ({
    value: product.id,
    label: codeWithName(product.product_code, product.product_name),
    searchText: `${product.product_code} ${product.product_name}`,
  }));
}

interface PartRowProps {
  part: ProductComboPartRow;
  groupOptions: SearchableSelectOption[];
  canEdit: boolean;
  isRemoving: boolean;
  onChoiceGroup: (label: string | null) => void;
  onRemove: () => void;
}

function PartRow({
  part,
  groupOptions,
  canEdit,
  isRemoving,
  onChoiceGroup,
  onRemove,
}: PartRowProps) {
  return (
    <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_13rem_auto] sm:items-center">
      <div className="min-w-0">
        <p className="truncate font-medium" title={part.code}>
          {part.code}
        </p>
        {distinctName(part.code, part.product_name) ? (
          <p className="truncate text-sm text-muted-foreground" title={part.product_name}>
            {part.product_name}
          </p>
        ) : null}
        <p className="truncate text-xs text-muted-foreground">{part.dimensions || '-'}</p>
      </div>
      {canEdit ? (
        <SearchableSelect
          clearable
          truncateTriggerLabel
          value={part.choice_group ?? ''}
          onChange={(value) => onChoiceGroup(value || null)}
          options={groupOptions}
          placeholder="Fixed part"
          emptyMessage="No choice groups yet."
          // The vocabulary is whatever marketing already called a group on THIS
          // combo, plus whatever they type now - a basin group is a word off the
          // catalogue page, not a list the system can know in advance.
          createOption={{
            label: (query) => {
              const trimmed = query.trim();
              // Nothing to create when the label already exists on this combo:
              // the row above it IS that group, and offering both reads as two
              // different answers to the same word.
              if (
                !trimmed ||
                groupOptions.some((opt) => opt.value.toLowerCase() === trimmed.toLowerCase())
              ) {
                return null;
              }
              return `Use "${trimmed}"`;
            },
            onCreate: (query) => onChoiceGroup(query.trim() || null),
          }}
          disabled={isRemoving}
        />
      ) : (
        <p className="text-sm text-muted-foreground">{part.choice_group || 'Fixed part'}</p>
      )}
      {canEdit ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="justify-self-start sm:justify-self-end"
          onClick={onRemove}
          disabled={isRemoving}
          aria-label={`Remove ${part.code}`}
        >
          {isRemoving ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <Trash2 className="size-4 text-destructive" />
          )}
        </Button>
      ) : null}
    </div>
  );
}

/**
 * The combo's own cover picture (AC-S5-2/S5-3/S5-5): Upload when there is
 * none, else the thumbnail with Replace and Clear. Through
 * `useProductComboImage`, the same query-cache mutation shape every other
 * combo edit on this page already uses - the list refetches itself on
 * success, so this control carries no `onChanged` callback of its own.
 */
function ComboImageControl({
  combo,
  canEdit,
}: {
  combo: ProductComboRow;
  canEdit: boolean;
}) {
  const { uploadMutateAsync, deleteMutateAsync, isUploading, isDeleting } =
    useProductComboImage(combo.host_product_id);
  const busy = isUploading || isDeleting;
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleFile = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      setError(null);
      try {
        await uploadMutateAsync({ comboId: combo.id, file });
      } catch (caught) {
        setError((caught as Error).message || 'Failed to upload the image');
      } finally {
        if (inputRef.current) inputRef.current.value = '';
      }
    },
    [combo.id, uploadMutateAsync],
  );

  const handleClear = useCallback(async () => {
    setError(null);
    try {
      await deleteMutateAsync(combo.id);
    } catch (caught) {
      setError((caught as Error).message || 'Failed to clear the image');
    }
  }, [combo.id, deleteMutateAsync]);

  if (!canEdit && !combo.image) return null;

  return (
    // `flex-wrap` (AC-S5-5 extended): the thumbnail, Replace/Clear and the
    // error text below crowd a single row off the edge at phone width.
    <div className="flex flex-wrap items-center gap-3 border-b p-3">
      {/* Images only (AC-S5-2) - the accept attribute plus the backend's own
          content-type check, which is what actually refuses a non-image. */}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        aria-label={`${combo.name} cover picture`}
        className="hidden"
        onChange={(event) => void handleFile(event.target.files?.[0])}
      />
      {combo.image ? (
        <>
          {/* A signed CDN URL, not a local/static asset next/image can
              optimise. */}
          <img
            src={combo.image.url}
            alt={combo.name}
            className="size-14 shrink-0 rounded-md border object-cover"
          />
          {canEdit ? (
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => inputRef.current?.click()}
                disabled={busy}
              >
                {busy ? <LoaderCircleIcon className="size-4 animate-spin" /> : <Upload className="size-4" />}
                Replace
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void handleClear()}
                disabled={busy}
              >
                <X className="size-4" />
                Clear
              </Button>
            </div>
          ) : null}
        </>
      ) : (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
        >
          {busy ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <ImagePlus className="size-4" />
          )}
          Upload picture
        </Button>
      )}
      {/* Its own line (AC-S5-5 extended): `w-full` forces a wrap onto a row
          by itself rather than squeezing in beside the buttons. */}
      {error ? <p className="w-full text-sm text-destructive">{error}</p> : null}
    </div>
  );
}

interface ComboBlockProps {
  combo: ProductComboRow;
  canEdit: boolean;
  isDeleting: boolean;
  removingPartId: string | null;
  onDelete: () => void;
  onRemovePart: (part: ProductComboPartRow) => void;
  onChoiceGroup: (part: ProductComboPartRow, label: string | null) => void;
  onAddPart: (comboId: string, productId: string) => void;
  addPartError: string | null;
  isAddingPart: boolean;
}

function ComboBlock({
  combo,
  canEdit,
  isDeleting,
  removingPartId,
  onDelete,
  onRemovePart,
  onChoiceGroup,
  onAddPart,
  addPartError,
  isAddingPart,
}: ComboBlockProps) {
  const groupOptions = useMemo(
    () =>
      [
        ...new Set(
          combo.parts
            .map((part) => part.choice_group)
            .filter((label): label is string => !!label),
        ),
      ].map((label) => ({ value: label, label })),
    [combo.parts],
  );

  // Fixed parts first, then one block per choice group in the order the groups
  // first appear - so the package reads top to bottom the way the catalogue page
  // lists it, rather than alphabetically.
  const fixedParts = combo.parts.filter((part) => !part.choice_group);
  const groups: { label: string; parts: ProductComboPartRow[] }[] = [];
  for (const part of combo.parts) {
    if (!part.choice_group) continue;
    const existing = groups.find((group) => group.label === part.choice_group);
    if (existing) existing.parts.push(part);
    else groups.push({ label: part.choice_group, parts: [part] });
  }

  const partRow = (part: ProductComboPartRow) => (
    <PartRow
      key={part.id}
      part={part}
      groupOptions={groupOptions}
      canEdit={canEdit}
      isRemoving={removingPartId === part.id}
      onChoiceGroup={(label) => onChoiceGroup(part, label)}
      onRemove={() => onRemovePart(part)}
    />
  );

  return (
    <div className="rounded-lg border" data-combo-id={combo.id} data-testid="combo-block">
      <div className="flex flex-col gap-2 border-b p-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="min-w-0 truncate font-medium" title={combo.name}>
          {combo.name}
        </p>
        {canEdit ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="shrink-0 justify-self-start"
            onClick={onDelete}
            disabled={isDeleting}
            aria-label={`Delete combo ${combo.name}`}
          >
            {isDeleting ? (
              <LoaderCircleIcon className="size-4 animate-spin" />
            ) : (
              <Trash2 className="size-4 text-destructive" />
            )}
          </Button>
        ) : null}
      </div>
      <ComboImageControl combo={combo} canEdit={canEdit} />
      <div className="space-y-3 p-3">
        {combo.parts.length === 0 ? (
          <p className="text-sm text-muted-foreground">No parts yet.</p>
        ) : null}
        {/* Its own scrollport (AC-S1-9): a kitchen-sink combo runs to a dozen
            parts and the page must not have to grow by a screen to hold one
            card. The Add part picker below stays put outside it. */}
        <div className="max-h-[22rem] space-y-3 overflow-y-auto">
          {fixedParts.length > 0 ? (
            <div className="space-y-3">{fixedParts.map(partRow)}</div>
          ) : null}
          {groups.map((group) => (
            <div key={group.label} className="space-y-3 rounded-md border border-dashed p-3">
              <p className="text-xs text-muted-foreground">
                {group.label}
                {group.parts.length > 1 ? ' - pick one' : ''}
              </p>
              {group.parts.map(partRow)}
            </div>
          ))}
        </div>
        {canEdit ? (
          <div className="space-y-1.5">
            {/* The shared product search, server-searched and paged (AC-S1-3). It
                holds no value of its own - picking a product adds a part row and the
                picker returns to its placeholder - so there is nothing for a clear
                affordance to clear. */}
            <SearchableSelect
              value=""
              onChange={(productId) => {
                if (productId) onAddPart(combo.id, productId);
              }}
              fetchOptions={fetchProductOptions}
              paginated
              pageSize={PRODUCT_PAGE_SIZE}
              placeholder="Add part"
              emptyMessage="No products found."
              disabled={isAddingPart}
            />
            {addPartError ? <p className="text-sm text-destructive">{addPartError}</p> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * "Combos" (AC-S1-1 to AC-S1-5): the catalogue packages this product is sold as.
 * A cabinet printed on two catalogue pages carries two combos, each named the way
 * the catalogue names it; a part with no choice group always comes with the
 * package, and parts sharing a group are the options the customer picks one of.
 *
 * On the overview tab rather than Suppliers: a combo is what the product IS sold
 * as, not a fact about how it is bought (the Suppliers tab keeps the purchasing
 * bundling rules). A part reads the mirror of this on its own page, read-only, as
 * "Sold with" (`ProductSoldWithSection`).
 */
export function ProductCombosSection({ productId }: { productId: string }) {
  const [addOpen, setAddOpen] = useState(false);
  const [addPartError, setAddPartError] = useState<{ comboId: string; message: string } | null>(
    null,
  );
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canEdit = useHasPermission('master_data.products.edit');
  const { data: combos, isLoading, isError } = useProductCombos(productId);

  const partProductIds = useMemo(
    () => [...new Set((combos ?? []).flatMap((combo) => combo.parts.map((p) => p.product_id)))],
    [combos],
  );
  const comboDeletion = useProductComboDelete(productId, partProductIds);
  const partDeletion = useProductComboPartDelete(productId, partProductIds);
  const addPart = useAddProductComboPart(productId);
  const updatePart = useUpdateProductComboPart(productId);

  const handleAddPart = useCallback(
    async (comboId: string, partProductId: string) => {
      setAddPartError(null);
      try {
        await addPart.mutateAsync({ comboId, write: { part_product_id: partProductId } });
      } catch (caught) {
        // Host-as-part and duplicate-part are answered under the picker, where
        // the choice that has to change was made (AC-S1-3).
        setAddPartError({
          comboId,
          message: (caught as Error).message || 'Failed to add the part',
        });
      }
    },
    [addPart],
  );

  // The new combo is created empty, so put it in view with its parts area ready
  // rather than leaving the reader to find it below the ones already there.
  const handleCreated = useCallback((comboId: string) => {
    requestAnimationFrame(() => {
      containerRef.current
        ?.querySelector(`[data-combo-id="${comboId}"]`)
        ?.scrollIntoView({ block: 'nearest' });
    });
  }, []);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle>Combos</CardTitle>
        {canEdit ? (
          <Button type="button" size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="size-4" />
            Add combo
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <SectionSkeleton rows={4} />
        ) : isError ? (
          <p className="text-sm text-destructive">
            Could not load combos. Try reloading the page.
          </p>
        ) : !combos || combos.length === 0 ? (
          // The sentence only: the header's own Add combo is the ONE call to
          // action, and a second button three lines under it reads as a second
          // thing to do.
          <p className="text-sm text-muted-foreground">No combos.</p>
        ) : (
          <div ref={containerRef} className="space-y-3">
            {combos.map((combo) => (
              <ComboBlock
                key={combo.id}
                combo={combo}
                canEdit={canEdit}
                isDeleting={comboDeletion.targetId === combo.id && comboDeletion.isPending}
                removingPartId={partDeletion.isPending ? partDeletion.targetId : null}
                onDelete={() => comboDeletion.run({ id: combo.id, subject: combo.name })}
                onRemovePart={(part) =>
                  partDeletion.run({ id: part.id, subject: `${part.code} from ${combo.name}` })
                }
                onChoiceGroup={(part, label) =>
                  updatePart.mutate({ partId: part.id, write: { choice_group: label } })
                }
                onAddPart={handleAddPart}
                addPartError={
                  addPartError?.comboId === combo.id ? addPartError.message : null
                }
                isAddingPart={addPart.isPending}
              />
            ))}
          </div>
        )}
      </CardContent>
      {canEdit ? (
        <AddComboModal
          open={addOpen}
          onOpenChange={setAddOpen}
          productId={productId}
          onCreated={handleCreated}
        />
      ) : null}
    </Card>
  );
}

export default ProductCombosSection;
