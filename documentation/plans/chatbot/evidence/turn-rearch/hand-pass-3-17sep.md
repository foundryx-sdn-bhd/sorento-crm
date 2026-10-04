# Owner hand pass 3 (AC-1593), 17 Sep 2026 17:31 to 17:38 MYT, phone via tunnel, head 99b97c8fb + parser v24, clone, contact 900000008

25 console turns stored on the clone. Rulings pending the owner's go (alignment list sent 17 Sep evening).

| # | Turns | What happened | Measured cause | Proposed ruling |
|---|-------|---------------|----------------|-----------------|
| 1 | 9b5e241e "Promo for srtwc286", c7cb01fc "1" | Tier question, then "promotions: No matching results" + escalate offer; 10 promotions exist for SRTWC286. | Fetch envelope for crm_marketing_promotions_list carried entities []: the tier pick settled the tier and dropped the product. No promo stamps on rosters. | A pick settles ONLY the kind it picked; every other carried entity stays on the fetch. Product rosters under promotion carry has promo / no promo stamps (same probe seam). |
| 2 | 463413b0 "All", 57873730 "4", ddffd9ee, 3be73a99 | Header prints the same ledger name once per ledger ("... [A/C I]" x6, "JIMMY - I, JIMMY - I"). | Header lists every uuid's name; family grouping not applied at the header. Confirm "All" fetched all four families. | The answer header names each family once. "All" = every family's uuids. |
| 3 | 8c31162b "For srtwc286 only" | Roster re-asked. | Parser: resolved false with a product entity in the message. | A message naming an entity of a kind the roster is not about is a refinement of the current subject (carry the roster, plan the message). Deterministic post-LLM guard + prompt line. |
| 4 | 9032e9c1 "This month only" | No date window applied. | Parser verdict date_mode null, date_filter empty. | Prompt: DATE section explaining date_mode / date_filter_start / end; engine then refines (R15). |
| 5 | bb233665 "Sales order" after the DO list | "Reply 1 for the delivery order list" re-printed. | Parser: document [] for "Sales order" (v24); engine: resolved false against the detail offer. | Prompt maps sales order / delivery order words to the document slot; engine: a named document while ANY outstanding pending is open = new scope, re-run. |
| 6 | 6463930f "1" (DO list) | Detail list starts at "1. DO Number", no Product / Customer / Location / Order date header. | Compose prints the filter header for the summary only. | The detail list carries the same filter header as the summary. |
| 7 | fb8b32cb "PO for this" | Product roster without stamps. | No PO probe. | Rosters under purchase_order carry has PO / no PO stamps; picker stays (owner). |
| 8 | f82ee09a "Eight" | clarify_menu re-print; "The eigthh one" worked. | Parser did not resolve a word number. | Prompt: word numbers are positions. |
| 9 | 97fb7b49 "2" after the promo miss | Escalate offer re-printed. | Hand pass 2 row 8 (coder 13 in flight). | As ruled. |

Worked: multi-pick "2 3 4", hanlim = one family (no picker), chin chun roster with DO stamps, purchase cost without picker (row 3), outstanding report + DO list with product lines (row 7), "PO for this" + "All".
