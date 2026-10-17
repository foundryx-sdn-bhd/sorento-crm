'use client';
import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import KeywordChipInput from './KeywordChipInput';
import { useCreateOption, useUpdateOption } from '../hooks/useLookupSets';
import type { LookupKeyword, LookupOption } from '../types/lookup.types';

export default function OptionFormDialog({
  open, onOpenChange, setId, editing,
}: {
  open: boolean; onOpenChange: (o: boolean) => void;
  setId: string; editing?: LookupOption | null;
}) {
  const create = useCreateOption(setId);
  const update = useUpdateOption(setId);
  const [value, setValue] = useState('');
  const [label, setLabel] = useState('');
  const [sortOrder, setSortOrder] = useState<number | null>(0);
  const [checked, setChecked] = useState(false);
  const [isActive, setIsActive] = useState(true);
  const [description, setDescription] = useState('');
  const [keywords, setKeywords] = useState<LookupKeyword[]>([]);

  useEffect(() => {
    if (editing) {
      setValue(editing.value); setLabel(editing.label);
      setSortOrder(editing.sort_order); setIsActive(editing.is_active);
      setChecked(false);
      setDescription(editing.description ?? '');
      setKeywords(editing.keywords.map((k) => ({ keyword: k.keyword, locale: k.locale ?? null })));
    } else {
      setValue(''); setLabel(''); setSortOrder(0); setIsActive(true);
      setDescription(''); setKeywords([]); setChecked(false);
    }
  }, [editing, open]);

  const sortOrderError = checked ? numberFieldError(sortOrder, { integer: true }) : undefined;

  async function submit() {
    setChecked(true);
    if (sortOrder === null || numberFieldError(sortOrder, { integer: true })) return;
    const payload = { value, label, sort_order: sortOrder, is_active: isActive, description, keywords };
    if (editing) await update.mutateAsync({ id: editing.id, data: payload });
    else await create.mutateAsync(payload);
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? 'Edit option' : 'Add option'}</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div><Label>Value (canonical)</Label><Input value={value} onChange={(e) => setValue(e.target.value)} /></div>
          <div><Label>Label (display)</Label><Input value={label} onChange={(e) => setLabel(e.target.value)} /></div>
          <div className="grid grid-cols-2 gap-3">
            <div><Label>Sort order</Label>
              <NumberInput value={sortOrder} onValueChange={setSortOrder} error={sortOrderError} /></div>
            <div className="flex items-end gap-2">
              <Switch checked={isActive} onCheckedChange={setIsActive} />
              <Label>Active</Label>
            </div>
          </div>
          <div><Label>Description</Label><Textarea value={description} onChange={(e) => setDescription(e.target.value)} /></div>
          <div><Label>Keywords (synonyms for n8n / resolve)</Label>
            <KeywordChipInput value={keywords} onChange={setKeywords} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={submit} disabled={!value || !label}>{editing ? 'Save' : 'Add'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
