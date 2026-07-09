"""
feedback.py

Owner-rating memory for the curation judge.

The web UI (web/server.py) records 1-5 ratings for finished videos into
ratings.json. Every future curation run builds a "taste profile" from those
ratings and appends it to the judge's system prompt, so the judge learns the
channel owner's preferences from concrete examples. This is prompt-injected
preference memory, not model fine-tuning: it persists because it is a file
on disk, and it applies to CLI runs and UI runs alike.
"""
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RATINGS_FILE = ROOT / "ratings.json"


def _load() -> dict:
    if RATINGS_FILE.exists():
        return json.loads(RATINGS_FILE.read_text(encoding="utf-8"))
    return {}


def _save(data: dict):
    RATINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def record_rating(video: str, rating: int, note: str = "", meta: dict | None = None):
    """Store (or update) the owner's rating for a finished video. `meta` is
    the video's curation metadata snapshot so the taste profile can describe
    the story even after run_output is overwritten."""
    data = _load()
    entry = data.get(video, {})
    entry.update({
        "rating": rating,
        "note": note.strip(),
        "rated_at": time.strftime("%Y-%m-%d %H:%M"),
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


def get_rating(video: str) -> dict | None:
    return _load().get(video)


def all_ratings() -> dict:
    return _load()


def taste_memory(max_each: int = 4) -> str:
    """Build the taste-profile block appended to the judge's system prompt.
    Returns "" when there are no ratings yet."""
    data = [e for e in _load().values() if e.get("rating") and e.get("title")]
    if not data:
        return ""

    by_sub: dict[str, list[int]] = {}
    for e in data:
        by_sub.setdefault(e.get("subreddit", "?"), []).append(e["rating"])
    track = ", ".join(
        f"r/{sub} avg {sum(r) / len(r):.1f}/5 ({len(r)} rated)"
        for sub, r in sorted(by_sub.items(), key=lambda kv: -len(kv[1]))
    )

    def fmt(e):
        line = f"- [{e['rating']}/5] \"{e['title']}\" (r/{e.get('subreddit', '?')}"
        if e.get("judged_score") is not None:
            line += f", judge scored {e['judged_score']}"
        if e.get("category"):
            line += f", {e['category']}"
        line += ")"
        if e.get("note"):
            line += f" -- owner note: {e['note']}"
        return line

    data.sort(key=lambda e: e.get("rated_at", ""), reverse=True)
    loved = [e for e in data if e["rating"] >= 4][:max_each]
    disliked = [e for e in data if e["rating"] <= 2][:max_each]

    parts = [
        "CHANNEL OWNER TASTE PROFILE (the owner rated previously produced "
        "videos; weight this heavily -- it is ground truth about what this "
        "channel wants):",
        f"Track record by subreddit: {track}",
    ]
    if loved:
        parts.append("Videos the owner rated highly:\n" + "\n".join(fmt(e) for e in loved))
    if disliked:
        parts.append("Videos the owner rated poorly:\n" + "\n".join(fmt(e) for e in disliked))
    parts.append(
        "Prefer candidates that resemble the highly rated examples in energy "
        "and shape; penalize the patterns the owner rated poorly, even when "
        "they otherwise score well on the rubric."
    )
    return "\n\n".join(parts)
