"""Ordering one contact's turns, and re-injecting a failed one (AC-705, AC-709, AC-710).

Two jobs, one module, because they are the same subject seen twice: WHICH turn for this
contact runs next. The ordering half is the S7 replacement for n8n's `sorento-dispatcher`,
which popped one contact per second and therefore served a 50-dealer burst over 50 seconds
no matter how fast the CRM was. The re-inject half puts an operator's Retry back through
the front door so it takes the same ordering everything else takes.

R4: there is no automatic retry anywhere. An operator presses Retry on the trace screen,
and what that does is put the customer's ORIGINAL message back through the front door -
n8n's inject webhook, the same one the failover poller uses - so the turn re-enters with
the same ordering, the same lanes and the same sending path a live message gets.

**The CRM does not send, and does not re-run the turn in place.** Both would be the same
mistake in different clothes: the engine answering from a click would bypass n8n's egress
containment (D9), and a fresh in-process run would write a reply nobody delivered. What
happens instead is one HTTP POST of bytes the CRM already has, and then the turn arrives
back the ordinary way, as its own row.

**Unset by default, on purpose.** With no URL on the default respond workspace the caller
answers 409 `retry_unavailable` and nothing is posted. A dev machine that silently
injected into production n8n would answer a real customer from a developer's click, and
the failure would look like a bug in the customer's conversation rather than in a config
screen.

**S8a: the URL and key live on the WORKSPACE ROW, not the environment** (AC-804; owner
ruling, 5 Sep: nothing chatbot-shaped in `.env`, it does not scale past one tenant). That
makes them admin-editable, so both are checked by `app/services/outbound_url_guard.py`
before anything is sent - https only, never loopback / private / link-local, never the CRM
itself - and the POST follows no redirects.

From S7 the URL points at the thin spine's own webhook. Nothing else here changes: the
body is the respond.io webhook body either way, which is exactly why this seam is one
function and one row rather than a transport abstraction.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from redis import exceptions as redis_exceptions

from app.services.outbound_url_guard import assert_safe_outbound_url
from app.services.queue_service import REDIS_SOCKET_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Per-contact ordering (AC-709, AC-710, H30, H31; rebuilt by CHATBOT-QUEUE-FIX, 1 Oct)
#
#   chatbot:seq:{contact}            the ticket counter. INCR on arrival; a turn's ticket is
#                                    its place in the queue for THIS contact only.
#   chatbot:done:{contact}           every ticket up to here has finished (or died). Absent
#                                    means zero.
#   chatbot:alive:{contact}:{ticket} ONE KEY PER TICKET, present while that ticket's turn is
#                                    alive. Stamped in the same script as the INCR, kept fresh
#                                    by a heartbeat thread (`Heartbeat`), deleted on release.
#
# A ticket may run once `done >= ticket - 1`. `done` only ever moves through `_settle`, which
# walks it forward over every ticket whose alive key is GONE - finished, or dead and lapsed -
# and stops at the first one that is still alive. That one rule replaces three that failed
# on prod (contact 900000081, 1 Oct):
#
# * The old single `running` key was overwritten by every new ticket, so a waiter read its
#   OWN stamp as a live predecessor and the dead-predecessor repair could never fire.
# * A ticket that gave up waiting marked itself done, which let its successors run beside a
#   predecessor that was still working. `_settle` cannot pass a live ticket, whoever calls it.
# * `seq` and `done` expired on separate clocks (seq's TTL refreshed at take, done's at
#   release), so after a quiet hour `seq` restarted at 1 under a stale, higher `done`: ticket
#   1's release was a no-op, the stale `done` lapsed, and ticket 2 waited 45 s for a ticket 1
#   that had finished a minute earlier. The take now resets a `done` from an earlier session.
#
# A turn whose process is killed simply stops heart-beating; its alive key lapses within
# `ALIVE_TTL_SECONDS` and the next poll walks past it. No grace timer, no repair path.
#
# Redis, not Postgres advisory locks: the wait is a poll of one integer, it must not hold a
# database connection while it waits (the capacity rule), and redis is already the queue
# substrate this process talks to. The alive keys are built inside the Lua scripts from a
# prefix because their name depends on the ticket number INCR returns; this is a single
# redis instance, not a cluster.
# --------------------------------------------------------------------------- #

# The counter is per conversation and a conversation goes quiet. An hour after the last
# message the keys are worthless, and a fresh contact starting again at ticket 1 is
# correct - nothing is waiting on the old numbers.
TICKET_TTL_SECONDS = 3600

# How long a ticket counts as alive after its last heartbeat. It must clear the redis
# socket timeout (`queue_service.REDIS_SOCKET_TIMEOUT_SECONDS`, 10 s) so ONE slow refresh
# cannot make a live turn look dead; with a beat every `HEARTBEAT_INTERVAL_SECONDS` a turn
# has to miss three refreshes in a row before its successors stop waiting for it.
ALIVE_TTL_SECONDS = float(REDIS_SOCKET_TIMEOUT_SECONDS) + 5.0
HEARTBEAT_INTERVAL_SECONDS = 4.0

# The longest a heartbeat keeps one ticket alive. A turn that is still holding its ticket
# past this is hung (n8n gave up on it at 90 s, `send_order.N8N_CHAT_TURN_TIMEOUT_SECONDS`),
# and beating forever would hold every later message from the contact at the queue cap for
# the life of the process. Past it the heartbeat stops with an ERROR and the ticket lapses.
MAX_HOLD_SECONDS = 120.0

# The waiter polls; it does not subscribe. One script call every 200 ms for at most the
# queue-wait window is a handful of cheap reads, and a pub/sub channel per contact would be
# a second mechanism to keep alive for no measured gain.
POLL_INTERVAL_SECONDS = 0.2

# How many tickets one `_settle` call walks. A conversation is a handful of tickets an hour;
# the cap only bounds a pathological backlog so one script call stays short.
SETTLE_MAX_STEPS = 64


# The queue wait is what is LEFT of n8n's turn budget once the waiting turn's own work is
# paid for (crew decision, 1 Oct: the reply must never be lost). A turn that waits the whole
# cap and then runs still has to answer inside n8n's HTTP timeout, or n8n drops the reply.
#   n8n turn budget   send_order.N8N_CHAT_TURN_TIMEOUT_SECONDS   90 s
#   parser call       llm_call.CALL_DEADLINE_SECONDS             35 s
#   lane work         LANE_BUDGET_SECONDS (DB + MCP fetches; prod looked_up <= 4.8 s)
#   margin            QUEUE_MARGIN_SECONDS (respond.io / n8n hops, row writes)
LANE_BUDGET_SECONDS = 15.0
QUEUE_MARGIN_SECONDS = 10.0


def max_queue_wait_seconds() -> float:
    """The ceiling on `chatbot_queue_wait_seconds`, derived from the budgets above."""
    from app.services.chatbot.llm_call import CALL_DEADLINE_SECONDS
    from app.services.chatbot.send_order import N8N_CHAT_TURN_TIMEOUT_SECONDS

    return max(
        0.0,
        N8N_CHAT_TURN_TIMEOUT_SECONDS
        - CALL_DEADLINE_SECONDS
        - LANE_BUDGET_SECONDS
        - QUEUE_MARGIN_SECONDS,
    )


def queue_wait_seconds(configured: float) -> float:
    """The wait a turn actually uses: the configured value, never above the ceiling."""
    return min(float(configured), max_queue_wait_seconds())


class QueueWait(RuntimeError):
    """The wait for this contact's turn exceeded the budget while a predecessor was still
    alive. The engine runs the turn anyway (ordering is best effort past the cap)."""


# What "redis is not answering" looks like from here. The engine catches these and runs the
# turn UNORDERED rather than failing it: ordering is an improvement on the reply's order,
# and a redis blip costs the chatbot nothing on this path today. Trading "answered out of
# order" for "not answered at all" would make the ordering flag a new way for the whole
# chatbot to go down. `OSError` rides along because a socket error can reach the caller
# before redis-py has wrapped it.
ORDERING_ERRORS: tuple[type[BaseException], ...] = (redis_exceptions.RedisError, OSError)


def seq_key(contact: str) -> str:
    return f"chatbot:seq:{contact}"


def done_key(contact: str) -> str:
    return f"chatbot:done:{contact}"


def _alive_prefix(contact: str) -> str:
    return f"chatbot:alive:{contact}:"


def alive_key(contact: str, ticket: int) -> str:
    return f"{_alive_prefix(contact)}{int(ticket)}"


def _as_int(value: Any) -> int:
    """A redis value as an int. Absent is 0, and so is anything unreadable.

    Both client shapes reach this: the engine's connection decodes nothing
    (`queue_service.redis_conn`, `decode_responses=False`), a test's client decodes
    everything.
    """
    if value is None:
        return 0
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# INCR, the session reset and the liveness stamp in ONE script: Lua runs to completion
# before redis serves anything else, so there is no instant at which the ticket exists
# and its holder does not. `done` gets its TTL refreshed here too, so the two counters
# age together; a `done` at or past the new ticket can only be from an earlier session
# (the counter restarted under it) and is pulled back to `ticket - 1`.
_TAKE_TICKET_LUA = """
local ticket = redis.call('INCR', KEYS[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
local done = tonumber(redis.call('GET', KEYS[2])) or 0
if done >= ticket then
  redis.call('SET', KEYS[2], ticket - 1, 'EX', ARGV[1])
elseif redis.call('EXISTS', KEYS[2]) == 1 then
  redis.call('EXPIRE', KEYS[2], ARGV[1])
end
redis.call('SET', ARGV[3] .. ticket, '1', 'PX', ARGV[2])
return ticket
"""

# Walk `done` forward over tickets that are no longer alive, never past `seq`, never past a
# live one. Monotone by construction: it only ever adds. Returns the new `done`.
_SETTLE_LUA = """
local done = tonumber(redis.call('GET', KEYS[1])) or 0
local seq = tonumber(redis.call('GET', KEYS[2])) or 0
local start = done
local steps = 0
while done < seq and steps < tonumber(ARGV[3]) do
  if redis.call('EXISTS', ARGV[2] .. (done + 1)) == 1 then
    break
  end
  done = done + 1
  steps = steps + 1
end
if done > start then
  redis.call('SET', KEYS[1], done, 'EX', ARGV[1])
end
return done
"""


def _settle(redis: Any, contact: str) -> int:
    return _as_int(
        redis.eval(
            _SETTLE_LUA,
            2,
            done_key(contact),
            seq_key(contact),
            int(TICKET_TTL_SECONDS),
            _alive_prefix(contact),
            int(SETTLE_MAX_STEPS),
        )
    )


def contact_ticket(redis: Any, contact: str) -> int:
    """This turn's place in the queue for this contact, stamped alive in the same script.

    The caller starts a `Heartbeat` right after, and must, or the stamp lapses after
    `ALIVE_TTL_SECONDS` and successors stop waiting for this turn.
    """
    return int(
        redis.eval(
            _TAKE_TICKET_LUA,
            2,
            seq_key(contact),
            done_key(contact),
            int(TICKET_TTL_SECONDS),
            int(ALIVE_TTL_SECONDS * 1000),
            _alive_prefix(contact),
        )
    )


def beat(redis: Any, contact: str, ticket: int) -> None:
    """Refresh this ticket's alive key once."""
    redis.set(alive_key(contact, ticket), 1, px=int(ALIVE_TTL_SECONDS * 1000))


class Heartbeat:
    """Keeps one ticket alive from the take to the release, on a daemon thread.

    A thread because the turn spends its life in blocking calls (the LLM, the database,
    MCP) and nothing in it yields. A refresh that fails is logged and retried on the next
    beat; three missed in a row and the ticket reads as dead, which is the intended
    behaviour for a process that has lost redis.
    """

    def __init__(self, redis: Any, contact: str, ticket: int) -> None:
        self._redis = redis
        self._contact = contact
        self._ticket = int(ticket)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name=f"chatbot-heartbeat-{self._ticket}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        started = time.monotonic()
        while not self._stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            if time.monotonic() - started > MAX_HOLD_SECONDS:
                logger.error(
                    "chatbot ordering: ticket %s of %s held its slot past %.0fs, "
                    "letting it lapse",
                    self._ticket,
                    self._contact,
                    MAX_HOLD_SECONDS,
                )
                return
            try:
                beat(self._redis, self._contact, self._ticket)
            except Exception:  # noqa: BLE001 - a heartbeat thread must never die loudly
                logger.warning(
                    "chatbot ordering: heartbeat failed for ticket %s of %s",
                    self._ticket,
                    self._contact,
                    exc_info=True,
                )

    def stop(self) -> None:
        """Stop beating. Joined for at most a second: a beat stuck on a hung redis call is
        not worth holding the request thread for. If one does land after the release, the
        key it writes lapses on its own TTL and only delays the next turn by that much."""
        self._stop.set()
        self._thread.join(timeout=1.0)


def start_heartbeat(redis: Any, contact: str, ticket: int) -> Heartbeat:
    return Heartbeat(redis, contact, ticket)


def mark_done(redis: Any, contact: str, ticket: int) -> None:
    """Release this ticket. Called from a `finally`, so a FAILED turn releases too.

    Deletes the alive key and settles: `done` moves up to (and past) this ticket only if
    every earlier one is finished or dead. A turn that ran past a timed-out wait while its
    predecessor was still working therefore never releases the predecessor's successors.
    The caller stops the `Heartbeat` FIRST, or a late beat could resurrect the key.
    """
    redis.delete(alive_key(contact, ticket))
    _settle(redis, contact)


def wait_for_turn(redis: Any, contact: str, ticket: int, *, timeout_s: float) -> None:
    """Block until every earlier ticket for this contact has finished or died.

    Each poll settles the counter, so a dead predecessor (alive key lapsed) is walked past
    on the first poll after its last heartbeat expires. Raises `QueueWait` if a predecessor
    is still alive after `timeout_s`; it never hangs the request past the budget, and it
    never writes `done` on its way out.
    """
    target = int(ticket) - 1
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        if _settle(redis, contact) >= target:
            return
        if time.monotonic() >= deadline:
            raise QueueWait(
                f"waited {timeout_s}s for ticket {target} of contact {contact}, still running"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


# --------------------------------------------------------------------------- #
# Re-injecting a failed turn (AC-705)
# --------------------------------------------------------------------------- #

# n8n's HTTP node waits the whole turn, so this is generous: the call being answered
# means "n8n accepted the message", not "the turn finished".
REINJECT_TIMEOUT_SECONDS = 10.0

RETRY_KEY_HEADER = "X-Chatbot-Retry-Key"


class RetryUnavailable(RuntimeError):
    """No ingress is configured, so nothing was posted. The caller answers 409."""


class ReinjectFailed(RuntimeError):
    """The ingress answered non-2xx. The caller answers 502 and leaves the row alone.

    **The message is the status code and a fixed sentence. It never carries the ingress
    response body**, and `body` is kept only so the server log can hold it. Echoing the
    body made the acknowledged DNS-rebinding residual a read oracle: a host that answers
    public to the guard's `getaddrinfo` and private to httpx's own resolution had its
    first 500 bytes returned in the 502 an operator reads, and written to
    `integration_logs.error_message`. That is blind SSRF upgraded to a read of an internal
    service, and it made the guard's "one DNS round trip wide" claim untrue.
    """

    def __init__(self, status_code: int, body: str = "") -> None:
        super().__init__(
            f"The retry ingress refused the re-injection (HTTP {status_code})."
            if status_code
            else "The retry ingress could not be reached."
        )
        self.status_code = status_code
        self.body = body


def _default_workspace(db: Any) -> Any:
    """The row the retry config lives on.

    The DEFAULT workspace, not the turn's contact's workspace, and the difference is
    deliberate: the ingress is one webhook per install (n8n has one inject entry point),
    while a contact's workspace says which respond.io space the CONVERSATION belongs to.
    Reading the retry URL per contact would let a second workspace silently change where
    a retry lands. The trigger to make it per contact is a second n8n instance.
    """
    from app.services.respond_workspace_service import RespondWorkspaceService

    return RespondWorkspaceService(db).get_default()


def ingress_url(db: Any) -> str | None:
    """The configured inject webhook, or None when retry is not wired for this install."""
    row = _default_workspace(db)
    url = (getattr(row, "chatbot_retry_ingress_url", None) or "").strip()
    return url or None


def retry_available(db: Any) -> bool:
    """Read by the endpoint so the UI can disable Retry rather than offer a 409."""
    return ingress_url(db) is not None


def reinject_envelope(db: Any, row: Any) -> None:
    """Re-post the turn's ORIGINAL respond.io webhook body to the ingress.

    `row` is a `ChatbotTurn`. What goes on the wire is `envelope.message` exactly as it
    was stored - not a rebuilt envelope. n8n's webhook is the producer's contract and it
    expects a respond.io body; handing it anything the CRM composed would make the retry
    path a second, untested shape of the thing being retried.

    Raises `RetryUnavailable` (nothing configured, nothing sent), `OutboundUrlRejected`
    (the stored URL breaks a rule - checked HERE and not only at save time, because a DNS
    answer or a directly-written row can change under a save-time check) or
    `ReinjectFailed` (the ingress refused).
    """
    import httpx

    from app.services.respond_workspace_service import RespondWorkspaceService

    workspace = _default_workspace(db)
    url = (getattr(workspace, "chatbot_retry_ingress_url", None) or "").strip()
    if not url:
        raise RetryUnavailable(
            "no retry ingress is configured for this install (set the chatbot retry "
            "webhook URL on the default workspace under System > Respond Workspaces)"
        )
    # Re-checked at USE time. See `outbound_url_guard`: a save-time check alone is a
    # TOCTOU window, and this is the moment a socket is about to open.
    assert_safe_outbound_url(url, label="The chatbot retry webhook URL")

    envelope = row.envelope if isinstance(row.envelope, dict) else {}
    body = envelope.get("message")
    if not isinstance(body, dict) or not body:
        raise RetryUnavailable(
            "this turn did not store the original message, so there is nothing to re-post"
        )

    headers = {"Content-Type": "application/json"}
    key = RespondWorkspaceService.decrypt_chatbot_retry_key(workspace)
    if key:
        headers[RETRY_KEY_HEADER] = key

    try:
        response = httpx.post(
            url,
            json=body,
            headers=headers,
            timeout=REINJECT_TIMEOUT_SECONDS,
            # Explicit, never the library default: a 30x from the ingress to a loopback or
            # metadata address would walk the request straight past every rule the guard
            # above just applied, and httpx's default has changed before.
            follow_redirects=False,
        )
    except Exception as exc:  # noqa: BLE001 - a transport failure is the ingress refusing
        logger.warning("chatbot retry re-inject failed to reach the ingress: %s", exc)
        raise ReinjectFailed(0, str(exc)) from exc

    if response.status_code >= 300:
        # The body goes to the SERVER LOG and stops there. See `ReinjectFailed`.
        logger.warning(
            "chatbot retry re-inject rejected: %s %s", response.status_code, response.text[:300]
        )
        raise ReinjectFailed(response.status_code, response.text[:500])

    logger.info(
        "chatbot retry re-injected turn %s for contact %s", row.id, row.contact_respond_id
    )
