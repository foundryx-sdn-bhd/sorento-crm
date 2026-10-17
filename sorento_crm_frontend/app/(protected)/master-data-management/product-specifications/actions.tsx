'use client';

/**
 * The Product Specifications record action set (D15): Try in chatbot, links to the
 * lists the chatbot also reads (Attachment Types, Lookup Sets), and Delete.
 *
 * The gear (and the registry grid's row "..." menu, `SpecKeyRowActions`) is
 * ALWAYS present. Delete itself is left OUT of the array entirely on a built-in
 * specification (it ships with the product and would simply reappear on the next
 * deploy, so the backend refuses it too) and without `master_data.spec_registry.delete`
 * (AC-S3.8) - a disabled row with no reason attached reads as broken, and there is no
 * explanation to attach per the no-explanation rule. One hook, two surfaces: the
 * record page's gear renders it inline, next to Save/Cancel; the row menu renders the
 * same array, with the countdown in a toast instead - a row has nowhere to put one.
 */

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Bot, Files, ListTree, Trash2 } from 'lucide-react';
import type { RecordAction, RecordActionSet } from '@/components/common/recordActions';
import { RowActionsMenu } from '@/components/common/RowActionsMenu';
import { useHasPermission } from '@/hooks/usePermissions';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { SPEC_REGISTRY_QUERY_KEY } from './hooks/useSpecRegistryQuery';
import { TryInChatbotDialog } from './components/TryInChatbotDialog';
import type { SpecRegistryKey } from './types/productSpec.types';

export interface UseSpecKeyActionsOptions {
  onDeleted?: () => void;
  /**
   * Where the countdown goes: `inline` hands it back as `pending` for the record
   * card's primary area; `toast` (the list row) puts it over the grid instead.
   */
  surface?: 'inline' | 'toast';
}

export function useSpecKeyActions(
  specKey: SpecRegistryKey | undefined | null,
  { onDeleted, surface = 'inline' }: UseSpecKeyActionsOptions = {},
): RecordActionSet {
  const router = useRouter();
  const canDelete = useHasPermission('master_data.spec_registry.delete');
  const canTry = useHasPermission('master_data.spec_registry.edit');
  const canOpenAttachmentTypes = useHasPermission('resource.attachment_types.view');
  const canOpenLookupSets = useHasPermission('master_data.lookup_sets.view');
  const [tryOpen, setTryOpen] = useState(false);

  // Delete asks nothing (D7): the countdown takes the primary button's place on the
  // record card, and Cancel is the way back.
  const deletion = useDeferredAction({
    actionKey: 'spec_key.delete',
    entityType: 'spec_key',
    entityId: specKey?.spec_key,
    verb: 'Deleting',
    subject: specKey?.label ?? '',
    surface,
    watchFromMount: surface === 'inline',
    successMessage: 'Specification deleted',
    invalidateKeys: [SPEC_REGISTRY_QUERY_KEY],
    onCommitted: onDeleted,
  });

  // A built-in specification carries no Delete item at all (AC-S3.8): it ships with
  // the product and would just reappear on the next deploy, so there is nothing this
  // action could honestly offer.
  const canDeleteThis = !!specKey && specKey.source === 'user' && canDelete;
  const secondary: RecordAction[] = [
    ...(canTry
      ? [
          {
            key: 'spec_key.try_chatbot',
            label: 'Try in chatbot',
            icon: Bot,
            run: () => setTryOpen(true),
          },
        ]
      : []),
    ...(canOpenAttachmentTypes
      ? [
          {
            key: 'spec_key.open_attachment_types',
            label: 'Attachment Types',
            icon: Files,
            run: () => router.push('/resource-management/attachment-types'),
          },
        ]
      : []),
    ...(canOpenLookupSets
      ? [
          {
            key: 'spec_key.open_lookup_sets',
            label: 'Lookup Sets',
            icon: ListTree,
            run: () => router.push('/master-data-management/lookup-sets'),
          },
        ]
      : []),
  ];
  const actions: RecordAction[] = canDeleteThis
    ? [
        ...secondary,
        {
          key: 'spec_key.delete',
          label: 'Delete specification',
          icon: Trash2,
          kind: 'destructive',
          disabled: deletion.isPending,
          run: deletion.start,
        },
      ]
    : secondary;

  const dialogs = canTry ? <TryInChatbotDialog open={tryOpen} onOpenChange={setTryOpen} /> : null;

  return { actions, dialogs, pending: deletion.countdown };
}

/** The registry grid row's "..." cell: Delete only. The record page's gear carries the
 *  try-and-link items too, which belong to one open record rather than to a list row. */
export function SpecKeyRowActions({ specKey }: { specKey: SpecRegistryKey }) {
  const { actions } = useSpecKeyActions(specKey, { surface: 'toast' });

  return (
    <RowActionsMenu
      actions={actions.filter((action) => action.kind === 'destructive')}
      ariaLabel="specification"
    />
  );
}
