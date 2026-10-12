"""Core services for embedding event queue and retrieval."""
from __future__ import annotations

from datetime import datetime
import hashlib
import uuid
import json
import logging
import re
from typing import Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy import and_
from sqlalchemy import false as sa_false
from sqlalchemy import func, or_

from app.config import settings
from app.models.base import get_company_scope
from app.models.embeddings import EmbeddingQueue, EmbeddingDocument, EmbeddingChunk
from app.services.queue_service import enqueue_job

logger = logging.getLogger(__name__)


DEBTOR_SEED_PREFIX = "debtor:"


def debtor_seed_id(company_id: str, code: str) -> str:
    """``source_id`` of the synthetic customer seed for an ``orders.debtor_code`` with no
    customer master row in ``company_id``. One seed per (company, code), so the seed
    carries its company and resolves only inside it."""
    return f"{DEBTOR_SEED_PREFIX}{company_id}:{code}"


def parse_debtor_seed_id(source_id: Any) -> Optional[tuple[Optional[str], str]]:
    """``(company_id, code)`` of a debtor seed id, or None when it is not one.

    A legacy ``debtor:<code>`` id (written company-less before SCOPE-SEEDS) parses as
    ``(None, code)``."""
    if not isinstance(source_id, str) or not source_id.startswith(DEBTOR_SEED_PREFIX):
        return None
    rest = source_id[len(DEBTOR_SEED_PREFIX):]
    head, sep, tail = rest.partition(":")
    if sep:
        try:
            return str(uuid.UUID(head)), tail
        except ValueError:
            pass
    return None, rest


def _embedding_company_filter(db: Session):
    """Company-scope predicate for vector search over embedding rows (multi-company
    isolation, AC-I4). Reads the four-state scope stamped on the session:

      None              -> None  (no filter - all companies / system / back-compat)
      frozenset({ids})  -> company_id IN (ids) OR company_id IS NULL
                           (shared / company-less knowledge stays visible),
                           except a company-less ``debtor:`` seed: a debtor is never
                           shared knowledge (a legacy row an old worker wrote)
      UNSET / empty     -> false()  (fail-closed: 0 rows)

    Embedding rows are NOT CompanyScopedMixin (the ORM auto-filter never touches
    them), so this predicate is injected manually into ``search_current``.
    """
    scope = get_company_scope(db)
    if scope is None:
        return None
    if isinstance(scope, frozenset) and scope:
        return or_(
            EmbeddingChunk.company_id.in_(list(scope)),
            and_(
                EmbeddingChunk.company_id.is_(None),
                ~EmbeddingChunk.source_id.startswith(DEBTOR_SEED_PREFIX),
            ),
        )
    return sa_false()  # UNSET / empty frozenset -> fail-closed


EMBEDDING_NOISE_FIELDS = {"created_at", "updated_at", "updated_by", "synced_to_excel", "last_synced_to_excel"}


class EmbeddingEventService:
    def __init__(self, db: Session):
        self.db = db

    def queue_event(
        self,
        *,
        source_type: str,
        source_id: str,
        event_type: str,
        source_key: Optional[str] = None,
        source_updated_at: Optional[datetime] = None,
        changed_fields: Optional[list[str]] = None,
        payload: Optional[dict[str, Any]] = None,
        triggered_by: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> EmbeddingQueue:
        changed_fields = changed_fields or []
        if changed_fields and not self._has_semantic_change(changed_fields):
            logger.debug("Skip embedding queue event for %s:%s due to non-semantic changes", source_type, source_id)
            raise ValueError("No embedding-relevant fields changed")

        event_id = str(uuid.uuid4())
        correlation_id = correlation_id or str(uuid.uuid4())
        event_payload = {
            "event_id": event_id,
            "event_type": event_type,
            "event_version": 1,
            "occurred_at": datetime.utcnow().isoformat(),
            "source_type": source_type,
            "source_id": source_id,
            "source_key": source_key,
            "source_updated_at": source_updated_at.isoformat() if source_updated_at else None,
            "changed_fields": changed_fields,
            "correlation_id": correlation_id,
            "triggered_by": triggered_by,
            "payload": payload or {},
        }
        queue_item = EmbeddingQueue(
            source_type=source_type,
            source_id=source_id,
            event_type=event_type,
            event_version=1,
            source_updated_at=source_updated_at,
            payload=event_payload,
            status="pending",
            correlation_id=correlation_id,
        )
        self.db.add(queue_item)
        self.db.flush()

        is_priority_source = source_type == "mcp_tool"
        job = enqueue_job(
            _get_embedding_worker(),
            str(queue_item.id),
            queue_name=settings.embedding_queue_name,
            job_timeout=900,
            at_front=is_priority_source,
        )
        queue_item.rq_job_id = job.id
        self.db.commit()
        self.db.refresh(queue_item)
        return queue_item

    def request_rebuild(self, source_type: str, source_id: str, triggered_by: Optional[str] = None) -> EmbeddingQueue:
        return self.queue_event(
            source_type=source_type,
            source_id=source_id,
            event_type="embedding.rebuild_requested",
            changed_fields=["manual_rebuild"],
            triggered_by=triggered_by or "system",
        )

    @staticmethod
    def _has_semantic_change(changed_fields: list[str]) -> bool:
        return any(field not in EMBEDDING_NOISE_FIELDS for field in changed_fields)


class EmbeddingReadService:
    def __init__(self, db: Session):
        self.db = db

    def find_latest_current_hash(
        self,
        source_type: str,
        source_id: str,
        model_name: str,
        model_version: str,
    ) -> Optional[str]:
        row = (
            self.db.query(EmbeddingChunk.source_hash)
            .filter(
                EmbeddingChunk.source_type == source_type,
                EmbeddingChunk.source_id == source_id,
                EmbeddingChunk.is_current.is_(True),
                EmbeddingChunk.model_name == model_name,
                EmbeddingChunk.model_version == model_version,
            )
            .first()
        )
        return row[0] if row else None

    def current_chunk_hashes_distinct(
        self,
        source_type: str,
        source_id: str,
        model_name: str,
        model_version: str,
    ) -> set[str]:
        """Distinct source_hash values among *current* chunks (detects split-brain / duplicate batches)."""
        rows = (
            self.db.query(EmbeddingChunk.source_hash)
            .filter(
                EmbeddingChunk.source_type == source_type,
                EmbeddingChunk.source_id == source_id,
                EmbeddingChunk.is_current.is_(True),
                EmbeddingChunk.model_name == model_name,
                EmbeddingChunk.model_version == model_version,
            )
            .distinct()
            .all()
        )
        return {str(r[0]) for r in rows if r[0]}

    def mark_previous_non_current(self, source_type: str, source_id: str, model_name: str, model_version: str) -> None:
        now = datetime.utcnow()
        (
            self.db.query(EmbeddingChunk)
            .filter(
                EmbeddingChunk.source_type == source_type,
                EmbeddingChunk.source_id == source_id,
                EmbeddingChunk.model_name == model_name,
                EmbeddingChunk.model_version == model_version,
                EmbeddingChunk.is_current.is_(True),
            )
            .update({EmbeddingChunk.is_current: False, EmbeddingChunk.superseded_at: now}, synchronize_session=False)
        )

    @staticmethod
    def deterministic_hash(payload: dict[str, Any]) -> str:
        body = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def search_current(
        self,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        source_type: Optional[str] = None,
        visibility_scope: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> list[tuple[EmbeddingChunk, EmbeddingDocument, float]]:
        similarity_expr = (1 - EmbeddingChunk.embedding.cosine_distance(query_embedding)).label("similarity")
        filters = [EmbeddingChunk.is_current.is_(True), EmbeddingDocument.is_active.is_(True)]
        if source_type:
            filters.append(EmbeddingChunk.source_type == source_type)
        if visibility_scope:
            filters.append(EmbeddingDocument.visibility_scope == visibility_scope)
        if tenant_id:
            filters.append(EmbeddingDocument.metadata_json["tenant_id"].astext == tenant_id)
        # Multi-company isolation (AC-I4): restrict results to the caller's scope so
        # e.g. a Mocha-scoped search never returns Sorento-owned entities.
        company_filter = _embedding_company_filter(self.db)
        if company_filter is not None:
            filters.append(company_filter)
        rows = (
            self.db.query(EmbeddingChunk, EmbeddingDocument, similarity_expr)
            .join(EmbeddingDocument, EmbeddingDocument.id == EmbeddingChunk.document_id)
            .filter(and_(*filters))
            .order_by(similarity_expr.desc())
            .limit(max(1, min(top_k, 30)))
            .all()
        )
        return rows

    def queue_metrics(self) -> dict[str, int]:
        rows = (
            self.db.query(EmbeddingQueue.status, func.count(EmbeddingQueue.id))
            .group_by(EmbeddingQueue.status)
            .all()
        )
        counts = {status: int(count) for status, count in rows}
        return {
            "pending": counts.get("pending", 0),
            "processing": counts.get("processing", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "dead_letter": counts.get("dead_letter", 0),
            "skipped": counts.get("skipped", 0),
        }

    @staticmethod
    def _extract_known_params(query: str) -> set[str]:
        q = (query or "").lower()
        hints: set[str] = set()
        keyword_to_param = {
            "product": "product_id",
            "sku": "product_id",
            "promotion": "promotion_id",
            "promo": "promotion_id",
            "campaign": "campaign_id",
            "attachment": "attachment_id",
            "warehouse": "warehouse_id",
            "shipment": "shipment_id",
            "spo": "spo_number",
            "grn": "grn_id",
            "order": "order_id",
            "customer": "customer_id",
            "status": "status",
            "date": "actual_delivery_date_from",
            "query": "query",
            "search": "query",
            "form": "definition_id",
            "submission": "submission_id",
            "policy": "policy_id",
            "tracking": "tracking_id",
        }
        for keyword, param in keyword_to_param.items():
            if keyword in q:
                hints.add(param)
        return hints

    def search_tool_candidates(
        self,
        query_embedding: list[float],
        *,
        query: str,
        top_k: int = 5,
        include_planned: bool = True,
        category: Optional[str] = None,
        implementation_status: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        q_lower = (query or "").lower()
        category_norm = (category or "").strip().lower()
        # n8n/query-formulator commonly emits inquiry_type=general.
        # Treat `general` as a broad search (no category filter) instead of an exact category key.
        if category_norm in {"", "general", "all", "*"}:
            category_filter: Optional[str] = None
        else:
            category_filter = category_norm
        looks_like_product_code = bool(re.search(r"\b[a-z]{2,}\d{2,}[a-z0-9-]*\b", q_lower))
        promo_intent_words = ("promo", "promotion", "discount", "campaign", "offer")
        has_promo_intent = any(w in q_lower for w in promo_intent_words)

        # Explicit submission-intent signal from the structured RAG query envelope:
        #
        #   Intent: stock_inquiry | complaint | purchase_request | sponsorship_form
        #   User goal: file stock inquiry
        #
        # When the orchestrator marks the intent as one of the four portal-backed
        # submission flows, the user always wants `crm_portal_link_get`, never an
        # inventory / incoming-stock / order lookup tool - regardless of how the
        # other lines ("Domain: warehouse", "Operation: search") bias the embedding
        # cosine. Pin the portal tool and aggressively demote read-only data tools.
        _portal_intent_values = (
            "stock_inquiry",
            "stock_enquiry",
            "product_inquiry",
            "product_enquiry",
            "complaint",
            "purchase_request",
            "sponsorship_form",
            "sponsorship",
        )
        _intent_line_match = re.search(r"intent:\s*([a-z_\-]+)", q_lower)
        _intent_value = (_intent_line_match.group(1) if _intent_line_match else "").strip()
        _submission_intent_from_envelope = _intent_value in _portal_intent_values
        # Also catch "user goal: file/submit/lodge … stock inquiry/complaint/…".
        _user_goal_match = re.search(r"user goal:\s*([^\n]+)", q_lower)
        _user_goal_text = (_user_goal_match.group(1) if _user_goal_match else "").strip()
        _goal_submission_verbs = ("file", "submit", "create", "lodge", "send", "i want to")
        _goal_form_words = (
            "stock inquiry",
            "stock enquiry",
            "product inquiry",
            "product enquiry",
            "complaint",
            "purchase request",
            "sponsorship form",
            "sponsorship",
        )
        _submission_intent_from_goal = bool(_user_goal_text) and any(
            v in _user_goal_text for v in _goal_submission_verbs
        ) and any(w in _user_goal_text for w in _goal_form_words)
        _submission_intent = _submission_intent_from_envelope or _submission_intent_from_goal
        initial = self.search_current(
            query_embedding,
            top_k=max(8, min(20, top_k * 3)),
            source_type="mcp_tool",
            visibility_scope="internal",
        )

        # When the orchestrator marks this as a submission flow, the portal tool
        # MUST be a candidate. Cosine often pushes it below the 20-row cutoff
        # because "Domain: warehouse" / "Operation: search" / "Resolved entities"
        # tokens dominate the embedding. Inject the top-similarity portal chunk
        # so the re-rank below can pin it at position 1.
        if _submission_intent:
            _portal_in_initial = any(
                (
                    (c.metadata_json or {}).get("tool_name") == "crm_portal_link_get"
                    or (d.source_key == "crm_portal_link_get")
                )
                for (c, d, _s) in initial
            )
            if not _portal_in_initial:
                similarity_expr = (
                    1 - EmbeddingChunk.embedding.cosine_distance(query_embedding)
                ).label("similarity")
                portal_row = (
                    self.db.query(EmbeddingChunk, EmbeddingDocument, similarity_expr)
                    .join(EmbeddingDocument, EmbeddingDocument.id == EmbeddingChunk.document_id)
                    .filter(
                        EmbeddingChunk.is_current.is_(True),
                        EmbeddingDocument.is_active.is_(True),
                        EmbeddingChunk.source_type == "mcp_tool",
                        EmbeddingDocument.source_key == "crm_portal_link_get",
                    )
                    .order_by(similarity_expr.desc())
                    .limit(1)
                    .first()
                )
                if portal_row is not None:
                    initial = list(initial) + [portal_row]

        known_params = self._extract_known_params(query)
        results: list[dict[str, Any]] = []
        for chunk, doc, similarity in initial:
            md = chunk.metadata_json or {}
            status = str(md.get("implementation_status") or "implemented")
            if not include_planned and status != "implemented":
                continue
            if implementation_status and status != implementation_status:
                continue
            tool_category = md.get("category")
            if category_filter and tool_category != category_filter:
                continue
            required_params = [str(x) for x in (md.get("required_params") or [])]
            missing_params = [p for p in required_params if p not in known_params]
            rerank_penalty = min(len(missing_params) * 0.02, 0.12)
            score = float(similarity) - rerank_penalty
            tool_name = str(md.get("tool_name") or doc.source_key or doc.source_id)
            cat = str(tool_category or "")

            # Intent nudges aligned with next_agents/* domains. These are small
            # (<=0.1) and only reinforce unambiguous keywords; they do NOT
            # replace the semantic match but keep ties sensible.
            if (
                "incoming" in q_lower
                or "inbound" in q_lower
                or "packing list" in q_lower
                or "shipment" in q_lower
                or "arriving" in q_lower
                or "arrival" in q_lower
                or "eta" in q_lower
                or "when will" in q_lower and "arriv" in q_lower
            ):
                if cat == "general_enquiries.incoming_stock":
                    score += 0.08
                if cat in ("general_enquiries.stock", "general_enquiries.stock_ledger"):
                    score -= 0.03
                # "incoming" is a procurement word; customer sales tools should NOT win here.
                if cat == "order_enquiries":
                    score -= 0.08
                # Admin / raw procurement tools should never beat the user-facing
                # incoming-stock tools for end-user enquiries.
                if cat.startswith("internal_admin"):
                    score -= 0.20
            # Always penalize admin tools for any user-style question. Only admin-
            # keyworded queries should surface raw procurement data.
            if cat.startswith("internal_admin") and not any(
                w in q_lower for w in ("admin", "raw", "back-office", "back office", "internal")
            ):
                score -= 0.15
            # Strong boost: product-centric incoming questions should land on the
            # dedicated user-facing tool and suppress the shipment-level sidecars.
            _is_product_incoming_query = (
                ("incoming" in q_lower or "pending" in q_lower or "arriv" in q_lower or "coming" in q_lower)
                and ("product" in q_lower or "sku" in q_lower or "item" in q_lower or "stock for" in q_lower)
            )
            if _is_product_incoming_query:
                if tool_name == "crm_incoming_stock_by_product":
                    score += 0.12
                if tool_name in (
                    "crm_incoming_stock_shipments",
                    "crm_incoming_stock_shipment_products",
                    "crm_incoming_stock_shipment_attachment",
                ):
                    score -= 0.25
            if (
                ("packing list" in q_lower or "attachment" in q_lower or "document" in q_lower or "file" in q_lower)
                and ("shipment" in q_lower or "container" in q_lower or "packing list" in q_lower)
                and tool_name == "crm_incoming_stock_shipment_attachment"
            ):
                score += 0.08
            if "ledger" in q_lower or "transaction history" in q_lower or "movement history" in q_lower:
                if cat == "general_enquiries.stock_ledger":
                    score += 0.08
                if cat == "general_enquiries.stock":
                    score -= 0.04
            if ("balance" in q_lower or "available" in q_lower or "how much stock" in q_lower or "on hand" in q_lower) and "ledger" not in q_lower:
                if cat == "general_enquiries.stock":
                    score += 0.06
                if cat == "general_enquiries.stock_ledger":
                    score -= 0.04
            if ("products" in q_lower or "catalog" in q_lower or "sell" in q_lower) and "stock" not in q_lower and "order" not in q_lower:
                if cat == "general_enquiries.product":
                    score += 0.06
                if cat in ("general_enquiries.stock", "general_enquiries.stock_ledger"):
                    score -= 0.04
            if ("order" in q_lower or "delivery" in q_lower or "lorry" in q_lower or "transporter" in q_lower) and cat == "order_enquiries":
                score += 0.08
            # "Delivery status" is OUTBOUND customer-order delivery (DO), NOT inbound stock.
            # The incoming-stock tools share "delivery" / "shipment" vocabulary and often
            # win cosine. Pin order tools above them whenever this phrase appears.
            _delivery_status_intent = (
                "delivery status" in q_lower
                or "status of delivery" in q_lower
                or "check delivery" in q_lower and "incoming" not in q_lower and "inbound" not in q_lower
                or "is my order delivered" in q_lower
                or "has order been delivered" in q_lower
                or "where is my delivery" in q_lower
            )
            if _delivery_status_intent:
                if cat == "order_enquiries":
                    score += 0.15
                if cat == "general_enquiries.incoming_stock":
                    score -= 0.20
                if cat in ("general_enquiries.stock", "general_enquiries.stock_ledger"):
                    score -= 0.10
            # When the query clearly talks about customer sales orders (not incoming/inbound),
            # downrank procurement/incoming-stock tools so they don't beat orders_by_product.
            _procurement_words = ("incoming", "inbound", "arriv", "packing list", "shipment", "eta", "grn", "purchase order", "spo ")
            _sales_words = ("order", "delivery", "customer", "sold", "bought", "buy")
            if (
                any(w in q_lower for w in _sales_words)
                and not any(w in q_lower for w in _procurement_words)
                and cat == "general_enquiries.incoming_stock"
            ):
                score -= 0.15
            # Specific boost for orders_by_product when the query mentions a product AND orders/customer buying context
            if (
                ("product" in q_lower or "sku" in q_lower)
                and any(w in q_lower for w in ("order", "bought", "sold", "customer", "buy"))
                and not any(w in q_lower for w in _procurement_words)
                and tool_name == "crm_order_management_orders_by_product_list"
            ):
                score += 0.10
            if ("complaint" in q_lower or "defect" in q_lower or "broken" in q_lower or "leak" in q_lower) and cat == "complaint.form_submission":
                score += 0.08
            # Complaint flow should begin with DO lookup from orders if user is still
            # providing customer/product/date filters and no concrete DO number yet.
            _complaint_intent = any(w in q_lower for w in ("complaint", "defect", "broken", "leak"))
            _has_do_number = bool(re.search(r"\bdo[-\s]?\d+\b", q_lower))
            if _complaint_intent and not _has_do_number:
                _mentions_product = any(w in q_lower for w in ("product", "sku", "item code", "product code")) or bool(
                    re.search(r"\b[A-Z]{2,}\d{2,}(?:-[A-Z0-9]+)?\b", query)
                )
                if tool_name in ("crm_order_management_orders_list", "crm_order_management_orders_by_product_list"):
                    score += 0.12
                if _mentions_product and tool_name == "crm_order_management_orders_by_product_list":
                    score += 0.10
                if _mentions_product and tool_name == "crm_order_management_orders_list":
                    score -= 0.06
                if tool_name == "crm_portal_link_get":
                    score -= 0.16
            # DO discovery intent (common in n8n "general" inquiry stage): prioritize
            # order lookup tools even when complaint keywords are absent.
            _do_lookup_words = (
                "delivery order",
                "do number",
                "find do",
                "search do",
                "locate do",
                "check do",
                "lookup do",
                "look up do",
                "get do",
                "show do",
                "checking do",
                "looking for do",
                "find delivery order",
                "search delivery order",
                "locate delivery order",
                "check delivery order",
            )
            _do_lookup_intent = any(w in q_lower for w in _do_lookup_words)
            # Bare "do" token counts as DO intent only when paired with a product-code
            # pattern or a customer/product/"for" anchor. "do" alone is too ambiguous
            # (English verb) to trigger order_enquiries boosts on its own.
            if not _do_lookup_intent and re.search(r"\bdo\b", q_lower):
                _do_context_anchor = (
                    looks_like_product_code
                    or "customer" in q_lower
                    or "product" in q_lower
                    or "sku" in q_lower
                    or re.search(r"\bdo for\b", q_lower) is not None
                )
                if _do_context_anchor:
                    _do_lookup_intent = True
            _explicit_submit_intent = any(
                w in q_lower
                for w in (
                    "submit complaint",
                    "file complaint",
                    "lodge complaint",
                    "submit stock inquiry",
                    "submit stock enquiry",
                    "submit product enquiry",
                    "submit product inquiry",
                    "submit purchase request",
                    "create purchase request",
                    "file purchase request",
                    "submit sponsorship",
                    "create sponsorship",
                    "file sponsorship",
                    "confirm submission",
                    "user_confirmed",
                )
            )
            # Direct intent: user wants to submit one of the four supported forms ->
            # always lift crm_portal_link_get above per-resource list/get tools.
            _portal_form_words = (
                "stock inquiry",
                "stock enquiry",
                "product enquiry",
                "product inquiry",
                "purchase request",
                "sponsorship form",
                "sponsorship",
                "complaint",
            )
            _submission_verbs = ("submit", "file", "create", "lodge", "send", "i want to")
            _wants_portal = any(v in q_lower for v in _submission_verbs) and any(
                w in q_lower for w in _portal_form_words
            )
            if _wants_portal and tool_name == "crm_portal_link_get":
                score += 0.20

            # Hard pin for the four portal-backed submission flows. Beats the
            # "Domain: warehouse" / "Operation: search" tokens that otherwise
            # surface crm_inventory_* and crm_order_management_* candidates.
            if _submission_intent:
                if tool_name == "crm_portal_link_get":
                    score += 0.60
                elif tool_name.startswith("crm_inventory_"):
                    score -= 0.45
                elif tool_name.startswith("crm_incoming_stock_"):
                    score -= 0.35
                elif tool_name.startswith("crm_order_management_"):
                    score -= 0.25
                elif tool_name.startswith("crm_procurement_"):
                    score -= 0.20
                elif tool_name.startswith("crm_master_products"):
                    score -= 0.15
            if _do_lookup_intent and not _has_do_number:
                if tool_name in ("crm_order_management_orders_list", "crm_order_management_orders_by_product_list"):
                    score += 0.20
                if tool_name == "crm_order_management_orders_by_product_list" and (
                    "product" in q_lower
                    or "sku" in q_lower
                    or "code" in q_lower
                    or bool(re.search(r"\b[A-Z]{2,}\d{2,}(?:-[A-Z0-9]+)?\b", query))
                ):
                    score += 0.10
                if tool_name == "crm_portal_link_get" and not _explicit_submit_intent:
                    score -= 0.30
            # n8n often sends inquiry_type hints in rewritten text/json strings.
            if "inquiry_type" in q_lower and "general" in q_lower and _do_lookup_intent and not _has_do_number:
                if cat == "order_enquiries":
                    score += 0.08
                if tool_name == "crm_portal_link_get":
                    score -= 0.12
            # Complaint submissions should not route to attachment linking unless
            # user explicitly mentions adding files/photos/videos.
            _attachment_intent = any(
                w in q_lower for w in ("attach", "attachment", "photo", "image", "video", "file", "upload")
            )
            if _complaint_intent and not _attachment_intent and tool_name == "crm_forms_entity_attachments_link":
                score -= 0.30
            if (
                "stock inquiry" in q_lower
                or "stock enquiry" in q_lower
                or "product enquiry" in q_lower
                or "product inquiry" in q_lower
                or "lead time" in q_lower
            ) and cat == "stock_inquiries.form_submission":
                score += 0.08
            # Promotion questions should not rank stock inquiry form flows.
            if any(w in q_lower for w in ("promo", "promotion", "campaign", "discount", "offer")) and cat == "stock_inquiries.form_submission":
                score -= 0.20
            if ("purchase request" in q_lower or "sponsorship" in q_lower) and cat == "purchase_request.form_submission":
                score += 0.08
            if ("application form" in q_lower or "flower stand" in q_lower or "renovation form" in q_lower) and cat in (
                "marketing_agent.marketing_assets",
                "marketing_agent.form_lookup",
            ):
                score += 0.08

            # The resolver MCP tool is meta: the system already pre-resolves codes
            # every turn. Keep it reachable but never let it take a top-k slot away
            # from a real data tool.
            if tool_name == "crm_resolve_reference":
                score -= 0.25

            # --- Entity-resolver-driven nudges -------------------------------
            # The AI assistant appends a "Resolved references:" line to the RAG
            # query for each resolved token (e.g. '"RF2601-025" is customer_order').
            # These nudges lock in the right tool once we KNOW the entity type.
            if "is customer_order" in q_lower:
                if tool_name == "crm_order_management_orders_get":
                    score += 0.20
                if tool_name == "crm_order_management_orders_list":
                    score += 0.05
                if tool_name in (
                    "crm_order_management_orders_by_product_list",
                    "crm_incoming_stock_by_product",
                    "crm_master_products_get",
                ):
                    score -= 0.15
            if "is product" in q_lower:
                # Product code known; prefer the incoming-stock tool when the user also
                # asks about availability / arrival, otherwise products_get / orders_by_product.
                product_incoming_intent = (
                    "incoming" in q_lower
                    or "pending" in q_lower
                    or "arriv" in q_lower
                    or "coming" in q_lower
                    or "stock for" in q_lower
                )
                if tool_name == "crm_incoming_stock_by_product" and product_incoming_intent:
                    score += 0.25
                if tool_name == "crm_master_products_get":
                    score += 0.10
                if tool_name == "crm_order_management_orders_get":
                    score -= 0.15
                # Lock out sidecar incoming-stock tools for product-centric incoming questions \u2014
                # crm_incoming_stock_by_product already returns everything they would (shipment
                # breakdown, warehouse allocation, packing-list attachment). Keeping them in top-k
                # tempts the LLM to make redundant calls.
                if product_incoming_intent and tool_name in (
                    "crm_incoming_stock_shipments",
                    "crm_incoming_stock_shipment_products",
                    "crm_incoming_stock_shipment_attachment",
                ):
                    score -= 0.35
            if "is inbound_shipment" in q_lower:
                if tool_name in ("crm_incoming_stock_shipments", "crm_incoming_stock_shipment_products"):
                    score += 0.18
                if tool_name == "crm_incoming_stock_shipment_attachment" and (
                    "packing list" in q_lower or "attachment" in q_lower or "document" in q_lower
                ):
                    score += 0.10
                if tool_name in ("crm_order_management_orders_get", "crm_master_products_get"):
                    score -= 0.15
            if "is customer" in q_lower and "is customer_order" not in q_lower:
                if tool_name == "crm_order_management_orders_list":
                    score += 0.10
                if tool_name in ("crm_master_customers_get", "crm_order_management_customers_get"):
                    score += 0.10
            if "is warehouse" in q_lower and tool_name.startswith("crm_inventory_"):
                score += 0.08
            if "is supplier" in q_lower and tool_name.startswith("crm_procurement_"):
                score += 0.08
            if "is promotion" in q_lower and tool_name.startswith("crm_marketing_promotions"):
                score += 0.15
            # Product-code + promo questions should first discover promo headers,
            # then drill into line items only when a specific promotion is known.
            if has_promo_intent and looks_like_product_code:
                if tool_name == "crm_marketing_promotions_list":
                    score += 0.18
                if tool_name == "crm_marketing_promotion_products_list":
                    score += 0.08
            # If we only have a product code token (e.g. "SRT7017-BL"), prefer
            # promotion discovery over line-item tool that often needs narrowing.
            if looks_like_product_code and not has_promo_intent:
                if tool_name == "crm_marketing_promotions_list":
                    score += 0.10
                if tool_name == "crm_marketing_promotion_products_list":
                    score -= 0.06
            if "is spo_allocation" in q_lower and tool_name.startswith("crm_procurement_spo"):
                score += 0.15
            # If a code is explicitly unresolved, strongly penalize every data tool - 
            # the LLM should just tell the user "no record found".
            if "unresolved" in q_lower and "is customer_order" not in q_lower and "is product" not in q_lower:
                score -= 0.10

            # Legacy heuristics kept for specific tool names
            if "workflow" in q_lower and "submission" in q_lower and "published_for_submission" in tool_name:
                score += 0.18
            if "download" in q_lower and "metadata" in q_lower and tool_name.endswith("_metadata"):
                score += 0.08
            if "order" in q_lower and "product" not in q_lower and tool_name.endswith("_by_product_list"):
                score -= 0.05
            results.append(
                {
                    "tool_name": tool_name,
                    "score": score,
                    "why_selected": str(md.get("when_to_use") or doc.title or "Semantic relevance match"),
                    "status": status,
                    "category": tool_category,
                    "required_params": required_params,
                    "missing_params": missing_params,
                    "source_id": doc.source_id,
                    "source_type": doc.source_type,
                    "chunk_text": chunk.chunk_text,
                }
            )
        results.sort(key=lambda x: x["score"], reverse=True)
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in results:
            key = row["tool_name"]
            if key in seen:
                continue
            seen.add(key)
            deduped.append(row)
            if len(deduped) >= max(3, min(top_k, 5)):
                break
        return deduped

    def replay_dead_letters(self, limit: int = 100) -> int:
        items = (
            self.db.query(EmbeddingQueue)
            .filter(EmbeddingQueue.status == "dead_letter")
            .order_by(EmbeddingQueue.created_at.asc())
            .limit(max(1, min(limit, 1000)))
            .all()
        )
        for item in items:
            item.status = "pending"
            item.available_at = datetime.utcnow()
        self.db.commit()
        for item in items:
            job = enqueue_job(
                _get_embedding_worker(),
                str(item.id),
                queue_name=settings.embedding_queue_name,
                job_timeout=900,
            )
            item.rq_job_id = job.id
        self.db.commit()
        return len(items)


def _get_embedding_worker():
    # Imported lazily to avoid circular imports.
    from app.services.embedding_worker import process_embedding_queue_item

    return process_embedding_queue_item
