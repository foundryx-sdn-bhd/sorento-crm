# UAC: PV2 redesign (14 Oct 2026)

Plan: `PLAN-pv2-redesign-14oct.md`. Each round adds its section before its red tests.

## R1 Answer register writer
R1-1 After a PV2 turn whose v1 lane printed a delivery-order list, the saved PV2 state's register holds one entry per
     printed order (kind delivery_order, label = the printed order number) in printed order, ids R1..Rn.
R1-2 Each order entry has child entries for its product lines (kind product, label = printed product code,
     parent = the order's R id), from the MCP item's unprinted `lines` key, never by parsing the reply text.
R1-3 A PO placed list writes purchase_order entries with their product as a child entry.
R1-4 Stock, incoming, matching-product, attachment, set-page and top-selling lists write product (or their
     entity_type) entries, one per printed row, in printed order.
R1-5 A sales-order card or SO list writes sales_order entries (label = SO number) from so_status's structured rows;
     PO/PR ref cards likewise.
R1-6 Report rows (report_ask drill rows; delivered by-customer/by-product breakdown) write entries of the group_by kind.
R1-7 A field removed by the reveal filter, and an item's unprinted title, are never recorded; keys (codes, uuids,
     numbers) are stored but no entry label shows a uuid.
R1-7b Only rows the reply printed are recorded: rows kept in `answers` but hidden from the text (summary mode,
     needs-quantity stock mode, duplicates skipped on a status-only ask) are not recorded.
R1-8 The register keeps at most 20 entries and only the last 2 replies; a PV2 direct reply (no lanes) adds nothing and
     keeps the previous reply's entries; a new session clears it.
R1-9 Stored state from before R1 (no register field) loads unchanged.
R1-10 MEMORY is byte-identical to today for the same state (the register is not rendered in R1).
R1-11 A non-staff contact asking for a product's PO list is not refused for lacking purchase_orders.placed (owner
      ruling 14 Oct); the answer matches v1's for the same contact. Staff behaviour is unchanged.
R1-12 v1 turns (parser mode v1) are unaffected: no register write, same replies.

## R2 Subject stack + PendingQuestion.origin
R2-1 Every subject gets a stable `sid` when it starts; S1 = held, S2..S5 = topics newest first (cap 5 as today). A subject
     keeps its sid when pushed to topics and when switched back to.
R2-2 Register entries carry the sid of the subject they were printed under; after a new topic pushes that subject,
     `subject_label(state, entry.subject)` is "S2".
R2-3 `subject_at(state, "S<n>")` returns that subject; an unknown or out-of-range S id returns None.
R2-4 MEMORY is byte-identical to today for the same state (no S<n>/R<n> rendering in R2).
R2-5 Whenever a question interrupts an ask (entity clarify, did-you-mean, flagged clarify, a missed ask's question, the v1
     picker after a PV2 Lookup), `PendingQuestion.origin` holds the original ask: the full ask where held did not take
     it (unclear act, flagged clarify, a missed ask's question, the v1 picker); on a question raised after held took the
     ask, only the items still waiting on a question, with no attachment kind (held carries the vetted kinds).
R2-6 A pick completes the ORIGINAL ask with the picked entity: "any eta mou?" -> pick "1" answers ETA (incoming) for the
     picked product; an ask with a period ("stock for SRTWC286 last month") keeps its period after the pick; attachment
     kinds, quantity, specs and status likewise.
R2-6b A pick never re-runs what the original ask already applied and never loops: a closest-product pick, a pick after
     a remove op, and a which_held pick each reach their Lookup on the first pick, as pre-R2; with two ambiguous items
     the second is asked next and both picks reach the Lookup; a second item that was not found is reported after the
     pick exactly as the same ask typed with the picked product gives it (pre-R2 silently dropped both). A pick on a
     which_held question (a miss or a flagged clarify) answers it: it never raises the same miss again. Otherwise, on a
     question raised after held took the ask, MEMORY after every pick equals pre-R2's for the same turns (no picked uuid
     in a report filter, no extra held entity, no attachment kind the pick did not vet). Where held did NOT take the ask
     (flagged clarify, a missed ask, a switch_back miss), R2-6 wins: the pick completes the original ask (its clears and
     its other entities apply), which can differ from pre-R2's MEMORY.
R2-6c A pick on a question raised under a sales report or a PO ask is refused once the contact lacks that grant, however
     many questions deep the chain is.
R2-7 State saved before R2 (with `deferred`, no `origin`, no `sid`) loads and a pick on it behaves as today.
R2-8 Every PV2 answer path that prints rows writes the register (each path named in the test); the stock-ask render
     records the rows it prints.
R2-9 output_structurer drops order `lines` whenever the Products field is hidden (test).
R2-10 `printed_rows` stays aligned with the envelope's `figures` when an answer is not a dict.
R2-11 No LLM calls; v1 turns unchanged.

## R-GAPS Frozen v1 lanes (owner rule 14 Oct; source crew/state/reports/PV2-FROZEN-LANES-coverage-14oct.md)
All from handwritten parses (no LLM). [cust] = a customer named in the earlier turn.
RG-1 (#1570) Staff: "[cust] 7/10/2026 有送货吗？" -> "SRT391-GM几时会安排送货？": the carried customer plus the new product is a
     clarify ("Delivery for SRT391-GM: only [cust] (from earlier) or all customers?"), nothing fetched. PV2 table case-072
     (converted_replay.jsonl:115) expects a lookup today and is corrected to expect this clarify. The 3rd identical clarify
     to staff gives v1's "I still need X for Y." instead of asking again (crew a76095395).
RG-1b Pick 1 keeps the earlier customer; pick 2 ("all customers") clears only it and keeps the earlier period; a
     subject-less reply while the question is open re-asks it and keeps its period for the pick. No carry question when
     the ask points back ("this customer"), opens a report, switches to a domain where the kinds do not both narrow, or
     the carried customer is the contact's own (scope-limited) customer: tested on staff and on a dealer with a non-own
     customer. The 2nd identical question to anyone counts as a miss (not only which_held).
RG-2 (#1582) "250mm srtwc286 got stock?" answers only the variants with 250 on any axis, with no Length/Width question;
     "srtwc286 300mm" with no 300 on any axis -> "No 300 mm for SRTWC286. Sizes on file: Length 250, 600 mm; Trap outlet
     length 180 mm." (en/zh/ms; axis names stay English until R3), no options, no closest-product picks; a class + size
     with no code lists the class products holding it on any axis (capped); a parser flag about a spec phrase the ask
     already carries asks nothing.
RG-1c (R-GAPS-b review) The carry question also fires when the new kind was held before (HANLIM + SRT391-GM, then a new
     topic "SRT392") and when the new item first needs its own question (asked after that pick, never dropped
     silently); a pick replays only the carried item and the period, never other held items (a held SO stays out); a
     repeated identical question counts as a miss only for the SAME subject ("SRT391 manual", "SRT392 manual" are two
     questions) and a restated ask keeps its new details (October replaces 7 Oct); at the cap a barred contact's refer
     line follows after a blank line (v1).
RG-2b (R-GAPS-b review) A follow-up size is checked against the held product's whole family, not the last size's hits
     ("srtwc286" -> "250mm?" -> "how about 600mm?" answers the 600 variant); two sizes in one ask narrow each other; held
     products with no size rows get no axis question.
RG-5b (R-GAPS-b review) A base code whose variants are partly in active sets offers those sets AND the variants in no
     set; set members are chat-visible, active and read company-scoped by set id; a set with no visible members is not a
     set match; family sets are offered only through chat-visible, active members.
RG-3 (#1603) A v1 escalate offer answered "yes" -> "Routed to your PIC, they will reply shortly." and the hand-over saves
     the asked text, never "".
RG-4 (#1617) "SRTWT5880-CR picture" -> "HAVE 10??" (no open question) -> the stock answer for 10, no photo re-send, no
     type question.
RG-5 (#1443) A set code -> its member stock, never "I need at least one filter"; "SRTWC8608 stock" offers its sets.
     Inventory only; a base code offers its ACTIVE sets as one pick for everyone; an inactive set does not answer; set
     members are read company-scoped.
RG-6 (#1517) An ask with no product ("product") -> a which-product question and no tool call; a product list never shows
     an unsearchable or inactive row (e.g. "FOR ACCOUNT DEPARTMENT ONLY"). The empty-ask question applies to inventory
     and master_products only (#1517 ruling); a chat product list (crm_master_products_list from the chatbot) applies
     the chat-visible filter (is_searchable product and category, is_active); PV2 family lists skip inactive products.
RG-7 (#1572) "Srtkt71-ss outstanding" -> "Dealer outstanding only" keeps the product and adds the channel; "CBDR4003
     stock" -> "Top sales Sorento and Mocha" -> "Sorento and mocha" never carries CBDR4003 or BK007A.
RG-7b (R2 review) "5 A1 and 3 mou" -> pick 1 for mou -> the answer covers A1 x 5 and the picked product x 3; brand
     "sorento + moc" -> pick mocha -> both brands. Today (and pre-R2) the pick drops the other same-kind items.
RG-7c (R2 review) "remove customer Z and this SO" with Z held and no SO held -> which_held pick -> Z is removed.
     Today (and pre-R2) the which_held pick drops the remove and Z stays.
RG-4b (R-GAPS-a shapes) After a photo answer, a count that names nothing reaches the stock answer for that count in
     every parse shape: refines_held or new_topic with no domain, with or without refers_to_held, and an
     answers_bot_question carrying only a count with nothing pending; a count after a stock answer stays a stock ask.
RG-7d (R-GAPS-a) After "CBDR4003 stock", a refine that opens a sales report starts a new subject (CBDR4003 is not on
     S1); brands named beside a newly opened report are its filters.
RG-7f (R-GAPS-a review) A which_held pick never re-misses on a replayed remove: held C1, Z, A1; "remove customer Z and
     this SO" -> pick C1 -> Lookup with customers [C1]; likewise two removes, a remove raiser, the new_topic raise site.
RG-7g (R-GAPS-a review) did-you-mean and closest-product questions keep the other items like RG-7b ("3 mou + 2
     srtwc28" -> both, each with its quantity); a count after an ETA answer is a stock ask with that count, and the held
     subject keeps no attachment kind the stock domain does not take; "sorento and mocha" on a held brand report keeps
     both brands.
RG-7h (R-GAPS-a review) A dealer's "no" to a did-you-mean asked after other items of the same ask landed answers those
     items (and asks any item still waiting); a did-you-mean alone still takes v1's declined lane. "customer Y and remove
     customer Z" with Z held is no question: held customers end as [Y].
RG-7e (R4) "Dealer outstanding only" needs a channel on the outstanding path and a parser field: built in R4.
RG-8 No LLM calls; v1 turns unchanged.

## R3a Entity-kind and domain registry columns (behaviour-preserving; plan "R3 design")
R3a-1 Migration `pv2_0004_kind_registry` (down_revision pv2_0003_label_reasoning) adds the R3a columns to
      chatbot_entity_kinds and chatbot_domains with ADD COLUMN IF NOT EXISTS and server defaults, seeds them by name
      (idempotent), and its downgrade drops them; alembic has a single head.
R3a-2 Every value the retired constants held equals the registry value: the seed (policy_rows), the migration's data
      and the live Policy read from migrated rows (a parity test with the old values hard-coded).
R3a-3 The retired tables no longer exist in code (a deletions guard): bridge V1_HINT; apply DOMAIN_BY_FOUND_KIND,
      DOCUMENT_KINDS, QUANTITY_DOMAINS, CARRY_SCOPE_KINDS, SUBJECT_REQUIRED_DOMAINS, SET_DOMAINS, FAMILY_DOMAINS,
      SINGLE_SUBJECT_DOMAINS, _FILTER_KINDS; register _FIELD_KIND; the per-kind / per-domain word tables in clarify_copy
      for kinds and domains that have a row (kinds with no row keep a code fallback until R4 adds their rows).
R3a-4 Behaviour is unchanged: the replay sets (test_core_replay, test_carry_converted, test_livecheck_replay) and every
      R1/R2/R-GAPS test pass unchanged; replies are byte-identical for the same parses.
R3a-5 A Policy loaded from rows that lack the new keys (stale schema) behaves exactly as the seed.
R3a-6 Changing a registry value changes PV2 behaviour with no code change (one data-driven test per table family: a
      kind's default_domain, a domain's reads_quantity, a kind's labels).
R3a-7 No new kind rows: v1's {{entity_kinds}} prompt vars, the prompt blocks hash and the PV2 index text are
      byte-identical.
R3a-8 The CI DB built by scripts/bootstrap_env.py carries the seeded values; PV2 reads the turn's live Policy
      (TurnV2Context.registry), pure callers the seed.
R3a-9 No LLM calls in tests; v1 turns unchanged.

### R3a amendment (owner 15 Oct 00:15 + 00:17): kind words editable on the existing Translations screen
R3a-12 Every user-facing kind word lives in data: chatbot_entity_kinds holds the English reply word (`word`,
      `word_plural`) for every kind PV2 names in a reply, with `role` 'entity' (the 18 parser kinds) | 'relation'
      (seat_cover, mirror, shelf; `host_kind` wc / cabinet) | 'host' (wc, cabinet) | 'word' (every other kind word PV2
      prints today, e.g. supplier, product_class, product_type, customer_order, order_number); chatbot_domains holds
      the English carry word (`ask_word`). No label columns per language.
R3a-13 The ms and zh text of those words comes from translation_memory (source_lang 'en', target 'ms' / 'zh'), the
      table the System Management > Translations screen edits: a migration seeds one row per word and language
      (inbound shipment zh 到货批次, seat cover, mirror 镜子, shelf 置物架, WC, cabinet, ...), replayed by
      scripts/bootstrap_env.py; an edit on the screen changes the next PV2 reply; a word with no row gets an editable
      placeholder row and the reply uses the English word. No kind-word copy stays in code (sentence patterns stay).
R3a-14 v1's {{entity_kinds}} prompt vars, the prompt blocks hash and the PV2 index read role 'entity' rows only, so
      adding relation / host / word rows changes none of them (R3a-7 holds).
R3a-15 Kind words are fill-only in the localizer: a printed data line that happens to equal a kind word ("shelf") is
      never rewritten.
