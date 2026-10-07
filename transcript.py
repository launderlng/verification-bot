"""Builds a standalone HTML transcript of a ticket. No Discord imports, so it's easy to test."""
import html
from datetime import timezone

CSS = """
body{margin:0;background:#1e1f22;color:#dbdee1;font-family:'Segoe UI',Helvetica,Arial,sans-serif;font-size:15px}
.wrap{max-width:900px;margin:0 auto;padding:24px}
.head{background:#2b2d31;border-radius:10px;padding:18px 22px;margin-bottom:18px;border-left:5px solid #5865f2}
.head h1{margin:0 0 6px;font-size:20px;color:#fff}.head .sub{color:#b5bac1;font-size:13px;line-height:1.6}
.note{background:#473a1b;color:#f0d79a;border-radius:8px;padding:10px 14px;margin-bottom:16px;font-size:13px}
.msg{display:flex;gap:12px;padding:8px 6px;border-radius:6px}.msg:hover{background:#2b2d31}
.av{width:40px;height:40px;border-radius:50%;background:#5865f2;flex:none;object-fit:cover}
.body{min-width:0;flex:1}.name{font-weight:600;color:#fff}.ts{color:#949ba4;font-size:12px;margin-left:8px}
.tag{background:#5865f2;color:#fff;border-radius:4px;font-size:10px;padding:1px 5px;margin-left:6px;vertical-align:middle}
.edited{color:#949ba4;font-size:11px}.content{white-space:normal;word-wrap:break-word;overflow-wrap:anywhere;margin-top:2px}
.att{margin-top:6px;font-size:13px}.att a{color:#00a8fc;text-decoration:none}
.emb{margin-top:6px;border-left:4px solid #4e5058;background:#2b2d31;border-radius:4px;padding:8px 12px;font-size:14px}
.foot{text-align:center;color:#949ba4;font-size:12px;margin-top:22px}
"""


def _safe_url(url: str) -> str:
    return html.escape(url, quote=True) if url and url.startswith("https://") else "#"


def _text(value: str) -> str:
    return html.escape(value or "").replace("\r\n", "\n").replace("\n", "<br>")


def render_transcript(title: str, details: list[str], messages: list[dict], note: str = "") -> str:
    """messages: dicts with author_name, author_id, avatar_url, bot, timestamp (aware datetime), content,
    attachments [(name, url)], embeds [(title, description)], edited."""
    rows = []
    for m in messages:
        stamp = m["timestamp"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        attachments = "".join(
            f'<div class="att">📎 <a href="{_safe_url(url)}">{html.escape(name)}</a></div>' for name, url in m.get("attachments", [])
        )
        embeds = "".join(
            f'<div class="emb"><b>{_text(t)}</b>{"<br>" if t and d else ""}{_text(d)}</div>' for t, d in m.get("embeds", []) if t or d
        )
        avatar = f'<img class="av" src="{_safe_url(m.get("avatar_url", ""))}" alt="">'
        tag = '<span class="tag">BOT</span>' if m.get("bot") else ""
        edited = ' <span class="edited">(edited)</span>' if m.get("edited") else ""
        content = f'<div class="content">{_text(m.get("content", ""))}{edited}</div>' if m.get("content") else ""
        rows.append(
            f'<div class="msg">{avatar}<div class="body"><span class="name">{html.escape(m["author_name"])}</span>{tag}'
            f'<span class="ts">{stamp}</span>{content}{attachments}{embeds}</div></div>'
        )
    detail_html = "<br>".join(html.escape(d) for d in details)
    note_html = f'<div class="note">{html.escape(note)}</div>' if note else ""
    body = "\n".join(rows) if rows else '<div class="note">No messages were found in this ticket.</div>'
    return (
        f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><div class=\"wrap\">"
        f'<div class="head"><h1>{html.escape(title)}</h1><div class="sub">{detail_html}</div></div>{note_html}{body}'
        f'<div class="foot">{len(messages)} message(s)</div></div></body></html>'
    )
