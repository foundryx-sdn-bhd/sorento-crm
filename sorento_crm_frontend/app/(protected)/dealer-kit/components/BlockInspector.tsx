'use client';

import { useState } from 'react';
import { toast } from '@/lib/toast';
import { Bookmark, MousePointerSquareDashed, Package } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { LiveNumberInput } from '@/components/ui/number-input';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { Block, BlockProps } from '@/lib/dealer-kit/types';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  listBundles,
  listCollections,
  listTileTemplates,
  saveCollectionAsLibrary,
} from '../services/catalogueService';
import {
  EMPTY_SELECTION,
  ProductPickerDialog,
  type ProductSelection,
} from './ProductPickerDialog';

/**
 * Editing the selected block's properties.
 *
 * Until now the canvas could place blocks but nothing could change what was IN
 * them, so a heading was stuck on its placeholder text. This is where that is
 * fixed and where product binding lives.
 *
 * A collection block stores a `collectionId`, a `tileTemplateId` and per
 * breakpoint column counts - and nothing else. No product list, no prices. What
 * a reader eventually sees is resolved server-side per viewer, so the same saved
 * document serves staff, a dealer and a consumer (AC-G1).
 */

const TEXT_SCALES = [
  { value: 'sm', label: 'Small' },
  { value: 'base', label: 'Body' },
  { value: 'lg', label: 'Large' },
  { value: 'xl', label: 'Extra large' },
  { value: '2xl', label: 'Display' },
];

const ALIGNMENTS = [
  { value: 'left', label: 'Left' },
  { value: 'center', label: 'Centre' },
  { value: 'right', label: 'Right' },
];

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label className="text-xs" htmlFor={htmlFor}>
        {label}
      </Label>
      {children}
    </div>
  );
}

export function BlockInspector({
  block,
  selection,
  defaultTileTemplateId,
  onChangeProps,
  onChangeSelection,
}: {
  block: Block | null;
  /** The page-scoped product selection behind a collection block, if any. */
  selection: ProductSelection;
  /**
   * The page's tile design. A block that names none of its own uses it, so the
   * control has to SAY so - an empty select that silently inherits reads as
   * unset, which is how "I chose a design and nothing happened" starts.
   */
  defaultTileTemplateId?: string | null;
  onChangeProps: (next: BlockProps) => void;
  onChangeSelection: (next: ProductSelection) => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [reusableName, setReusableName] = useState('');
  const [saving, setSaving] = useState(false);
  const queryClient = useQueryClient();

  // Hooks must run before the early return below, so these are declared here
  // even though only a collection or bundle block reads them.
  const { data: collections = [] } = useQuery({
    queryKey: ['dealer-kit', 'collections'],
    queryFn: listCollections,
  });
  const { data: bundles = [] } = useQuery({
    queryKey: ['dealer-kit', 'bundles'],
    queryFn: listBundles,
  });
  const { data: tileTemplates = [] } = useQuery({
    queryKey: ['dealer-kit', 'tile-templates'],
    queryFn: listTileTemplates,
  });

  // What this block falls back to, by name. Null when the page has chosen
  // nothing either, which is the renderer's built-in field list.
  const inheritedName =
    tileTemplates.find((candidate) => candidate.id === defaultTileTemplateId)?.name ?? null;

  if (!block) {
    return (
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-sm">Block</CardTitle>
        </CardHeader>
        <CardContent className="pb-4">
          <div className="rounded-lg border border-dashed border-border p-4 text-center">
            <MousePointerSquareDashed
              className="mx-auto size-4 text-muted-foreground"
              aria-hidden
            />
            <p className="mt-2 text-xs font-medium text-foreground">Nothing selected</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Click a block on the canvas to edit it.
            </p>
          </div>
        </CardContent>
      </Card>
    );
  }

  const props = block.props;
  // A bound collection that appears in the LIBRARY list is shared with other
  // pages. The list holds reusable ones only, so membership is the test.
  const boundToLibrary =
    props.kind === 'collection' &&
    Boolean(props.collectionId) &&
    collections.some((collection) => collection.id === props.collectionId);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between gap-2 text-sm">
          <span className="capitalize">{block.type}</span>
          <Badge variant="outline" className="text-xs">
            block
          </Badge>
        </CardTitle>
      </CardHeader>

      <CardContent className="flex flex-col gap-3 pb-4">
        {(props.kind === 'text' || props.kind === 'heading') && (
          <>
            <Field label="Text">
              <Textarea
                rows={3}
                value={props.text}
                aria-label="Block text"
                onChange={(event) => onChangeProps({ ...props, text: event.target.value })}
              />
            </Field>

            <Field label="Size" htmlFor="dk-text-size">
              <SearchableSelect
                id="dk-text-size"
                value={props.scale ?? 'base'}
                onChange={(value) =>
                  onChangeProps({ ...props, scale: value as typeof props.scale })
                }
                options={TEXT_SCALES}
              />
            </Field>

            <Field label="Alignment" htmlFor="dk-text-align">
              <SearchableSelect
                id="dk-text-align"
                value={props.align ?? 'left'}
                onChange={(value) =>
                  onChangeProps({ ...props, align: value as typeof props.align })
                }
                options={ALIGNMENTS}
              />
            </Field>
          </>
        )}

        {(props.kind === 'image' || props.kind === 'asset') && (
          <Field label="Alt text">
            <Input
              value={props.alt ?? ''}
              aria-label="Alt text"
              placeholder="Describe the image"
              onChange={(event) => onChangeProps({ ...props, alt: event.target.value })}
            />
          </Field>
        )}

        {props.kind === 'collection' && (
          <>
            <Button
              variant="outline"
              size="sm"
              className="justify-start"
              onClick={() => setPickerOpen(true)}
            >
              <Package className="size-4" />
              Choose products
            </Button>

            {boundToLibrary ? (
              // Changing products here rewrites the shared set, which IS the
              // point of a reusable collection - but it must never be a
              // surprise. Silently editing other people's pages from inside
              // this one is the failure mode worth spending three lines on.
              <div className="rounded-md border border-amber-500/50 bg-amber-500/5 px-2.5 py-2">
                <p className="text-xs text-foreground">
                  This block uses a shared collection. Changing its products changes it
                  everywhere it is used, not just on this page.
                </p>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                {selection.pinnedProductIds.length === 0 && !selection.conditions
                  ? 'Nothing chosen yet. The block stays empty until it has products.'
                  : 'Products are resolved when the page is viewed, so prices follow the reader.'}
              </p>
            )}

            {props.collectionId && (
              // Promotes the SAME row, so this page stays bound to it. That is
              // what makes "one edit reaches every page" true rather than a
              // copy that immediately starts drifting (AC-F5, AC-F7).
              <Button
                variant="outline"
                size="sm"
                className="justify-start"
                onClick={() => setSaveOpen(true)}
              >
                <Bookmark className="size-4" />
                Save as reusable
              </Button>
            )}

            <Field label="Reusable collection" htmlFor="dk-collection">
              <SearchableSelect
                id="dk-collection"
                clearable
                value={props.collectionId ?? ''}
                onChange={(value) => onChangeProps({ ...props, collectionId: value || null })}
                options={collections.map((collection) => ({
                  value: collection.id,
                  label: `${collection.name ?? 'Untitled'} (${collection.memberCount})`,
                }))}
                placeholder="Use this page's own selection"
              />
            </Field>

            <Field label="Tile design" htmlFor="dk-tile-template">
              <SearchableSelect
                id="dk-tile-template"
                clearable
                value={props.tileTemplateId ?? ''}
                onChange={(value) => onChangeProps({ ...props, tileTemplateId: value || null })}
                options={tileTemplates.map((template) => ({
                  value: template.id,
                  label: template.name,
                }))}
                /* Names what this block ACTUALLY uses when it chooses nothing,
                   rather than inviting a choice it does not need: the page has
                   already made one, and 341 rows agreeing with it is the point
                   of the page-level control. */
                placeholder={
                  inheritedName ? `Same as the page (${inheritedName})` : 'Standard tile'
                }
              />
            </Field>

            {/* Rows, not three columns. At the inspector's width a 3-column grid
                gives each label ~75px, so "desktop cols" wrapped to two lines
                while "tablet cols" did not - the labels misaligned and the
                inputs sat at different heights. */}
            <div className="flex flex-col gap-2">
              {(['desktop', 'tablet', 'mobile'] as const).map((breakpoint) => (
                <div key={breakpoint} className="flex items-center justify-between gap-3">
                  <Label className="text-xs capitalize" htmlFor={`dk-cols-${breakpoint}`}>
                    {breakpoint}
                  </Label>
                  <div className="w-20">
                    <LiveNumberInput
                      id={`dk-cols-${breakpoint}`}
                      min={1}
                      max={8}
                      rules={{ min: 1, max: 8, integer: true }}
                      value={props.columns[breakpoint]}
                      aria-label={`Tiles across on ${breakpoint}`}
                      onCommit={(columns) =>
                        onChangeProps({
                          ...props,
                          columns: { ...props.columns, [breakpoint]: columns },
                        })
                      }
                    />
                  </div>
                </div>
              ))}
            </div>

            <p className="text-xs text-muted-foreground">
              Tiles across is the block&apos;s own setting, which is why stacking the block
              full-width on a phone does not make a four-across product grid.
            </p>
          </>
        )}

        {props.kind === 'bundle' && (
          <>
            <Field label="Bundle" htmlFor="dk-bundle">
              <SearchableSelect
                id="dk-bundle"
                clearable
                value={props.bundleId ?? ''}
                onChange={(value) => onChangeProps({ ...props, bundleId: value || null })}
                options={bundles.map((bundle) => ({
                  value: bundle.id,
                  label: `${bundle.name} · ${bundle.price}${bundle.available ? '' : ' (unavailable)'}`,
                }))}
                placeholder="Pick a bundle"
              />
            </Field>
            <p className="text-xs text-muted-foreground">
              A bundle shows as one price with its parts beneath. Whether it can be ordered is
              worked out from its parts each time it is viewed, so a discontinued part takes it
              out of stock automatically.
            </p>
          </>
        )}

        {props.kind === 'spacer' && (
          <p className="text-xs text-muted-foreground">
            A spacer has nothing to configure. Resize it on the canvas.
          </p>
        )}
      </CardContent>

      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Save as reusable collection</DialogTitle>
            <DialogDescription>
              Give this set of products a name and it becomes available to other pages. This
              page keeps using it, and editing it later updates every page that does.
            </DialogDescription>
          </DialogHeader>

          <Field label="Name" htmlFor="dk-reusable-name">
            <Input
              id="dk-reusable-name"
              value={reusableName}
              placeholder="Kitchen range 2026"
              onChange={(event) => setReusableName(event.target.value)}
            />
          </Field>

          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!reusableName.trim() || saving}
              onClick={async () => {
                if (props.kind !== 'collection' || !props.collectionId) return;
                setSaving(true);
                try {
                  await saveCollectionAsLibrary(props.collectionId, reusableName.trim());
                  await queryClient.invalidateQueries({
                    queryKey: ['dealer-kit', 'collections'],
                  });
                  toast.success(`Saved "${reusableName.trim()}" to the library`);
                  setSaveOpen(false);
                  setReusableName('');
                } catch (error) {
                  toast.error(
                    error instanceof Error ? error.message : 'Could not save this collection.',
                  );
                } finally {
                  setSaving(false);
                }
              }}
            >
              {saving ? 'Saving' : 'Save block'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ProductPickerDialog
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        value={selection ?? EMPTY_SELECTION}
        onSave={onChangeSelection}
      />
    </Card>
  );
}
