'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
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
import { useContactTagMutations } from '../hooks/useContactTags';
import type { ContactTag } from '../types/contactTag.types';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Null creates; a tag edits it. */
  tag: ContactTag | null;
}

const EMPTY = { name: '', description: '', is_active: true, sort_order: '0' };

export default function ContactTagFormDialog({
  open,
  onOpenChange,
  tag,
}: Props) {
  const [form, setForm] = useState(EMPTY);
  const { create, update } = useContactTagMutations(() => onOpenChange(false));

  useEffect(() => {
    if (!open) return;
    setForm(
      tag
        ? {
            name: tag.name,
            description: tag.description ?? '',
            is_active: tag.is_active,
            sort_order: String(tag.sort_order),
          }
        : EMPTY,
    );
  }, [open, tag]);

  const sort = Number(form.sort_order);
  const invalid =
    !form.name.trim() ||
    form.sort_order.trim() === '' ||
    !Number.isInteger(sort);
  const busy = create.isPending || update.isPending;

  const save = () => {
    const body = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      is_active: form.is_active,
      sort_order: sort,
    };
    if (tag) update.mutate({ id: tag.id, body });
    else create.mutate(body);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {tag ? 'Edit contact tag' : 'Add contact tag'}
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="contact-tag-name">Name</Label>
              <Input
                id="contact-tag-name"
                value={form.name}
                maxLength={100}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value }))
                }
                placeholder="e.g. Dealer"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="contact-tag-description">
                Description (optional)
              </Label>
              <Input
                id="contact-tag-description"
                value={form.description}
                onChange={(e) =>
                  setForm((f) => ({ ...f, description: e.target.value }))
                }
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="contact-tag-sort">Sort order</Label>
              <Input
                id="contact-tag-sort"
                type="number"
                value={form.sort_order}
                onChange={(e) =>
                  setForm((f) => ({ ...f, sort_order: e.target.value }))
                }
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                id="contact-tag-active"
                checked={form.is_active}
                onCheckedChange={(v) =>
                  setForm((f) => ({ ...f, is_active: v === true }))
                }
              />
              <Label htmlFor="contact-tag-active">Active</Label>
            </div>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={busy || invalid}>
            {tag ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
