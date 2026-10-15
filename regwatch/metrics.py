"""Impact and quality metrics from live usage.

* **Alert precision (online)**: of the items users rated, the share rated
  relevant or "saved me". Recall can't be measured online, because users
  don't know what we missed. It is measured offline in evals/.
* **Open rate / click-through**: per digest sent, and per item delivered.
* **"This saved me"**: count plus the notes users left.
* **Summary quality**: faithfulness distribution, LLM vs extractive mix,
  and the "summary was wrong" report rate.
"""
from __future__ import annotations


def compute(conn) -> dict:
    one = lambda q, *a: conn.execute(q, a).fetchone()[0] or 0  # noqa: E731
    sent = one("SELECT COUNT(*) FROM digests WHERE sent_at IS NOT NULL")
    opened = one("SELECT COUNT(DISTINCT digest_id) FROM events WHERE kind='open'")
    items = one("SELECT COUNT(*) FROM digest_items di JOIN digests d ON d.id=di.digest_id WHERE d.sent_at IS NOT NULL")
    clicked = one("SELECT COUNT(DISTINCT digest_id || ':' || change_id) FROM events WHERE kind='click'")
    pos = one("SELECT COUNT(DISTINCT user_id || ':' || change_id) FROM events WHERE kind IN ('relevant','saved_me')")
    neg = one("SELECT COUNT(DISTINCT user_id || ':' || change_id) FROM events WHERE kind='not_relevant'")
    saved = one("SELECT COUNT(*) FROM events WHERE kind='saved_me'")
    wrong = one("SELECT COUNT(*) FROM events WHERE kind='wrong_summary'")
    summarised = one("SELECT COUNT(*) FROM changes WHERE summary IS NOT NULL")
    by_method = {r[0]: r[1] for r in conn.execute(
        "SELECT summary_method, COUNT(*) FROM changes WHERE summary IS NOT NULL GROUP BY summary_method")}
    faith = conn.execute("SELECT AVG(faithfulness), MIN(faithfulness) FROM changes WHERE summary IS NOT NULL").fetchone()
    by_band = {r[0]: r[1] for r in conn.execute("SELECT band, COUNT(*) FROM digest_items GROUP BY band")}
    prec_by_band = {}
    for band in ("likely", "possible", "check"):
        p = one("SELECT COUNT(*) FROM events e JOIN digest_items di ON di.digest_id=e.digest_id AND di.change_id=e.change_id "
                "WHERE di.band=? AND e.kind IN ('relevant','saved_me')", band)
        n = one("SELECT COUNT(*) FROM events e JOIN digest_items di ON di.digest_id=e.digest_id AND di.change_id=e.change_id "
                "WHERE di.band=? AND e.kind='not_relevant'", band)
        prec_by_band[band] = round(p / (p + n), 3) if p + n else None
    stories = [dict(r) for r in conn.execute(
        "SELECT e.at, e.note, d.title FROM events e JOIN changes c ON c.id=e.change_id JOIN documents d ON d.id=c.document_id "
        "WHERE e.kind='saved_me' ORDER BY e.at DESC LIMIT 10")]
    return {
        "documents": one("SELECT COUNT(*) FROM documents"),
        "versions": one("SELECT COUNT(*) FROM versions"),
        "changes": one("SELECT COUNT(*) FROM changes"),
        "material_changes": one("SELECT COUNT(*) FROM changes WHERE material=1"),
        "users": one("SELECT COUNT(*) FROM users WHERE parent_id IS NULL"),
        "client_profiles": one("SELECT COUNT(*) FROM users WHERE parent_id IS NOT NULL"),
        "digests_sent": sent,
        "open_rate": round(opened / sent, 3) if sent else None,
        "items_delivered": items,
        "click_through_rate": round(clicked / items, 3) if items else None,
        "rated_items": pos + neg,
        "online_precision": round(pos / (pos + neg), 3) if pos + neg else None,
        "precision_by_band": prec_by_band,
        "items_by_band": by_band,
        "saved_me": saved,
        "saved_me_stories": stories,
        "wrong_summary_reports": wrong,
        "wrong_summary_rate": round(wrong / items, 4) if items else None,
        "summaries": summarised,
        "summary_methods": by_method,
        "faithfulness_mean": round(faith[0], 3) if faith[0] is not None else None,
        "faithfulness_min": faith[1],
    }
