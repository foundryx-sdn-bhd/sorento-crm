'use client';

import { useEffect, useState } from 'react';
import { LoaderCircle } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useCategoryOptions } from '../../hooks/useScmOptions';
import { CADENCE_OPTIONS, CURRENCY_OPTIONS } from '../lib/marketLabels';
import type {
  MarketCadence,
  MarketResearchTopic,
  MarketResearchTopicWrite,
} from '../types/market.types';

/** A labelled control with helper text + inline error. */
function Field({
  label,
  help,
  error,
  children,
}: {
  label: string;
  help?: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label className="mb-1 block">{label}</Label>
      {children}
      {help ? <p className="mt-1 text-2xs leading-snug text-muted-foreground">{help}</p> : null}
      {error ? <p className="mt-1 text-2xs text-destructive">{error}</p> : null}
    </div>
  );
}

interface FormState {
  label: string;
  category_ref: string;
  currency: string;
  search_prompt: string;
  cadence: MarketCadence;
  is_active: boolean;
}

const EMPTY_FORM: FormState = {
  label: '',
  category_ref: '',
  currency: '',
  search_prompt: '',
  cadence: 'weekly',
  is_active: true,
};

function hydrate(topic: MarketResearchTopic): FormState {
  return {
    label: topic.label,
    category_ref: topic.category_ref ?? '',
    currency: topic.currency ?? '',
    search_prompt: topic.search_prompt,
    cadence: topic.cadence,
    is_active: topic.is_active,
  };
}

export function AddEditTopicModal({
  open,
  onOpenChange,
  mode,
  initial,
  onSubmit,
  isSubmitting,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mode: 'create' | 'edit';
  /** The row being edited (edit mode), else null. */
  initial: MarketResearchTopic | null;
  onSubmit: (body: MarketResearchTopicWrite) => Promise<void>;
  isSubmitting: boolean;
}) {
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);

  const {
    data: categoryOptions,
    isLoading: categoriesLoading,
    error: categoriesError,
    refetch: refetchCategories,
  } = useCategoryOptions();

  useEffect(() => {
    if (!open) return;
    setErrors({});
    setFormError(null);
    setForm(initial ? hydrate(initial) : EMPTY_FORM);
  }, [open, initial]);

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  // Ensure an edited topic's stored category_ref stays selectable even if it is
  // not one of the live category options (mock seeds use readable codes).
  const categoryChoices =
    form.category_ref && !categoryOptions?.some((o) => o.value === form.category_ref)
      ? [{ value: form.category_ref, label: form.category_ref }, ...(categoryOptions ?? [])]
      : categoryOptions ?? [];

  const validate = (): MarketResearchTopicWrite | null => {
    const errs: Record<string, string> = {};
    if (!form.label.trim()) errs.label = 'Give the topic a short label.';
    if (!form.search_prompt.trim()) {
      errs.search_prompt = 'Enter the search prompt that drives the web search.';
    }
    setErrors(errs);
    if (Object.keys(errs).length) {
      setFormError('Please fix the highlighted fields before saving.');
      return null;
    }
    setFormError(null);
    return {
      label: form.label.trim(),
      category_ref: form.category_ref || null,
      currency: form.currency || null,
      search_prompt: form.search_prompt.trim(),
      cadence: form.cadence,
      is_active: form.is_active,
    };
  };

  const submit = async () => {
    const body = validate();
    if (!body) return;
    await onSubmit(body);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>
            {mode === 'edit' ? 'Edit research topic' : 'Add research topic'}
          </DialogTitle>
        </DialogHeader>

        <DialogBody className="space-y-5">
          {formError ? (
            <Alert variant="destructive">
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}

          <Field
            label="Label"
            help="A short name for this topic, shown on captured signals."
            error={errors.label}
          >
            <Input
              value={form.label}
              onChange={(e) => set('label', e.target.value)}
              placeholder="e.g. Ceramic tile FX exposure"
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field
              label="Category"
              help="Optional. Used to match resulting signals to recommendations."
            >
              <SearchableSelect
                value={form.category_ref}
                onChange={(v) => set('category_ref', v)}
                options={categoryChoices}
                loadError={categoriesError}
                onRetry={() => void refetchCategories()}
                disabled={categoriesLoading}
                placeholder={categoriesLoading ? 'Loading categories…' : 'Any category'}
                emptyMessage="No categories found."
              />
            </Field>
            <Field label="Currency" help="Optional. The currency this topic's prices are quoted in.">
              <SearchableSelect
                value={form.currency}
                onChange={(v) => set('currency', v)}
                options={CURRENCY_OPTIONS}
                placeholder="Any currency"
              />
            </Field>
          </div>

          <Field
            label="Search prompt"
            help="The free-form text that drives the actual web search. Be specific about the index, region, and timeframe."
            error={errors.search_prompt}
          >
            <Textarea
              value={form.search_prompt}
              onChange={(e) => set('search_prompt', e.target.value)}
              rows={4}
              placeholder="e.g. USD/MYR exchange rate trend and its impact on imported ceramic tile landed cost over the last 30 days"
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="Cadence" help="How often the research job re-runs this topic.">
              <SearchableSelect
                value={form.cadence}
                onChange={(v) => set('cadence', v as MarketCadence)}
                options={CADENCE_OPTIONS}
              />
            </Field>
            <Field
              label="Active"
              help="Inactive topics are skipped by the research job (scheduled and manual runs)."
            >
              <div className="flex h-10 items-center">
                <Switch checked={form.is_active} onCheckedChange={(v) => set('is_active', v)} />
                <span className="ms-2 text-sm text-muted-foreground">
                  {form.is_active ? 'Active' : 'Inactive'}
                </span>
              </div>
            </Field>
          </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={isSubmitting}>
            {isSubmitting ? <LoaderCircle className="size-4 animate-spin" /> : null}
            {mode === 'edit' ? 'Save changes' : 'Add topic'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
