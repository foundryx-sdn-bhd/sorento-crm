/**
 * Mirrors `user_management/contact_tags.py`: reads need `contacts.view`, every write
 * (catalog, per-contact set, bulk) needs `contacts.edit`.
 */
export const CONTACT_TAG_PERMS = {
  view: 'user_management.contacts.view',
  edit: 'user_management.contacts.edit',
} as const;
