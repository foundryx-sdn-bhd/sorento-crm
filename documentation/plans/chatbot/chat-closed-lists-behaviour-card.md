# Behaviour card: CHAT-CLOSED-LISTS (chatbot step 2)

Status: answered by crew 17 Oct (all five (a)). Plan: `PLAN-chat-closed-lists-17oct.md`.

## Rules

- **R1 Lists, not guesses.** Every turn, the parser is given the closed lists the message could be about: spec values
  (the full registry, as today, plus a short per-turn candidate list for the words of this message), file types (every
  attachment type a product can carry) and certification types (cert kinds plus the schemes on file).
- **R2 The parser picks.** It answers with an item from a list, or "none". It never writes a free word for a spec
  value, a file type or a certification.
- **R3 "None" is asked, never guessed.** When the parser picks nothing for a file type, the bot asks which file,
  listing the file types that product has. When it picks nothing for a spec word, the bot says plainly it does not
  know that word for that product kind and names the choices (today's unknown-value sentence).
- **R4 No needless menu.** When the parser picks a kind with confidence ("WC" = the water closet class), the bot
  does not ask "product or attachment type?". The kind menu stays only for a token the parser itself marks unsure.
- **R5 Code checks, never interprets.** A pick off the list the turn rendered is dropped and asked (R3). No code reads
  the customer's words to fix a pick.
- **R6 A missing attribute is said plainly** (owner CP0 ruling): "SRTWC286 has no technical drawing on record."
- **R7 A set reply with no product is a failure** (owner CP0 ruling): a spec or certificate set answer lists products,
  capped, with "reply to see more"; never a count alone.
- **R8 Price tier only after a match** (owner CP0 ruling): the price-tier question is asked only once a product that
  matches the spec has been found.

## Examples (from the owner's CP0 notes; exact card texts are crew-only)

| # | Message (shape) | Today | After |
|---|---|---|---|
| E1 (c06) | "WC ..." | "WC (product) or WC (attachment type)?" | WC read as the water closet class from the list, answered, no menu |
| E2 (c04/c19) | a certification ask naming a type ("SIRIM cert for ...") | regex strips words, scheme guessed from leftovers | parser picks the scheme from the certification list; the files of that scheme are sent, or "no SIRIM certificate on record" |
| E3 (c07) | a spec ask in the customer's words | free word, exact match misses, "couldn't find" | the word's candidate (synonym or near spelling) is on the list; the parser picks the registry value; products listed |
| E4 (c27) | a file ask by a type word ("TD", "lukisan") | prefix/description probe, sometimes the wrong type | parser picks `technical_specifications` from the file-type list |
| E5 | a type the system does not have ("BIM file") | dropped silently, generic reply | "I don't have BIM files. I have: Product photos, Technical drawings, ..." |

## Edge cases

- A word that is both a code fragment and a class ("WC" also prefixes codes): both are candidates, each labelled with
  its kind; the parser picks; a pick of the class with a code in the same message keeps the code.
- Two certification schemes in one message: both picked, an AND over the register.
- A spec value the registry has but no product carries: R6 wording, not "unknown".
- A list that grew past the cap (many near spellings): the closest first by code order of retrieval (exact, prefix,
  name, synonym, near spelling), cut at the cap; the cap is printed in the trace.
- Empty lists (fresh install, no attachment types): the section's lists render empty and `listed_item` is null-only, so every file ask is asked (R3); the section text itself stays (reviewer S5, 17 Oct).

## Questions (at most 5)

1. **Near spellings for the shortlist.** May code use trigram similarity ONLY to ADD candidates to the list (the parser
   still picks, code never binds on it)? (a) yes, shortlist only; (b) no, exact / prefix / synonym hits only.
   Recommendation: (a), because without it "phtos" or "basn" never reaches the list and the parser cannot pick it.
2. **What counts as a certification type.** (a) cert kinds (`is_certificate` attachment types) plus every scheme on an
   active certificate plus the `certificate_scheme` lookup set; (b) schemes only. Recommendation: (a), because staff
   ask by both ("cert", "SIRIM", "Ikram").
3. **"None" for a file type.** (a) ask which file, listing the types that product has (R3); (b) hand over.
   Recommendation: (a), one question, real options.
4. **Delete order vs the production prompt.** The rewrites read old-prompt emissions too. (a) delete only those the
   L1 replay shows dead under the production prompt now; hold the rest until the owner moves the label; (b) delete
   all behind the gate in this PR. Recommendation: (a), because production keeps working whatever label is live.
5. **Card texts.** Can crew paste the five CP0 TRAIN cards (c04, c06, c07, c19, c27: last messages + the bot reply) into
   this PR so the red tests use the real shapes? (a) yes; (b) no, keep shapes only. Recommendation: (a), they are TRAIN.

## Crew answers (17 Oct)

Q1 (a): the owner's D2. Similarity only ADDS candidates to the shortlist; code never binds on it. Q2 (a): cert kinds
+ schemes on file + the `certificate_scheme` lookup set. Q3 (a): one question listing the file types that product
really has. Q4 (a): delete only rewrites the L1 replay proves dead under the production prompt; hold the rest. Q5:
crew sends the five CP0 card texts masked, as a session message; they are NOT committed (public repo, owner rule).
Red tests use the same shapes with synthetic codes and names.
