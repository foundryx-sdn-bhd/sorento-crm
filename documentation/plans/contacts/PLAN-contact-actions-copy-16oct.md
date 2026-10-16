# PLAN: contacts list Actions dropdown + copy access copies everything (CONTACT-ACTIONS-COPY)

Status: in review (small fix track: FE swap to an existing component + extend an existing copy service; no migration, no auth/RBAC change)

Owner, 16 Oct 2026: "also please put these all actions under 'Actions' dropdown just like other list view,
and when we copy, it doesn't copy things like brands, image access, voice note access, it doesn't copy all".

## 1. Actions dropdown
The contacts list bulk strip (`components/ContactsList.tsx`) renders the shared `BulkActionsMenu`
(`app/(protected)/scm/components/BulkActionsMenu.tsx`, already used by Purchase Orders and Ideas) through
`DataGridListToolbar.bulkActionsSlot`. Items come from the pure `lib/contactBulkActions.ts`: Export, Link
customers, Enable messaging, Sync from Respond, Copy access from contact, then the red items Disable
messaging and Delete, which still open their existing dialogs. "n selected" and Clear stay (toolbar).

## 2. Copy access copies every access setting
Copied (`app/services/contact_access_copy_service.py`), all through the one bulk-copy endpoint:

| Facet | Stored in | Since |
|---|---|---|
| Access types | `respond_contact_access_types` | 3 Oct |
| Tier | `respond_contacts.chatbot_profile.tier` (only that key) | 3 Oct |
| Stock checks, Notify salesman, Packing list, ETA buffer days, Escalation, AI replies, Skip date-range questions | `respond_contacts` booleans | 3 Oct onwards |
| Send files as links | `respond_contacts.chatbot_files_as_links` | **this lane** |
| Regions | `respond_contacts.regions` | CONTACT-REGION |
| Field reveals (incl. cost visibility) | `contact_field_reveals` | 3 Oct |
| Agent access | `contact_agent_access` (needs `access_agents.edit`) | 3 Oct |
| Stock check mode + locations | `stock_visibility_policies` contact row (needs `inventory.stock.edit`) | 4 Oct |
| Companies | `respond_contact_companies` (superadmin/admin) | 4 Oct |
| Market segments | `respond_contact_market_segments` | 4 Oct |
| Brands | `respond_contacts.brand_ids` | **this lane** |
| Image access | `contact_media_limit` (image: on/off, monthly limit) | **this lane** |
| Voice note access | `contact_media_limit` (voice: on/off, monthly limit, max clip seconds) | **this lane** |

Brands and media access are edited directly under `user_management.contacts.edit`, the permission the copy
endpoint already requires, so no `facets_to_skip` entry. No media row reads as Off (the gate fails closed).

Not copied (identity or the person's own state): name, phone, Respond.io id, portal slug, workspace,
linked customers, CS routing, memory level, chatbot language / ledgers / facts (rest of `chatbot_profile`),
outbound messaging switch (own bulk action), "Requires registered project" (portal form rule on the identity
edit dialog), media usage counters and notice stamps, voice `languages` (no editor writes it).
