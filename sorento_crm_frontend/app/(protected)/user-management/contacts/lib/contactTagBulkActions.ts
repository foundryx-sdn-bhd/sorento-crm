import { Tag, TagsIcon, X } from 'lucide-react';

/**
 * CONTACT-TAGS: the two bulk actions the contacts list adds ("Add tag (n)" / "Remove tag (n)"),
 * kept apart from the list so the bulk-action registry move can pick them up whole.
 */
export function contactTagBulkActions({
  count,
  canEdit,
  onAdd,
  onRemove,
  onApply,
}: {
  count: number;
  canEdit: boolean;
  onAdd: () => void;
  onRemove: () => void;
  onApply: () => void;
}) {
  if (!canEdit) return [];
  return [
    { key: 'tag-add', label: `Add tag (${count})`, icon: Tag, onClick: onAdd },
    {
      key: 'tag-remove',
      label: `Remove tag (${count})`,
      icon: X,
      onClick: onRemove,
    },
    { key: 'tag-apply', label: `Apply tag settings (${count})`, icon: TagsIcon, onClick: onApply },
  ];
}
