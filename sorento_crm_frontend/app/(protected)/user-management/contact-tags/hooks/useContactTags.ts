import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import { toast } from '@/lib/toast';
import type { ListPagerParams } from '@/hooks/useListPager';
import {
  applyContactTagSettings,
  applyTagSettings,
  bulkTagContacts,
  createContactTag,
  getContactTag,
  getContactTags,
  updateContactTag,
} from '../services/contactTagService';
import {
  contactTagsListQueryKey,
  fetchContactTagsPage,
} from '../lib/listQuery';
import type {
  BulkTagInput,
  ContactTagInput,
  ContactTagListResponse,
} from '../types/contactTag.types';

export function useContactTagsQuery(params: ListPagerParams) {
  return useQuery({
    ...LIST_QUERY_OPTIONS,
    queryKey: contactTagsListQueryKey(params),
    queryFn: () =>
      fetchContactTagsPage(params) as Promise<ContactTagListResponse>,
    retry: 1,
  });
}

export function useContactTagQuery(id: string) {
  return useQuery({
    queryKey: ['contact-tag', id],
    queryFn: () => getContactTag(id),
    enabled: !!id,
  });
}

/** Every active tag as picker options (the catalog is small; the API caps a page at 1000). */
export function useContactTagOptions(enabled = true) {
  return useQuery({
    queryKey: ['contact-tag-options'],
    queryFn: async () => {
      const page = await getContactTags(
        {
          pageIndex: 0,
          pageSize: 1000,
          sorting: [{ id: 'sort_order', desc: false }],
          searchQuery: '',
        },
        { is_active: 'true' },
      );
      return page.data.map((t) => ({ value: t.id, label: t.name }));
    },
    enabled,
    staleTime: 60 * 1000,
  });
}

/** Create and update share the success path: refetch every tag list and picker, toast. */
export function useContactTagMutations(onSaved?: () => void) {
  const queryClient = useQueryClient();
  const onSuccess = (message: string) => {
    queryClient.invalidateQueries({ queryKey: ['contact-tags'] });
    queryClient.invalidateQueries({ queryKey: ['contact-tag'] });
    queryClient.invalidateQueries({ queryKey: ['contact-tag-options'] });
    toast.success(message);
    onSaved?.();
  };
  const onError = (e: Error) => toast.error(e.message);

  const create = useMutation({
    mutationFn: (body: ContactTagInput) => createContactTag(body),
    onSuccess: () => onSuccess('Contact tag created'),
    onError,
  });
  const update = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: Partial<ContactTagInput>;
    }) => updateContactTag(id, body),
    onSuccess: () => onSuccess('Contact tag updated'),
    onError,
  });
  return { create, update };
}

/**
 * Preview (dry run) and apply are the same call with a flag, so both are one mutation:
 * the dialog reads `data`/`error` of the latest call. The list refetches after an apply only.
 */
export function useBulkTagContacts() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: BulkTagInput) => bulkTagContacts(input),
    onSuccess: (result) => {
      if (result.dry_run) return;
      queryClient.invalidateQueries({ queryKey: ['respond-contacts'] });
      queryClient.invalidateQueries({ queryKey: ['respond-contact'] });
      queryClient.invalidateQueries({ queryKey: ['contact-tags'] });
      queryClient.invalidateQueries({ queryKey: ['contact-tag'] });
    },
  });
}

/** Preview and apply of tag settings are one call with a flag; the lists refresh after an apply only. */
export function useApplyTagSettings(target: { contactIds: string[]; tagId?: string }) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dryRun: boolean) =>
      target.tagId
        ? applyContactTagSettings(target.tagId, { dryRun })
        : applyTagSettings({ contactIds: target.contactIds, dryRun }),
    onSuccess: (result) => {
      if (result.dry_run) return;
      for (const key of ['respond-contacts', 'respond-contact', 'contact-tag-settings', 'contact-tags', 'contact-tag']) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}
