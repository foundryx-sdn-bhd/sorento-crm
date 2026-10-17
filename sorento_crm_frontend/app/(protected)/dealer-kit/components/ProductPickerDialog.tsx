'use client';

import { useMemo, useRef, useState } from 'react';
import { Check, ChevronDown, ChevronRight, ImageOff, Loader2, Minus, Package, X } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { RuleBuilder } from '@/components/rule-builder/RuleBuilder';
import { ruleTreeHasBadNumber } from '@/components/rule-builder/ruleTreeBadNumber';
import { useRuleFacts } from '@/components/rule-builder/useRuleFacts';
import type { RuleGroup } from '@/components/rule-builder/types';
import { cn } from '@/lib/utils';
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { Skeleton } from '@/components/ui/skeleton';
import { isSearchInFlight, useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import {
  PICKER_PAGE_SIZE,
  listPickerCategories,
  listPickerProducts,
  listProductThumbnails,
  type PickerProduct,
} from '../services/productPickerService';
import { distinctName } from '@/lib/product-display';

/**
 * Choosing what goes in a collection.
 *
 * Two ways in, deliberately, because Designers work both ways: a rule ("every
 * sink under RM1500") that keeps earning its keep as the catalogue changes, and
 * hand-picking for the cases no rule expresses. They compose - membership is
 * **rule union pins minus exclusions** - so a Designer can start from a rule and
 * then add or drop individual products without abandoning it.
 *
 * The rule tab uses the SHARED `RuleBuilder` against the `product` fact source,
 * which is the same component and the same evaluator the automation rules use.
 *
 * The preview is the important part. A rule you cannot see the results of is a
 * rule you have to publish to test, so the match list is always on screen and
 * always reflects the current rule plus pins minus exclusions.
 */

export interface ProductSelection {
  conditions: RuleGroup | null;
  pinnedProductIds: string[];
  excludedProductIds: string[];
}

export const EMPTY_SELECTION: ProductSelection = {
  conditions: null,
  pinnedProductIds: [],
  excludedProductIds: [],
};

/**
 * The rule's own matches come from the SERVER, because the server is where the
 * rule engine lives. Evaluating a second copy of the rule in the browser is the
 * exact "two evaluators that drift" trap the shared engine exists to avoid, and
 * the drift would only show up as a preview that disagreed with the published
 * page. So a rule edit saves and the resolved count comes back.
 */

/**
 * One product, as a picture.
 *
 * **Why a card and not a row.** Choosing four products out of seventeen
 * thousand is a LOOKING task, and it was built as a reading task: rows of code
 * and name, which is exactly what a consumer cannot navigate. `SRTBF11404` and
 * `SRTBF11608` are indistinguishable as text and obvious as photographs.
 *
 * The code stays on the card because that is what staff search and reorder by.
 */
function ProductCard({
  product,
  imageUrl,
  state,
  onToggle,
}: {
  product: PickerProduct;
  imageUrl?: string;
  state: 'in' | 'out' | 'rule';
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={state !== 'out'}
      aria-label={`${state === 'out' ? 'Include' : 'Exclude'} ${product.name}`}
      data-dk-picker-card={product.code}
      className={cn(
        'group relative flex flex-col gap-1.5 rounded-lg border p-2 text-start transition-colors',
        state === 'out'
          ? 'border-border bg-background hover:border-primary/50'
          : 'border-primary bg-primary/5 ring-1 ring-primary/25',
      )}
    >
      <div className="relative aspect-square overflow-hidden rounded-md bg-muted">
        {imageUrl ? (
          // A plain img, not next/image: the src is a signed URL on a storage
          // host that changes per request, which the optimiser cannot cache.
          <img src={imageUrl} alt={product.name} className="size-full object-cover" />
        ) : (
          <div className="flex size-full items-center justify-center text-muted-foreground">
            <ImageOff className="size-5" />
          </div>
        )}
        {state !== 'out' && (
          <span className="absolute end-1 top-1 rounded-full bg-primary p-1 text-primary-foreground">
            {state === 'rule' ? <Minus className="size-3" /> : <Check className="size-3" />}
          </span>
        )}
      </div>

      <span className="block truncate font-mono text-xs text-foreground" title={product.code}>
        {product.code}
      </span>
      {distinctName(product.code, product.name) ? (
        <span className="block truncate text-xs text-muted-foreground" title={product.name}>
          {product.name}
        </span>
      ) : null}
      {product.price && (
        <span className="block truncate text-xs text-muted-foreground">{product.price}</span>
      )}
      {product.isDiscontinued && (
        <Badge variant="warning" className="w-fit text-xs">
          Discontinued
        </Badge>
      )}
    </button>
  );
}


export function ProductPickerDialog({
  open,
  onOpenChange,
  value,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: ProductSelection;
  onSave: (selection: ProductSelection) => void;
}) {
  const [draft, setDraft] = useState<ProductSelection>(value);
  const { data: ruleFacts } = useRuleFacts(['product']);
  const [tab, setTab] = useState('rule');
  const [ruleRefused, setRuleRefused] = useState(false);
  const {
    value: search,
    setValue: setSearch,
    debouncedValue: debounced,
    isSettling: debouncedSettling,
  } = useDebouncedSearch();

  // Remount-on-open keeps the draft honest when the dialog is reopened after a
  // cancel; without it the previous edit would silently persist.
  const [openedWith, setOpenedWith] = useState(value);
  // A fresh opening starts on By rule, with no leftover refusal and the saved selection.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setTab('rule');
      setRuleRefused(false);
      setDraft(value);
    }
  }
  if (open && openedWith !== value) {
    setOpenedWith(value);
    setDraft(value);
  }

  /**
   * Search on the SERVER, a page at a time.
   *
   * This used to load one page and filter it in the browser, so with 22,000
   * active products a search for a code that exists 998 times answered "no
   * products match". Debounced so typing does not fire a request per keystroke.
   */
  /** The category chip in effect, or '' for the whole catalogue. */
  const [categoryId, setCategoryId] = useState('');
  const { data: categories = [] } = useQuery({
    queryKey: ['dealer-kit', 'picker-categories'],
    queryFn: listPickerCategories,
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });

  const {
    data: pages,
    isLoading,
    isFetching,
    hasNextPage,
    fetchNextPage,
  } = useInfiniteQuery({
    queryKey: ['dealer-kit', 'picker-products', debounced, categoryId],
    queryFn: ({ pageParam = 0 }) =>
      listPickerProducts(debounced, pageParam as number, PICKER_PAGE_SIZE, categoryId),
    initialPageParam: 0,
    // A full page means there may be another; a short one means this is the end.
    getNextPageParam: (last: PickerProduct[], all) =>
      last.length < PICKER_PAGE_SIZE ? undefined : all.length,
    enabled: open,
  });

  const products = useMemo(() => (pages?.pages ?? []).flat(), [pages]);

  /**
   * Every product this dialog has loaded, by id.
   *
   * The chosen list has to keep naming a product after the user searches for
   * something else or pages past it - it is THEIR list, and it emptying itself
   * as they browse is the bug that makes a basket useless. The current page
   * alone cannot answer that, so what has been seen is remembered.
   */
  const knownById = useRef(new Map<string, PickerProduct>());
  products.forEach((product) => knownById.current.set(product.id, product));

  // One request per page of results, for the products on it.
  const productIds = products.map((product) => product.id);
  const { data: thumbnails = {} } = useQuery({
    queryKey: ['dealer-kit', 'picker-thumbnails', productIds.join(',')],
    queryFn: () => listProductThumbnails(productIds),
    enabled: open && productIds.length > 0,
    staleTime: 5 * 60 * 1000,
  });

  // Rule matches are resolved server-side after saving, so the live count here
  // reflects hand-picked products only. Saying so beats implying the rule has
  // already been applied.
  const matchedByRule = useMemo<string[]>(() => [], []);

  const members = useMemo(() => {
    const excluded = new Set(draft.excludedProductIds);
    const ordered = [...matchedByRule, ...draft.pinnedProductIds];
    return Array.from(new Set(ordered)).filter((id) => !excluded.has(id));
  }, [matchedByRule, draft.pinnedProductIds, draft.excludedProductIds]);

  // No client-side filtering: the server already answered this search.
  const visible = products;

  /**
   * Results grouped the way the catalogue is already organised.
   *
   * A flat list of 22,000 products is not browsable by anyone who does not
   * already know the code, and the person this ends up in front of is a
   * consumer. Grouping is over the LOADED pages only - the server decides which
   * products, this decides how they are stacked - so a group grows as more
   * pages are loaded rather than lying about how many there are.
   */
  const grouped = useMemo(() => {
    const byCategory = new Map<string, PickerProduct[]>();
    for (const product of visible) {
      const key = product.category || 'Uncategorised';
      const bucket = byCategory.get(key);
      if (bucket) bucket.push(product);
      else byCategory.set(key, [product]);
    }
    // Insertion order, which is the server's order: re-sorting here would make
    // the list jump around as later pages arrive.
    return Array.from(byCategory.entries());
  }, [visible]);

  /** Folded categories. Open by default - a picker that starts closed hides
      everything the user came for. */
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const stateOf = (product: PickerProduct): 'in' | 'out' | 'rule' => {
    if (draft.excludedProductIds.includes(product.id)) return 'out';
    if (draft.pinnedProductIds.includes(product.id)) return 'in';
    if (matchedByRule.includes(product.id)) return 'rule';
    return 'out';
  };

  /**
   * Take a product back out, from the chosen list rather than by finding it.
   *
   * Same arithmetic as un-toggling it in the results: drop the pin if that is
   * what put it in, and exclude it if the rule would otherwise keep pulling it
   * back. Kept separate because the chosen list can name a product that is not
   * on the current page at all, so there is no card to toggle.
   */
  const removeMember = (productId: string) => {
    setDraft((current) => {
      const pinned = new Set(current.pinnedProductIds);
      const excluded = new Set(current.excludedProductIds);
      pinned.delete(productId);
      if (matchedByRule.includes(productId)) excluded.add(productId);
      return {
        ...current,
        pinnedProductIds: [...pinned],
        excludedProductIds: [...excluded],
      };
    });
  };

  const toggle = (product: PickerProduct) => {
    setDraft((current) => {
      const pinned = new Set(current.pinnedProductIds);
      const excluded = new Set(current.excludedProductIds);
      const isMember = members.includes(product.id);

      if (isMember) {
        // Removing a member: drop the pin if that is what put it in, and
        // exclude it if the rule would otherwise keep pulling it back.
        pinned.delete(product.id);
        if (matchedByRule.includes(product.id)) excluded.add(product.id);
      } else {
        excluded.delete(product.id);
        if (!matchedByRule.includes(product.id)) pinned.add(product.id);
      }

      return {
        ...current,
        pinnedProductIds: [...pinned],
        excludedProductIds: [...excluded],
      };
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-5xl">
        <DialogHeader>
          <DialogTitle>Choose products</DialogTitle>
          <DialogDescription>
            Match products with a rule, pick them by hand, or do both. A rule keeps working as
            the catalogue changes; hand-picked products stay put.
          </DialogDescription>
        </DialogHeader>

        <Tabs value={tab} onValueChange={setTab}>
          <TabsList variant="default">
            <TabsTrigger value="rule">By rule</TabsTrigger>
            <TabsTrigger value="manual">By hand</TabsTrigger>
          </TabsList>

          <TabsContent value="rule" className="pt-3">
            <RuleBuilder
              sources={['product']}
              value={draft.conditions}
              onChange={(conditions) => setDraft((current) => ({ ...current, conditions }))}
            />
          </TabsContent>

          <TabsContent value="manual" className="pt-3">
            <ListSearchInput
              value={search}
              onChange={setSearch}
              isSettling={isSearchInFlight(debouncedSettling, isFetching, debounced)}
              placeholder="Search products"
              aria-label="Search products"
            />

            {/* Browse by category alongside the search box. Chips scroll rather
                than wrap into a wall: there are hundreds of categories, and a
                picker that pushes the product list off screen has traded one
                problem for another. */}
            {categories.length > 0 && (
              <div className="flex gap-1 overflow-x-auto pb-1" data-dk-category-chips>
                <Button
                  size="sm"
                  variant={categoryId === '' ? 'primary' : 'outline'}
                  className="shrink-0"
                  onClick={() => setCategoryId('')}
                >
                  All
                </Button>
                {categories.map((category) => (
                  <Button
                    key={category.id}
                    size="sm"
                    variant={categoryId === category.id ? 'primary' : 'outline'}
                    className="shrink-0"
                    data-dk-category-chip={category.id}
                    onClick={() => setCategoryId(category.id === categoryId ? '' : category.id)}
                  >
                    {category.name}
                  </Button>
                ))}
              </div>
            )}

            {/*
              Two panes: what there is, and what has been chosen.

              The chosen list used to be one truncated line of codes under the
              dialog, which is a receipt, not a basket - you could add a product
              and have no way to take it back out except finding it again in
              22,000. It is beside the results now, always visible, and every
              entry removes itself.
            */}
            <div className="flex flex-col gap-3 lg:flex-row">
              {/* h-, not max-h-: a Radix ScrollArea with only a max height never
                  scrolls - the content simply overflows and the rows below the
                  fold become unreachable. */}
              <ScrollArea className="h-96 min-w-0 flex-1">
                <div className="flex flex-col gap-4 pe-3">
                  {isLoading && (
                    <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
                      {[0, 1, 2, 3, 4, 5].map((row) => (
                        <Skeleton key={row} className="aspect-square w-full" />
                      ))}
                    </div>
                  )}
                  {!isLoading && visible.length === 0 && (
                    <p className="py-6 text-center text-sm text-muted-foreground">
                      No products match that search.
                    </p>
                  )}

                  {grouped.map(([category, items]) => (
                    <div key={category} data-dk-picker-group={category}>
                      <button
                        type="button"
                        onClick={() =>
                          setCollapsed((current) => ({
                            ...current,
                            [category]: !current[category],
                          }))
                        }
                        aria-expanded={!collapsed[category]}
                        className="mb-2 flex w-full items-center gap-1.5 text-start text-xs font-medium text-muted-foreground hover:text-foreground"
                      >
                        {collapsed[category] ? (
                          <ChevronRight className="size-3.5" />
                        ) : (
                          <ChevronDown className="size-3.5" />
                        )}
                        <span className="truncate">{category}</span>
                        <span className="shrink-0 font-normal">({items.length})</span>
                      </button>

                      {!collapsed[category] && (
                        <div className="grid grid-cols-[repeat(auto-fill,minmax(120px,1fr))] gap-2">
                          {items.map((product) => (
                            <ProductCard
                              key={product.id}
                              product={product}
                              imageUrl={thumbnails[product.id]}
                              state={stateOf(product)}
                              onToggle={() => toggle(product)}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  ))}

                  {hasNextPage && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={isFetching}
                      onClick={() => fetchNextPage()}
                    >
                      {isFetching ? (
                        <Loader2 className="size-4 animate-spin" aria-label="Loading" />
                      ) : (
                        'Load more products'
                      )}
                    </Button>
                  )}
                </div>
              </ScrollArea>

              <div className="flex w-full shrink-0 flex-col rounded-lg border border-border lg:w-64">
                <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                  <Package className="size-4 text-muted-foreground" aria-hidden />
                  <span className="text-sm font-medium" data-dk-chosen-count>
                    {members.length} chosen
                  </span>
                </div>
                <ScrollArea className="h-[21rem]">
                  {members.length === 0 ? (
                    <p className="p-3 text-xs text-muted-foreground">
                      Nothing chosen yet. Tap a product to add it.
                    </p>
                  ) : (
                    <ul className="flex flex-col divide-y divide-border">
                      {members.map((id) => {
                        const product = knownById.current.get(id);
                        return (
                          <li
                            key={id}
                            className="flex items-center gap-2 px-3 py-2"
                            data-dk-chosen={product?.code ?? id}
                          >
                            <div className="size-8 shrink-0 overflow-hidden rounded bg-muted">
                              {thumbnails[id] ? (
                                <img
                                  src={thumbnails[id]}
                                  alt=""
                                  className="size-full object-cover"
                                />
                              ) : (
                                <div className="flex size-full items-center justify-center text-muted-foreground">
                                  <ImageOff className="size-3" />
                                </div>
                              )}
                            </div>
                            <span className="min-w-0 flex-1">
                              <span
                                className="block truncate font-mono text-xs"
                                title={product?.code ?? ''}
                              >
                                {/* Falls back to nothing rather than the id: a
                                    uuid on a screen is banned, and a product
                                    loaded on a page the user has since left is
                                    still THEIR choice. */}
                                {product?.code ?? 'Chosen product'}
                              </span>
                              <span className="block truncate text-xs text-muted-foreground">
                                {product?.name ?? ''}
                              </span>
                            </span>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="size-6 shrink-0 p-0"
                              onClick={() => removeMember(id)}
                              title="Remove"
                              aria-label={`Remove ${product?.code ?? 'product'}`}
                            >
                              <X className="size-3.5" />
                            </Button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </ScrollArea>
              </div>
            </div>
          </TabsContent>
        </Tabs>

        {draft.excludedProductIds.length > 0 && (
          <p className="text-xs text-muted-foreground">
            {draft.excludedProductIds.length} product
            {draft.excludedProductIds.length === 1 ? '' : 's'} excluded from the rule.
          </p>
        )}

        {ruleRefused && ruleTreeHasBadNumber(draft.conditions, ruleFacts) && (
          <p role="alert" className="text-xs text-destructive">
            Fix the number in the rule before using these products.
          </p>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => {
              if (ruleTreeHasBadNumber(draft.conditions, ruleFacts)) {
                // Back to the rule, where the box carries its sentence.
                setTab('rule');
                setRuleRefused(true);
                return;
              }
              setRuleRefused(false);
              onSave(draft);
              onOpenChange(false);
            }}
          >
            Use these products
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
