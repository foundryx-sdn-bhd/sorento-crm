"""Paired bootstrap comparison of two eval arms with repetitions (pure stdlib)."""

from __future__ import annotations

import random

MIN_REPS = 3
MIN_ITEMS = 10


def _wmean(values: list[float], weights: list[float]) -> float:
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total if total else 0.0


def _percentile(sorted_vals: list[float], q: float) -> float:
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _ci(samples: list[float]) -> tuple[float, float]:
    s = sorted(samples)
    return (_percentile(s, 0.025), _percentile(s, 0.975))


def paired_compare(
    base: dict[str, list[bool]],
    head: dict[str, list[bool]],
    *,
    n_boot: int = 10000,
    seed: int = 0,
    min_reps: int = MIN_REPS,
    min_items: int = MIN_ITEMS,
    weights: dict[str, float] | None = None,
) -> dict:
    """Items present in both arms are paired; each item's reps are averaged, then items are resampled."""
    ids = sorted(set(base) & set(head))
    excluded = sorted(set(base) ^ set(head))
    reps = min([len(base[i]) for i in ids] + [len(head[i]) for i in ids], default=0)
    out: dict = {"n_items": len(ids), "excluded": excluded, "reps": reps}
    if reps < min_reps:
        out.update(verdict="refused", reason=f"{reps} reps < {min_reps}")
        return out
    if len(ids) < min_items:
        out.update(verdict="refused", reason=f"{len(ids)} items < {min_items}")
        return out
    b = [sum(base[i]) / len(base[i]) for i in ids]
    h = [sum(head[i]) / len(head[i]) for i in ids]
    w = [float((weights or {}).get(i, 1.0)) for i in ids]
    out["base_mean"], out["head_mean"] = _wmean(b, w), _wmean(h, w)
    out["delta"] = out["head_mean"] - out["base_mean"]
    rng = random.Random(seed)
    n = len(ids)
    bs: list[float] = []
    hs: list[float] = []
    ds: list[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        rw = [w[i] for i in idx]
        bm = _wmean([b[i] for i in idx], rw)
        hm = _wmean([h[i] for i in idx], rw)
        bs.append(bm)
        hs.append(hm)
        ds.append(hm - bm)
    out["base_ci"], out["head_ci"], out["delta_ci"] = _ci(bs), _ci(hs), _ci(ds)
    lo, hi = out["delta_ci"]
    out["verdict"] = "better" if lo > 0 else "worse" if hi < 0 else "within noise"
    return out


def format_compare(r: dict) -> str:
    lines = [f"items {r['n_items']}, reps {r['reps']}, excluded {len(r['excluded'])}"]
    if r["excluded"]:
        lines[0] += f" ({', '.join(r['excluded'][:10])})"
    if r["verdict"] == "refused":
        lines.append(f"verdict refused: {r['reason']}")
        return "\n".join(lines)

    def pct(x: float) -> str:
        return f"{x * 100:.1f}%"

    for name in ("base", "head"):
        lo, hi = r[f"{name}_ci"]
        lines.append(f"{name}: {pct(r[name + '_mean'])} [{pct(lo)}, {pct(hi)}]")
    lo, hi = r["delta_ci"]
    lines.append(f"delta: {r['delta'] * 100:+.1f} pts [{lo * 100:+.1f}, {hi * 100:+.1f}]")
    lines.append(f"verdict {r['verdict']}")
    return "\n".join(lines)
