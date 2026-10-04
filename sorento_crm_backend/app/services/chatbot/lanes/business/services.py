"""The three I/O seams `sub-resolve-and-gate` has, as injectable callables.

n8n makes them two HTTP calls, an `executeWorkflow` call and an MCP client node; the port
makes them in-process service calls. They are grouped here rather than reached for inline
so a test can replay a captured turn with NO database, NO MCP server and NO network - which is what
makes the 254-fixture replay a pure function over JSON (AC-602).

| n8n node | this seam | CRM service |
| --- | --- | --- |
| `get-access-types` (httpRequest) | `access_types` | `ContactAccessTypeService.resolve_active_access_levels_for_contact` |
| `resolve-entity` (httpRequest) | `resolve_entity` | the function behind `POST /api/v1/system/references/resolve` |
| `probe-incoming` / `probe-customer-orders` (executeWorkflow) | `probe` | S6b's fetch, over `MCPRuntimeClient` (D10) |
| `MCP Client1` (mcpClient, raw IP) | `mcp_call` | `MCPRuntimeClient` at `settings.ai_assistant_mcp_url` (H52) |

`Execute 'sub-get-rag'` (executeWorkflow -> an embedding call plus pgvector SQL) has NO
seam: the tool is read off the domain row's own first tool in `fetch.select_tool`,
which touches neither a provider nor the database (H53). Measured over the 740 business
turns in the 7 Sep 2026 prod copy, the search picked that first-listed tool every time,
and its seeding chain cannot run in the deployed backend image at all - so the seam was
paying for an embedding call per turn to answer a question with one answer.

`space_id` is the default respond workspace's, not n8n's hard-coded `364817` (D5).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class AccessTypesFn(Protocol):
    def __call__(self, *, contact_id: str, space_id: str | None) -> list[dict[str, Any]]: ...


class ResolveEntityFn(Protocol):
    def __call__(self, body: dict[str, Any]) -> dict[str, Any]: ...


class ProbeFn(Protocol):
    def __call__(
        self,
        *,
        tool: str,
        contact_id: Any,
        entities: Any,
        semantic_input: dict[str, Any],
        user_prompt: str,
    ) -> Any: ...


class McpCallFn(Protocol):
    def __call__(self, name: str, args: dict[str, Any]) -> Any: ...


class McpProbeFn(Protocol):
    """One MCP tool call, by name, with arguments already built (D10).

    No `db` / `session` parameter, deliberately: this is a NETWORK call, and threading a
    session in is the hold-a-connection-across-I/O hazard the plan's 96/100-connection
    incident is the evidence against. The production binding opens nothing.
    """

    def __call__(self, name: str, args: dict[str, Any]) -> Any: ...


class FamilyFetchFn(Protocol):
    """The product-family read the miss lane makes before offering a sibling.

    n8n does this as an HTTP call to a raw host; in process it is the products service.
    The seam takes a QUERY STRING and nothing else - no url, no session. Its production
    binding opens its own short session, which is why the caller (the answer lane) can
    stay session-free.
    """

    def __call__(self, query: str) -> Any: ...


@dataclass(frozen=True)
class AnswerServices:
    """S6c's two seams: the did-you-mean / sibling probes, and the family fetch."""

    mcp_probe: McpProbeFn
    family_fetch: FamilyFetchFn


@dataclass(frozen=True)
class FetchServices:
    """S6b's ONE seam, same shape as `ResolveGateServices` for the same reason.

    It was three. `embed` and `tool_search` were the two halves of `sub-get-rag` (embed the
    prompt, then search the tool pool) and both are gone: `fetch.select_tool` reads the
    domain's tool off `turn.policy.default_policy()`, so the only I/O the fetch step still does is
    the MCP call. A bundle with one field is kept as a dataclass rather than collapsed to a
    bare callable because every lane takes a bundle and the next seam this step grows
    belongs in it.
    """

    mcp_call: McpCallFn


@dataclass(frozen=True)
class ResolveGateServices:
    """One bundle, three callables. Stub any of them in a test; none of them is optional."""

    access_types: AccessTypesFn
    resolve_entity: ResolveEntityFn
    probe: ProbeFn


# --------------------------------------------------------------------------- #
# Production bindings
# --------------------------------------------------------------------------- #


def _access_types(db: Session, *, default_space_id: str | None = None) -> AccessTypesFn:
    def call(*, contact_id: str, space_id: str | None) -> list[dict[str, Any]]:
        """`GET /external/contact-access-types/active` - `[{name, keywords}]`.

        A contact / space pair the CRM does not know raises, exactly as the endpoint's
        404 did. n8n carries `onError: continueErrorOutput` on this node with the error
        branch unwired, so today that turn simply STOPS; in the port it becomes a failed
        turn with a reason, which `run_turn`'s handler records (H32).
        """
        from app.services.contact_access_type_service import ContactAccessTypeService

        workspace = space_id if space_id else default_space_id
        return ContactAccessTypeService(db).resolve_active_access_levels_for_contact(
            str(contact_id or "").strip(), str(workspace or "").strip()
        )

    return call


def _resolve_entity(db: Session) -> ResolveEntityFn:
    def call(body: dict[str, Any]) -> dict[str, Any]:
        """`POST /api/v1/system/references/resolve`, in process.

        The ROUTE function is called, not `_resolve_input` alone: the spec-search
        fallback, the brand stamp and the `ENTITY_PIN_MISMATCH` 400 (H38) all live in the
        route body, and re-implementing that half here would be a second resolver that
        drifts. `contact_id` / `space_id` are query params the route never reads, so the
        in-process call needs neither.

        `current_user` exists only to attribute the spec-search LLM usage log. It is the
        same principal the n8n key resolves to (`EXTERNAL_API_KEY_ACT_AS_USER_ID`), so the
        usage rows keep landing on the same user after the cut.
        """
        from app.api.v1.system.references import ResolveReferenceRequest, resolve_reference_post
        from app.config import settings

        principal = {"id": getattr(settings, "external_api_key_act_as_user_id", None)}
        return resolve_reference_post(
            ResolveReferenceRequest(**body), current_user=principal, db=db
        )

    return call


def _probe(db: Session | None = None) -> ProbeFn:
    """No session parameter is USED: the probe is an MCP call, not a database one (D10).

    The parameter exists so the bundle builder can pass its session without knowing that,
    exactly as `_mcp_probe` takes one and ignores it.
    """
    probe = _mcp_probe(db)

    def call(
        *,
        tool: str,
        contact_id: Any,
        entities: Any,
        semantic_input: dict[str, Any],
        user_prompt: str,
    ) -> Any:
        """`Call 'sub-get-results'` for one picker probe, via the in-process MCP client.

        D10: the business lane calls the MCP server through `MCPRuntimeClient` (precedent:
        `ai_assistant_service`), at the CONFIGURED url, never a raw IP (H52). The result is
        the tool's own render envelope, which is what both annotators read
        (`{answers|items: [...]}`).

        This seam raised `NotImplementedError` until 7 Sep 2026, on the grounds that
        `sub-get-results`' first node (`entity-ids-transformer`) and its renderer had not
        been ported - so every ambiguous-customer turn rendered the BARE picker and the
        owner's "has DO" / "no DO" stamp could never appear on a live turn. Both halves
        landed with `_mcp_probe` (#705, lesson 102): the transformer runs at the
        sub-workflow boundary and `parse_mcp_content` turns the client's STRING back into
        the envelope. The picker probe is the same sub-workflow call with the same
        `workflowInputs`, so it is wired to the same seam rather than to a second one -
        `crm_order_management_orders_list` is the orders read the CRM already serves, and
        rebuilding its render envelope in process would be a second presenter to keep in
        step with `sorento_crm_mcp/presenters.py`, which is the file the annotators' field
        labels ("Customer", "Actual Delivery Date") are read from.

        A failure still reaches the annotators as the documented UNPROBED arm:
        `resolve_gate._run_probe` catches, logs and returns None, which renders the bare
        picker rather than a confident miss on evidence nobody gathered.
        """
        return probe(
            tool,
            {
                "tool": tool,
                "contact_id": contact_id,
                "entities": entities,
                "semantic_input": semantic_input,
                "user_prompt": user_prompt,
            },
        )

    return call


def _mcp_call(db: Session | None = None) -> McpCallFn:
    def call(name: str, args: dict[str, Any]) -> Any:
        """One MCP tool call at the CONFIGURED url (H52, D10).

        n8n bakes `http://<raw ip>:8765/mcp` into two nodes. This reads
        `settings.ai_assistant_mcp_url`, the same setting the AI assistant already uses, so
        an environment moves the endpoint without a deploy of anything but config.

        H58: the read-only check is on THIS seam, not only on `fetch.call_tool`, because
        the probes reach for the bundle directly (`answer.mcp_probe`, `miss_suggest`'s
        three, the did-you-mean probe) and go nowhere near the ported call node. This is
        the single choke point where a tool name becomes an MCP request, so it is where the
        rule has to hold. The probes name read tools and are unaffected, and so is the
        fetch step, whose tool is the domain row's own and therefore on the list by
        construction.

        **The PARSE is here too, for the same reason the read-only check is.**
        `MCPRuntimeClient.call_tool` returns a STRING - `"\\n".join` of the `content[]`
        text blocks, or `json.dumps(result)` when there are none. The fetch step ran the
        answer through `fetch.parse_mcp_content`; the four probe seams
        (`answer.run_crossdomain` and `miss_suggest`'s three) did not, so every probe
        answer arrived as a string, `run_crossdomain`'s
        `probe_result if isinstance(probe_result, dict) else {}` dropped it, and
        `crossdomain_render` degraded to `no_envelope` on turns that had a perfectly good
        answer. Parsing at the seam fixes all four at once and is idempotent: the fetch
        path's own `parse_mcp_content` returns a non-string unchanged.
        """
        from app.config import settings
        from app.services.ai_assistant_service import MCPRuntimeClient
        from app.services.chatbot.lanes.business.fetch import ensure_read_only, parse_mcp_content

        ensure_read_only(name)
        if db is not None:
            from app.models.base import get_brand_scope
            from app.services.chatbot.lanes.business.fetch import ToolNotAllowed
            from app.services.contact_brand_scope import brand_scope_allows_tool

            if not brand_scope_allows_tool(name, get_brand_scope(db)):
                # Same arm as any tool the chatbot may not call; no new customer-facing text.
                raise ToolNotAllowed(f"MCP tool {name} is not allowed for a brand-scoped contact")

        # The plan's capacity section bounds each MCP call at 10 s, and the AI assistant's
        # own 20 is a different budget for a different surface (a user watching a screen,
        # not a customer waiting on WhatsApp inside a whole turn's latency target).
        # `CHATBOT_MCP_TIMEOUT_SECONDS` overrides it per environment.
        timeout = int(getattr(settings, "chatbot_mcp_timeout_seconds", 0) or 10)
        client = MCPRuntimeClient(settings.ai_assistant_mcp_url, timeout_seconds=timeout)
        result = parse_mcp_content(client.call_tool(name, args))
        # CONTACT-BRAND-SCOPE: the probes (cross-domain, did-you-mean, siblings) read through
        # this seam too, so a brand-scoped session's result is guarded here as well.
        if db is not None:
            from app.models.base import get_brand_scope
            from app.services.chatbot.lanes.business.brand_guard import guard_result

            result = guard_result(name, result, get_brand_scope(db), db)
        return result

    return call


def _mcp_probe(db: Session | None = None, *, call: McpCallFn | None = None) -> McpProbeFn:
    """The PROBE seam: `sub-get-results`' workflowInputs in, the tool's answer out.

    The four probe call sites build what the n8n node builds - `crossdomain-probe`'s and
    `dym-probe` / `sibling-probe` / `promo-dym-probe`'s `workflowInputs`, which is
    `{tool, contact_id, entities, semantic_input, user_prompt}`. In n8n that goes to a
    SUB-WORKFLOW whose first node is `entity-ids-transformer`; the port called the MCP
    client with it directly, so the tool got `entities` / `semantic_input` / `user_prompt`
    and none of the filter keys it takes.

    MEASURED against the local MCP on 6 Sep 2026: `crm_incoming_stock_list` answers those
    arguments with `{"data": [], "total": 0, "page": 1, "limit": null}` - the raw list
    serialiser, with no `items` / `answers` / `has_result`, so the renderer degrades even
    once the string is parsed. The same call with `entity_ids_transformer`'s arguments
    answers with the render envelope. So this is not tidiness: without it the probe asks
    the wrong question and cannot be answered.

    The transform lives HERE rather than at the four call sites because those sites are
    ported NODES, graded against captures of the `workflowInputs` they emit
    (`test_s6c_answer_lane.py::TestCrossdomainProbe`). The sub-workflow boundary is the
    seam, so the seam is where its first node runs - which is exactly what `run_fetch`'s
    own tier probe already does inline.

    `call`: the underlying MCP call to transform-and-dispatch through. Defaults to a
    fresh `_mcp_call(db)`, kept for `answer_services_for`'s no-session bundle. Passed
    explicitly by `production_answer_services`, which hands in the SAME `mcp_call`
    `fetch_services(db)` already built for this session, so a rung probe and the
    primary fetch go through one `MCPRuntimeClient`, not two.
    """
    call = call or _mcp_call(db)

    def probe(name: str, args: dict[str, Any]) -> Any:
        from app.services.chatbot.lanes.business.fetch import entity_ids_transformer

        trigger = dict(args) if isinstance(args, dict) else {}
        # `miss_suggest._probe_args` passes the tool as the first parameter and leaves it
        # off the body; `crossdomain_probe_args` puts it on both. The name wins either way,
        # because it is the name `_mcp_call` will actually call.
        trigger["tool"] = name
        semantic = trigger.get("semantic_input")
        # Both builders resolve the workspace through `fetch.space_id_or_default` before
        # they write it, so reading it back is exact - and passing it on keeps the
        # transformer from falling back to n8n's literal on an install that has a
        # workspace row.
        space_id = semantic.get("space_id") if isinstance(semantic, dict) else None
        return call(name, entity_ids_transformer(trigger, space_id=space_id))

    return probe


def _family_row(row: Any) -> dict[str, Any]:
    """One products row as `sibling-transform` reads it: `product_code` and `id`.

    Those are the ONLY two keys the port takes off a family row
    (`miss_suggest._sibling_transform`, which also accepts the `code` / `uuid` spellings),
    so nothing else is serialised - a wider dict would be a shape nobody reads and a set
    of ORM attributes to keep loaded.
    """
    if isinstance(row, dict):
        return row
    uuid = getattr(row, "id", None)
    return {
        "product_code": getattr(row, "product_code", None),
        "id": str(uuid) if uuid else None,
    }


def _family_fetch(db: Session) -> FamilyFetchFn:
    def call(query: str) -> Any:
        """`family-fetch`: the sibling/family lookup, through the products service.

        n8n calls a raw host over HTTP for this (the same class of hazard as H52's MCP
        endpoint). In process it is a service call, so there is no url to go stale and no
        credential in the workflow.

        **The rows are SERIALISED, and that is what makes the seam usable.** n8n receives
        JSON; `ProductService.list_products` returns `{"data": [Product ORM objects]}`,
        and `jsc.get(row, "product_code")` returns its default on anything that is not a
        dict - so `sibling_transform` found no siblings on any live turn and the D3
        family offer was silently skipped for every partly-typed variant code.
        """
        from app.services.product_service import ProductService

        # n8n's `family-fetch` is
        # `GET https://<raw ip>/api/v1/master-data/products?query=..&variant_filter=all&limit=5000`.
        # Same read, same three parameters, no host and no credential (the H52 class of
        # hazard, on a second node).
        payload = ProductService(db).list_products(
            query=query, variant_filter="all", limit=5000, page=1
        )
        rows = payload.get("data") if isinstance(payload, dict) else None
        return {"data": [_family_row(row) for row in (rows if isinstance(rows, list) else [])]}

    return call


def production_answer_services(db: Session) -> AnswerServices:
    """S6c's bundle. The probe is the SAME MCP client the fetch step uses (H52, D10) -
    `fetch_services(db).mcp_call`, not a second `_mcp_call(db)` instance, so a turn that
    climbs the ladder through `answer_bridge.answer_for` (`bridge_owns_ladder`, reviewer
    S1) makes its rung probe through the identical seam a test's `fetch_services` double
    already covers, the same way `ctx.tool_runner` and `turn/fetch.py::_climb` share one
    seam for a turn the bridge does NOT own. `fetch_services` is looked up by its bare
    module name (not re-imported), so a caller that monkeypatches this module's own
    `fetch_services` attribute - `business_services.fetch_services` from `engine.py` -
    is picked up here too.

    `parse_mcp_content` wraps it: production's own `_mcp_call(db)` already parses before
    returning (its docstring: "The PARSE is here too"), so `fetch_services(db).mcp_call`
    hands back an already-structured dict there and this is a no-op (idempotent on a
    non-string). A test's `FetchServices(mcp_call=...)` double is the OTHER shape the
    `McpCallFn` contract allows - the raw MCP wire string, parsed downstream by whatever
    reads the fetch fragment - so without parsing here too, `_mcp_probe`'s own
    `run_crossdomain`/`_apply_crossdomain_rung` callers see the STRING where they expect
    the envelope dict and degrade to `no_envelope` on every double this module does not
    build itself."""
    from app.services.chatbot.lanes.business.fetch import parse_mcp_content

    raw_call = fetch_services(db).mcp_call

    def call(name: str, args: dict[str, Any]) -> Any:
        return parse_mcp_content(raw_call(name, args))

    return AnswerServices(mcp_probe=_mcp_probe(db, call=call), family_fetch=_family_fetch(db))


def answer_services_for(session_factory: Any) -> AnswerServices:
    """S6c's bundle, bound to a session FACTORY rather than to a live session.

    The answer lane makes two MCP probes and a products read, and the capacity rule says no
    database session is held across either. `production_answer_services(db)` binds
    `family_fetch` to the caller's session, which is right for a caller that already has one
    open and wrong for this lane, whose whole point is that it runs with none. So the family
    read opens its OWN short session and closes it again; the probe seam needs no session at
    all (`_mcp_probe` never touches its parameter).

    The wider "every bundle takes a factory" change is deliberately NOT made here - only the
    ONE bundle whose caller holds no session needs it today, and the rest have no such caller.
    """

    def family_fetch(query: str) -> Any:
        db = session_factory()
        try:
            return _family_fetch(db)(query)
        finally:
            db.close()

    return AnswerServices(mcp_probe=_mcp_probe(None), family_fetch=family_fetch)


def fetch_services(db: Session) -> FetchServices:
    """S6b's bundle. The session is taken and never used: `_mcp_call` is a network call.

    The parameter stays so the engine's call site reads like every other lane's, and so the
    day this step grows a seam that does need a session there is one to bind it to.
    """
    return FetchServices(mcp_call=_mcp_call(db))


def fetch_space_id(db: Session) -> str | None:
    """The default respond workspace's `space_id` (D5), for the fetch step's tool args.

    n8n hard-codes `364817` in `entity-ids-transformer` and overrides `semantic_input` with
    it. D5 reassigns the VALUE, not a list of call sites, so the fetch step reads the
    workspace row like every other producer of it. Falls back to n8n's literal inside
    `entity_ids_transformer` when there is no workspace row, which keeps this install
    byte-identical and any other install correct.
    """
    from app.services.chatbot.head.access import default_space_id

    return default_space_id(db)


def outstanding_customer_echo(db: Session, customer_ids: Any) -> str:
    """The `Customer:` line for a set of resolved customer ids - ONE source, the
    customer ROWS (R19b, owner round 7, 13 Sep 2026).

    Delegates to the report route's own `_customer_echo`, which is what renders the
    line under the REPORT (AC-1136) and applies AC-1163's distinct, first-seen-order
    rule. The scope question asked the same thing of a different source and got a
    different answer: it printed the PICKER's roster label, company-code suffix and
    all ("CHIN CHUN HARDWARE SDN BHD (MCH, SRT)"), while the report two turns later
    named the eight ledger rows. A roster label is a display string for the PICK, not
    a customer name, and one line asked about one filter cannot have two sources.

    Reads on the ENGINE's own per-contact scoped session, like
    `resolve_warehouse_token` above and for the same reason.
    """
    from app.services.outstanding_report_service import _customer_echo

    ids = [str(uid) for uid in (customer_ids or []) if uid]
    if not ids:
        return ""
    return _customer_echo(db, None, ids) or ""


def outstanding_brand_echo(db: Session, brand_ids: Any) -> str:
    """The `Brand:` line for a set of resolved brand ids - the SAME "one source, the
    rows" rule `outstanding_customer_echo` above follows, so the scope question and the
    report cannot name the brand differently. Delegates to the report route's own
    `_brand_echo` (#1262 slice 9, F1a)."""
    from app.services.outstanding_report_service import _brand_echo

    ids = [str(bid) for bid in (brand_ids or []) if bid]
    if not ids:
        return ""
    return _brand_echo(db, ids) or ""


def resolve_warehouse_token(db: Session, token: str) -> list[str]:
    """D5 (PLAN-chatbot-outstanding-report.md, S4 point 7): a token equal to a
    `warehouse_code` (case-insensitive) resolves to that code ONLY (`BRW` -> `BRW`); a
    token that is the suffix of one or more codes after the LAST `-` resolves to all of
    them (`IB` -> `BRW-IB`, `MWH-IB`); a token matching neither resolves to nothing.
    Reads the `warehouses` table, never a hard-coded list - the risk section measured
    codes with `/` and no `-` at all (`SPARE/P`), which this simply never matches.

    Runs on `db` under the ENGINE's own per-contact scope
    (`app/services/chatbot/engine.py`'s `_scoped_factory`), same as every other
    company-scoped read this turn makes (the product/customer resolution the generic
    resolver runs on the SAME session). `warehouses` is `CompanyScopedMixin` like
    `products`; there is no narrower argument for widening THIS read past the
    contact's own company than there would be for widening the product lookup, and a
    contact with no `respond_contact_companies` row already gets zero rows there too -
    a genuinely UNMAPPED contact fails closed everywhere in the turn, not selectively.
    """
    from sqlalchemy import func

    from app.models.inventory import Warehouse

    word = (token or "").strip()
    if not word:
        return []
    exact = (
        db.query(Warehouse.warehouse_code)
        .filter(func.lower(Warehouse.warehouse_code) == word.lower())
        .first()
    )
    if exact:
        return [exact[0]]
    suffix = word.lower()
    return [
        code
        for (code,) in db.query(Warehouse.warehouse_code).all()
        if "-" in code and code.rsplit("-", 1)[-1].lower() == suffix
    ]


def production_services(db: Session, *, space_id: str | None = None) -> ResolveGateServices:
    """The bundle the engine uses. One session, bound at the call site.

    `space_id` is the bundle's DEFAULT workspace, used when the caller does not name one
    per call. `resolve_gate.run()` passes its own (the default respond workspace's, D5) on
    every production turn, so this only matters to a caller that builds the bundle with a
    workspace already in hand.
    """
    return ResolveGateServices(
        access_types=_access_types(db, default_space_id=space_id),
        resolve_entity=_resolve_entity(db),
        probe=_probe(db),
    )


def account_levels_reader(db: Session) -> Callable[[list[str]], dict[str, int | None]]:
    """`customers.account_level` by customer uuid, one query (ACCOUNT-LEDGER, staff narrowing)."""

    def read(ids: list[str]) -> dict[str, int | None]:
        from app.models.order import Customer

        if not ids:
            return {}
        return {
            str(i): lvl
            for i, lvl in db.query(Customer.id, Customer.account_level).filter(Customer.id.in_(ids)).all()
        }

    return read


def top_selling_dealer_ledgers(
    db: Session, contact_respond_id: Any, space_id: str | None
) -> list[tuple[str, str]] | None:
    """A linked dealer contact's own customers as `(id, customer_name)`, or None when
    the contact is staff or is linked to none (someone the route refuses on its own).

    Reviewer S2 on PR #1273 (owner: a dealer sees only its own customers; ruling
    pending owner confirmation): the lane never shows a linked dealer the customer
    picker, which lists other customers' names before the route could refuse. The
    rule is the route's own (`orders._top_selling_dealer_scope`): any active office
    access type makes the contact staff whatever else it holds (fix round 3, owner
    hand test 27 Sep 2026); otherwise any `respond_contact_customers` row makes it
    that customer's dealer. Read on the engine's per-contact scoped session."""
    from app.services import contact_customer_scope as scope_mod
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    if not contact_respond_id:
        return None
    contact_id = resolve_contact_with_null_workspace_fallback(
        db, contact_id=str(contact_respond_id), space_id=space_id
    )
    if not contact_id:
        return None
    scope = scope_mod.contact_customer_scope(db, contact_id)
    if scope.staff or not scope.linked:
        return None
    return [(cid, name) for cid, name, _code in scope.linked]


def customer_scope(db: Session, contact_respond_id: Any, space_id: str | None) -> dict[str, Any] | None:
    """The asking contact's customer scope, read ONCE per turn
    (PLAN-chatbot-customer-scope-29sep.md D1/D3): None when nobody linked the contact to a
    customer, else `{"ids", "names", "linked": [[id, name, code]], "staff", "enforced"}`.
    `enforced` is the top selling rule (linked and no active office access type); a
    linked staff contact keeps `linked` for a "my" ask only. Read through the ONE core
    function `contact_customer_scope` (as a module attribute: the tests spy on it)."""
    from app.services import contact_customer_scope as scope_mod
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    if not contact_respond_id or not str(contact_respond_id).strip():
        return None
    contact_id = resolve_contact_with_null_workspace_fallback(
        db,
        contact_id=str(contact_respond_id).strip(),
        space_id=(str(space_id).strip() or None) if space_id is not None else None,
    )
    if not contact_id:
        return None
    scope = scope_mod.contact_customer_scope(db, contact_id)
    if not scope.linked:
        return None
    return {
        "ids": scope.customer_ids,
        "names": {cid: name for cid, name, _code in scope.linked},
        "linked": [list(row) for row in scope.linked],
        "staff": scope.staff,
        "enforced": scope.enforced,
        "levels": dict(scope.levels),
        "refusal": scope_mod.refusal_line(scope),
    }


def top_selling_dealer_customer_ids(
    own: list[tuple[str, str]], words: list[str]
) -> list[str] | None:
    """The dealer's own ledgers the customer words name, or None when any word names
    none of them (the lane then refuses). Matched the way the route matches a
    `customer_query` (a case-insensitive substring of the name), over the dealer's own
    ledgers only, so nothing outside them is ever looked at."""
    matched: list[str] = []
    for word in words:
        needle = " ".join((word or "").split()).lower()
        hits = [cid for cid, name in own if needle and needle in (name or "").lower()]
        if not hits:
            return None
        matched.extend(h for h in hits if h not in matched)
    return matched


def _category_words(text: str) -> list[str]:
    """Lower-cased words, each plural folded to its singular by one trailing `s`
    ("sinks" -> "sink"), applied to both sides so a match is word for word."""
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in (text or "").lower().split()]


def resolve_category_token(db: Session, token: str) -> list[tuple[str, str, str]]:
    """A top selling ask's category word, as `(id, category_code, category_name)` rows
    (PLAN-chatbot-top-x-hot-selling-24sep.md S4 point 4, AC-1954).

    Not the generic resolver: under `order` it re-types a category token as a customer
    (`entity_resolver._DOMAIN_HINT_EXPANSIONS`). An exact `category_code` or
    `category_name` (case-insensitive, a plural folded) wins alone; otherwise a category
    matches when the word is a whole-word run of its name ("sinks" is in "KITCHEN
    SINK"). Never the reverse (reviewer S1, PR #1273: "kitchen sinks" took the category
    KIT and "basin mixers" took BASIN beside BASIN MIXER, a silent widening). Several
    matches are returned for the lane to ASK about, never all kept. Read under the
    engine's per-contact company scope, like `resolve_warehouse_token` above."""
    from app.models.product import ProductCategory

    word = " ".join((token or "").split()).lower()
    if not word:
        return []
    words = _category_words(word)
    rows = [
        (str(r[0]), r[1] or "", r[2] or "")
        for r in db.query(ProductCategory.id, ProductCategory.category_code, ProductCategory.category_name)
        .order_by(ProductCategory.category_name)
        .all()
    ]
    exact = [
        r for r in rows
        if r[1].lower() == word or r[2].lower() == word or _category_words(r[2]) == words
    ]
    if exact:
        return exact

    def _has_run(name: str) -> bool:
        have = _category_words(name)
        return any(have[i : i + len(words)] == words for i in range(len(have) - len(words) + 1))

    return [r for r in rows if _has_run(r[2])]



# --------------------------------------------------------------------------- #
# Fix lane round 4 (owner retest of top selling, 27 Sep 2026): the words a ranking is
# narrowed by, matched against their own tables under the engine's per-contact company
# scope. Never the generic resolver, which types a brand or a person as a customer.
# --------------------------------------------------------------------------- #


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).lower()


def _whole_word_run(needle: str, haystack: str) -> bool:
    """`needle`'s words appear together, in order, inside `haystack`'s words."""
    want, have = _category_words(needle), _category_words(haystack)
    return bool(want) and any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1))


def resolve_sales_agent_token(db: Session, token: str) -> list[tuple[str, str]]:
    """Sales agents a word names, as `(id, sales_agent code)`. Matched the way the
    route matches a customer name (a case-insensitive part of the name), on the agent
    code ("FANNY", "SEAN I") and on its person label, whole words only, so "an" never
    names every agent. Inactive agents still sold what they sold, so they match too."""
    from app.models.sales_agent import SalesAgent

    word = _norm(token)
    if not word:
        return []
    rows = (
        db.query(SalesAgent.id, SalesAgent.sales_agent, SalesAgent.person_label, SalesAgent.aliases)
        .order_by(SalesAgent.sales_agent)
        .all()
    )
    exact = [(str(r[0]), r[1]) for r in rows if word in (_norm(r[1]), _norm(r[2]))]
    if exact:
        return exact
    named = [(str(r[0]), r[1]) for r in rows if _whole_word_run(word, r[1] or "") or _whole_word_run(word, r[2] or "")]
    if named:
        return named
    # "Also known as" (PR #1273 round 7: "WT is william"). A name belongs to the PERSON,
    # so it covers every account of theirs, whichever one the owner typed it on.
    hits = [r for r in rows if any(_whole_word_run(word, alias) for alias in (r[3] or "").split(","))]
    stems = {_code_stem(r[1]) for r in hits}
    labels = {_norm(r[2]) for r in hits if _norm(r[2])}
    return [(str(r[0]), r[1]) for r in rows if _code_stem(r[1]) in stems or (_norm(r[2]) and _norm(r[2]) in labels)]


#: The account numeral a person's codes end in ("WT I", "WT III", "SEAN IV").
_ACCOUNT_NUMERAL = re.compile(r"\s+(?:I|II|III|IV|V|VI|VII|VIII|IX|X)$", re.IGNORECASE)


def _code_stem(code: str | None) -> str:
    """An agent code without its account numeral ("WT III" -> "wt"): the person the
    master's own `(name, I|III|IV)` split names."""
    return _norm(_ACCOUNT_NUMERAL.sub("", code or ""))


def customers_named(db: Session, token: str, *, limit: int = 50) -> list[tuple[str, str]]:
    """Customers whose name carries the word as whole words, `(id, customer_name)`, at
    most `limit`: enough to tell whether "fanny" could also be a customer."""
    from app.models.order import Customer
    from app.services.sales_report_service import _LIKE_ESCAPE, _escape_like

    word = _norm(token)
    if len(word) < 3:
        return []
    rows = (
        db.query(Customer.id, Customer.customer_name)
        .filter(Customer.customer_name.ilike(f"%{_escape_like(word)}%", escape=_LIKE_ESCAPE))
        .order_by(Customer.customer_name)
        .limit(limit)
        .all()
    )
    return [(str(r[0]), r[1] or "") for r in rows if _whole_word_run(word, r[1] or "")]


def resolve_brand_token(db: Session, token: str, *, exact_only: bool = False) -> list[tuple[str, str]]:
    """Brands a word names, `(id, brand_name)`, from the live `brands` table (the list
    PR #1301 reads for its `Known brands` line). An exact brand name or code wins;
    otherwise, unless `exact_only`, a brand whose name is a whole word of the message's
    word ("sorento brand" names Sorento)."""
    from app.models.product import Brand

    word = _norm(token)
    if not word:
        return []
    rows = db.query(Brand.id, Brand.brand_name, Brand.brand_code).order_by(Brand.brand_name).all()
    exact = [(str(r[0]), r[1]) for r in rows if word in (_norm(r[1]), _norm(r[2]))]
    if exact or exact_only:
        return exact
    return [(str(r[0]), r[1]) for r in rows if _whole_word_run(r[1] or "", word)]


def resolve_category_class(db: Session, token: str) -> tuple[list[str], list[str]]:
    """A category word through the catalogue's class vocabulary, the one the stock ask
    reads (`product_class_signal.resolve_classes_for_term`: a class label, its synonyms,
    classes products carry, staff synonyms). Returns `(category ids, class labels)`.
    Category names are copies of their codes on live rows (SRT-WC), so "water closet"
    only ever meets its categories here.

    No exact hit: the class whose label or synonym is a whole word run of the word
    ("water tap" holds "tap"), taken only when exactly one class answers; several are
    returned as labels with no ids for the lane to ask about."""
    from app.models.product import ProductCategory
    from app.services.product_class_signal import resolve_classes_for_term

    word = _norm(token)
    if not word:
        return [], []
    labels = set(resolve_classes_for_term(db, word))
    singular = " ".join(_category_words(word))
    if not labels and singular != word:
        labels = set(resolve_classes_for_term(db, singular))
    if not labels:
        rows = (
            db.query(ProductCategory.class_label, ProductCategory.search_synonyms)
            .filter(ProductCategory.is_searchable.is_(True), ProductCategory.class_label.isnot(None))
            .all()
        )
        vocabulary: dict[str, set[str]] = {}
        for label, synonyms in rows:
            words = {label, *[str(x) for x in (synonyms or [])]}
            vocabulary.setdefault(label, set()).update(words)
        labels = {
            label for label, words in vocabulary.items() if any(_whole_word_run(w, word) for w in words if w)
        }
        if len(labels) > 1:
            return [], sorted(labels)
    if not labels:
        return [], []
    ids = [
        str(r[0])
        for r in db.query(ProductCategory.id)
        .filter(ProductCategory.class_label.in_(sorted(labels)))
        .order_by(ProductCategory.category_code)
        .all()
    ]
    return ids, sorted(labels)
