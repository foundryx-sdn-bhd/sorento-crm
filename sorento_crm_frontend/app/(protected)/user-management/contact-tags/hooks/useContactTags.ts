import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LIST_QUERY_OPTIONS } from '@/lib/list-query/options';
import { getContacts } from '../../contacts/[id]/services/contactService';
import {
  EMPTY_CATALOG,
  getSettingsCatalog,
} from '../services/contactTagSettingsCatalogService';
import { toast } from '@/lib/toast';
import type { ListPagerParams } from '@/hooks/useListPager';
import {
  applyContactTagSettings,
  applyTagSettings,
  bulkTagContacts,
  createContactTag,
  getContactTag,
  getContactTags,
  setContactTags,
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
        { is_active: 'true', counts: 'false' },
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
export function useApplyTagSettings(target: {
  contactIds: string[];
  tagId?: string;
}) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dryRun: boolean) =>
      target.tagId
        ? applyContactTagSettings(target.tagId, { dryRun })
        : applyTagSettings({ contactIds: target.contactIds, dryRun }),
    onSuccess: (result) => {
      if (result.dry_run) return;
      for (const key of [
        'respond-contacts',
        'respond-contact',
        'contact-tag-settings',
        'contact-tags',
        'contact-tag',
      ]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
    },
  });
}

/** The contacts holding one tag (the detail page's Contacts section). */
export function useContactTagHolders(
  tagId: string,
  enabled: boolean,
  limit = 50,
) {
  return useQuery({
    queryKey: ['respond-contacts', 'tag-holders', tagId],
    queryFn: () =>
      getContacts(
        {
          pageIndex: 0,
          pageSize: limit,
          sorting: [{ id: 'name', desc: false }],
          searchQuery: '',
        },
        { tag: tagId },
      ),
    enabled,
  });
}

/** The pick lists behind the settings editor; a list the viewer cannot read is absent. */
export function useSettingsCatalog() {
  const query = useQuery({
    queryKey: ['contact-tag-settings-catalog'],
    queryFn: getSettingsCatalog,
    staleTime: 60_000,
  });
  return { ...query, catalog: query.data ?? EMPTY_CATALOG };
}

/** Save a tag's settings; the editor shows the thrown message in place, so there is no error toast. */
export function useSaveTagSettings(tagId: string, onSaved: () => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (settings: Record<string, unknown>) =>
      updateContactTag(tagId, { settings }),
    onSuccess: () => {
      for (const key of [
        'contact-tag',
        'contact-tags',
        'respond-contacts',
        'contact-tag-settings',
      ]) {
        queryClient.invalidateQueries({ queryKey: [key] });
      }
      toast.success('Tag settings saved');
      onSaved();
    },
  });
}

/** Replace one contact's tag set (the contact edit dialog). */
export function useSetContactTags() {
  return useMutation({
    mutationFn: ({
      contactId,
      tagIds,
    }: {
      contactId: string;
      tagIds: string[];
    }) => setContactTags(contactId, tagIds),
  });
}
