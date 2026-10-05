#!/usr/bin/env bash
# ORDER-BROADEN red-first + kill tests, in a detached worktree of the branch head so the
# running suite in the primary checkout is untouched. Each fix is reverted to origin/main
# on its own, then both together; the addendum is emptied for the static tests.
set -u
# Usage: kill-tests.sh <detached worktree of the branch head> [python with the backend venv]
#   git worktree add --detach /tmp/kill-wt HEAD && cp sorento_crm_backend/.env.ci-tests /tmp/kill-wt/sorento_crm_backend/
WT=${1:?worktree path}
PY=${2:-$(git -C "$WT" rev-parse --show-toplevel)/sorento_crm_backend/venv/bin/python}
BAK=$(mktemp)
cd "$WT/sorento_crm_backend"
export SORENTO_ENV_FILE=.env.ci-tests
T=tests/chatbot/test_order_broaden_presentation.py
COMPOSE=app/services/chatbot/turn/compose.py
APPLY=app/services/chatbot/turn/apply.py
ADD=alembic/data/chatbot_semantic_parser.order-broaden.addendum.txt

run() { timeout 900 $PY -m pytest "$@" -p no:warnings -rN 2>&1 | grep -E "::test_.* (PASSED|FAILED|ERROR)|^=+ [0-9]+ (passed|failed)" | sed 's#tests/chatbot/##'; }
revert() { git checkout -q origin/main -- "$@"; }
restore() { git checkout -q HEAD -- "$@"; }

echo "### head $(git rev-parse --short HEAD)"
echo; echo "### A. both fixes in place (expected: 4 passed)"; run $T -v | sed 's/ \[.*//'
echo; echo "### B. red first: BOTH fixes reverted to origin/main (expected: 3 failed, control passes)"
revert $COMPOSE $APPLY; run $T -v | sed 's/ \[.*//'; restore $COMPOSE $APPLY
echo; echo "### C. kill 1: compose.py alone reverted (dealer-view date line)"
revert $COMPOSE; run $T -v | sed 's/ \[.*//'; restore $COMPOSE
echo; echo "### D. kill 2: apply.py alone reverted (domain change drops window)"
revert $APPLY; run $T -v | sed 's/ \[.*//'; restore $APPLY
echo; echo "### E. kill 3: addendum emptied (static patch tests)"
cp $ADD "$BAK"; printf '    nothing\n' > $ADD
run tests/chatbot/test_order_broaden_prompt_patch.py -v | sed 's/ \[.*//'
cp "$BAK" $ADD
echo; echo "### worktree clean? (expect no output)"; git status --short
