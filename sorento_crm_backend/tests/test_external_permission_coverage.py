"""Every /external route enforces a permission - checked structurally.

A per-endpoint test can only cover the endpoints someone remembered to write a
test for. The failure mode that matters here is *omission*: a new router mounted
without a guard, reachable by any key holder, with nothing failing to say so.

These tests inspect the mounted application itself, so a route added tomorrow is
covered by construction.
"""
import pytest
from fastapi import FastAPI

from app.api.v1.external import router as external_router
from app.api.v1.external.permissions import EXTERNAL_ENDPOINT_PERMISSIONS
from app.rbac.permission_registry import PERMISSION_REGISTRY

# Prefixes whose permission is resolved from the path at request time,
# because one route serves several entities with different slugs. Kept as an
# explicit allowlist so adding one is a deliberate act, not an oversight.
PATH_RESOLVED_PREFIXES = {"ingest", "read"}

# Mount the external router onto a throwaway app to get the SAME flattened
# APIRoute objects app.main would, without depending on app.main being fully
# initialised. Two failure modes this dodges, both seen on CI:
#   * app.main.app.routes intermittently carried only its ~6 default routes --
#     an earlier test leaves app.main half-initialised so its bottom-of-module
#     include_router() had not run on the object the test saw (same note in
#     test_form_void_action::test_no_unvoid_route);
#   * external_router.routes on a newer FastAPI holds lazy _IncludedRouter
#     wrappers (no .path) rather than flattened APIRoutes -- include_router()
#     onto a real app forces the flatten deterministically across versions.
_PROBE_APP = FastAPI()
_PROBE_APP.include_router(external_router, prefix="/api/v1/external")


def _external_routes():
    return [
        r
        for r in _PROBE_APP.routes
        if getattr(r, "path", "").startswith("/api/v1/external/")
    ]


class TestCoverage:
    def test_there_are_external_routes_to_check(self):
        # Guards the tests below against silently passing on an empty set.
        assert len(_external_routes()) > 20

    def test_every_external_route_carries_a_permission_dependency(self):
        unguarded = []
        for route in _external_routes():
            deps = getattr(route, "dependencies", []) or []
            names = [getattr(d.dependency, "__qualname__", "") for d in deps]
            if not any("require_external_permission" in n for n in names):
                unguarded.append(route.path)
        assert not unguarded, f"external routes without a permission guard: {unguarded}"

    def test_every_mounted_prefix_has_a_mapping_entry(self):
        # The guard is applied by looking the prefix up in the map, so a missing
        # entry would be a KeyError at import -- but assert it explicitly so the
        # failure names the gap instead of a traceback.
        mounted = set()
        for route in _external_routes():
            tail = route.path[len("/api/v1/external/") :]
            if tail:
                mounted.add(tail.split("/")[0])

        mapped = {k.split("/")[0] for k in EXTERNAL_ENDPOINT_PERMISSIONS}
        # These serve several entities from one route, so their slug is resolved
        # from the path at request time rather than fixed at mount. Listed
        # explicitly, and their maps are asserted below -- an unguarded router
        # cannot hide here just by being named 'ingest'.
        assert mounted <= mapped | PATH_RESOLVED_PREFIXES, (
            f"mounted but unmapped: {sorted(mounted - mapped - PATH_RESOLVED_PREFIXES)}"
        )

    def test_path_resolved_routes_cover_every_entity_they_serve(self):
        # The escape hatch above is only safe if these maps are complete: an
        # entity present in ingest but missing from the permission map would be
        # refused at runtime, which is safe, but silently unreachable.
        from app.api.v1.external.ingest import (
            DELETE_PERMISSIONS,
            INGEST_PERMISSIONS,
            READ_PERMISSIONS,
        )
        from app.services.autocount_cancel_ingest_service import AUTOCOUNT_CANCEL_ENTITIES
        from app.services.autocount_doc_ingest_service import (
            AUTOCOUNT_BRANCH_ENTITIES,
            AUTOCOUNT_DOC_ENTITIES,
        )
        from app.services.document_ingest_service import DOCUMENT_ENTITIES
        from app.services.finance.ar_outstanding_ingest_service import AR_OUTSTANDING_ENTITIES
        from app.services.finance.billing_document_ingest_service import (
            BILLING_DOCUMENT_ENTITIES,
        )
        from app.services.master_ingest_service import ENTITY_SPECS
        from app.services.shipping_order_ingest_service import SHIPPING_ORDER_ENTITIES
        from app.services.stock_balance_ingest_service import STOCK_BALANCE_ENTITIES

        # Masters, documents, shipping orders AND stock balances: one route
        # serves all four since group A3/S3/SR5, so a map that covers only
        # some of the registries leaves the rest reachable-but-refused, which
        # reads as a broken feature rather than a protected one. Billing documents
        # (finance S0, contract 2.6) are the fifth registry on the same route.
        served = (
            set(ENTITY_SPECS)
            | set(DOCUMENT_ENTITIES)
            | set(SHIPPING_ORDER_ENTITIES)
            | set(STOCK_BALANCE_ENTITIES)
            | set(BILLING_DOCUMENT_ENTITIES)
            # Contract 2.7 (#1354 S2): AutoCount DOs, GRNs and branches, same route.
            | set(AUTOCOUNT_DOC_ENTITIES)
            | set(AUTOCOUNT_BRANCH_ENTITIES)
            # Contract 2.8 (AC-CANCEL-CRM): Cancel SO / Cancel PO, consignments stored only.
            | set(AUTOCOUNT_CANCEL_ENTITIES)
        )
        # Contract 2.10 (AC-BILLING): `ar_outstanding`, pushed and read with the billing slugs.
        with_outstanding = served | set(AR_OUTSTANDING_ENTITIES)
        assert set(INGEST_PERMISSIONS) == with_outstanding
        assert set(READ_PERMISSIONS) == with_outstanding
        # `/{entity}/deletions` (group A4) resolves its second guard from the same
        # path, so its map has to cover the same set, except `ar_outstanding`, which has no
        # deletions door (the path guard answers it 404).
        assert set(DELETE_PERMISSIONS) == served

    def test_ingest_read_and_delete_use_different_slugs_per_entity(self):
        # Writing a warehouse must not be authorised by the products slug.
        from app.api.v1.external.ingest import (
            DELETE_PERMISSIONS,
            INGEST_PERMISSIONS,
            READ_PERMISSIONS,
        )

        # `ar_outstanding` shares the billing documents' slugs (card Q2: no slug of its own).
        shared = {"ar_outstanding"}
        assert len({v for k, v in INGEST_PERMISSIONS.items() if k not in shared}) == len(
            INGEST_PERMISSIONS
        ) - len(shared)
        assert len({v for k, v in READ_PERMISSIONS.items() if k not in shared}) == len(
            READ_PERMISSIONS
        ) - len(shared)
        assert len(set(DELETE_PERMISSIONS.values())) == len(DELETE_PERMISSIONS)
        # ...and reading must not require the write permission, nor removing a
        # record be authorised by the permission to sync one.
        for entity, write_slug in INGEST_PERMISSIONS.items():
            assert READ_PERMISSIONS[entity] != write_slug
            if entity in DELETE_PERMISSIONS:
                assert DELETE_PERMISSIONS[entity] != write_slug


class TestSlugsAreReal:
    def test_every_mapped_slug_exists_in_the_registry(self):
        # A guard referencing a slug that no role can ever hold is a permanent
        # 403 -- the feature would look broken rather than protected.
        known = {p["slug"] for p in PERMISSION_REGISTRY}
        unknown = {v for v in EXTERNAL_ENDPOINT_PERMISSIONS.values() if v not in known}
        assert not unknown, f"mapped to non-existent permission slugs: {sorted(unknown)}"

    def test_no_route_is_mapped_to_an_empty_slug(self):
        assert all(v and v.strip() for v in EXTERNAL_ENDPOINT_PERMISSIONS.values())
