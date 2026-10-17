'use client';

import { SearchableSelect } from '@/components/common/SearchableSelect';
import { searchProjectsForLink } from '@/app/(protected)/complaint-management/complaints/services/complaintService';

interface ProjectLinkSelectProps {
  value: string | null | undefined;
  onChange: (next: string | null) => void;
  /** Saved link's `CODE - Title`, so an edit page shows the label before any search. */
  selectedLabel?: string | null;
  /** A suggestion from free text; only used while nothing is linked. */
  initialQuery?: string;
  disabled?: boolean;
  id?: string;
}

/** The staff "Project (optional)" picker: server-searched, clearable. */
export function ProjectLinkSelect({
  value,
  onChange,
  selectedLabel,
  initialQuery,
  disabled,
  id,
}: ProjectLinkSelectProps) {
  return (
    <SearchableSelect
      id={id}
      aria-label="Project"
      value={value || ''}
      onChange={(next) => onChange(next || null)}
      fetchOptions={(query, pageIndex) => searchProjectsForLink(query, pageIndex + 1)}
      paginated
      pageSize={25}
      clearable
      disabled={disabled}
      initialQuery={initialQuery}
      selectedOption={
        value && selectedLabel
          ? { value, label: selectedLabel }
          : undefined
      }
      placeholder="Search a project by code or title"
      emptyMessage="No project matches that."
    />
  );
}
