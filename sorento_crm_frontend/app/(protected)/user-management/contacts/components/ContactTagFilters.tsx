'use client';

import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import type { TagSettingsFilter } from '../lib/listQuery';
import { useContactTagOptions } from '../../contact-tags/hooks/useContactTags';

interface Props {
  tagIds: string[];
  onTagIdsChange: (ids: string[]) => void;
  tagSettings: TagSettingsFilter | null;
  onTagSettingsChange: (value: TagSettingsFilter | null) => void;
}

/** CONTACT-TAGS: the contacts list's Tag filter (any-of) and the Tag settings select. */
export default function ContactTagFilters({
  tagIds,
  onTagIdsChange,
  tagSettings,
  onTagSettingsChange,
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
      <SearchableSelect
        value={tagSettings ?? 'any'}
        onChange={(v) => onTagSettingsChange(v === 'matches' || v === 'differs' ? v : null)}
        options={[
          { value: 'any', label: 'Tag settings: Any' },
          { value: 'matches', label: 'Tag settings: Matches' },
          { value: 'differs', label: 'Tag settings: Differs' },
        ]}
        placeholder="Tag settings"
        triggerClassName="w-52"
      />
    </>
  );
}
