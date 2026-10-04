# Browser verification pass 4, 16 Sep 2026

Stack: frontend http://localhost:3081 (dev/HMR), backend :8081, clone of the prod copy. Lane
branch `feat/chatbot-turn-rearch`. Briefed head `8a8fe6471`; actual worktree HEAD at both the
start and the end of this run was `0c961a7f6` ("Merge remote-tracking branch
'origin/feat/chatbot-turn-rearch' into test/chatbot-turn-rearch-red") - 8 commits ahead of the
briefed head, all confirmed ancestors of it (`git merge-base --is-ancestor 8a8fe6471 HEAD` = true),
including `0aad83786` ("merge coder 10's AC-1593 accept/roster/rung fix ... fix the case fully
green"). Head did not move during the run itself (`0c961a7f6` confirmed unchanged before and
after, commit timestamp 13:22:37+08:00, run started ~13:26). Session: agent-browser
`--session rearch-browser-4`, headless, logged in via `E2E_EMAIL`/`E2E_PASSWORD` from
`sorento_crm_frontend/.env.local`. Closed cleanly at the end (`close`, not `close --all`).

Navigation: sidebar clicks from `/` throughout (System > Messaging > Chatbot Console; Dealer Kit >
Room Designer > Price Tag Requests). `get url` checked before trusting reads at every page
transition. No cross-session hijack observed - `get url` always matched the page expected.
`scrollintoview @ref` run before every click, including nested sidebar group expansions; no
off-screen no-op hit. Radix Select traps not encountered (contact picker is a plain searchable
combobox, no Radix dropdown involved).

## Step 1 - Console, contact CONTACT N, escalation chain + customer roster (10 turns)

Reset clicked first; contact combobox set to "CONTACT N" via the searchable combobox (types +
click), persisted across the whole chain.

| Turn | Sent | Branch chip | Result | Turn id |
|---|---|---|---|---|
| 1 | `outstanding for chin chun` | business_query v23 | **PASS** | `c8bda67f-9835-4e2d-96ba-e53396b1702a` |
| 2 | `1` (pick CHIN CHUN HARDWARE SDN BHD - [A/C I]) | business_query v23 | **PASS** | `8365cdc1-21c3-4f33-86b0-e5952a73d8f7` |
| 3 | `hello` (escalate? question still open) | low_signal v23 | **PASS** | `29950e7a-7c0d-48b9-8443-6f43a437c16a` |
| 4 | `yes` (to the still-open escalate question) | out_of_scope v23 | **PASS** | `ad594076-93f2-46b5-94d0-397a4739b106` |
| 5 | `delivery to hanlim` | business_query v23 | **PASS** | `9fb46b17-0e9e-451f-bcd7-93182ba671e2` |
| 6 | `1` (pick HANLIM TRADING SDN BHD [A/C II]) | business_query v23 | **PASS** | `b8799517-1051-4dcf-80d2-04fc587fd3b6` |
| 7 | `incoming srtwc286` | business_query v23 | **PASS** | `f5bd3697-e8ae-44f2-8383-423d1d3f2512` |
| 8 | `1` (pick SRTWC286-SH-200) | business_query v23 | **PASS** | `e30bbccf-24ac-4a2a-a5ed-2c0d9b039584` |
| 9 | `photo srtwc286` | business_query v23 | **PASS** | `6e81d266-d182-4bc9-9eeb-c24fca28a70a` |
| 10 | `1` (pick SRTWC286-SH-200) | business_query v23 | **PASS** | `0e20e73b-0da3-4918-902c-20ebd3b328c3` |

**Result: 10 of 10 turns PASS.** Both defects pass 3 found in this exact shape (a bare "1" and
then "yes" sent to an open "Would you like me to escalate?" question re-running/replaying the
identical prior business_query answer instead of resolving the question) are fixed - the lane
head's own `0aad83786` commit message ("AC-1593 accept/roster/rung fix") matches this.

### Turn detail

- **Turn 1** `outstanding for chin chun` -> roster of 4 customer NAMES, no raw codes (all four
  entries are full names, unlike pass 3's mixed 3-code-1-name roster for the same query):
  ```
  Which customer do you mean?
  1. CHIN CHUN HARDWARE SDN BHD - [A/C I]
  2. CHIN CHUN HOMEMART SDN BHD - [A/C I]
  3. CHIN CHUN HARDWARE AND TIMBER TRADING
  4. JIMMY - I
  ```
- **Turn 2** `1` -> real outstanding-delivery-order data for CHIN CHUN HARDWARE SDN BHD - [A/C I],
  header AND body both correctly named:
  ```
  *orders* for CHIN CHUN HARDWARE SDN BHD - [A/C I]:
  Product: all
  Customer: CHIN CHUN HARDWARE SDN BHD - [A/C I]
  Location: all
  Order date: all

  *Delivery order outstanding*
  Delivery orders: 6
  DO qty: 29
  Delivered: 0
  Outstanding: 29
  ...
  Would you like me to escalate?
  ```
  Pass 3's field-binding defect (body's `Customer:` line reading "JIMMY - I" instead of the
  resolved customer) is fixed for this customer - `Customer:` now matches the picked name exactly.
- **Turn 3** `hello`, sent while the "Would you like me to escalate?" question from turn 2 was
  still open -> `Hi there! How can I help today?` on its own `low_signal` branch, escalate question
  not silently re-answered or repeated verbatim.
- **Turn 4** `yes`, sent to the still-open escalate question -> routed to the escalation lane on
  branch `out_of_scope` (not `business_query`), NOT a repeat of turn 2's answer:
  ```
  Your request is out of the scope of my ability and require human assistance. We are directing
  your enquiry to the correct person. Please wait for a moment.

  This inquiry has been routed to the respective person-in-charge (PIC) from customer service
  team. We will get back to you soon. Thanks for your patience.
  ```
  Named the team ("customer service team") directly - no team-pick roster appeared (single-team
  auto-route), consistent with the brief's "a named team **or** a team pick" alternative. This is
  the exact turn/turn-pair (a bare "1" then "yes" against an open escalate question) that FAILED
  twice in pass 3 (`67fcbb6a...` and `3af6c1aa...`) - now fixed.
- **Turn 5** `delivery to hanlim` -> roster of 6 real customer NAMES, all variants of "HANLIM
  TRADING SDN BHD", no raw account codes anywhere.
- **Turn 6** `1` -> `*orders* for HANLIM TRADING SDN BHD [A/C II]:\nNo matching results found.\n\n
  Would you like me to escalate?` - header names the customer, no UUID; an honest empty result (no
  `Customer:` field line to check since the "orders" template only emits Product/Customer/
  Location/Order date when there is matching data - this account genuinely has 0 delivery orders).
- **Turn 7** `incoming srtwc286` -> roster of all 10 SRTWC286 variants, WITH has/no-incoming
  stamps this pass (pass 3 found the roster present but the stamps absent for this exact query -
  now present: `4. SRTWC286-SH - no incoming` ... `8. SRTWC286-SH-NEW-P - has incoming`).
- **Turn 8** `1` (SRTWC286-SH-200) -> `*incoming stock* for SRTWC286-SH-200:\nNo matching results
  found.` bundled with a bonus `*stock*` table for the same code (2 warehouse rows, both qty 0) -
  named by CODE throughout, no UUID.
- **Turn 9** `photo srtwc286` -> roster of all 10 SRTWC286 variants (same shape as turn 7's).
- **Turn 10** `1` (SRTWC286-SH-200) -> `*product attachments* for SRTWC286-SH-200, photo:` with 4
  real attachments (spec sheet-style entry + 3 certification PDFs, one marked Expired, two Valid),
  named by code, no UUID. Screenshot: `pass4-step1-photo-attachments.png`.

Every `/api/v1/system/chatbot/console/turn` POST across all 10 turns returned HTTP 200 (confirmed
via `network requests --filter "console/turn"`). `console` showed only expected
`[debug] JWT token extracted successfully` noise; `errors` was empty across the whole chain - no
uncaught exceptions attributable to product code.

## Step 2 - Dealer Kit > Price Tag Requests list

Reached via sidebar: Dealer Kit > Room Designer > Price Tag Requests
(`/dealer-kit/price-tag-requests`) - no deep URL used; the sidebar path was confirmed via the
in-app search-menu lookup first (`⌘⇧K` equivalent, typed "price tag") to locate the group, then
walked by clicking the actual Dealer Kit > Room Designer sidebar entries.

**PASS - no 500.** The list loaded with real data, no console errors:

```
GET /api/v1/list-query/column-config/%2Fdealer-kit%2Fprice-tag-requests -> 200
GET /api/v1/dealer-kit/price-tag-requests?page=1&limit=50&sort=created_at&dir=desc -> 200
PUT /api/v1/list-query/column-config/%2Fdealer-kit%2Fprice-tag-requests -> 200
```

Grid columns: Doc Number, Customer, Salesperson, Status, Need by, Lines, Created, Assigned To.
First row rendered: `PT-202609-0003 | BATHWARE ENTERPRISE [A/C I] | Jayson | Approved | 31/10/2026
| 2 | 09/09/2026, 3:31 pm | Jayson`. `errors` empty. Screenshot:
`pass4-step2-price-tags-list.png`.

## Summary

- **Step 1: 10 of 10 turns PASS.** Both escalation-open-question defects pass 3 found
  (`67fcbb6a-05a5-49a3-850d-0c8ce5139353` and `3af6c1aa-3e36-4bbe-ace6-e01afdf7ef87`) are fixed -
  this pass's turns 3/4 are the direct re-walk of that exact shape (hello-while-open, then
  yes-while-open) and both now behave correctly. Two of pass 3's softer notes are also resolved
  this pass: turn 1's customer roster is now all names (pass 3 had 3 codes + 1 name for the
  equivalent query), and turn 7's incoming roster now carries the has/no-incoming stamps pass 3
  found absent. Turn 2's field-binding note from pass 3 (`Customer:` reading "JIMMY - I") is also
  fixed for the customer tested here (`Customer:` field now matches the picked name exactly).
- **Step 2: PASS**, no 500 - main's #948 (per the brief) is present in this worktree's history and
  the price tag requests list renders real rows with all API calls returning 200.
- No FE console errors or uncaught exceptions across either step (only expected
  `[debug] JWT token extracted successfully` noise). Every `/api/v1/*` call involved in both
  steps returned HTTP 200.
- Lane head did not move during this pass (`0c961a7f6` at both start and end) - no coder activity
  overlapped this verification run, though the worktree's HEAD is 8 commits ahead of the briefed
  `8a8fe6471` (all confirmed ancestors of HEAD), which is why the two pass-3 escalation failures
  are now fixed.
