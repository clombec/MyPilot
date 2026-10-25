"""
text.py

Text helpers for Discord: message splitting and user-facing error messages.
"""

from agent.backend import AgentErrorKind

DISCORD_LIMIT = 2000

AGENT_ERROR_MESSAGES = {
    AgentErrorKind.TIMEOUT: (
        "⏳ L'agent n'a pas répondu à temps. Ton message est enregistré, rien n'est perdu. "
        "Réessaie dans un moment ; `/note`, `/capture` et `/statut` marchent toujours."
    ),
    AgentErrorKind.USAGE_LIMIT: (
        "🚦 Limite d'usage Claude atteinte pour le moment. Ton message est enregistré. "
        "`/note`, `/capture` et `/statut` marchent toujours, sans Claude."
    ),
    AgentErrorKind.UNAVAILABLE: (
        "🔌 Claude Code est indisponible sur le PC (non installé, déconnecté ou PC en "
        "veille ?). Ton message est enregistré. `/note`, `/capture` et `/statut` marchent "
        "toujours."
    ),
    AgentErrorKind.SESSION_MISSING: (
        "⚠️ La conversation du jour n'a pas pu être reprise. Réessaie : une nouvelle "
        "conversation démarrera."
    ),
    AgentErrorKind.FAILED: (
        "⚠️ L'agent a rencontré une erreur. Ton message est enregistré, rien n'est perdu. "
        "Réessaie plus tard ; `/note`, `/capture` et `/statut` marchent toujours."
    ),
}


def split_message(text: str, limit: int = DISCORD_LIMIT) -> list[str]:
    """Split a text into Discord-sized chunks, preferring line then word breaks.

    Args:
        text: Text to split.
        limit: Maximum characters per chunk.

    Returns:
        Non-empty chunks, each at most `limit` characters.
    """
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks
