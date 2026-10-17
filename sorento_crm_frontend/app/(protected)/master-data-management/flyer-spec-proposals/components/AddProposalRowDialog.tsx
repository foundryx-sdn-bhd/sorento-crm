'use client';

import { useEffect, useMemo, useState } from 'react';

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
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { readable } from '@/lib/spec-readable';
import type { SpecProposalValue } from '@/components/spec-proposals';

import { useApplicableSpecKeysQuery } from '../hooks/useFlyerSpecProposals';
import { fromDraft } from './ProposalValueEditor';
import { codeWithName } from '@/lib/product-display';

/**
 * A specification the flyer states in a way no rule caught, added to a product it
 * names (AC-G.4).
 *
 * The reader reads what the rules describe, and a flyer that spells a value out
 * in a sentence nobody wrote a rule for leaves a gap the reviewer can SEE on the
 * paper in front of them. Sending them to the product's own Specifications tab
 * for it is the round trip this whole screen exists to remove.
 *
 * **The picker offers the keys this product may carry and does not already have
 * a row for.** Applicability comes from the same `applies_when` gate derivation
 * uses (`getApplicableSpecKeys`, the picker on the product tab reads the same
 * call), and the keys already in this product's group are removed because adding
 * a second row for one of them is refused server-side - the answer there is to
 * edit the row the flyer proposed.
 *
 * Creating a NEW registry key is deliberately not offered here. A key is
 * vocabulary the ranker and the parser both hold, and minting one in the middle
 * of reviewing a flyer is how a catalogue ends up with two keys meaning the same
 * thing. The registry screen is where a key is created.
 */

export interface AddProposalRowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  productCode: string;
  productName: string;
  /** The keys this product's group already holds a row for, in any state. */
  existingKeys: readonly string[];
  saving?: boolean;
  onAdd: (input: { spec_key: string; value: SpecProposalValue }) => void;
}

const BOOLEAN_OPTIONS = [
  { value: 'true', label: 'Yes' },
  { value: 'false', label: 'No' },
];

export function AddProposalRowDialog({
  open,
  onOpenChange,
  productCode,
  productName,
  existingKeys,
  saving = false,
  onAdd,
}: AddProposalRowDialogProps) {
  const { data, isLoading, isError, refetch } = useApplicableSpecKeysQuery(
    productCode,
    open,
  );
  const [picked, setPicked] = useState('');
  const [draft, setDraft] = useState('');
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (open) return;
    setPicked('');
    setDraft('');
    setChecked(false);
  }, [open]);

  const offered = useMemo(() => {
    const already = new Set(existingKeys);
    return (data?.keys ?? []).filter(
      (key) => key.applicable && !already.has(key.spec_key),
    );
  }, [data, existingKeys]);

  const key = useMemo(
    () => offered.find((entry) => entry.spec_key === picked) ?? null,
    [offered, picked],
  );

  const value = key ? fromDraft(draft, key.data_type) : null;
  const numeric = key?.data_type === 'numeric';
  const numberError = numeric && checked ? numberFieldError(draft) : undefined;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Add a specification</DialogTitle>
          <DialogDescription>
            {codeWithName(productCode, productName, ' \u00b7 ')}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fsp-add-key">Specification</Label>
            {isError ? (
              <div
                className="flex flex-col items-start gap-2 rounded-md border border-destructive/50 px-3 py-2 text-sm text-destructive"
                data-testid="fsp-add-key-error"
              >
                <span>
                  The specifications this product can carry could not be loaded.
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => refetch()}
                  disabled={saving}
                >
                  Try again
                </Button>
              </div>
            ) : (
              <SearchableSelect
                id="fsp-add-key"
                value={picked}
                onChange={(next) => {
                  setPicked(next);
                  setDraft('');
                }}
                options={offered.map((entry) => ({
                  value: entry.spec_key,
                  label: entry.label || readable(entry.spec_key),
                }))}
                placeholder={
                  isLoading ? 'Loading specifications' : 'Search specifications'
                }
                emptyMessage={
                  isLoading
                    ? 'Loading specifications'
                    : 'Every specification this product can carry is already in this batch.'
                }
                disabled={saving}
                clearable
              />
            )}
          </div>

          {key && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="fsp-add-value">
                {key.label || readable(key.spec_key)}
              </Label>
              {key.data_type === 'boolean' ? (
                <SearchableSelect
                  id="fsp-add-value"
                  value={draft}
                  onChange={setDraft}
                  options={BOOLEAN_OPTIONS}
                  placeholder="Yes or No"
                  disabled={saving}
                />
              ) : key.allowed_values.length > 0 ? (
                <SearchableSelect
                  id="fsp-add-value"
                  value={draft}
                  onChange={setDraft}
                  options={key.allowed_values.map((option) => ({
                    value: option,
                    label: readable(option),
                  }))}
                  placeholder={`Pick a ${(key.label || key.spec_key).toLowerCase()}`}
                  disabled={saving}
                />
              ) : (
                <div className="relative">
                  {numeric ? (
                    <NumberInput
                      id="fsp-add-value"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      disabled={saving}
                      error={numberError}
                    />
                  ) : (
                    <Input
                      id="fsp-add-value"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      type="text"
                      disabled={saving}
                    />
                  )}
                  {key.unit && (
                    <span className="pointer-events-none absolute inset-y-0 end-2 flex items-center text-xs text-muted-foreground">
                      {key.unit}
                    </span>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            Cancel
          </Button>
          <Button
            disabled={!key || (!numeric && value === null) || saving}
            data-testid="fsp-add-row-submit"
            onClick={() => {
              if (!key) return;
              if (numeric) setChecked(true);
              if (value === null) return;
              onAdd({ spec_key: key.spec_key, value });
            }}
          >
            {saving ? 'Adding' : 'Add'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
