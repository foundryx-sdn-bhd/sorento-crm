# UAC: availability-mode wording and discontinued marker (AVAIL-RENDER)

Plan: `PLAN-avail-render-05oct.md`.

- **AC-AR01:** an availability line for a product with `is_discontinued` true prints the
  marker line `⚠️  *(PRODUCT DISCONTINUED)*` under the verdict, on every branch (in_stock,
  short in_stock, too_big, incoming, no_incoming). A product not discontinued prints no marker.
- **AC-AR02:** a dealer ETA line (`CODE: ✅ ETA dd/mm/yyyy`, `CODE: No ETA`) for a
  discontinued product prints the same marker line under it.
- **AC-AR03:** `in_stock` covering the asked quantity prints `✅ Got stock.` and does not end
  with `Please refer to your salesman.`
- **AC-AR04:** `in_stock` short of the asked quantity prints `✅ Got stock, N available.` and
  does not end with the refer. N is the only figure of ours on the line.
- **AC-AR05:** `too_big` and `no_incoming` still end with `Please refer to your salesman.`;
  `incoming` is unchanged (`❌ ETA dd/mm/yyyy.`).
- **AC-AR06:** `_stamp_refers` stamps an in-stock entry `refers_to_salesman = False`, a
  too_big / no_incoming entry True; the Customer asks writer logs only the stamped ones.
- **AC-AR07:** ms and zh: the two new tails have a catalog row each with matching tokens;
  the marker translates as it does for compact (`PRODUK DIHENTIKAN` / `产品已停产`).
- **AC-AR08:** no word-matching on customer text is added; no prompt change.
