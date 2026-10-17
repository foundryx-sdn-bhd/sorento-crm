'use client';

/**
 * Modal for creating a new tag template.
 *
 * Fields: name, family (select from the 7 families), print size width_mm
 * and height_mm. Creates with an empty layers array.
 */

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from '@/lib/toast';
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
import { numberFieldError, parseNumberDraft } from '@/lib/number-field';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import {
  DEFAULT_TAG_SIZE,
  TAG_TEMPLATE_FAMILIES,
  type TagTemplateFamily,
} from '@/lib/dealer-kit/tag-template-types';
import { createTemplate } from '../../services/tagTemplateService';

interface TagTemplateDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: () => void;
}

export function TagTemplateDialog({
  open,
  onOpenChange,
  onCreated,
}: TagTemplateDialogProps) {
  const router = useRouter();

  const [name, setName] = useState('');
  const [family, setFamily] = useState<TagTemplateFamily>('ala_carte');
  const [widthMm, setWidthMm] = useState<number | null>(DEFAULT_TAG_SIZE.width_mm);
  const [heightMm, setHeightMm] = useState<number | null>(DEFAULT_TAG_SIZE.height_mm);
  const [checked, setChecked] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setName('');
      setFamily('ala_carte');
      setWidthMm(DEFAULT_TAG_SIZE.width_mm);
      setHeightMm(DEFAULT_TAG_SIZE.height_mm);
      setError(null);
      setChecked(false);
    }
  }, [open]);

  const canSubmit = name.trim().length > 0 && !saving;
  const widthError = checked ? numberFieldError(widthMm, { min: 10 }) : undefined;
  const heightError = checked ? numberFieldError(heightMm, { min: 10 }) : undefined;

  const submit = async () => {
    setError(null);
    setChecked(true);
    if (numberFieldError(widthMm, { min: 10 }) || numberFieldError(heightMm, { min: 10 })) return;
    setSaving(true);
    try {
      const template = await createTemplate({
        name: name.trim(),
        family,
        print_size: {
          width_mm: parseNumberDraft(widthMm) as number,
          height_mm: parseNumberDraft(heightMm) as number,
        },
      });
      toast.success(`Created template "${template.name}"`);
      onCreated?.();
      onOpenChange(false);
      router.push(`/dealer-kit/tag-templates/${template.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create the template');
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New tag template</DialogTitle>
          <DialogDescription>
            Define the template name, product family and print dimensions.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4 py-2">
          {/* Name */}
          <div className="flex flex-col gap-2">
            <Label htmlFor="tt-name">Name</Label>
            <Input
              id="tt-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Sink Combo Tag A4"
              autoFocus
            />
          </div>

          {/* Family */}
          <div className="flex flex-col gap-2">
            <Label>Family</Label>
            <SearchableSelect
              value={family}
              onChange={(v: string) => setFamily(v as TagTemplateFamily)}
              options={TAG_TEMPLATE_FAMILIES}
            />
          </div>

          {/* Print size */}
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-2">
              <Label htmlFor="tt-width">Width (mm)</Label>
              <NumberInput
                id="tt-width"
                value={widthMm}
                min={10}
                onValueChange={setWidthMm}
                error={widthError}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="tt-height">Height (mm)</Label>
              <NumberInput
                id="tt-height"
                value={heightMm}
                min={10}
                onValueChange={setHeightMm}
                error={heightError}
              />
            </div>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={saving}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {saving ? 'Creating...' : 'Create template'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
