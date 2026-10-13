"""PV2 alias seeds (contract section 4), applied by `alembic/versions/pv2_0002_alias_seed.py`.

Seeds only ADD: a code is filled only where the row has none and no other row holds it, an
alias only where its type does not already list it, a precedence row only where none
exists. Re-running is a no-op. Kept out of the migration so a test can run the same SQL.
"""
from __future__ import annotations

import json

from sqlalchemy import text

from app.services.chatbot.pv2.aliases import norm

#: code -> the type names (casefolded) a row of that type is stored under. First match wins.
#: A code is filled only on a row found by one of these names; no row, no code.
TYPE_CODES: dict[str, tuple[str, ...]] = {
    "certification": ("certification", "certifications", "certificate", "certificates"),
    "product_photos": ("product photos", "product photo", "product images", "product image"),
    "technical_specifications": (
        "technical specifications", "technical specification", "technical specs", "technical spec",
        "technical drawing", "technical drawings",
    ),
    "product_videos": ("product videos", "product video"),
    "installation_guide": ("installation guide", "installation guides", "installation manual"),
    "catalogue": ("catalogue", "catalog", "product catalogue", "product catalog"),
}

#: code -> aliases: the words recorded chat turns use for each type (tests/chatbot/replay_turns,
#: console_cases: photo, image, drawing, technical drawing, certificate, Ikram certificate,
#: catalogue) plus the common en / zh / ms words. Owner 13 Oct: seed them; "gambar" ->
#: Product Photos. No word is listed under two codes (a test holds this).
TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "technical_specifications": (
        "TD", "CAD", "drawing", "technical drawing", "tech drawing", "dimension drawing", "spec sheet",
        "datasheet", "specification", "图纸", "技术图纸", "尺寸图", "规格书", "lukisan", "lukisan teknikal",
        "spesifikasi",
    ),
    "product_photos": (
        "photo", "photos", "picture", "pic", "image", "product image", "gambar", "foto", "gambar produk",
        "图片", "照片", "相片", "产品图",
    ),
    "certification": (
        "cert", "certificate", "certification", "ikram", "sirim", "证书", "认证", "sijil", "sijil pengesahan",
    ),
    "installation_guide": (
        "manual", "installation manual", "install guide", "user manual", "安装", "安装说明", "安装指南", "说明书",
        "panduan pemasangan", "manual pemasangan",
    ),
    "product_videos": ("video", "videos", "product video", "视频", "影片", "video produk"),
    "catalogue": ("catalog", "katalog", "brochure", "目录", "产品目录"),
}

#: (kind, alias, winner_key) for the words the shipped spec registry seed already lists under
#: two keys (measured on SPEC_REGISTRY_SEED, 13 Oct). Crew call, owner can change the rows.
PRECEDENCE: tuple[tuple[str, str, str], ...] = (
    ("spec", "mixer", "product_type.mixer_tap"),
    ("spec", "mixer tap", "product_type.mixer_tap"),
    ("spec", "handrail", "product_type.handrail"),
    ("spec", "hose", "product_type.flexible_hose"),
    ("spec", "floor waste", "product_type.floor_trap"),
)


def apply(bind) -> dict[str, int]:
    """Run every seed on `bind` (a Connection or Session). Returns counts per step."""
    counts = {"codes": 0, "aliases": 0, "precedence": 0}

    for code, names in TYPE_CODES.items():
        taken = bind.execute(text("SELECT 1 FROM attachment_types WHERE code = :c"), {"c": code}).first()
        if taken:
            continue
        for name in names:
            row = bind.execute(
                text(
                    "SELECT id FROM attachment_types WHERE code IS NULL "
                    "AND lower(trim(type_name)) = :n ORDER BY created_at NULLS LAST, id LIMIT 1"
                ),
                {"n": name},
            ).first()
            if row:
                bind.execute(text("UPDATE attachment_types SET code = :c WHERE id = :id"), {"c": code, "id": row[0]})
                counts["codes"] += 1
                break

    for code, aliases in TYPE_ALIASES.items():
        row = bind.execute(
            text("SELECT id, COALESCE(search_synonyms, '[]'::jsonb) FROM attachment_types WHERE code = :c"),
            {"c": code},
        ).first()
        if not row:
            continue
        type_id, current = row
        have = {norm(w) for w in (current or [])}
        added = [a for a in aliases if norm(a) not in have]
        if not added:
            continue
        bind.execute(
            text(
                "UPDATE attachment_types SET search_synonyms = "
                "COALESCE(search_synonyms, '[]'::jsonb) || CAST(:add AS jsonb) WHERE id = :id"
            ),
            {"add": json.dumps(added, ensure_ascii=False), "id": type_id},
        )
        counts["aliases"] += len(added)

    for kind, alias, winner in PRECEDENCE:
        exists = bind.execute(
            text("SELECT 1 FROM chatbot_alias_precedence WHERE kind = :k AND alias_norm = :a"),
            {"k": kind, "a": norm(alias)},
        ).first()
        if exists:
            continue
        bind.execute(
            text("INSERT INTO chatbot_alias_precedence (kind, alias_norm, winner_key) VALUES (:k, :a, :w)"),
            {"k": kind, "a": norm(alias), "w": winner},
        )
        counts["precedence"] += 1

    return counts
