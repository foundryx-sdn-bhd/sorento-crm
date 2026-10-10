"""Generic AI form-prefill engine.

Pipeline (see ``docs`` of the ``ai_extract`` package):

1. Resolve form schema by ``form_key`` from
   :mod:`app.services.ai_extract.form_schema_registry`.
2. Build per-field guidance: lookup option list (with keywords), product-code
   examples, etc.
3. Render every PDF page to PNG with PyMuPDF; pass-through images.
4. Call the configured ``LLMProvider`` (same provider/model the AI assistant
   chat uses) with multimodal user content + ``response_format=json_object``.
5. Validate each returned field; canonicalize lookup values via
   :class:`LookupResolverService`. Drop unresolvable values rather than
   coerce.
6. Log token usage to ``AIAssistantUsageLog`` so it shows up on the existing
   usage dashboard tagged ``feature=ai_extract``.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.ai_assistant import AIAssistantUsageLog
from app.models.lookup import (
    LookupBinding,
    LookupOption,
    LookupOptionKeyword,
    LookupSet,
)
from app.models.product import Product, ProductCategory
from app.services.ai_extract.form_schema_registry import (
    ExtractFieldSpec,
    get_form_schema,
)
from app.services.entity_resolver import TokenResolution, resolve_references
from app.services.error_handler import AppException
from app.services.llm_provider import (
    ChatResult,
    ImagePart,
    LLMProvider,
    default_model_for,
    get_provider,
    resolve_api_key,
)
from app.services.lookup_resolver import LookupResolverService

logger = logging.getLogger(__name__)


# ---- Tunables (kept centralized so tests can monkey-patch) ----------------

PDF_MAX_PAGES = 12
PDF_RENDER_DPI = 144
PRODUCT_HINT_LIMIT = 30
ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
ALLOWED_PDF_MIMES = {"application/pdf"}
ALLOWED_VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
    "video/x-m4v",
    "video/3gpp",
}
_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".3gp")
VIDEO_MAX_FRAMES = 8
VIDEO_FRAME_MAX_DIM = 1280  # downscale frames before base64 to stay within token budget
# Pasted text arrives as a text/plain ".txt" upload (the portal converts a paste
# into a file so it flows through the same upload path).
ALLOWED_TEXT_MIMES = {"text/plain"}
_TEXT_EXTS = (".txt",)
TEXT_MAX_CHARS = 200_000

# Forms that ship a separate items-list section. Complaints now capture one
# line per affected product (code + quantity), so they receive a `products`
# array too. stock_inquiry / master.* still keep product codes inline.
FORMS_WITH_LINE_ITEMS: frozenset[str] = frozenset(
    {
        "portal.purchase_request",
        "portal.sponsorship_form",
        "portal.complaint",
        "portal.price_tag_request",
    }
)


def _form_has_line_items(form_key: str) -> bool:
    return form_key in FORMS_WITH_LINE_ITEMS


def extract_prompt_key(form_key: str) -> str:
    """The `PROMPT_KEYS` name for one form's own AI-extract system prompt
    (PLAN price-tag-currency-token-extract-prompt, Slice B) - one key per
    form key, so a production override on one form's prompt (the price tag
    request's rule 8) never leaks into another form's extract.
    """
    return "ai_extract_" + form_key.replace(".", "_")


# ---- Result schemas -------------------------------------------------------


class ExtractFieldMeta(BaseModel):
    raw: Any | None = None
    canonical: Any | None = None
    source: str | None = None  # "llm", "lookup_resolved", "product_master"


class ExtractedProductSuggestion(BaseModel):
    """One "did you mean" candidate for a code with no exact match
    (AI-EXTRACT-DYM). Never applied by the server: the user picks it."""

    product_id: str
    product_code: str
    product_name: str | None = None


class ExtractedProductLine(BaseModel):
    product_code: str | None = None
    product_name: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    total: float | None = None
    notes: str | None = None
    # D2: set when `product_code` resolved to exactly one company-scoped
    # `product` or `product_set` row via `resolve_references` (exact tier
    # only). `None` on no match AND on an ambiguous token - either way the
    # caller has nothing to pin, and `product_code` keeps the raw extracted
    # text so the dialog can show what was read.
    match: Literal["product", "product_set"] | None = None
    product_id: str | None = None
    product_set_id: str | None = None
    # AI-EXTRACT-DYM: only on a line with no match - the resolver's own trigram
    # neighbours for the misread code, active products only, best first. A
    # suggestion never sets `match` / `product_id`; the user confirms one.
    suggestions: list[ExtractedProductSuggestion] = []


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ExtractResult(BaseModel):
    values: dict[str, Any]
    products: list[ExtractedProductLine] = []
    per_field: dict[str, ExtractFieldMeta] = {}
    usage: TokenUsage = TokenUsage()
    model: str | None = None
    provider: str | None = None


# ---- Inputs ---------------------------------------------------------------


@dataclass
class ExtractFile:
    """In-memory representation of an uploaded file."""

    filename: str
    mime: str
    data: bytes


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AIExtractService:
    # No price-tag / portal line ceiling exists to defer to for the resolver
    # batch cap (`_resolve_product_codes`) - a plain sales order this long is
    # not a real extract, so 100 is the bound.
    _MAX_RESOLVE_CANDIDATES = 100
    # AI-EXTRACT-DYM: "did you mean" candidates shown per misread line. The
    # right code ranked first in all four real misreads (9 Oct), and more rows
    # make the Match cell tall at 375px.
    _MAX_SUGGESTIONS = 3

    def __init__(self, db: Session):
        self.db = db

    # ----- Public API -------------------------------------------------------

    def get_schema_with_guidance(self, form_key: str) -> dict[str, Any]:
        """Return the schema + resolved per-field guidance (lookup options,
        product-code examples). FE uses this to preview which fields will be
        attempted before the user uploads anything."""
        schema = get_form_schema(form_key)
        guidance = self._build_field_guidance(schema)
        return {
            "form_key": form_key,
            "fields": [
                {
                    "name": f.name,
                    "label": f.label,
                    "kind": f.kind,
                    "note": f.note,
                    "guidance": guidance.get(f.name, {}),
                }
                for f in schema
            ],
        }

    def extract_against_attachment(
        self,
        form_key: str,
        attachment_id: str,
        *,
        field_subset: list[str] | None = None,
        user_id: str | None = None,
    ) -> ExtractResult:
        """Run extract against an already-stored attachment.

        Used by the per-product Specifications tooltip. Loads the attachment
        bytes via ``storage_router``, optionally narrows the registered schema
        to ``field_subset`` (so the LLM only emits requested fields), then runs
        the same pipeline as :meth:`extract`.
        """
        from app.models.resources import Attachment
        from app.services import storage_router

        if not attachment_id:
            raise AppException(
                status_code=400,
                message="attachment_id is required.",
                code="ai_extract_no_attachment",
            )
        row = (
            self.db.query(Attachment)
            .filter(Attachment.id == attachment_id, Attachment.is_deleted.is_(False))
            .first()
        )
        if row is None:
            raise AppException(
                status_code=404,
                message="Attachment not found.",
                code="ai_extract_attachment_not_found",
            )
        key = storage_router.extract_key(row.file_path)
        if not key:
            raise AppException(
                status_code=400,
                message="Attachment has no storage key.",
                code="ai_extract_attachment_no_key",
            )
        try:
            data = storage_router.get_backend(row.storage_provider).download_file(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ai_extract attachment download failed (id=%s): %s", attachment_id, exc
            )
            raise AppException(
                status_code=502,
                message="Failed to load attachment bytes from storage.",
                code="ai_extract_attachment_download_failed",
            ) from exc

        files = [
            ExtractFile(
                filename=row.original_filename or row.stored_filename or "attachment",
                mime=(row.mime_type or "").lower(),
                data=data,
            )
        ]
        return self._run_extract(
            form_key,
            files,
            field_subset=field_subset,
            user_id=user_id,
            portal_contact_id=None,
        )

    def extract(
        self,
        form_key: str,
        files: list[ExtractFile],
        *,
        user_id: str | None = None,
        portal_contact_id: str | None = None,
    ) -> ExtractResult:
        return self._run_extract(
            form_key,
            files,
            field_subset=None,
            user_id=user_id,
            portal_contact_id=portal_contact_id,
        )

    def _run_extract(
        self,
        form_key: str,
        files: list[ExtractFile],
        *,
        field_subset: list[str] | None,
        user_id: str | None,
        portal_contact_id: str | None,
    ) -> ExtractResult:
        if not files:
            raise AppException(
                status_code=400,
                message="At least one file is required for AI extract.",
                code="ai_extract_no_files",
            )
        schema = get_form_schema(form_key)
        if field_subset:
            wanted = set(field_subset)
            schema = [f for f in schema if f.name in wanted]
            if not schema:
                raise AppException(
                    status_code=400,
                    message=(
                        f"None of the requested field_keys are registered for "
                        f"{form_key}: {', '.join(sorted(wanted))}"
                    ),
                    code="ai_extract_unknown_fields",
                )
        guidance = self._build_field_guidance(schema)
        image_parts = self._render_files(files)
        pasted_text = self._collect_text(files)
        if not image_parts and not pasted_text:
            raise AppException(
                status_code=400,
                message="No readable image, PDF page, or text in the uploaded files.",
                code="ai_extract_no_content",
            )

        provider, provider_name, model_name = self._resolve_provider()
        messages = self._build_messages(
            form_key,
            schema,
            guidance,
            has_line_items=_form_has_line_items(form_key),
            pasted_text=pasted_text,
        )
        started = time.perf_counter()
        try:
            result: ChatResult = provider.chat(
                messages=messages,
                images=image_parts,
                response_format={"type": "json_object"},
                temperature=0.0,
                max_tokens=4096,
                model=model_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_extract provider call failed: %s", exc)
            raise AppException(
                status_code=502,
                message=f"AI provider call failed: {exc}",
                code="ai_extract_provider_failed",
            ) from exc
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        parsed = self._parse_json(result.content)
        values, per_field = self._validate_and_canonicalize(parsed, schema)
        products = (
            self._extract_products(parsed)
            if _form_has_line_items(form_key)
            else []
        )

        self._log_usage(
            provider_name=provider_name,
            model=model_name,
            result=result,
            elapsed_ms=elapsed_ms,
            user_id=user_id,
            contact_id=portal_contact_id,
            form_key=form_key,
            page_count=len(image_parts),
            answered=bool(values),
        )

        return ExtractResult(
            values=values,
            products=products,
            per_field=per_field,
            usage=TokenUsage(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
            ),
            model=model_name,
            provider=provider_name,
        )

    # ----- Provider ---------------------------------------------------------

    def _resolve_provider(self) -> tuple[LLMProvider, str, str]:
        """Resolve the active provider from ``AIAssistantConfig`` (same source
        as the chat assistant).

        The key comes from the shared ``resolve_api_key``, which reads the
        provider-specific column first and only hands over the generic
        ``api_key_ciphertext`` when it belongs to the provider being asked for.
        Reading that generic column alone left an install configured on
        Anthropic or Gemini with no key here and silently posted the OpenAI
        environment key to the wrong vendor.
        """
        from app.models.ai_assistant import AIAssistantConfig

        cfg = (
            self.db.query(AIAssistantConfig)
            .order_by(AIAssistantConfig.created_at.asc())
            .first()
        )
        provider_name = (cfg.provider if cfg else "openai") or "openai"
        model_name = (cfg.model if cfg else "") or ""
        api_key = resolve_api_key(cfg, provider_name)
        if not api_key:
            raise AppException(
                status_code=400,
                message=(
                    "AI provider API key is not configured. Set it in System "
                    "→ AI Assistant Settings."
                ),
                code="ai_extract_no_api_key",
            )
        if not model_name:
            model_name = default_model_for(provider_name)
        try:
            provider = get_provider(provider_name, api_key, model=model_name)
        except ValueError as exc:
            raise AppException(
                status_code=400,
                message=str(exc),
                code="ai_extract_unknown_provider",
            ) from exc
        return provider, provider_name, model_name

    # ----- Text collection --------------------------------------------------

    def _collect_text(self, files: Iterable[ExtractFile]) -> str:
        """Concatenate the decoded contents of pasted text/plain (.txt) uploads."""
        chunks: list[str] = []
        for f in files:
            mime = (f.mime or "").lower()
            name_lower = (f.filename or "").lower()
            if mime in ALLOWED_TEXT_MIMES or name_lower.endswith(_TEXT_EXTS):
                decoded = f.data.decode("utf-8", errors="replace").strip()
                if decoded:
                    chunks.append(decoded)
        return "\n\n".join(chunks)[:TEXT_MAX_CHARS]

    # ----- File rendering ---------------------------------------------------

    def _render_files(self, files: Iterable[ExtractFile]) -> list[ImagePart]:
        out: list[ImagePart] = []
        pages_emitted = 0
        for f in files:
            mime = (f.mime or "").lower()
            name_lower = f.filename.lower()
            if mime in ALLOWED_PDF_MIMES or name_lower.endswith(".pdf"):
                pages = self._render_pdf(f.data, remaining=PDF_MAX_PAGES - pages_emitted)
                out.extend(pages)
                pages_emitted += len(pages)
            elif mime in ALLOWED_VIDEO_MIMES or name_lower.endswith(_VIDEO_EXTS):
                remaining = PDF_MAX_PAGES - pages_emitted
                frames = self._render_video(
                    f.data,
                    filename=f.filename,
                    remaining=min(VIDEO_MAX_FRAMES, remaining),
                )
                out.extend(frames)
                pages_emitted += len(frames)
            elif mime in ALLOWED_IMAGE_MIMES or any(
                name_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp")
            ):
                normalized_mime = "image/jpeg" if mime == "image/jpg" else (mime or "image/png")
                out.append(
                    ImagePart(
                        mime=normalized_mime,
                        data_b64=base64.b64encode(f.data).decode("ascii"),
                    )
                )
                pages_emitted += 1
            else:
                logger.info("ai_extract skipping unsupported file mime=%s name=%s", mime, f.filename)
            if pages_emitted >= PDF_MAX_PAGES:
                break
        return out

    def _render_video(
        self,
        data: bytes,
        *,
        filename: str,
        remaining: int,
    ) -> list[ImagePart]:
        if remaining <= 0:
            return []
        try:
            import cv2  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - install guard
            raise AppException(
                status_code=500,
                message=(
                    "opencv-python-headless is not installed. Run "
                    "pip install -r requirements.txt."
                ),
                code="ai_extract_opencv_missing",
            ) from exc

        suffix = ""
        if "." in filename:
            suffix = "." + filename.rsplit(".", 1)[-1].lower()
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            tmp.write(data)
            tmp.flush()
            tmp.close()
            cap = cv2.VideoCapture(tmp.name)
            if not cap.isOpened():
                logger.warning("ai_extract failed to open video name=%s", filename)
                return []
            try:
                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if total_frames <= 0:
                    return self._scan_video_sequentially(cap, remaining)
                # Sample frames evenly across the clip, skipping the first/last
                # few percent (often black / fade) by biasing toward the middle.
                count = min(remaining, VIDEO_MAX_FRAMES, total_frames)
                if count <= 0:
                    return []
                step = total_frames / (count + 1)
                out: list[ImagePart] = []
                for i in range(1, count + 1):
                    idx = int(step * i)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        continue
                    part = self._encode_video_frame(cv2, frame)
                    if part is not None:
                        out.append(part)
                return out
            finally:
                cap.release()
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    def _scan_video_sequentially(self, cap, remaining: int) -> list[ImagePart]:
        import cv2  # type: ignore[import-not-found]

        out: list[ImagePart] = []
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        # Pick a frame every ~2 seconds when the container hides frame count.
        stride = max(1, int(fps * 2))
        read = 0
        while len(out) < min(remaining, VIDEO_MAX_FRAMES):
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if read % stride == 0:
                part = self._encode_video_frame(cv2, frame)
                if part is not None:
                    out.append(part)
            read += 1
        return out

    def _encode_video_frame(self, cv2, frame) -> ImagePart | None:
        h, w = frame.shape[:2]
        scale = min(1.0, VIDEO_FRAME_MAX_DIM / float(max(h, w)))
        if scale < 1.0:
            frame = cv2.resize(
                frame,
                (int(w * scale), int(h * scale)),
                interpolation=cv2.INTER_AREA,
            )
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok:
            return None
        return ImagePart(
            mime="image/jpeg",
            data_b64=base64.b64encode(buf.tobytes()).decode("ascii"),
        )

    def _render_pdf(self, data: bytes, *, remaining: int) -> list[ImagePart]:
        if remaining <= 0:
            return []
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - install guard
            raise AppException(
                status_code=500,
                message="PyMuPDF is not installed. Run pip install -r requirements.txt.",
                code="ai_extract_pymupdf_missing",
            ) from exc
        out: list[ImagePart] = []
        with fitz.open(stream=data, filetype="pdf") as doc:
            zoom = PDF_RENDER_DPI / 72.0
            mtx = fitz.Matrix(zoom, zoom)
            for i, page in enumerate(doc):
                if i >= remaining:
                    break
                pix = page.get_pixmap(matrix=mtx, alpha=False)
                out.append(
                    ImagePart(
                        mime="image/png",
                        data_b64=base64.b64encode(pix.tobytes("png")).decode("ascii"),
                    )
                )
        return out

    # ----- Field guidance ---------------------------------------------------

    def _build_field_guidance(
        self, schema: list[ExtractFieldSpec]
    ) -> dict[str, dict[str, Any]]:
        guidance: dict[str, dict[str, Any]] = {}
        for f in schema:
            entry: dict[str, Any] = {"kind": f.kind}
            if f.note:
                entry["note"] = f.note
            if f.examples:
                entry["examples"] = list(f.examples)
            if f.kind == "lookup":
                lk = self._lookup_options(f)
                if lk is not None:
                    entry["lookup"] = lk
            elif f.kind == "fk_product":
                entry["product_examples"] = self._product_examples()
            elif f.kind == "do_number":
                entry["multi"] = True
            guidance[f.name] = entry
        return guidance

    def _lookup_options(self, f: ExtractFieldSpec) -> dict[str, Any] | None:
        # Prefer explicit set_key; otherwise resolve via (table, column) binding.
        s: LookupSet | None = None
        if f.set_key:
            s = self.db.query(LookupSet).filter(LookupSet.set_key == f.set_key).first()
        if s is None and f.table and f.column:
            binding = (
                self.db.query(LookupBinding)
                .filter(
                    LookupBinding.table_name == f.table,
                    LookupBinding.column_name == f.column,
                )
                .first()
            )
            if binding is not None:
                s = self.db.query(LookupSet).filter(LookupSet.id == binding.set_id).first()
        if s is None or not s.is_active:
            return None
        rows = (
            self.db.query(LookupOption)
            .filter(LookupOption.set_id == s.id, LookupOption.is_active.is_(True))
            .order_by(LookupOption.sort_order.asc(), LookupOption.label.asc())
            .all()
        )
        opts: list[dict[str, Any]] = []
        for o in rows:
            kws = (
                self.db.query(LookupOptionKeyword)
                .filter(LookupOptionKeyword.option_id == o.id)
                .all()
            )
            opts.append(
                {
                    "value": o.value,
                    "label": o.label,
                    "keywords": [k.keyword for k in kws],
                }
            )
        return {
            "set_key": s.set_key,
            "set_name": s.name,
            "strict": True,
            "options": opts,
            "instruction": (
                "Return ONLY one of the listed `value` strings exactly - "
                "case-sensitive. Use the `keywords` list per option to map "
                "free text. If nothing matches, omit the field."
            ),
        }

    def _product_examples(self) -> list[dict[str, str]]:
        try:
            rows = (
                self.db.query(Product.product_code, Product.product_name)
                .filter(Product.is_active.is_(True))
                .order_by(Product.product_code.asc())
                .limit(PRODUCT_HINT_LIMIT)
                .all()
            )
        except Exception:  # noqa: BLE001
            return []
        return [
            {"product_code": str(r[0]), "product_name": str(r[1] or "")}
            for r in rows
        ]

    # ----- Prompt construction ---------------------------------------------

    def _build_messages(
        self,
        form_key: str,
        schema: list[ExtractFieldSpec],
        guidance: dict[str, dict[str, Any]],
        has_line_items: bool = True,
        pasted_text: str = "",
    ) -> list[dict]:
        field_specs = []
        for f in schema:
            spec: dict[str, Any] = {
                "name": f.name,
                "label": f.label,
                "kind": f.kind,
            }
            g = guidance.get(f.name, {})
            if g.get("note"):
                spec["note"] = g["note"]
            if "lookup" in g:
                spec["lookup"] = {
                    "set_key": g["lookup"]["set_key"],
                    "options": g["lookup"]["options"],
                    "instruction": g["lookup"]["instruction"],
                }
            if "product_examples" in g and g["product_examples"]:
                spec["product_code_examples"] = g["product_examples"]
            if "examples" in g and g["examples"]:
                spec["examples"] = g["examples"]
            if g.get("multi"):
                spec["multi"] = True
            field_specs.append(spec)

        # PLAN price-tag-currency-token-extract-prompt, Slice B: the system
        # text is now PER FORM KEY, resolved from the prompt registry so it
        # can be edited from System Management > AI Assistant without a
        # deploy - a hardcoded string here had no key at all. A local import:
        # this module must not import `ai_prompt_registry` at module scope,
        # since that registry's own `_register_ai_extract_keys()` imports
        # `extract_prompt_key` from here (call shape as
        # `product_spec_understanding.py:552`).
        from app.services.ai_prompt_registry import get_prompt

        system = get_prompt(self.db, extract_prompt_key(form_key)).text
        # Security review 16 Sep: a blank system prompt (a bad publish that
        # wiped the production version, a bug in the registry's own fallback)
        # must not send the model an LLM call with no rules at all - that
        # answers SOMETHING, silently ungoverned, rather than failing loudly.
        if not system or not system.strip():
            raise AppException(
                status_code=500,
                message="AI extract prompt is not configured for this form.",
                code="ai_extract_prompt_missing",
            )
        is_price_tag_form = form_key == "portal.price_tag_request"
        if has_line_items and is_price_tag_form:
            # S2 (PLAN-price-tag-r10.md, rule 9): the entry shape itself drops
            # the fields a document never states for this form - how many
            # tags marketing wants is decided later, never read off a DO or a
            # photo. The word this clause must never use is spelled out in
            # rule 9's own text, not repeated here, so a stray mention never
            # regresses AC-S2-2's "not present anywhere" assertion.
            line_items_clause = (
                " Include a top-level `products` array of "
                "{product_code, product_name, notes} "
                "when the document lists line items (see rule 8). Do not state "
                "how many of each are wanted or their prices - see rule 9."
            )
        elif has_line_items:
            line_items_clause = (
                " Include a top-level `products` array of "
                "{product_code, product_name, quantity, unit_price, total, notes} "
                "when the document lists line items"
                ". Only include `unit_price` and `total` when the document "
                "actually shows them; omit otherwise."
            )
        else:
            line_items_clause = (
                " Do NOT include a top-level `products` array - this form has "
                "no line-item table. Distinct product codes belong in the "
                "`product_code` field as a comma-separated string."
            )
        user_text = (
            f"Form: {form_key}\n"
            f"Fields:\n{json.dumps(field_specs, ensure_ascii=False, indent=2)}\n\n"
            "Return ONLY a single JSON object keyed by field name."
            + line_items_clause
        )
        if pasted_text:
            user_text += (
                "\n\nThe user pasted the following text; treat it as an additional "
                "source document and extract from it too:\n---\n"
                + pasted_text
                + "\n---"
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ]

    # ----- Response parsing ------------------------------------------------

    def _parse_json(self, content: str) -> dict[str, Any]:
        if not content:
            raise AppException(
                status_code=502,
                message="AI provider returned an empty response.",
                code="ai_extract_empty_response",
            )
        text = content.strip()
        # Strip code-fence wrappers some models emit despite the instruction.
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:].lstrip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            # Try to locate the first JSON object in the string as a fallback.
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    raise AppException(
                        status_code=502,
                        message=f"AI provider returned non-JSON content: {exc}",
                        code="ai_extract_bad_json",
                    ) from exc
            else:
                raise AppException(
                    status_code=502,
                    message=f"AI provider returned non-JSON content: {exc}",
                    code="ai_extract_bad_json",
                ) from exc
        if not isinstance(data, dict):
            raise AppException(
                status_code=502,
                message="AI provider returned a non-object JSON value.",
                code="ai_extract_bad_json",
            )
        return data

    def _validate_and_canonicalize(
        self,
        parsed: dict[str, Any],
        schema: list[ExtractFieldSpec],
    ) -> tuple[dict[str, Any], dict[str, ExtractFieldMeta]]:
        resolver = LookupResolverService(self.db)
        values: dict[str, Any] = {}
        per_field: dict[str, ExtractFieldMeta] = {}
        for f in schema:
            raw = parsed.get(f.name)
            if raw is None or raw == "" or (isinstance(raw, list) and not raw):
                continue
            if f.kind == "lookup":
                key = f.set_key or self._set_key_for(f)
                if not key:
                    continue
                try:
                    resolved = resolver.resolve(key, str(raw))
                except AppException:
                    logger.info(
                        "ai_extract dropped unresolvable lookup %s=%r", f.name, raw
                    )
                    continue
                values[f.name] = resolved.value
                per_field[f.name] = ExtractFieldMeta(
                    raw=raw, canonical=resolved.value, source="lookup_resolved"
                )
            elif f.kind == "do_number":
                items = self._coerce_do_list(raw)
                if items:
                    values[f.name] = items
                    per_field[f.name] = ExtractFieldMeta(
                        raw=raw, canonical=items, source="llm"
                    )
            elif f.kind == "fk_product":
                # S6 (code review): a `fk_product` field names a PRODUCT, on
                # a stock inquiry / purchase request form - never a set. The
                # wider `{"product", "product_set"}` default is only for
                # `_extract_products`'s own sales-order lines below.
                if isinstance(raw, list):
                    raw_codes = [str(x).strip() for x in raw if str(x).strip()]
                    resolved = self._resolve_product_codes(
                        raw_codes, allowed_entity_types=frozenset({"product"})
                    )
                    codes = [
                        resolved[c][0] if c in resolved else c for c in raw_codes
                    ]
                    csv = ", ".join(c for c in codes if c)
                    if not csv:
                        continue
                    values[f.name] = csv
                    per_field[f.name] = ExtractFieldMeta(
                        raw=raw, canonical=csv, source="product_master"
                    )
                else:
                    raw_code = str(raw).strip()
                    resolved = (
                        self._resolve_product_codes(
                            [raw_code], allowed_entity_types=frozenset({"product"})
                        )
                        if raw_code
                        else {}
                    )
                    code = resolved[raw_code][0] if raw_code in resolved else raw_code
                    values[f.name] = code
                    per_field[f.name] = ExtractFieldMeta(
                        raw=raw,
                        canonical=code,
                        source="product_master" if code != raw_code else "llm",
                    )
            elif f.kind == "date":
                s = str(raw).strip()
                if s:
                    values[f.name] = s
                    per_field[f.name] = ExtractFieldMeta(raw=raw, canonical=s, source="llm")
            elif f.kind == "number":
                values[f.name] = raw
                per_field[f.name] = ExtractFieldMeta(raw=raw, canonical=raw, source="llm")
            else:  # text, textarea, fk_customer, multi_text
                if isinstance(raw, list):
                    parts = [str(x).strip() for x in raw if str(x).strip()]
                    s = ", ".join(parts)
                else:
                    s = str(raw).strip()
                if s:
                    values[f.name] = s
                    per_field[f.name] = ExtractFieldMeta(raw=raw, canonical=s, source="llm")

        # Derive product_type from product master categories so portal forms
        # (complaint, stock_inquiry, ...) don't rely on the LLM's freeform
        # taxonomy. Only fires when both fields are in the schema and
        # product_code resolved to one or more codes in the master.
        has_product_type = any(f.name == "product_type" for f in schema)
        if has_product_type and isinstance(values.get("product_code"), str):
            derived = self._derive_product_type_csv(values["product_code"])
            if derived:
                values["product_type"] = derived
                per_field["product_type"] = ExtractFieldMeta(
                    raw=parsed.get("product_type"),
                    canonical=derived,
                    source="product_master",
                )
        return values, per_field

    def _derive_product_type_csv(self, codes_csv: str) -> str:
        codes = [c.strip() for c in codes_csv.split(",") if c.strip()]
        if not codes:
            return ""
        try:
            rows = (
                self.db.query(Product.product_code, ProductCategory.category_code, ProductCategory.category_name)
                .join(ProductCategory, Product.category_id == ProductCategory.id)
                .filter(Product.product_code.in_(codes))
                .all()
            )
        except Exception:  # noqa: BLE001
            return ""
        seen: list[str] = []
        for _code, ccode, cname in rows:
            label = (ccode or cname or "").strip()
            if not label:
                continue
            if label not in seen:
                seen.append(label)
        return ", ".join(seen)

    def _set_key_for(self, f: ExtractFieldSpec) -> str | None:
        if f.set_key:
            return f.set_key
        if not (f.table and f.column):
            return None
        binding = (
            self.db.query(LookupBinding)
            .filter(
                LookupBinding.table_name == f.table,
                LookupBinding.column_name == f.column,
            )
            .first()
        )
        if not binding:
            return None
        s = self.db.query(LookupSet).filter(LookupSet.id == binding.set_id).first()
        return s.set_key if s else None

    def _coerce_do_list(self, raw: Any) -> list[str]:
        if isinstance(raw, list):
            return [str(x).strip() for x in raw if str(x).strip()]
        if isinstance(raw, str):
            parts = [p.strip() for p in raw.replace(";", ",").split(",")]
            return [p for p in parts if p]
        return []

    def _resolve_product_codes(
        self,
        codes: list[str],
        *,
        allowed_entity_types: frozenset[str] = frozenset({"product", "product_set"}),
        suggestions_out: dict[str, list[ExtractedProductSuggestion]] | None = None,
    ) -> dict[
        str,
        tuple[str, Literal["product", "product_set"] | None, str | None, str | None, str | None],
    ]:
        """codes -> (canonical_code, match, product_id, product_set_id,
        display_name), `resolve_references` called once per 100-code chunk
        (D1), exact tier only (`enable_prefix_fallback=False`,
        `enable_embedding_fallback=False`): a prefix or semantic guess would
        print a tag for a product the sheet never named. A code with no exact
        match, or an ambiguous one (more than one scoped hit), is left out of
        the map so the caller keeps the raw extracted text (D2). Company-scoped
        by whatever `company_scope` the caller is already inside.

        `allowed_entity_types` lets a caller (the scalar `fk_product` field
        path below) narrow the match to `{"product"}` alone - a stock inquiry
        / purchase request field names a PRODUCT, never a set, and the wider
        default is only for `_extract_products`'s own sales-order lines.

        Security review: `resolve_references` itself TRUNCATES its token list
        to `max_candidates` (its own `PRE_EXTRACTED_TOKEN_CEILING`, 50, when
        the caller sets none; the cut is echoed in `truncated_tokens` since
        RESOLVER-TOKEN-CAP) rather than merely capping the cost of its own
        trigram "did you mean" pass over the misses - so a flat cap silently
        dropped code 101 onward instead of just bounding that pass. Chunked into batches of
        `_MAX_RESOLVE_CANDIDATES` and resolved one call per chunk instead, so
        every code is still answered and no single call runs the resolver's
        alternatives pass over an unbounded batch (no line ceiling exists in
        the price tag / portal code to defer to for the chunk size, and
        `resolve_references` exposes no switch to skip that pass on its own -
        simplest is to bound the batch, not add one).

        AI-EXTRACT-DYM: when `suggestions_out` is given, every code left out of
        the map gets its "did you mean" candidates written there - the
        `alternatives` the same resolver call already computed for a miss
        (company-scoped trigram neighbours, `entity_resolver.resolve_references`),
        products only, active only (the manual Item picker's own filter,
        `tag_data_service.search_products`), at most `_MAX_SUGGESTIONS`. No
        second lookup and no auto-apply: the user confirms one in the dialog.
        """
        cleaned = [c for c in (codes or []) if c]
        if not cleaned:
            return {}
        out: dict[
            str,
            tuple[
                str, Literal["product", "product_set"] | None, str | None, str | None, str | None
            ],
        ] = {}
        for start in range(0, len(cleaned), self._MAX_RESOLVE_CANDIDATES):
            chunk = cleaned[start : start + self._MAX_RESOLVE_CANDIDATES]
            try:
                result = resolve_references(
                    self.db,
                    chunk,
                    allowed_entity_types=allowed_entity_types,
                    enable_prefix_fallback=False,
                    enable_embedding_fallback=False,
                    max_candidates=len(chunk),
                )
            except Exception:
                # A resolver failure must not turn an LLM extract that already
                # cost real money into a 500 on the public route - every code
                # in THIS chunk answers as the D2 "no match" shape (raw text,
                # no ids) instead; the other chunks are unaffected.
                logger.exception(
                    "resolve_references failed during AI extract product matching"
                )
                continue
            for tr in result.resolutions:
                if not tr.resolved or len(tr.matches) != 1:
                    continue
                match = tr.matches[0]
                # Re-review finding: `resolve_references` widens a `product`
                # request to ALSO probe `product_set` internally (n8n relies
                # on that widening elsewhere - `_expand_entity_types` in
                # `entity_resolver.py`), so a caller that asked for
                # `{"product"}` alone (the fk_product path, S6) still gets a
                # `product_set` row back in `tr.matches` when that is the
                # only thing that matched. The resolver's own D10b guard only
                # uses this distinction to decide whether to compute "did you
                # mean" alternatives - `tr.matches` itself is unfiltered.
                # Filtered against THIS caller's own `allowed_entity_types`
                # (not the resolver's internally-widened set), so a type the
                # caller never asked for reads exactly like no match at all,
                # not a match to something it has nowhere to send.
                if match.entity_type not in allowed_entity_types:
                    continue
                if match.entity_type == "product":
                    name = (match.display or {}).get("product_name")
                    out[tr.token] = (match.canonical_code, "product", match.uuid, None, name)
                elif match.entity_type == "product_set":
                    name = (match.display or {}).get("name")
                    out[tr.token] = (match.canonical_code, "product_set", None, match.uuid, name)
            if suggestions_out is not None:
                self._collect_suggestions(
                    [tr for tr in result.resolutions if tr.token not in out],
                    suggestions_out,
                )
        return out

    def _collect_suggestions(
        self,
        misses: list[TokenResolution],
        suggestions_out: dict[str, list[ExtractedProductSuggestion]],
    ) -> None:
        """Writes each missed token's active product `alternatives` (in the
        resolver's own ranking) into `suggestions_out`. One bounded IN query for
        the active check across the chunk."""
        candidates = {
            tr.token: [
                a for a in tr.alternatives if a.entity_type == "product" and a.uuid
            ]
            for tr in misses
        }
        ids = {a.uuid for alts in candidates.values() for a in alts}
        if not ids:
            return
        active = {
            str(pid)
            for (pid,) in self.db.query(Product.id)
            .filter(Product.id.in_(ids), Product.is_active.is_(True))
            .all()
        }
        for token, alts in candidates.items():
            picked = [
                ExtractedProductSuggestion(
                    product_id=str(a.uuid),
                    product_code=a.canonical_code,
                    product_name=(a.display or {}).get("product_name"),
                )
                for a in alts
                if str(a.uuid) in active
            ][: self._MAX_SUGGESTIONS]
            if picked:
                suggestions_out[token] = picked

    def _extract_products(self, parsed: dict[str, Any]) -> list[ExtractedProductLine]:
        raw = parsed.get("products")
        if not isinstance(raw, list):
            return []
        items = [item for item in raw if isinstance(item, dict)]
        raw_codes = [str(item.get("product_code") or "").strip() for item in items]
        suggestions: dict[str, list[ExtractedProductSuggestion]] = {}
        resolved = self._resolve_product_codes(
            [c for c in raw_codes if c], suggestions_out=suggestions
        )

        def _coerce_float(raw: Any) -> float | None:
            if raw is None or raw == "":
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None

        out: list[ExtractedProductLine] = []
        for item, raw_code in zip(items, raw_codes):
            code: str | None = raw_code or None
            match: Literal["product", "product_set"] | None = None
            product_id: str | None = None
            product_set_id: str | None = None
            # S1 (code review): the CRM's own name on a resolved match, not
            # the sales order's freeform description the LLM read off the
            # page - the two drift (abbreviations, a customer's own wording),
            # and the applied line's Item is what marketing/the salesperson
            # reads back. A miss keeps the LLM text; there is nothing else to
            # show for a code the resolver could not place.
            llm_name = str(item.get("product_name") or "").strip() or None
            product_name = llm_name
            if raw_code and raw_code in resolved:
                code, match, product_id, product_set_id, display_name = resolved[raw_code]
                if display_name:
                    product_name = display_name
            out.append(
                ExtractedProductLine(
                    product_code=code,
                    product_name=product_name,
                    quantity=_coerce_float(item.get("quantity")),
                    unit_price=_coerce_float(item.get("unit_price")),
                    total=_coerce_float(item.get("total")),
                    notes=str(item.get("notes") or "").strip() or None,
                    match=match,
                    product_id=product_id,
                    product_set_id=product_set_id,
                    suggestions=[] if match else suggestions.get(raw_code, []),
                )
            )
        return out

    # ----- Usage logging ----------------------------------------------------

    def _log_usage(
        self,
        *,
        provider_name: str,
        model: str,
        result: ChatResult,
        elapsed_ms: int,
        user_id: str | None,
        contact_id: str | None,
        form_key: str,
        page_count: int,
        answered: bool,
    ) -> None:
        try:
            self.db.add(
                AIAssistantUsageLog(
                    user_id=user_id,
                    contact_id=contact_id,
                    feature="ai_extract",
                    form_key=form_key,
                    provider=provider_name,
                    model=model,
                    prompt_tokens=int(result.prompt_tokens or 0),
                    completion_tokens=int(result.completion_tokens or 0),
                    total_tokens=int(result.total_tokens or 0),
                    tool_calls_count=0,
                    response_time_ms=elapsed_ms,
                    was_answered=answered,
                )
            )
            self.db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ai_extract usage log failed (non-fatal): %s", exc)
            self.db.rollback()
