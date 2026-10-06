"""Helpers for reading Discord server templates. No Discord imports, so they're easy to test."""
import re
from typing import Optional

TEMPLATE_RE = re.compile(r"(?:discord\.new/|discord(?:app)?\.com/template/)([A-Za-z0-9]+)", re.I)
INVITE_RE = re.compile(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)([A-Za-z0-9-]+)", re.I)
BARE_CODE_RE = re.compile(r"[A-Za-z0-9]{8,20}")

# Discord channel types -> what we can build in an existing server
KIND = {0: "text", 5: "text", 15: "text", 16: "text", 2: "voice", 13: "voice", 4: "category"}
CONVERTED_TYPES = {5, 13, 15, 16}  # announcement, stage, forum, media need Community features, so they become text/voice
ADMIN_BIT = 1 << 3


def extract_template_code(text: Optional[str]) -> Optional[str]:
    text = (text or "").strip()
    match = TEMPLATE_RE.search(text)
    if match:
        return match.group(1)
    if INVITE_RE.search(text):
        return None
    return text if BARE_CODE_RE.fullmatch(text) else None


def is_invite(text: Optional[str]) -> bool:
    return bool(INVITE_RE.search(text or "")) and not TEMPLATE_RE.search(text or "")


def _is_role_overwrite(entry: dict) -> bool:
    return entry.get("type") in (0, "0", "role")


def parse_template(data: dict) -> dict:
    """Turn Discord's raw template JSON into a simple structure we can build from."""
    source = data.get("serialized_source_guild") or {}
    everyone_id, roles = None, []
    for r in source.get("roles") or []:
        if r.get("name") == "@everyone":
            everyone_id = r.get("id")
            continue
        roles.append({
            "id": r["id"], "name": (r.get("name") or "role")[:100], "color": int(r.get("color") or 0), "hoist": bool(r.get("hoist")),
            "mentionable": bool(r.get("mentionable")), "permissions": int(r.get("permissions") or 0), "position": int(r.get("position") or 0),
        })
    roles.sort(key=lambda r: r["position"], reverse=True)  # build the highest role first so the order is kept

    categories, channels, skipped, converted = [], [], 0, 0
    for c in source.get("channels") or []:
        kind = KIND.get(c.get("type"))
        if kind is None:
            skipped += 1
            continue
        item = {
            "id": c["id"], "name": (c.get("name") or "channel")[:100], "kind": kind, "original_type": c.get("type"),
            "parent_id": c.get("parent_id"), "position": int(c.get("position") or 0), "topic": c.get("topic") or None,
            "nsfw": bool(c.get("nsfw")), "bitrate": c.get("bitrate"), "user_limit": int(c.get("user_limit") or 0),
            "slowmode": int(c.get("rate_limit_per_user") or 0),
            "overwrites": [(o["id"], int(o.get("allow") or 0), int(o.get("deny") or 0)) for o in (c.get("permission_overwrites") or []) if _is_role_overwrite(o)],
        }
        if kind == "category":
            categories.append(item)
        else:
            if c.get("type") in CONVERTED_TYPES:
                converted += 1
            channels.append(item)
    categories.sort(key=lambda c: c["position"])
    channels.sort(key=lambda c: c["position"])
    return {
        "name": data.get("name") or "Unnamed template", "description": data.get("description"), "usage_count": data.get("usage_count") or 0,
        "source_name": source.get("name"), "everyone_id": everyone_id, "roles": roles, "categories": categories, "channels": channels,
        "skipped": skipped, "converted": converted,
    }


def tree_text(parsed: dict, limit: int = 3500) -> str:
    """A folder-style outline of the template's channels."""
    category_ids = {c["id"] for c in parsed["categories"]}
    icon = lambda ch: "🔊" if ch["kind"] == "voice" else "#"
    lines = [f"{icon(c)} {c['name']}" for c in parsed["channels"] if c["parent_id"] not in category_ids]
    for cat in parsed["categories"]:
        lines.append(f"📁 **{cat['name']}**")
        lines.extend(f"　{icon(c)} {c['name']}" for c in parsed["channels"] if c["parent_id"] == cat["id"])
    text, used = [], 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > limit:
            text.append(f"…and {len(lines) - i} more")
            break
        text.append(line)
        used += len(line) + 1
    return "\n".join(text) or "(no channels)"


def safe_permissions(requested: int, bot_permissions: int) -> int:
    """Never hand out more than the bot itself has, and never Administrator."""
    return requested & bot_permissions & ~ADMIN_BIT
