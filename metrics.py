"""
metrics.py

Real-world performance tracking: the layer that checks whether the judge's
scores actually predict engagement.

The web UI records per-video platform stats (views, likes, comments,
completion rate) into metrics.json. From that this module produces:

  - analysis(): per-video rows joined with the judge's score, per-subreddit
    aggregates, and the correlation between judged score and each metric.
    This is the "evaluate the evaluator" view.
  - performance_memory(): a prompt block summarizing real results, appended
    to the judge's system prompt so future scoring is grounded in what
    actually performed, not just the rubric.

Tip: record numbers at a consistent video age (say 48 hours after posting)
or the comparison across videos is apples to oranges.
"""
import json
import math
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
METRICS_FILE = ROOT / "metrics.json"


def _load() -> dict:
    if METRICS_FILE.exists():
        return json.loads(METRICS_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict):
    METRICS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def record(video: str, views: int, likes: int, comments: int,
           completion_pct: float | None = None, platform: str = "tiktok",
           meta: dict | None = None):
    data = _load()
    entry = data.get(video, {})
    entry.update({
        "platform": platform,
        "views": views,
        "likes": likes,
        "comments": comments,
        "completion_pct": completion_pct,
        "updated_at": time.strftime("%Y-%m-%d %H:%M"),
    })
    if meta:
        curation = meta.get("curation", {})
        entry.update({
            "title": meta.get("title", ""),
            "subreddit": meta.get("subreddit", ""),
            "judged_score": curation.get("overall"),
            "category": curation.get("category", ""),
            "ending_rewritten": meta.get("ending_rewritten"),
        })
    data[video] = entry
    _save(data)


def get(video: str) -> dict | None:
    return _load().get(video)


def _rows() -> list[dict]:
    rows = []
    for video, e in _load().items():
        views = e.get("views") or 0
        engagement = round(100 * (e.get("likes", 0) + e.get("comments", 0)) / views, 2) if views else None
        rows.append({**e, "video": video, "engagement_pct": engagement})
    rows.sort(key=lambda r: -(r.get("views") or 0))
    return rows


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    vy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if vx == 0 or vy == 0:
        return None
    return round(cov / (vx * vy), 2)


def analysis() -> dict:
    rows = _rows()
    scored = [r for r in rows if r.get("judged_score") is not None]

    correlations = {}
    for metric in ("views", "engagement_pct", "completion_pct"):
        pairs = [(r["judged_score"], r[metric]) for r in scored if r.get(metric) is not None]
        r_val = _pearson([p[0] for p in pairs], [p[1] for p in pairs])
        correlations[metric] = {"r": r_val, "n": len(pairs)}

    by_sub: dict[str, dict] = {}
    for r in rows:
        sub = r.get("subreddit") or "?"
        agg = by_sub.setdefault(sub, {"videos": 0, "views": 0, "likes": 0, "comments": 0})
        agg["videos"] += 1
        agg["views"] += r.get("views") or 0
        agg["likes"] += r.get("likes") or 0
        agg["comments"] += r.get("comments") or 0
    for sub, agg in by_sub.items():
        agg["avg_views"] = round(agg["views"] / agg["videos"])
        eng = agg["likes"] + agg["comments"]
        agg["engagement_pct"] = round(100 * eng / agg["views"], 2) if agg["views"] else None

    return {"rows": rows, "correlations": correlations, "by_subreddit": by_sub}


def performance_memory(max_examples: int = 3) -> str:
    """Prompt block grounding the judge in real platform results."""
    rows = [r for r in _rows() if r.get("title") and (r.get("views") or 0) > 0]
    if not rows:
        return ""

    a = analysis()
    track = ", ".join(
        f"r/{sub} avg {agg['avg_views']} views, {agg['engagement_pct']}% engagement ({agg['videos']} posted)"
        for sub, agg in sorted(a["by_subreddit"].items(), key=lambda kv: -kv[1]["avg_views"])
        if sub != "?"
    )

    def fmt(r):
        line = (f"- \"{r['title']}\" (r/{r.get('subreddit', '?')}"
                f", judge scored {r.get('judged_score', '?')}): "
                f"{r['views']} views, {r.get('engagement_pct') or 0}% engagement")
        if r.get("completion_pct") is not None:
            line += f", {r['completion_pct']}% completion"
        return line

    top = rows[:max_examples]
    bottom = [r for r in rows[max_examples:]][-max_examples:]

    parts = [
        "REAL PLATFORM RESULTS (actual metrics from posted videos; this is "
        "harder evidence than the rubric or the owner's ratings -- when they "
        "conflict, trust what performed):",
        f"Track record: {track}" if track else "",
        "Best performers:\n" + "\n".join(fmt(r) for r in top),
    ]
    if bottom:
        parts.append("Weakest performers:\n" + "\n".join(fmt(r) for r in bottom))
    parts.append(
        "Favor candidates that resemble the best performers; be skeptical of "
        "patterns that looked good on the rubric but performed poorly."
    )
    return "\n\n".join(p for p in parts if p)
