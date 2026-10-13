"""Read an uploaded document page by page with a vision model.

Shared by customer PO intake and delivery-schedule intake, which are the same
mechanical job on different paper: rasterise, ask per page, parse JSON, keep the
raw answer.

Four decisions worth knowing before you use it:

**Per page, never whole document.** A ten page scan asked as one request gives one
answer that is wrong in an unlocatable way, and the failure mode of a vision model on
a long document is quiet omission. Per page, a bad page is a bad page, the others
stand, and the retry is cheap.

**Pages run concurrently, but the model call is the only thing that leaves the main
thread.** ``document_ai_page_concurrency`` pages are in flight at once, each doing its
own HTTP round trip to the provider; nothing about that step touches the database. The
DB session that ``on_page`` writes progress through stays on the calling thread, called
once per page as its answer lands - a ``Session`` is not safe to share across threads,
so this is the one rule that keeps the pool correct rather than merely fast.

**The raw answer is kept.** ``PageResult.data`` goes into ``extracted_json`` verbatim.
Every number is checked downstream by arithmetic that does not consult the model, and
when a check fails the only way to tell a misread from a bad rule is to still have what
the model actually said.

**A page that fails does not fail the document.** ``PageResult.error`` is set and the
page carries no data. Nine good pages out of ten is a document a person can finish; an
exception is a document they cannot start.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.services.llm_provider import ImagePart, get_provider, resolve_api_key

logger = logging.getLogger(__name__)

RASTER_MIMES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}

# The page's own text layer is sent alongside the image (19 Aug follow-up: "text +
# image together"), capped so one dense CAD-exported page cannot blow up the prompt.
# 6k characters is generous for a purchase order or a schedule matrix - both are
# tables, not paragraphs - but the cap is real and a page beyond it IS truncated
# mid-token, so a cut page carries `_PAGE_TEXT_TRUNCATED_MARKER` (below) rather than
# stopping silently: the prompt tells the model the text layer is authoritative for
# codes and numbers, and a truncated layer that says nothing about being truncated
# would have the model trust a cut-off code as if it were the whole one.
PAGE_TEXT_CHAR_LIMIT = 6000

# Appended to a page's text layer when it was cut at PAGE_TEXT_CHAR_LIMIT, so the
# prompt's own "authoritative for codes/numbers" rule is read against text that
# says plainly it does not cover the whole page.
_PAGE_TEXT_TRUNCATED_MARKER = "... [text layer truncated]"

# The environment leg of ``resolve_api_key``, named per provider so an operator
# with no key on the settings page is told which variable to set rather than
# being sent to look at the wrong one. Anything outside these two falls back to
# the OpenAI key in the resolver, so it is named the same way here.
_ENV_KEY_NAME = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


class ExtractionUnavailable(RuntimeError):
    """No key configured. Distinct from a failed read: nothing was attempted."""


@dataclass
class PageResult:
    page_no: int  # 1-based, matching what the person sees in the viewer
    data: Optional[dict] = None
    error: Optional[str] = None
    raw_text: Optional[str] = None  # kept only when parsing failed, for diagnosis
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    elapsed_ms: int = 0


@dataclass
class ExtractionResult:
    model: str
    page_count: int
    pages: list[PageResult] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    # How long the model actually took. Reading ten scanned pages is minutes of
    # waiting, and a person who is told "2m 14s" once stops wondering whether it hung.
    elapsed_ms: int = 0

    @property
    def ok_pages(self) -> list[PageResult]:
        return [p for p in self.pages if p.data is not None]

    @property
    def failed_pages(self) -> list[int]:
        return [p.page_no for p in self.pages if p.data is None]


@dataclass
class RenderedPage:
    """One page's own answer to "what does the model see". ``text`` is the PDF's own
    text layer (``page.get_text("text")``), not OCR - present only when the document
    was exported with one, which is true of every schedule and most POs. Capturing it
    here means the vision pass and the text pass share the one PyMuPDF read of the
    page rather than opening the document twice."""

    image_b64: str
    image_mime: str
    text: Optional[str] = None


def _encode_page_image(pixmap) -> tuple[bytes, str]:
    """JPEG at quality 85 instead of PNG (19 Aug follow-up: "absolutely the fastest") -
    measured on the golden PO (10 pages, 170 DPI): PNG 4.64 MB total, JPEG 2.46 MB,
    53% of the size for the same upload, which is that much less to transmit before the
    model can start reading. A rendered page carries no alpha channel, so nothing about
    the picture is lost. Falls back to PNG only if the installed PyMuPDF cannot emit
    JPEG at all, so an older build keeps working rather than failing every upload.
    """
    try:
        return pixmap.tobytes("jpeg", jpg_quality=85), "image/jpeg"
    except Exception:  # noqa: BLE001 - genuinely defensive; not expected to fire
        logger.warning("PyMuPDF could not encode JPEG; falling back to PNG", exc_info=True)
        return pixmap.tobytes("png"), "image/png"


def _cap_page_text(raw_text: str) -> Optional[str]:
    """Cap a page's text layer at ``PAGE_TEXT_CHAR_LIMIT``, flagging the cut.

    The prompt tells the model the text layer is authoritative for codes and
    numbers; a page cut mid-token with no marker leaves the model no way to know
    it is trusting an incomplete layer.
    """
    if not raw_text:
        return None
    if len(raw_text) <= PAGE_TEXT_CHAR_LIMIT:
        return raw_text
    cutoff = max(PAGE_TEXT_CHAR_LIMIT - len(_PAGE_TEXT_TRUNCATED_MARKER), 0)
    return raw_text[:cutoff].rstrip() + _PAGE_TEXT_TRUNCATED_MARKER


def _render_pages_rich(
    content: bytes, mime: str, *, dpi: Optional[int] = None, page_limit: Optional[int] = None
) -> list[RenderedPage]:
    """Rasterise a PDF (or pass through an image), one page per entry.

    A photographed PO arrives as a JPEG and needs no rasterising, which is why the
    image case is not an error - and carries no text layer, a photograph has none.
    """
    mime = (mime or "").lower()
    if mime in RASTER_MIMES:
        return [RenderedPage(image_b64=base64.b64encode(content).decode(), image_mime=mime)]

    import fitz  # PyMuPDF; imported here so the module stays cheap to import

    dpi = dpi or settings.document_ai_render_dpi
    limit = page_limit or settings.document_ai_page_limit
    document = fitz.open(stream=content, filetype="pdf")
    try:
        pages: list[RenderedPage] = []
        for index in range(min(len(document), limit)):
            page = document[index]
            pixmap = page.get_pixmap(dpi=dpi)
            image_bytes, image_mime = _encode_page_image(pixmap)
            # `get_text()`'s overloads are keyed on the option string, which pyright
            # cannot narrow from a runtime constant; "text" always returns a str.
            raw_text = str(page.get_text("text") or "").strip()
            page_text = _cap_page_text(raw_text)
            pages.append(
                RenderedPage(
                    image_b64=base64.b64encode(image_bytes).decode(),
                    image_mime=image_mime,
                    text=page_text,
                )
            )
        if len(document) > limit:
            logger.warning(
                "document has %s pages, extracting the first %s (document_ai_page_limit)",
                len(document), limit,
            )
        return pages
    finally:
        document.close()


def render_pages(content: bytes, mime: str, *, dpi: Optional[int] = None,
                 page_limit: Optional[int] = None) -> list[str]:
    """Rasterise a PDF (or pass through an image) as base64 image data, one per page.

    Thin wrapper over ``_render_pages_rich`` for a caller that only wants the images,
    kept for the same reason ``page_count`` stays a separate one-line function: fewer
    surprises for whatever reads this module next.
    """
    return [page.image_b64 for page in _render_pages_rich(content, mime, dpi=dpi, page_limit=page_limit)]


def page_count(content: bytes, mime: str) -> int:
    if (mime or "").lower() in RASTER_MIMES:
        return 1
    import fitz

    document = fitz.open(stream=content, filetype="pdf")
    try:
        return len(document)
    finally:
        document.close()


def _parse_json(text: str) -> dict:
    """Tolerate a fenced answer. Structured output makes this rare, not impossible."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("```")[1]
        if body.startswith("json"):
            body = body[4:]
    return json.loads(body)


def _assistant_config(db: Session):
    """The AI assistant config row, or ``None`` when the install has never saved one.

    Read exactly the way ``ai_extract`` and the media lane read it: oldest row
    first, no write. ``AIAssistantConfigService._ensure_singleton`` would create
    and commit a row, which extraction must not do - it runs on the worker
    inside somebody else's transaction and has no principal to attribute a new
    row to. ``resolve_api_key`` treats ``None`` as "no columns set" and falls
    through to the environment key, so an install with no row configured keeps
    working exactly as it did before.
    """
    from app.models.ai_assistant import AIAssistantConfig

    return (
        db.query(AIAssistantConfig)
        .order_by(AIAssistantConfig.created_at.asc())
        .first()
    )


def _prepare_prompt(db: Session, prompt_key: str) -> tuple[str, set[str]]:
    """Resolve ``prompt_key``'s live template ONCE, before any page runs.

    Every page needs the same template with only ``page_no``/``page_text`` changed, and
    the page pool below can have several pages in flight at once - resolving it per page
    would mean N threads racing the same DB session and the same TTL cache for an answer
    that cannot change mid-document. Fetched here, on the caller's thread; each page then
    does nothing but cheap string substitution.
    """
    from app.services.ai_prompt_registry import PROMPT_KEYS, get_prompt

    spec = PROMPT_KEYS.get(prompt_key)
    declared = set(spec.variables) if spec else set()
    rendered = get_prompt(db, prompt_key)
    return rendered.text, declared


def _render_page_prompt(
    template: str, declared: set[str], *, page_no: int, page_count: int, page_text: str
) -> str:
    """Substitute this one page's ``{{page_no}}``/``{{page_count}}``/``{{page_text}}``
    into a template already fetched by ``_prepare_prompt``. ``page_text`` is filled in
    only for a prompt that declares it - the two document readers do, most other prompt
    keys never will - and is always supplied as at worst an empty string, never left out,
    the same "declared variable, optionally blank" contract ``render`` enforces."""
    from app.services.ai_prompt_registry import _substitute

    variables: dict[str, object] = {"page_no": page_no, "page_count": page_count}
    if "page_text" in declared:
        variables["page_text"] = page_text
    missing = declared - set(variables.keys())
    if missing:
        raise ValueError(
            f"Missing required variable(s) for prompt: {', '.join(sorted(missing))}"
        )
    return _substitute(template, variables)


def extract_document(
    db: Session,
    content: bytes,
    mime: str,
    *,
    prompt_key: str,
    model: Optional[str] = None,
    on_page: Optional[Callable[[PageResult], None]] = None,
) -> ExtractionResult:
    """Run ``prompt_key`` over every page of the document, ``document_ai_page_concurrency``
    pages at a time.

    ``prompt_key`` is a name in the AI prompt registry, so the prompt is versioned and
    can be rolled back without a deploy. It must declare ``page_no`` and
    ``page_count``: telling the model where it is in the document is what stops it
    inventing a header on page 7. When the page carries a text layer it rides along as
    ``page_text`` in the SAME turn as the image - the text is authoritative for exact
    codes and numbers, the image for strike-throughs, handwriting and highlights text
    alone cannot show (both prompts spell out the same rule).

    ``on_page`` is called, on THIS function's own thread, as each page's answer comes
    back - not from the pool - so a caller can persist progress through its own DB
    session without that session ever crossing a thread boundary. It fires in whatever
    order pages finish, which is fine for a progress counter; ``ExtractionResult.pages``
    below is always reassembled in page order regardless.

    The key is resolved through the shared ``resolve_api_key`` ladder against the
    AI assistant config row, for the provider ``document_ai_provider`` names: the
    provider-specific column an operator saved in the UI first, then the generic
    column when the assistant itself runs on that provider, then the environment
    key. Reading the environment alone meant a key saved on the settings page was
    silently ignored and the feature reported itself unavailable.

    The usage row this writes carries no principal. Extraction runs on the worker with
    no request behind it, and neither version row records who uploaded it, so there is
    nothing truthful to attribute it to yet. Recording a guess would be worse than the
    blank.
    """
    provider_name = (settings.document_ai_provider or "").strip().lower()
    api_key = resolve_api_key(_assistant_config(db), provider_name)
    if not api_key:
        env_var = _ENV_KEY_NAME.get(provider_name, "OPENAI_API_KEY")
        raise ExtractionUnavailable(
            f"No document extraction key configured for the '{provider_name}' provider. "
            "Set one on the AI assistant settings page (System > AI Assistant), or as "
            f"the {env_var} environment variable. "
            "The document is stored and can be completed by hand."
        )

    chosen_model = model or settings.document_ai_model
    provider = get_provider(provider_name, api_key, chosen_model)
    rendered_pages = _render_pages_rich(content, mime)
    page_total = len(rendered_pages)
    result = ExtractionResult(model=f"{provider.name}/{chosen_model}", page_count=page_total)
    template, declared_vars = _prepare_prompt(db, prompt_key)

    def _read_page(index: int, rendered: RenderedPage) -> PageResult:
        """Everything a page needs off the network. No DB access - runs in the pool."""
        page_started = time.perf_counter()
        page = PageResult(page_no=index)
        try:
            prompt = _render_page_prompt(
                template, declared_vars,
                page_no=index, page_count=page_total, page_text=rendered.text or "",
            )
            answer = provider.chat(
                [{"role": "user", "content": prompt}],
                images=[ImagePart(mime=rendered.image_mime, data_b64=rendered.image_b64)],
                temperature=0,
                response_format={"type": "json_object"},
            )
            page.prompt_tokens = answer.prompt_tokens
            page.completion_tokens = answer.completion_tokens
            page.cached_tokens = int(getattr(answer, "cached_tokens", 0) or 0)
            page.data = _parse_json(answer.content)
        except json.JSONDecodeError:
            page.error = "the model's answer was not valid JSON"
            page.raw_text = (answer.content or "")[:2000]
        except Exception as exc:  # noqa: BLE001 - one bad page must not lose the rest
            page.error = str(exc)[:300]
            logger.warning("extraction failed on page %s: %s", index, page.error)
        page.elapsed_ms = int((time.perf_counter() - page_started) * 1000)
        return page

    started = time.perf_counter()
    pages_by_no: dict[int, PageResult] = {}
    workers = max(1, min(int(settings.document_ai_page_concurrency or 1), page_total or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_read_page, index, rendered): index
            for index, rendered in enumerate(rendered_pages, start=1)
        }
        for future in as_completed(futures):
            page = future.result()
            pages_by_no[page.page_no] = page
            result.prompt_tokens += page.prompt_tokens
            result.completion_tokens += page.completion_tokens
            result.cached_tokens += page.cached_tokens
            if on_page is not None:
                try:
                    on_page(page)
                except Exception:  # noqa: BLE001 - progress reporting is best effort
                    logger.warning(
                        "on_page callback failed for page %s", page.page_no, exc_info=True
                    )

    result.pages = [pages_by_no[no] for no in sorted(pages_by_no)]
    result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    _log_usage(db, result, prompt_key=prompt_key, provider=provider.name)
    return result


def _log_usage(db: Session, result: "ExtractionResult", *, prompt_key: str, provider: str) -> None:
    """Record what this document cost, next to every other model call in the product.

    Extraction is by far the most expensive thing the system asks a model to do, and it
    was the only thing not showing up in the usage figures. Best effort on purpose: a
    telemetry write must never be the reason a document that WAS read is reported as
    failed.
    """
    try:
        from app.models.ai_assistant import AIAssistantUsageLog

        db.add(
            AIAssistantUsageLog(
                model=result.model,
                provider=provider,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.prompt_tokens + result.completion_tokens,
                cached_tokens=result.cached_tokens,
                # One call per page, so the page count IS the number of model calls.
                tool_calls_count=result.page_count,
                response_time_ms=result.elapsed_ms,
                was_answered=bool(result.ok_pages),
                feature="ai_document_extract",
                form_key=prompt_key,
            )
        )
        db.flush()
    except Exception:  # noqa: BLE001
        logger.warning("could not record extraction usage", exc_info=True)
