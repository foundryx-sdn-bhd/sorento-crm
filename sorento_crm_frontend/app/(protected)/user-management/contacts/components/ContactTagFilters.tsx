'use client';

import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { useContactTagOptions } from '../../contact-tags/hooks/useContactTags';

interface Props {
  tagIds: string[];
  noTags: boolean;
  onTagIdsChange: (ids: string[]) => void;
  onNoTagsChange: (noTags: boolean) => void;
}

/** CONTACT-TAGS: the contacts list's Tag filter (any-of) and its "No tags" checkbox. */
export default function ContactTagFilters({
  tagIds,
  noTags,
  onTagIdsChange,
  onNoTagsChange,
}: Props) {
  const { data: options = [], isError, refetch } = useContactTagOptions();
  return (
    <>
      <SearchableMultiSelect
        value={tagIds}
        onChange={onTagIdsChange}
        options={options}
        placeholder="Tag"
        emptyMessage="No tags."
        loadError={isError ? new Error('Failed to load tags') : undefined}
        onRetry={() => void refetch()}
        className="w-56"
      />
      <Checkbox
        id="contacts-no-tags"
        checked={noTags}
        onCheckedChange={(v) => onNoTagsChange(v === true)}
      />
      <Label htmlFor="contacts-no-tags" className="text-sm font-normal">
        No tags
      </Label>
    </>
  );
}
