'use client';

import { useMemo, useState } from 'react';

import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { useBrandSelectQuery } from '@/app/(protected)/master-data-management/shared/hooks/use-brand-select-query';
import {
  useContactBrandScopeMutations,
  useContactBrandScopeQuery,
} from '@/hooks/useContactBrandScope';

/**
 * Contact Profile -> Brands. The brands the chatbot and MCP may tell this contact about;
 * empty means every brand. One wholesale Save, like the cards around it.
 */
export default function ContactBrandScopeSection({ contactId }: { contactId: string }) {
  const { data, isLoading, isError, error } = useContactBrandScopeQuery(contactId);
  const brandsQuery = useBrandSelectQuery();
  const { save } = useContactBrandScopeMutations(contactId);

  // null = untouched: the picker shows what the server holds until the first edit.
  const [draft, setDraft] = useState<string[] | null>(null);
  const saved = useMemo(() => data?.brand_ids ?? [], [data]);
  const options = useMemo(
    () => (brandsQuery.data ?? []).map((b) => ({ value: b.id, label: b.brand_name })),
    [brandsQuery.data],
  );
  // A brand that no longer exists must not show as a raw id chip. Until the brand list loads
  // the saved ids are kept as they are, so Save never silently drops one.
  const known = useMemo(() => new Set(options.map((o) => o.value)), [options]);
  const value = (draft ?? saved).filter((id) => !brandsQuery.data || known.has(id));

  const isDirty =
    draft !== null && (draft.length !== saved.length || draft.some((id) => !saved.includes(id)));

  if (isLoading) {
    return (
      <div>
        <Skeleton className="h-5 w-24" />
        <Skeleton className="mt-3 h-9 w-full" />
      </div>
    );
  }
  if (isError) {
    return (
      <div>
        <p className="text-sm text-muted-foreground">Brands</p>
        <p className="text-sm text-destructive">{error?.message || 'Could not load the brands.'}</p>
      </div>
    );
  }

  return (
    <div className="grid gap-2">
      <Label htmlFor="contact-brand-scope">Brands</Label>
      <SearchableMultiSelect
        id="contact-brand-scope"
        value={value}
        onChange={setDraft}
        options={options}
        placeholder="All brands"
        emptyMessage="No brands found"
        clearable
        loadError={brandsQuery.error}
        onRetry={() => brandsQuery.refetch()}
        disabled={save.isPending}
      />
      <div>
        <Button
          type="button"
          size="sm"
          disabled={!isDirty || save.isPending}
          onClick={() =>
            save.mutate(value, {
              onSuccess: () => setDraft(null),
            })
          }
        >
          Save
        </Button>
      </div>
    </div>
  );
}
