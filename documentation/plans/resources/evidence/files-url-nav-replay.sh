#!/usr/bin/env bash
# FILES-URL-NAV browser replay (agent-browser; stands in for a Playwright spec, CLAUDE.md
# "Browser verification"). Replays the crew-audit blocker on PR #1759:
#   open A > B > C, RELOAD, Back, Back, open D (a child of A), Back
# and fails if the page ends on a loading skeleton or the breadcrumb disagrees with the URL.
#
# Usage: FILES_URL=http://localhost:3000/resource-management/attachment-directories \
#        A=<folder id> B=<child of A id> B_NAME="<its name>" C=<child of B id> C_NAME="<its name>" \
#        D_NAME="<name of another child of A>" \
#        bash files-url-nav-replay.sh
# The browser session must already be signed in (agent-browser --session "$SESSION").
set -euo pipefail
SESSION="${SESSION:-fun}"
AB() { npx -y agent-browser@0.27.0 --session "$SESSION" "$@"; }
crumbs() { AB eval "[...document.querySelectorAll('nav[aria-label=Breadcrumb] button')].map(b=>b.textContent.trim()).join(' > ')"; }
skeletons() { AB eval "document.querySelectorAll('[data-slot=skeleton], .animate-pulse').length"; }
dir() { AB eval "new URLSearchParams(location.search).get('directoryId') || ''" | tr -d '"'; }
fail() { echo "FAIL: $*"; exit 1; }

# A by URL, then B and C by clicks: the clicks are in-app history entries, which is what
# the blocker needs (a full page load per step does not reproduce it).
AB open "$FILES_URL?directoryId=$A" >/dev/null; sleep 8
AB find text "$B_NAME" click >/dev/null; sleep 4; [ "$(dir)" = "$B" ] || fail "click $B_NAME not on B"
AB find text "$C_NAME" click >/dev/null; sleep 4; [ "$(dir)" = "$C" ] || fail "click $C_NAME not on C"
AB reload >/dev/null; sleep 8
AB back >/dev/null; sleep 4; [ "$(dir)" = "$B" ] || fail "Back 1 not on B"
AB back >/dev/null; sleep 4; [ "$(dir)" = "$A" ] || fail "Back 2 not on A"
AB find text "$D_NAME" click >/dev/null; sleep 6
[ "$(dir)" != "$A" ] || fail "opening $D_NAME did not change the URL"
AB back >/dev/null; sleep 6
[ "$(dir)" = "$A" ] || fail "Back 3 not on A"
[ "$(skeletons)" = "0" ] || fail "stuck on a loading skeleton ($(skeletons) skeletons), crumbs: $(crumbs)"
crumbs | grep -qv "$D_NAME" || fail "breadcrumb still shows $D_NAME: $(crumbs)"
echo "PASS: crumbs $(crumbs)"
