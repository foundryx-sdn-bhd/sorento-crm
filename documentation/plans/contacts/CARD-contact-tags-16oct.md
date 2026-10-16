# CARD: contact tags + tag access settings (CONTACT-TAGS, 16 Oct)

Status: filed to owner 16 Oct, awaiting answers Q1-Q5. Facts measured on origin/main 68e0e036 and the
dev copy `sorento_cagent_stack` (read-only, 104 contacts). Phones masked to last 3 digits.

## What exists today

| Thing | Where | What it means |
|---|---|---|
| Contacts list | `sorento_crm_frontend/app/(protected)/user-management/contacts/components/ContactsList.tsx:580` | DataGrid + toolbar :598, Columns toggle on; no row expand, no right-click; filters `ContactAccessFilters.tsx:108-213`; bulk actions :626-676 |
| Access types | `contact_access_types` + M2M `respond_contact_access_types` (`sorento_crm_backend/app/models/access.py:69,92`) | Brand x dealer/office ENTITLEMENT (Sorento Dealer, Mocha Office...). Carry no switch defaults. Dev: 48/104 contacts have none, 24 have all 7. Not a person's role. |
| Market segments | `market_segments` + M2M `respond_contact_market_segments` (`access.py:126,161`) | retail 34 / project 30 on dev. Drive CS team routing and extra portal forms (`access.py:150`). Closest thing to a tag today. |
| Portal forms | `portal_service.py:81` SUPPORTED_TYPES = complaint, stock_inquiry, purchase_request, sponsorship_form (every contact by default); per-contact on/off in `contact_portal_form_overrides` (`app/models/price_tag.py:56`) | The owner's "stock inquiry, purchase request" = these two forms. 5 dev contacts were hand-switched off one by one. |
| Switches on the contact | `respond_contacts` `access.py:266-305` | AI replies, stock checks, escalation, packing list, notify salesman, ETA offset, skip period, files as links |
| Chatbot agents | `contact_agent_access` (`access.py:434`) | Per-contact allow/deny per agent (e.g. "Purchase Request" agent: 2 grants on dev) |
| Copy access | `POST /contacts/bulk-copy-access` (`app/api/v1/user_management/contacts.py:208`, `app/services/contact_access_copy_service.py:666`) | Whole access set from one contact, REPLACE, preview first. Copies access types too, so it cannot set "just PR off" on a group without overwriting their brand entitlements. Portal forms not copied. |
| Access differs from | filter `access_differs_from=<contact>` (`contacts.py:112`, `contact_access_copy_service.py:753`) | Compares contact vs contact only. No role to compare against. |
| Tags / roles / presets | none on main | ACCESS-MODEL PR #1434 (roles + overrides, draft, 68 commits, idle since 11 Oct, held behind #1429) |
| New contact from sync | `respond_sync_handler.py:261` -> `contact_service.py:342` | Gets column defaults only, no access types, no segment |

## Proposed rules

1. **Tags.** Owner-managed tag list (Users & Access, next to Access types). A contact holds 0..n tags.
   Tags are set only by a person (edit dialog or bulk Tag / Untag on selected rows). Never guessed from
   names, never set by sync.
2. **Tag access settings.** A tag may carry settings, but only the ones the owner ticks (e.g. Retail
   dealer: Purchase request form Off, Stock inquiry form Off). Settings not ticked are never touched.
3. **Apply.** "Apply tag settings" writes those values onto every contact with the tag, after a preview
   that lists each contact and each change (same preview as Copy access). It writes the same per-contact
   fields the chatbot and portal already read, so nothing at runtime changes.
4. **Cross-check.** List columns Tags and "Tag settings" (Matches / Differs: n, hover names the setting);
   filters Tag, No tags, "Differs from its tag settings"; Export carries tags.
5. **Hand edits stay allowed.** A contact edited by hand after apply shows "Differs" until someone applies
   again (preview shows the revert).

## Real examples (dev copy)

1. **Akmal (xxx880)**, segment retail: PR, stock inquiry, complaint, sponsorship forms all hand-switched
   Off, price tag On. With a tag "Retail dealer" (PR Off, stock inquiry Off) he would show Matches.
2. **Jayson (xxx328)**, segment retail, Sorento Dealer, 5 customers incl. HANLIM TRADING SDN BHD (300-H030),
   AND linked to a staff user: same hand-set forms as Akmal plus extra forms On. Two roles at once
   (staff + dealer) - see Q4.
3. **Mohamad Aiman (xxx548)**, segments project + retail, all 7 access types, no customer: tagged both
   "Retail salesperson" and "Project salesperson" - see Q4.
4. **Ahmad Shakir Irfan (xxx797)**, staff user linked, no segment, no access types: "Internal staff".
   18 dev contacts are linked to a staff user; owner can filter them and bulk-tag in one go.
5. **Contact 1788624217951 (xxx007)**, created 5 Sep by sync, no name, no type, no agents: shows under
   "No tags" until a person tags it.

## Edge cases

- Contact in two tags whose settings disagree: Q4.
- Contact hand-edited after apply: shows Differs (rule 5); no "exception" flag in v1 (Q5).
- New contact from Respond.io sync: no tags, listed under "No tags"; nothing auto-applied.
- Staff vs dealer: "Internal staff" is a tag like any other; the staff-user link is a filter to find
  them, not an auto-tag.
- Removing a tag from a contact does not undo settings already applied (preview never shows a reverse).
- Deleting a tag: deferred-delete countdown (D7), contacts keep their current settings.
- Found, not in scope: several contact write routes check login only, no permission slug: bulk delete
  `contacts.py:264`, bulk sync :246, create :310, delete :582, market segments :852, attachment types :928.
  Recommend a separate small security lane; this lane's new routes use `user_management.contacts.edit`.

## Questions

**Q1. Tags: new labels or reuse market segments?**
(a) New tag list; segments stay as they are (CS routing + forms) and show as their own column.
(b) Turn segments into the tags (add Dealer, Internal staff segments).
Recommend (a): a segment change re-routes CS tickets and grants forms (`access.py:150`); a tag must be
safe to add without side effects.

**Q2. Mass config: stored on the tag, live role, or one-off?**
(a) Tag carries ticked settings; Apply with preview; contacts keep the values (rules 2-3).
(b) Live role: contacts follow the tag at runtime with per-contact overrides (that is ACCESS-MODEL #1434;
needs every gate rewired).
(c) No stored settings: a one-off "Set access" dialog on the selected rows; nothing to compare against later.
Recommend (a): it gives the owner a role to cross-check against (goal 3) with no runtime change; (c) loses
the "differs" check; (b) is #1434.

**Q3. Which settings can a tag set?**
Recommend: portal forms (each kind On/Off), AI replies, stock checks, escalation, packing list, the other
switches, cost visibility, chatbot agents (allow/deny each), image + voice note access.
Not on a tag: access types and brands (brand entitlement differs per dealer), linked customers, companies,
segments, regions, tier.

**Q4. A contact with two tags that disagree (e.g. Retail salesperson: PR On, Dealer: PR Off)?**
(a) Most restrictive wins (Off / Hidden / Deny), preview shows which tag decided.
(b) Tag order wins (top of the tag list).
(c) Refuse: list the contact as "Conflict" and skip that setting.
Recommend (a): same rule the stock policy already uses across access types
(`documentation/plans/_archive/inventory/PLAN-stock-visibility-policy.md:91-93`) and fails safe.

**Q5. When a contact gets a tag, do its settings apply right away?**
(a) Tag dialog has a second step: preview of the tag's settings changes, "Apply settings too" ticked;
untick to only label.
(b) Tagging only labels; the contact shows Differs until "Apply tag settings" is run.
Recommend (a): one action instead of two, and the preview still shows every change before it writes.
