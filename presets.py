"""Ready-made server layouts. Pure data with no Discord imports, so it's easy to test."""

BITS = {
    "create_invite": 1 << 0, "kick": 1 << 1, "ban": 1 << 2, "manage_channels": 1 << 4, "manage_guild": 1 << 5, "add_reactions": 1 << 6,
    "view_audit_log": 1 << 7, "stream": 1 << 9, "view_channel": 1 << 10, "send_messages": 1 << 11, "manage_messages": 1 << 13,
    "embed_links": 1 << 14, "attach_files": 1 << 15, "read_message_history": 1 << 16, "mention_everyone": 1 << 17,
    "external_emojis": 1 << 18, "connect": 1 << 20, "speak": 1 << 21, "use_voice_activation": 1 << 25, "change_nickname": 1 << 26,
    "manage_roles": 1 << 28, "manage_webhooks": 1 << 29, "use_application_commands": 1 << 31, "manage_threads": 1 << 34,
    "create_public_threads": 1 << 35, "send_messages_in_threads": 1 << 38, "moderate_members": 1 << 40,
}


def P(*names: str) -> int:
    return sum(BITS[n] for n in names)


VIEW, SEND = P("view_channel"), P("send_messages")
READ = P("view_channel", "read_message_history", "add_reactions")
WRITE = P("view_channel", "send_messages", "read_message_history", "embed_links", "attach_files", "add_reactions", "external_emojis")
VOICE_USE = P("view_channel", "connect", "speak", "use_voice_activation", "stream")
BASE = P("view_channel", "send_messages", "embed_links", "attach_files", "add_reactions", "read_message_history", "external_emojis", "connect",
         "speak", "use_voice_activation", "change_nickname", "use_application_commands", "create_public_threads", "send_messages_in_threads")

R = dict(owner=1, co_owner=2, admin=3, head=4, mod=5, support=6, bots=7, premium=8, verified=9, member=10, unverified=11)
EVERYONE = 0
PUBLIC = [R["premium"], R["verified"], R["member"]]
STAFF = [R["owner"], R["co_owner"], R["admin"], R["head"], R["mod"], R["support"]]
TOP_STAFF = [R["owner"], R["co_owner"], R["admin"]]
ADMINS = TOP_STAFF + [R["head"]]


def overwrites(*entries) -> list:
    """Each entry is (role_id or [role_ids], allow, deny). Later entries replace earlier ones for the same role."""
    merged = {}
    for roles, allow, deny in entries:
        for role in ([roles] if isinstance(roles, int) else roles):
            merged[role] = (allow, deny)
    return [(role, allow, deny) for role, (allow, deny) in merged.items()]


def readonly(writers=(), also_see=(), bots_write=False) -> list:
    entries = [(EVERYONE, 0, VIEW), (PUBLIC + list(also_see), READ, SEND), (STAFF, READ, SEND)]
    if writers:
        entries.append((list(writers), WRITE, 0))
    if bots_write:
        entries.append((R["bots"], WRITE, 0))
    return overwrites(*entries)


def chat() -> list:
    return overwrites((EVERYONE, 0, VIEW), (PUBLIC + STAFF, WRITE, 0), (R["bots"], WRITE, 0))


def staff_chat() -> list:
    return overwrites((EVERYONE, 0, VIEW), (STAFF, WRITE, 0), (R["bots"], WRITE, 0))


def staff_log() -> list:
    return overwrites((EVERYONE, 0, VIEW), (STAFF, READ, SEND), (TOP_STAFF, WRITE, 0), (R["bots"], WRITE, 0))


def voice() -> list:
    return overwrites((EVERYONE, 0, VIEW | P("connect")), (PUBLIC + STAFF, VOICE_USE, 0))


def staff_voice() -> list:
    return overwrites((EVERYONE, 0, VIEW | P("connect")), (STAFF, VOICE_USE, 0))


def public_category() -> list:
    return overwrites((EVERYONE, 0, VIEW), (PUBLIC + STAFF, VIEW, 0))


def staff_category() -> list:
    return overwrites((EVERYONE, 0, VIEW), (STAFF, VIEW, 0))


RULES_TEXT = (
    "**1️⃣ RESPECT EVERYONE**\nTreat everyone with respect. No harassment, bullying or targeted abuse.\n\n"
    "**2️⃣ NO SPAM**\nNo message, mention, reaction or command spam.\n\n"
    "**3️⃣ NO ADVERTISING**\nNo advertising other servers, services or products without permission.\n\n"
    "**4️⃣ NO NSFW**\nKeep all content appropriate.\n\n"
    "**5️⃣ NO SCAMS**\nScamming, phishing, token theft and malicious links are prohibited.\n\n"
    "**6️⃣ FOLLOW DISCORD RULES**\nFollow Discord's Terms of Service and Community Guidelines.\n\n"
    "**7️⃣ LISTEN TO STAFF**\nRespect staff decisions. If you disagree, open a ticket.\n\n"
    "**8️⃣ USE CHANNELS CORRECTLY**\nKeep conversations in the correct channels.\n\n"
    "**9️⃣ NO MALICIOUS ACTIVITY**\nNo malware, account theft or attempts to compromise other users.\n\n"
    "**🔟 HAVE FUN**\nEnjoy the community and help keep the server welcoming."
)
BOT_INFO_TEXT = (
    "Our bot provides server utilities, verification, moderation and other useful features.\n\n"
    "Use ⚙️・commands for available commands.\n\n"
    "If you find a problem, tell us in 🐛・bug-reports."
)


def community() -> dict:
    """The 'Community server' layout: staff roles, verification, tickets, voice and staff areas."""
    manage_base = BASE | P("manage_messages", "manage_threads")
    everything = BASE | P("manage_guild", "manage_channels", "manage_roles", "manage_messages", "kick", "ban", "manage_webhooks",
                          "view_audit_log", "moderate_members", "manage_threads", "mention_everyone")
    # position = rank (higher = more important). Administrator is never included: the importer strips it on purpose.
    roles = [
        (R["owner"], "👑 Owner", 0xF1C40F, True, False, everything, 11),
        (R["co_owner"], "🛡️ Co Owner", 0xE67E22, True, False, everything, 10),
        (R["admin"], "⚙️ Administrator", 0xE74C3C, True, False,
         BASE | P("manage_guild", "manage_channels", "manage_roles", "manage_messages", "kick", "ban", "manage_webhooks", "view_audit_log"), 9),
        (R["head"], "🔨 Head Staff", 0x9B59B6, True, True, BASE | P("manage_messages", "kick", "moderate_members", "manage_threads", "view_audit_log"), 8),
        (R["mod"], "🛠️ Moderator", 0x3498DB, True, True, BASE | P("manage_messages", "moderate_members", "kick", "manage_threads"), 7),
        (R["support"], "🔧 Support", 0x2ECC71, True, True, manage_base, 6),
        (R["bots"], "🤖 Bots", 0x95A5A6, True, False, P("view_channel", "send_messages", "embed_links", "attach_files", "read_message_history", "add_reactions", "external_emojis"), 5),
        (R["premium"], "💎 Premium", 0xE91E63, True, False, BASE, 4),
        (R["verified"], "✅ Verified", 0x1ABC9C, False, False, BASE, 3),
        (R["member"], "👤 Member", 0x7F8C8D, False, False, BASE, 2),
        (R["unverified"], "🤍 Unverified", 0xBDC3C7, False, False, 0, 1),
    ]
    role_items = [
        {"id": rid, "name": name, "color": color, "hoist": hoist, "mentionable": mentionable, "permissions": perms, "position": pos}
        for rid, name, color, hoist, mentionable, perms, pos in roles
    ]
    role_items.sort(key=lambda r: r["position"], reverse=True)

    categories_spec = [
        (101, "📌 INFORMATION", public_category()), (102, "🤖 BOT", public_category()), (103, "💬 COMMUNITY", public_category()),
        (104, "🎫 SUPPORT", public_category()), (105, "🔊 VOICE", public_category()), (106, "🔒 STAFF", staff_category()),
    ]
    channels_spec = [
        # id, name, kind, parent, topic, overwrites
        (201, "📢・announcements", "text", 101, "Server news and updates", readonly(ADMINS)),
        (202, "📜・rules", "text", 101, "Please read before chatting", readonly(TOP_STAFF, also_see=[R["unverified"]])),
        (203, "📖・information", "text", 101, "Everything you need to know", readonly(TOP_STAFF, also_see=[R["unverified"]])),
        (204, "🔗・links", "text", 101, "Useful links", readonly(TOP_STAFF)),
        (205, "❓・faq", "text", 101, "Frequently asked questions", readonly(TOP_STAFF)),
        (206, "🤖・bot-info", "text", 102, "About our bot", readonly(TOP_STAFF)),
        (207, "🔑・verification", "text", 102, "Verify here to unlock the server", readonly((), also_see=[R["unverified"]], bots_write=True)),
        (208, "⚙️・commands", "text", 102, "Use bot commands here", chat()),
        (209, "📊・bot-status", "text", 102, "Bot status and updates", readonly(TOP_STAFF, bots_write=True)),
        (210, "💡・suggestions", "text", 102, "Suggest a feature for the bot", chat()),
        (211, "🐛・bug-reports", "text", 102, "Found a problem? Tell us here", chat()),
        (212, "💬・general", "text", 103, "General chat", chat()),
        (213, "👋・introductions", "text", 103, "Say hello", chat()),
        (214, "🎮・gaming", "text", 103, "Talk games", chat()),
        (215, "📸・media", "text", 103, "Share pictures and clips", chat()),
        (216, "😂・memes", "text", 103, "Memes only", chat()),
        (217, "💡・suggestions", "text", 103, "Suggest something for the server", chat()),
        (218, "🎫・create-ticket", "text", 104, "Open a ticket with the button below", readonly(TOP_STAFF, bots_write=True)),
        (219, "📩・support", "text", 104, "Get help from the team", chat()),
        (220, "🚨・report", "text", 104, "Report a problem or a member", chat()),
        (221, "🔊・General", "voice", 105, None, voice()),
        (222, "🎮・Gaming", "voice", 105, None, voice()),
        (223, "💬・Chilling", "voice", 105, None, voice()),
        (224, "🔒・Staff VC", "voice", 105, None, staff_voice()),
        (225, "💤・AFK", "voice", 105, None, voice()),
        (226, "👑・staff-chat", "text", 106, "Staff discussion", staff_chat()),
        (227, "📋・staff-logs", "text", 106, "Moderation logs", staff_log()),
        (228, "🚨・reports", "text", 106, "Reports from members", staff_chat()),
        (229, "🎫・ticket-logs", "text", 106, "Ticket transcripts and activity", staff_log()),
        (230, "🤖・bot-logs", "text", 106, "Bot logs", staff_log()),
        (231, "⚙️・staff-commands", "text", 106, "Staff bot commands", staff_chat()),
    ]
    categories = [
        {"id": cid, "name": name, "kind": "category", "original_type": 4, "parent_id": None, "position": i, "topic": None, "nsfw": False,
         "bitrate": None, "user_limit": 0, "slowmode": 0, "overwrites": ow}
        for i, (cid, name, ow) in enumerate(categories_spec)
    ]
    channels = [
        {"id": cid, "name": name, "kind": kind, "original_type": 0 if kind == "text" else 2, "parent_id": parent,
         "position": i, "topic": topic, "nsfw": False, "bitrate": None, "user_limit": 0, "slowmode": 0, "overwrites": ow}
        for i, (cid, name, kind, parent, topic, ow) in enumerate(channels_spec)
    ]
    return {
        "name": "Community server", "description": "Staff roles, verification, tickets, voice and staff areas", "usage_count": 0,
        "source_name": "Built-in layout", "everyone_id": EVERYONE, "roles": role_items, "categories": categories, "channels": channels,
        "skipped": 0, "converted": 0,
        "messages": [
            {"channel_id": 202, "title": "📜 SERVER RULES", "text": RULES_TEXT},
            {"channel_id": 206, "title": "🤖 BOT INFORMATION", "text": BOT_INFO_TEXT},
        ],
        "notes": [
            "👑 Owner and 🛡️ Co Owner get every management permission **except Administrator** (I never create Administrator roles). Tick it yourself in Server Settings → Roles if you want it.",
            "I can only give roles permissions I have myself. Manage Webhooks and Manage Threads aren't in my invite, so those two are skipped unless I have them.",
            "I also post the **rules** and **bot info** messages for you.",
        ],
        "next_steps": [
            "`/verify setup channel:#🔑・verification role:✅ Verified unverified_role:🤍 Unverified`",
            "`/ticket setup channel:#🎫・create-ticket staff_role:🔧 Support transcript_channel:#🎫・ticket-logs`",
            "`/logs setup channel:#🤖・bot-logs`  and  `/welcome settings channel:#💬・general`",
            "Drag my role **above** all the new roles, then run `/template create` to get your shareable template link.",
        ],
    }


# ---------------------------------------------------------------------------------------------
# FiveM mod community, rebuilt from the screenshots of a server. Channels built around cracked software, cheats,
# ban evasion, account selling and leaks are deliberately NOT included (see LEFT_OUT).
# ---------------------------------------------------------------------------------------------
LEFT_OUT = {
    "leaker-app": "leaks", "account-sell": "account selling", "windows-activate": "cracked Windows activation",
    "spotify-lifetime": "cracked Spotify", "capcut-pro": "cracked software", "fl-studio": "cracked software",
    "email-gen": "email/account generators", "unban-method": "ban evasion", "bhop-macro": "game macros",
    "val-triggerbot": "cheats", "r6-recoil": "cheats", "macro": "game macros", "fortnite-macro": "game macros",
    "fake-lag": "game exploits", "fake-crashes": "game exploits",
}

STAFF_ROLE, BOOSTER_ROLE = 1, 2


def _staff_only() -> list:
    return overwrites((EVERYONE, 0, VIEW), (STAFF_ROLE, WRITE, 0))


def _everyone_read() -> list:
    return overwrites((EVERYONE, READ, SEND), (STAFF_ROLE, WRITE, 0))


def _bot_post() -> list:
    return overwrites((EVERYONE, READ, SEND), (STAFF_ROLE, WRITE, 0), (R["bots"], WRITE, 0))


def _booster_read() -> list:
    return overwrites((EVERYONE, 0, VIEW), ([BOOSTER_ROLE, STAFF_ROLE], READ, SEND), (STAFF_ROLE, WRITE, 0))


def _booster_chat() -> list:
    return overwrites((EVERYONE, 0, VIEW), ([BOOSTER_ROLE, STAFF_ROLE], WRITE, 0))


def fivem_hub() -> dict:
    staff_perms = BASE | P("manage_messages", "manage_channels", "kick", "ban", "moderate_members", "view_audit_log", "manage_threads")
    roles = [
        {"id": STAFF_ROLE, "name": "🛡️ Staff", "color": 0xE74C3C, "hoist": True, "mentionable": True, "permissions": staff_perms, "position": 2},
        {"id": BOOSTER_ROLE, "name": "💎 Booster", "color": 0xF47FFF, "hoist": True, "mentionable": False, "permissions": BASE, "position": 1},
    ]
    deco = lambda name: f"╔═════ {name} ═════╗"
    categories_spec = [
        (101, deco("community"), []), (102, deco(".gg/uav"), []), (103, deco("fivem"), []), (104, deco("extras"), []),
        (105, deco("rz"), []), (106, deco("boosters"), overwrites((EVERYONE, 0, VIEW), ([BOOSTER_ROLE, STAFF_ROLE], VIEW, 0))),
        (107, deco("priv settings"), overwrites((EVERYONE, 0, VIEW), (STAFF_ROLE, VIEW, 0))),
    ]
    chat = []  # no overwrites: everyone can read and talk (inherits the category)
    ch = [
        # community
        (101, "👋・joined", "text", _bot_post()), (101, "💥・custom-invite", "text", chat), (101, "💫・invite-checker", "text", chat),
        # .gg/uav
        (102, "🚨・anc", "text", _everyone_read()), (102, "🚨・mini-anc", "text", _everyone_read()), (102, "🔢・number-count", "text", chat),
        (102, "📄・general", "text", chat), (102, "📷・montys", "text", _everyone_read()), (102, "🔮・giveaways", "text", _bot_post()),
        (102, "🙌・reviews", "text", chat), (102, "🤡・clowns", "text", chat), (102, "🏷️・request", "text", chat),
        (102, "📃・suggestions", "text", chat), (102, "👕・partners", "text", chat), (102, "💎・boosting-perks", "text", _everyone_read()),
        (102, "💰・premium", "text", _everyone_read()), (102, "🚀・services", "text", _everyone_read()), (102, "💲・mlo-showcase", "text", chat),
        (102, "🔥・discord-intro", "text", chat), (102, "🎫・create-ticket", "text", _bot_post()),
        (102, "🔇・drag", "voice", chat), (102, "🔊・vc", "voice", chat), (102, "🎭・stage", "voice", chat),
    ]
    for symbol, name in (("✗", "fivem-reshades"), ("✗", "fivem-soundpacks"), ("✓", "monty-songs"), ("✗", "pink-reshade"), ("✗", "roads"), ("✗", "nve"),
                         ("✗", "mods"), ("✗", "fps-rpf"), ("✗", "boost-fps"), ("✗", "crosshairs"), ("✗", "paid-fps-pack"),
                         ("✗", "realistic-gp"), ("✗", "f8-commands"), ("✗", "how-to-install")):
        ch.append((103, f"{symbol}・{name}", "text", _everyone_read()))
    for name in ("spotify-settings", "spotify-playlist", "pfp", "banners", "pc-opti", "sizes", "discord-templates", "programs",
                 "clip-trimmer", "kovakks-aimtrain", "intros", "separators"):
        ch.append((104, f"✓・{name}", "text", _everyone_read()))
    for name in ("rz-gang-clothing", "rz-fps-pack", "rz-bundles", "rz-soundpacks", "rz-reshades", "rz-bloodfx", "rz-killfx",
                 "rz-tracers", "rz-ragdoll", "rz-ramps", "rz-crosshairs", "rz-opti-mods"):
        ch.append((105, f"🟡・{name}", "text", _everyone_read()))
    ch.append((106, "💎・booster-announcements", "text", _booster_read()))
    ch.append((106, "💎・booster-chat", "text", _booster_chat()))
    for name in ("36-vault-reshades", "fivem-compys", "fivem-clothing", "fivem-logos", "pure-mode", "rpf-files", "free-crosshair-x", "sites",
                 "editing-presets", "invisible-username", "onlyday-onlynight", "free-vpn-sites", "custom-gif-maker", "backup-files", "quantv",
                 "scripts", "better-tracking", "fivem-locos", "blur"):
        ch.append((106, f"💎・{name}", "text", _booster_read()))
    ch.append((107, "🔑・access", "text", _staff_only()))

    categories = [
        {"id": cid, "name": name, "kind": "category", "original_type": 4, "parent_id": None, "position": i, "topic": None, "nsfw": False,
         "bitrate": None, "user_limit": 0, "slowmode": 0, "overwrites": ow}
        for i, (cid, name, ow) in enumerate(categories_spec)
    ]
    channels = [
        {"id": 300 + i, "name": name, "kind": kind, "original_type": 0 if kind == "text" else 2, "parent_id": parent, "position": i,
         "topic": None, "nsfw": False, "bitrate": None, "user_limit": 0, "slowmode": 0, "overwrites": ow}
        for i, (parent, name, kind, ow) in enumerate(ch)
    ]
    return {
        "name": "FiveM mod community", "description": "Rebuilt from screenshots, minus piracy and cheat channels", "usage_count": 0,
        "source_name": "Built-in layout", "everyone_id": EVERYONE, "roles": roles, "categories": categories, "channels": channels,
        "skipped": 0, "converted": 0, "messages": [],
        "excluded": [f"{name} ({why})" for name, why in LEFT_OUT.items()],
        "notes": [
            "This was rebuilt from screenshots, so emojis and symbols in channel names are my best guess. Rename anything you like afterwards.",
            "`request`, `suggestions`, `partners` and `mlo-showcase` were forum channels. They become normal text channels here (forums need Community turned on).",
            "Only 🛡️ Staff and 💎 Booster roles are created. Boosters see the boosters category; only Staff see `access`.",
            "Content channels are read-only for members, and only staff can post in them.",
        ],
        "next_steps": [
            "`/ticket setup channel:#🎫・create-ticket staff_role:🛡️ Staff`",
            "`/welcome settings channel:#👋・joined`",
            "Drag my role **above** the new roles, then run `/template create` to get your shareable template link.",
        ],
    }


PRESETS = {"community": community, "fivem": fivem_hub}


def build(name: str) -> dict:
    return PRESETS[name]()
