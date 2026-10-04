"""External semantic retrieval endpoint for n8n RAG workflows."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
import httpx

from app.database import get_db
from app.dependencies import get_external_api_user
from app.config import settings
from app.schemas.embeddings import (
    EmbeddingSearchRequest,
    EmbeddingSearchResponse,
    EmbeddingSearchResultItem,
    ToolRagSearchRequest,
    ToolRagSearchResponse,
    ToolRagResultItem,
)
from app.services.embedding_service import EmbeddingReadService

router = APIRouter()


def _embed_query(query: str) -> list[float]:
    from app.services import llm_cassette

    if llm_cassette.mode() == "replay":
        # Test runs only (LLM-TEST-COST): the same recording the embedding worker keeps.
        from app.services.embedding_worker import _embed_text_chunks

        return _embed_text_chunks([query])[0]
    if not settings.openai_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Embedding provider not configured")
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    payload = {"model": settings.embedding_model_name, "input": query}
    with httpx.Client(timeout=20) as client:
        response = client.post(settings.openai_embeddings_url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    vector = data.get("data", [{}])[0].get("embedding")
    if not vector:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Embedding provider returned empty vector")
    return vector


@router.post("/search", response_model=EmbeddingSearchResponse)
def semantic_search(
    body: EmbeddingSearchRequest,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query is required")

    query_embedding = _embed_query(query)
    rows = EmbeddingReadService(db).search_current(
        query_embedding,
        top_k=body.top_k,
        source_type=body.source_type,
        visibility_scope=body.visibility_scope,
        tenant_id=body.tenant_id,
    )
    data = [
        EmbeddingSearchResultItem(
            id=str(chunk.id),
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
            source_key=doc.source_key,
            title=doc.title,
            visibility_scope=doc.visibility_scope,
            model_name=chunk.model_name,
            model_version=chunk.model_version,
            similarity=float(similarity),
            metadata=chunk.metadata_json or {},
        )
        for chunk, doc, similarity in rows
    ]
    return EmbeddingSearchResponse(
        data=data,
        model=settings.embedding_model_name,
        dimensions=settings.embedding_dimensions,
    )


@router.post("/tool-search", response_model=ToolRagSearchResponse)
def tool_search(
    body: ToolRagSearchRequest,
    current_user: dict = Depends(get_external_api_user),
    db: Session = Depends(get_db),
):
    query = (body.query or "").strip()
    if not query:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="query is required")
    query_embedding = _embed_query(query)
    rows = EmbeddingReadService(db).search_tool_candidates(
        query_embedding,
        query=query,
        top_k=body.top_k,
        include_planned=body.include_planned,
        category=body.category,
        implementation_status=body.implementation_status,
    )
    return ToolRagSearchResponse(
        data=[ToolRagResultItem(**r) for r in rows],
        model=settings.embedding_model_name,
        dimensions=settings.embedding_dimensions,
    )
