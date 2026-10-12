'use client';

import { useMemo, useState } from 'react';
import { ArrowRight, Archive, LoaderCircleIcon } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Skeleton } from '@/components/ui/skeleton';
import { ListSearchInput } from '@/components/common/ListSearchInput';
import { useDebouncedSearch } from '@/hooks/useDebouncedSearch';
import { useHasPermission } from '@/hooks/usePermissions';
import {
  useApplyFlyerReview,
  useFlyerReview,
  useRetireSeatCoverSpec,
} from '../hooks/useProductCompatibility';
import type {
  FlyerModel,
  FlyerSku,
  FlyerUnresolvedReason,
  SeatCoverSpecState,
} from '../types/productCompatibility.types';

const REASON: Record<FlyerUnresolvedReason, string> = {
  finish_not_printed: 'Finish not on the flyer',
  sub_model_not_printed: 'Code part printed only on another flyer code',
  unknown_segment: 'Code part not recognised',
  ambiguous: 'Matches two flyer codes',
  not_printed: 'No flyer code matches',
};

function StatusBadge({ sku }: { sku: FlyerSku }) {
  if (sku.status === 'ready') {
    return (
      <Badge variant="success" appearance="light" size="sm">
        Ready
      </Badge>
    );
  }
  if (sku.status === 'linked') {
    return (
      <Badge variant="primary" appearance="light" size="sm">
        Linked
      </Badge>
    );
  }
  if (sku.status === 'cover_missing') {
    return (
      <Badge variant="destructive" appearance="light" size="sm">
        Seat cover not in catalogue
      </Badge>
    );
  }
  return (
    <Badge variant="warning" appearance="light" size="sm">
      Unresolved
    </Badge>
  );
}

function SkuRow({
  sku,
  checked,
  canEdit,
  onToggle,
}: {
  sku: FlyerSku;
  checked: boolean;
  canEdit: boolean;
  onToggle: () => void;
}) {
  const unresolved = sku.status === 'unresolved';
  return (
    <div
      className={`flex flex-wrap items-center gap-x-3 gap-y-1 border-t px-3 py-2 text-sm ${
        unresolved ? 'bg-warning/5' : ''
      }`}
      data-testid={`sku-${sku.product_code}`}
    >
      <Checkbox
        checked={checked}
        onCheckedChange={onToggle}
        disabled={!canEdit || sku.status !== 'ready'}
        aria-label={`Link ${sku.product_code}`}
      />
      <div className="min-w-0 basis-56 grow sm:grow-0">
        <div
          className="truncate font-mono text-xs font-medium"
          title={sku.product_code}
        >
          {sku.product_code}
        </div>
        {sku.company_name || !sku.is_active ? (
          <div className="truncate text-xs text-muted-foreground">
            {[sku.company_name, sku.is_active ? null : 'Inactive']
              .filter(Boolean)
              .join(' / ')}
          </div>
        ) : null}
      </div>
      <div className="flex min-w-0 basis-72 grow items-center gap-1.5 text-xs">
        {sku.printed_code ? (
          <>
            <span
              className="truncate font-mono"
              title={`Flyer row ${sku.flyer_row}`}
            >
              {sku.printed_code}
            </span>
            <ArrowRight className="size-3 shrink-0 text-muted-foreground" />
            <span
              className="truncate font-mono"
              title={sku.cover_codes.join(', ')}
            >
              {sku.cover_codes.length ? sku.cover_codes.join(' + ') : '-'}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground">-</span>
        )}
      </div>
      <div className="flex basis-40 flex-wrap gap-1">
        {sku.extra_segments.map((seg) => (
          <span
            key={seg}
            className="rounded bg-muted px-1.5 font-mono text-2xs"
          >
            {seg}
          </span>
        ))}
      </div>
      <div className="ms-auto flex items-center gap-2">
        <StatusBadge sku={sku} />
        {unresolved && sku.reason ? (
          <span className="text-xs text-muted-foreground">
            {REASON[sku.reason]}
          </span>
        ) : null}
      </div>
    </div>
  );
}

function ModelCard({
  model,
  ticked,
  canEdit,
  onToggle,
  onToggleModel,
}: {
  model: FlyerModel;
  ticked: Set<string>;
  canEdit: boolean;
  onToggle: (id: string) => void;
  onToggleModel: (ids: string[]) => void;
}) {
  const readyIds = model.skus
    .filter((s) => s.status === 'ready')
    .map((s) => s.product_id);
  const allOn = readyIds.length > 0 && readyIds.every((id) => ticked.has(id));
  const unresolved = model.skus.filter((s) => s.status === 'unresolved').length;
  const linked = model.skus.filter((s) => s.status === 'linked').length;

  return (
    <Card className="overflow-hidden">
      <div className="space-y-2 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Checkbox
            checked={allOn}
            onCheckedChange={() => onToggleModel(readyIds)}
            disabled={!canEdit || readyIds.length === 0}
            aria-label={`Link every ready SKU of ${model.family_code}`}
          />
          <span className="font-medium">{model.family_code}</span>
          {model.skus.length === 0 ? (
            <Badge variant="destructive" appearance="light" size="sm">
              No SKU in the catalogue
            </Badge>
          ) : (
            <>
              <Badge variant="secondary" size="sm">
                {model.skus.length} SKU{model.skus.length === 1 ? '' : 's'}
              </Badge>
              {readyIds.length ? (
                <Badge variant="success" appearance="light" size="sm">
                  {readyIds.length} ready
                </Badge>
              ) : null}
              {linked ? (
                <Badge variant="primary" appearance="light" size="sm">
                  {linked} linked
                </Badge>
              ) : null}
              {unresolved ? (
                <Badge variant="warning" appearance="light" size="sm">
                  {unresolved} unresolved
                </Badge>
              ) : null}
            </>
          )}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {model.printed.map((p) => (
            <span
              key={p.wc_code}
              className="rounded-md border bg-muted/40 px-2 py-0.5 text-xs"
            >
              Row {p.flyer_row}{' '}
              <span className="font-mono font-medium">{p.wc_code}</span>{' '}
              <ArrowRight className="inline size-3 text-muted-foreground" />{' '}
              {p.covers.map((c, i) => (
                <span key={c.code}>
                  {i > 0 ? ' + ' : ''}
                  <span
                    className={`font-mono ${c.found ? '' : 'text-destructive line-through'}`}
                    title={c.found ? undefined : 'Not in the catalogue'}
                  >
                    {c.code}
                  </span>
                </span>
              ))}
            </span>
          ))}
        </div>
      </div>
      {model.skus.map((sku) => (
        <SkuRow
          key={sku.product_id}
          sku={sku}
          checked={ticked.has(sku.product_id)}
          canEdit={canEdit}
          onToggle={() => onToggle(sku.product_id)}
        />
      ))}
    </Card>
  );
}

function RetireCard({ spec }: { spec: SeatCoverSpecState }) {
  const canRetirePermission = useHasPermission(
    'master_data.spec_registry.edit',
  );
  const retire = useRetireSeatCoverSpec();
  const retiring = retire.targetId === spec.registry_id && retire.isPending;
  return (
    <Card className="flex flex-wrap items-center justify-between gap-3 p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Old Seat Cover spec</span>
        <Badge
          variant={spec.is_active ? 'warning' : 'secondary'}
          appearance="light"
          size="sm"
        >
          {spec.is_active ? 'Active' : 'Retired'}
        </Badge>
        <span className="text-muted-foreground">
          {spec.value_count} product{spec.value_count === 1 ? '' : 's'} hold a
          value
        </span>
      </div>
      {canRetirePermission ? (
        <Button
          variant="outline"
          size="sm"
          disabled={!spec.can_retire || retiring}
          onClick={() =>
            retire.run({ id: spec.registry_id, subject: 'Seat Cover spec' })
          }
        >
          {retiring ? (
            <LoaderCircleIcon className="size-4 animate-spin" />
          ) : (
            <Archive className="size-4" />
          )}
          Retire spec
        </Button>
      ) : null}
    </Card>
  );
}

export default function SeatCoverFlyerReview() {
  const { data: review, isLoading, isError, error } = useFlyerReview();
  const apply = useApplyFlyerReview();
  const canEdit = useHasPermission('master_data.products.edit');
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [hideLinked, setHideLinked] = useState(false);
  const {
    value: searchInput,
    setValue: setSearchInput,
    debouncedValue: search,
  } = useDebouncedSearch();

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return (review?.models ?? [])
      .map((m) => ({
        ...m,
        skus: hideLinked ? m.skus.filter((s) => s.status !== 'linked') : m.skus,
      }))
      .filter((m) => {
        if (!needle) return true;
        return (
          m.family_code.toLowerCase().includes(needle) ||
          m.printed.some(
            (p) =>
              p.wc_code.toLowerCase().includes(needle) ||
              p.covers.some((c) => c.code.toLowerCase().includes(needle)),
          ) ||
          m.skus.some((s) => s.product_code.toLowerCase().includes(needle))
        );
      });
  }, [review, search, hideLinked]);

  function toggle(id: string) {
    setTicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleModel(ids: string[]) {
    setTicked((prev) => {
      const next = new Set(prev);
      const allOn = ids.every((id) => next.has(id));
      for (const id of ids) {
        if (allOn) next.delete(id);
        else next.add(id);
      }
      return next;
    });
  }

  function onApply() {
    const ids = [...ticked];
    if (!ids.length) return;
    apply.mutate(ids, { onSuccess: () => setTicked(new Set()) });
  }

  if (isLoading) {
    return (
      <Card className="space-y-3 p-4">
        <Skeleton className="h-6 w-52" />
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-14 w-full" />
      </Card>
    );
  }

  if (isError || !review) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
        {error instanceof Error
          ? error.message
          : 'Failed to load the flyer review.'}
      </div>
    );
  }

  const c = review.counts;
  return (
    <div className="space-y-3">
      {review.seat_cover_spec ? (
        <RetireCard spec={review.seat_cover_spec} />
      ) : null}

      <Card className="flex flex-wrap items-center justify-between gap-3 p-3">
        <div className="text-sm text-muted-foreground">
          <span className="font-medium text-foreground">{c.models}</span>{' '}
          printed models,{' '}
          <span className="font-medium text-foreground">{c.ready}</span> ready,{' '}
          <span className="font-medium text-foreground">{c.linked}</span>{' '}
          linked,{' '}
          <span className="font-medium text-foreground">{c.unresolved}</span>{' '}
          unresolved
          {c.models_missing ? `, ${c.models_missing} with no SKU` : ''}
          {c.covers_missing
            ? `, ${c.covers_missing} seat covers not in the catalogue`
            : ''}
          .
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <ListSearchInput
            value={searchInput}
            onChange={setSearchInput}
            placeholder="Search model or SKU"
            className="w-56"
          />
          <Button variant="outline" onClick={() => setHideLinked((v) => !v)}>
            {hideLinked ? 'Show linked' : 'Hide linked'}
          </Button>
        </div>
      </Card>

      {visible.length === 0 ? (
        <Card className="px-6 py-10 text-center text-sm text-muted-foreground">
          No models match that search.
        </Card>
      ) : null}

      {visible.map((model) => (
        <ModelCard
          key={model.family_code}
          model={model}
          ticked={ticked}
          canEdit={canEdit}
          onToggle={toggle}
          onToggleModel={toggleModel}
        />
      ))}

      {canEdit && ticked.size > 0 ? (
        <div className="sticky bottom-4 z-10">
          <Card className="flex flex-wrap items-center justify-between gap-3 p-3 shadow-lg">
            <span className="text-sm">
              <span className="font-medium">{ticked.size}</span> SKU
              {ticked.size === 1 ? '' : 's'} ticked
            </span>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setTicked(new Set())}>
                Clear
              </Button>
              <Button onClick={onApply} disabled={apply.isPending}>
                {apply.isPending
                  ? 'Linking...'
                  : `Link ${ticked.size} SKU${ticked.size === 1 ? '' : 's'}`}
              </Button>
            </div>
          </Card>
        </div>
      ) : null}
    </div>
  );
}
