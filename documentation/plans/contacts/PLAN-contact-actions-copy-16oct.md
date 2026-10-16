# PLAN: contacts list Actions dropdown + copy access copies everything (CONTACT-ACTIONS-COPY)

Status: in progress (small fix track: FE swap to an existing component + extend an existing copy service; no migration, no auth change)

Owner, 16 Oct 2026: "also please put these all actions under 'Actions' dropdown just like other list view,
and when we copy, it doesn't copy things like brands, image access, voice note access, it doesn't copy all".

## 1. Actions dropdown
Contacts list bulk strip (`ContactsList.tsx`) moves its bulk buttons into the shared
`BulkActionsMenu` (`app/(protected)/scm/components/BulkActionsMenu.tsx`, already used by the PO list
and Ideas), fed through `DataGridListToolbar.bulkActionsSlot`. "n selected" and Clear stay (toolbar).
Export stays reachable via the slot's `openExport`.

## 2. Copy access copies every access setting
Facets added to `contact_access_copy_service`: see UAC.
