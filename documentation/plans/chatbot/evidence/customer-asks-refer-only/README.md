# Browser evidence: CUSTOMER-ASKS-REFER-ONLY (1 Oct 2026)

agent-browser 0.27.0, headless Chromium, `npm run dev` against a cloud-sandbox backend on a
freshly bootstrapped database (no prod data): one superadmin linked to sales agent `SEAN I`,
a second agent `WT I`, five open asks across 27 Sep to 1 Oct and one done today.

Reached by sidebar click from `/`: Sales > Customer asks.

| File | Viewport | What it shows |
|---|---|---|
| `asks-1280-cards.png` | 1280x900 | One `Open` heading (not red), cards oldest first (27/09, 30/09, 01/10, 01/10), each with its date; `Done today` below. No `Needs attention`, no `Today`. |
| `asks-1280-list.png` | 1280x900 | DataGrid: an `Open` group row, the four open rows oldest first, then `Done today`. |
| `asks-375-cards.png` | 375x812 | The same cards, no horizontal page scroll (`scrollWidth` 360). |
| `asks-375-list.png` | 375x812 | The grid scrolls inside its own container. |

The Agent select listed `All agents`, `SEAN I · 4 open`, `WT I · 1 open` (accessibility snapshot;
no "need attention" count).
