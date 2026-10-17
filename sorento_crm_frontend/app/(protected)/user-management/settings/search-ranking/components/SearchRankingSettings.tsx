'use client';

import { useEffect, useState } from 'react';
import { RiErrorWarningFill } from '@remixicon/react';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Skeleton } from '@/components/ui/skeleton';
import type { SpecSearchPolicyRow } from '@/app/(protected)/master-data-management/product-specifications/types/productSpec.types';
import { useSearchPolicyMutations } from '../../hooks/useSearchPolicyMutations';
import { useSearchPolicyQuery } from '../../hooks/useSearchPolicyQuery';

/**
 * Settings -> Search ranking (AC-C.1). Moved off Product Specifications so
 * changing "discontinued products should rank lower" is an operator action
 * instead of a deploy - each row is a merchandising question, not a scoring
 * function, so it reads as one.
 */
export function SearchRankingSettings() {
  const policyQuery = useSearchPolicyQuery();
  const { save } = useSearchPolicyMutations();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // Rows whose Save found a wrong number; their sentence then follows the typing.
  const [checkedKeys, setCheckedKeys] = useState<Set<string>>(() => new Set());

  // Seed a draft the first time a row is seen; never overwrite one the operator
  // is mid-edit, including across the refetch a save triggers.
  useEffect(() => {
    if (!policyQuery.data) return;
    setDrafts((current) => {
      let changed = false;
      const next = { ...current };
      for (const row of policyQuery.data) {
        if (!(row.policy_key in next)) {
          next[row.policy_key] = String(row.value);
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [policyQuery.data]);

  const doSave = (row: SpecSearchPolicyRow) => {
    const draft = drafts[row.policy_key] ?? String(row.value);
    if (numberFieldError(draft, { min: 0 })) {
      setCheckedKeys((current) => new Set(current).add(row.policy_key));
      return;
    }
    save.mutate({ policyKey: row.policy_key, value: Number(draft) });
  };

  if (policyQuery.isError) {
    return (
      <Alert variant="mono" icon="destructive">
        <AlertIcon>
          <RiErrorWarningFill />
        </AlertIcon>
        <AlertTitle>
          {policyQuery.error instanceof Error
            ? policyQuery.error.message
            : 'Search ranking settings could not be loaded.'}
        </AlertTitle>
      </Alert>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Search ranking</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {policyQuery.isLoading || !policyQuery.data ? (
          <div className="flex flex-col gap-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : policyQuery.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No ranking settings are configured yet.
          </p>
        ) : (
          policyQuery.data.map((row) => {
            const draft = drafts[row.policy_key] ?? String(row.value);
            // A blank or half-typed draft is a change too, so Save stays live and says what is wrong.
            const parsed = parseNumberDraft(draft);
            const dirty = parsed === null ? draft.trim() !== String(row.value) : parsed !== row.value;
            const saving = save.isPending && save.variables?.policyKey === row.policy_key;
            return (
              <div
                key={row.policy_key}
                className="flex flex-col gap-2 border-b pb-3 last:border-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                    {row.label}
                    {row.value !== row.default_value && (
                      <Badge variant="secondary" size="sm" appearance="light" shape="circle">
                        Changed from {row.default_value}
                      </Badge>
                    )}
                  </div>
                  <p className="mt-0.5 text-xs text-muted-foreground">{row.help_text}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <div className="w-28">
                    <NumberInput
                      step="0.5"
                      min="0"
                      className="tabular-nums"
                      value={draft}
                      onChange={(e) =>
                        setDrafts((current) => ({ ...current, [row.policy_key]: e.target.value }))
                      }
                      error={
                        checkedKeys.has(row.policy_key)
                          ? numberFieldError(draft, { min: 0 })
                          : undefined
                      }
                    />
                  </div>
                  <Button
                    size="sm"
                    variant={dirty ? 'primary' : 'outline'}
                    disabled={!dirty || saving}
                    onClick={() => doSave(row)}
                  >
                    {saving ? 'Saving…' : 'Save'}
                  </Button>
                </div>
              </div>
            );
          })
        )}
      </CardContent>
    </Card>
  );
}
