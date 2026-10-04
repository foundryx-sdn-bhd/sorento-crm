'use client';

import * as React from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { useProjectParties } from '../../_shared/hooks/useProjects';
import type {
  InformantSource,
  LeadInformantBody,
} from '../../_shared/types/leadAcceptance.types';
import { INFORMANT_SOURCE_OPTIONS } from './acceptance';

export type InformantDraft = {
  informant_source: string;
  informant_ref: string;
  informant_party_id: string;
  informant_contact_name: string;
};

export const EMPTY_INFORMANT: InformantDraft = {
  informant_source: '',
  informant_ref: '',
  informant_party_id: '',
  informant_contact_name: '',
};

/** Trims the draft into the body the API takes, nulling anything left blank. */
export function informantBody(draft: InformantDraft): LeadInformantBody {
  return {
    informant_source: (draft.informant_source || null) as InformantSource | null,
    informant_ref: draft.informant_ref.trim() || null,
    informant_party_id: draft.informant_party_id || null,
    informant_contact_name: draft.informant_contact_name.trim() || null,
  };
}

/**
 * Who told us. Rendered inside its own tinted block wherever it appears, because the
 * field next to it is the BUYER and the two must never be read as the same thing: an
 * informant is a data source and never issues a purchase order.
 *
 * A firm is optional. A named person with no firm on record is the common case.
 */
export function InformantFieldset({
  value,
  onChange,
  idPrefix = 'informant',
}: {
  value: InformantDraft;
  onChange: (next: InformantDraft) => void;
  idPrefix?: string;
}) {
  const parties = useProjectParties({ limit: 200 });
  const partyOptions = (parties.data?.data ?? []).map((party) => ({
    value: party.id,
    label: party.name,
    description: party.party_type.replace(/_/g, ' '),
  }));

  const set = (patch: Partial<InformantDraft>) => onChange({ ...value, ...patch });

  return (
    <div className="space-y-3 rounded-lg border border-border bg-muted/40 p-3">
      <p className="text-sm font-medium">Who told us</p>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-source`}>Source</Label>
          <SearchableSelect
            id={`${idPrefix}-source`}
            value={value.informant_source}
            onChange={(next) => set({ informant_source: next })}
            clearable
            options={INFORMANT_SOURCE_OPTIONS.map((option) => ({
              value: option.value,
              label: option.label,
            }))}
            placeholder="-"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-ref`}>Their reference</Label>
          <Input
            id={`${idPrefix}-ref`}
            value={value.informant_ref}
            onChange={(event) => set({ informant_ref: event.target.value })}
            placeholder="e.g. BCI job 1234567"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-party`}>Firm</Label>
          <SearchableSelect
            id={`${idPrefix}-party`}
            value={value.informant_party_id}
            onChange={(next) => set({ informant_party_id: next })}
            clearable
            loadError={parties.error}
            onRetry={() => void parties.refetch()}
            options={partyOptions}
            placeholder="Often none"
            emptyMessage="No match. Add the firm under Parties"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-contact`}>Contact name</Label>
          <Input
            id={`${idPrefix}-contact`}
            value={value.informant_contact_name}
            onChange={(event) => set({ informant_contact_name: event.target.value })}
            placeholder="e.g. Lim, QS"
          />
        </div>
      </div>
    </div>
  );
}
