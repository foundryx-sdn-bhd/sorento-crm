'use client';

/**
 * The Contacts action set (D15): Impersonate in portal, then Delete.
 *
 * The list row had three icon buttons and the record's gear had Delete alone.
 * One array now, rendered in the row's "..." and in the record's gear. The portal
 * link keeps its own control on both surfaces: it is a two-step send with its own
 * dialog rather than a single menu press.
 *
 * The handlers are passed in because the two surfaces own different dialog state:
 * the list confirms over a row, the record over the record it is showing.
 */

import { Tags, Trash2, UserCog, UserPlus } from 'lucide-react';
import type { RecordAction } from '@/components/common/recordActions';
import type { RespondContact } from './types/contact.types';

export interface ContactActionHandlers {
  impersonate: () => void;
  remove: () => void;
  /** Opens the Add user modal with this contact locked (S3, AC-59). Omitted on
   *  a surface that has no `users.add` grant or no room for the dialog. */
  createUser?: () => void;
  /** CONTACT-TAGS S3: opens Apply tag settings for this contact. Omitted without `contacts.edit`. */
  applyTagSettings?: () => void;
}

export interface ContactActionOptions {
  /** `user_management.users.add` AND `user_management.users.view` - the row's
   *  "Create user" item is absent without either. Without `users.view` the row
   *  carries no `linked_user_id`, so a linked contact would look unlinked. */
  canCreateUser?: boolean;
}

export function contactActions(
  contact: RespondContact,
  handlers: ContactActionHandlers,
  options: ContactActionOptions = {},
): RecordAction[] {
  const actions: RecordAction[] = [];

  // First in the menu (AC-59), and only for a contact with no user yet - once
  // linked, the user account lives on the contact's own Profile tab.
  if (options.canCreateUser && handlers.createUser && !contact.linked_user_id) {
    actions.push({
      key: 'contact.create_user',
      label: 'Create user',
      icon: UserPlus,
      run: handlers.createUser,
    });
  }

  if (handlers.applyTagSettings) {
    actions.push({
      key: 'contact.apply_tag_settings',
      label: 'Apply tag settings',
      icon: Tags,
      run: handlers.applyTagSettings,
    });
  }

  actions.push(
    {
      key: 'contact.impersonate',
      label: 'Impersonate in portal',
      icon: UserCog,
      run: handlers.impersonate,
    },
    {
      key: 'contact.delete',
      label: 'Delete contact',
      icon: Trash2,
      kind: 'destructive',
      run: handlers.remove,
    },
  );

  return actions;
}
