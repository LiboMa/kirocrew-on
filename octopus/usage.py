"""What a Session's Attempts used (D46 P6a): Kiro credits, tokens, cost and wall time, as each Attempt's evidence
recorded them (octopus/worker.py ``_usage``), totalled for the Session and said in one line."""

SUMMED = ("credits", "input_tokens", "output_tokens", "cost_usd", "seconds")


def total(evidences):
    """The Session's total over its Attempts' evidence; None when none of them recorded usage (a Worker before P6a)."""
    found = [e["usage"] for e in evidences if isinstance(e, dict) and isinstance(e.get("usage"), dict)]
    if not found:
        return None
    result = {key: 0 for key in SUMMED}
    for item in found:
        for key in SUMMED:
            value = item.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] += value
    result = {key: round(value, 6) if isinstance(value, float) else value for key, value in result.items()}
    result.update(attempts=len(found), tokens_unavailable=sum(1 for item in found if item.get("tokens") == "unavailable"))
    return result


def by_person(db, run, rows):
    """D74: the Session's usage per person it ran as, when more than one did — an Attempt counts for whoever was its
    executor when it was created, as the executor_id changes the journal recorded (a takeover, an assignment) say."""
    import json
    changes = []
    for event in db.execute("SELECT payload, created FROM events WHERE run_id=? AND payload LIKE '%\"executor_id\"%' "
                            "ORDER BY id", (run["id"],)):
        moved = (json.loads(event["payload"]).get("changes") or {}).get("executor_id")
        if isinstance(moved, list) and len(moved) == 2:
            changes.append((event["created"], moved[1] or run["owner_id"]))
    if not changes:
        return None
    people = {}
    for row in rows:
        person = next((who for at, who in reversed(changes) if at <= row["created"]), run["owner_id"])
        people.setdefault(person, []).append(json.loads(row["evidence"] or "{}"))
    if len(people) < 2:
        return None
    names = dict(db.execute(f"SELECT id, username FROM users WHERE id IN ({','.join('?' * len(people))})", tuple(people)).fetchall())
    split = [{"id": person, "username": names.get(person), **(total(found) or {})} for person, found in people.items()]
    return [item for item in split if item.get("attempts")] or None


def _count(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if n >= 1000:
        return f"{n / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    return str(n)


def _duration(seconds):
    seconds = int(seconds + 0.5)   # half up, as the console rounds
    return f"{seconds // 60} 分 {seconds % 60} 秒" if seconds >= 60 else f"{seconds} 秒"


def line(item):
    """One line a person reads: credits, tokens, cost and time, whichever were recorded."""
    item, parts = item or {}, []
    if item.get("credits"):
        parts.append(f"{item['credits']:g} credits")
    if item.get("input_tokens") or item.get("output_tokens"):
        parts.append(f"输入 {_count(item.get('input_tokens') or 0)} / 输出 {_count(item.get('output_tokens') or 0)} token")
    if item.get("cost_usd"):
        parts.append(f"${item['cost_usd']:.2f}")
    if isinstance(item.get("seconds"), (int, float)):
        parts.append(_duration(item["seconds"]))
    return " · ".join(parts) or "—"
