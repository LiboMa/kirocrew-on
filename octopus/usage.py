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
