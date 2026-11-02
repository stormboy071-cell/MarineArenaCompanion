from __future__ import annotations

from html import unescape
import re
import uuid


def split_player_name(value: object) -> tuple[str | None, str]:
    name = unescape(str(value or "Unknown")).strip() or "Unknown"
    clan = None
    clan_match = re.match(r"^<([^<>]+)><sp/>(.*)$", name, re.IGNORECASE)
    if clan_match:
        clan = clan_match.group(1).strip() or None
        name = clan_match.group(2).strip()
    name = re.sub(r"<sp/>", " ", name, flags=re.IGNORECASE)
    name = re.sub(r"</?c(?:\s+[^>]*)?>", "", name, flags=re.IGNORECASE).strip()
    return clan, name or "Unknown"


def player_identity(player: dict[str, object], replay_scope: str = "") -> tuple[str, str]:
    """Prefer stable replay account identifiers; names are only a last resort."""
    toon = player.get("toon") or player.get("m_toon") or {}
    if isinstance(toon, dict):
        components = [
            toon.get("region", toon.get("m_region")),
            toon.get("program_id", toon.get("m_programId")),
            toon.get("realm", toon.get("m_realm")),
            toon.get("id", toon.get("m_id")),
        ]
        if any(value not in (None, "", 0) for value in components):
            key = "toon:" + ":".join(str(value or "") for value in components)
            return key, str(player.get("name") or player.get("m_name") or "Unknown")
    name = str(player.get("name") or player.get("m_name") or "Unknown").strip()
    slot = player.get("slot", "unknown")
    if replay_scope:
        return f"unresolved:{replay_scope}:{slot}", name
    return f"unresolved:{uuid.uuid4().hex}", name
