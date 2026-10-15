# UAC: CHAT-FIT-LINE (15 Oct)

Card `CARD-chat-fit-line-15oct.md`, crew answers 15 Oct: Q1 (a), Q2 (a), Q3 (a), no split, ms / zh
nouns as proposed. Plan `PLAN-chat-fit-line-15oct.md`.

Codes below are placeholders `<WC>`, `<cover>`, `<cabinet>`, `<mirror>`, `<shelf>`; tests seed
their own `ZZT*` products and links (CI's DB is empty).

## Slice 1: every link kind resolves (seat cover, mirror, shelf; both directions)

- AC-1 The parse's find filter carries `kind`, one of the `KINDS` keys
  (`product_compatibility_service.py:60-64`) or null; the strict schema lists exactly those keys
  plus null, and generating it from `KINDS` means a new kind needs no schema edit.
- AC-2 `{{find_filters}}` names every `KINDS` pair (seat cover / WC, mirror / cabinet, shelf /
  cabinet) and asks the parser for `kind`; the seat cover wording that ships today stays.
- AC-3 `fits` + kind `mirror` on a cabinet code resolves to that cabinet's mirror link(s) only;
  kind `shelf` to its shelf link(s) only; kind `seat_cover` to seat cover links only (a cabinet's
  mirror link never answers a seat cover ask).
- AC-4 Reverse: an accessory code with its kind resolves to its host code(s) (the data decides
  the direction, as today for seat covers).
- AC-5 No link of that kind on record for any named code: the whole reply is one line per code,
  no fetch: `No <accessory> is on record for <code>.` for a host code, `No <host> is on record for
  <code>.` for an accessory code. Seat cover wording unchanged (`No seat cover is on record for
  <code>.`, `No WC is on record for <code>.`); new: `No mirror ...`, `No shelf ...`, `No cabinet ...`.
- AC-6 A carried fit ask (bare code right after a fit answer) keeps the kind of the fit ask it
  carries. A relation stored before this lane (no kind) reads as seat cover.
- AC-7 Code never maps a message word to a kind; kind comes from the parse (or the carry).

## Slice 2: the fit line

- AC-8 A reply whose products were reached through a link starts with the fit line, then a blank
  line, then the usual answer exactly as it is for the same codes typed directly (entity-only,
  stock, ETA, price, photos; staff and dealer).
- AC-9 Forward (host named), one linked product: `<Accessory> for <host> is <linked>.`
  e.g. `Seat cover for <WC> is <cover>.`, `Mirror for <cabinet> is <mirror>.`,
  `Shelf for <cabinet> is <shelf>.`
- AC-10 Forward, several: `<Accessory> for <host>: <a>, <b>` (no full stop, no " · "), codes in
  the order the answer lists them.
- AC-11 Reverse (accessory named), one: `<accessory> fits <host>.`; several:
  `<accessory> fits these <hosts>: <a>, <b>` (`WCs`, `cabinets`).
- AC-12 Two anchors in one message: one line per anchor; two kinds asked of one cabinet: one line
  per kind.
- AC-13 No line when no product was reached through a link: typed codes, a narrowing follow-up
  (`find` null), the no-link reply of AC-5.
- AC-14 ms and zh: the line is said in the turn's language from the label catalog:
  ms `Penutup tempat duduk untuk <WC> ialah <cover>.`, `Cermin untuk <cabinet> ialah <mirror>.`,
  `Rak untuk <cabinet> ialah <shelf>.`, reverse `<x> sesuai untuk <host>.`;
  zh `<WC> 的马桶盖是 <cover>。`, `<cabinet> 的镜子是 <mirror>。`, `<cabinet> 的置物架是 <shelf>。`,
  reverse `<x> 适配 <host>。`; several use the catalogued heads (`Penutup tempat duduk untuk
  {code}:` / `{code} 适配的马桶盖：` and per kind). Nouns: cermin / 镜子, rak / 置物架, kabinet / 浴室柜.
- AC-15 The line holds product codes only: no ids, no stock numbers; same for every audience.

## Live (opt-in, `CHATBOT_LIVE_LLM=1`, dev parser v94 = prod v63 shape)

- AC-16 >= 8 varied real messages (en / zh / ms / mixed; seat cover, mirror, shelf; with and
  without a stock / price ask; WC variants) parse to `fits` with the right `kind`, and the seat
  cover asks that pass today still pass.
