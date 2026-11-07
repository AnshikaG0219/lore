"""Direct and group conversation parser (``messages/<folder>/<thread>/message_N.json``).

Known shape (to be confirmed against real fixtures):
``{"participants": [{"name"}], "messages": [{"sender_name", "timestamp_ms", "content",
"share": {"link", "share_text", "original_content_owner"}}], "title", "thread_path"}``

Privacy: only messages that contain an Instagram URL are turned into references and kept
as raw records. Other message text is never persisted.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.instagram.jsonutil import first_timestamp, parse_timestamp
from app.ingestion.instagram.models import (
    ContentReference,
    ConversationInfo,
    FileCategory,
    FileParseResult,
    ParseIssue,
    SourceType,
)
from app.ingestion.instagram.parsers.shared_links import links_from_node
from app.ingestion.url_parser import parse_url

PARSER_NAME = "messages.v1"

# Keys of a message that may carry a shared link or link-bearing text.
_LINK_FIELDS = ("share", "content", "link", "photos", "videos", "attachments")


def _str(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def conversation_id_for(path: str, data: dict) -> str:
    thread = _str(data.get("thread_path")) or _str(data.get("thread_id"))
    if thread:
        return thread
    parts = path.split("/")
    return "/".join(parts[-3:-1]) if len(parts) >= 3 else path


def is_group_conversation(data: dict) -> bool:
    participants = data.get("participants")
    count = len(participants) if isinstance(participants, list) else 0
    return count > 2


def parse_messages(path: str, data: Any) -> FileParseResult:
    result = FileParseResult(path=path, category=FileCategory.MESSAGES, parser=PARSER_NAME)
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        result.issues.append(
            ParseIssue(path, "No messages list found (unrecognised structure)", "warning")
        )
        return result

    participants = data.get("participants") if isinstance(data.get("participants"), list) else []
    names = [_str(p.get("name")) for p in participants if isinstance(p, dict)]
    is_group = is_group_conversation(data)
    conv_id = conversation_id_for(path, data)
    title = _str(data.get("title")) or ", ".join(n for n in names if n) or conv_id
    info = ConversationInfo(
        conversation_id=conv_id,
        title=title,
        participant_count=len(participants),
        is_group=is_group,
    )
    result.conversation = info
    source_type = SourceType.GROUP_MESSAGE if is_group else SourceType.DIRECT_MESSAGE

    for index, message in enumerate(data["messages"]):
        if not isinstance(message, dict):
            result.issues.append(
                ParseIssue(path, "Non-object message entry", "warning", f"{path}#messages[{index}]")
            )
            continue
        result.records_seen += 1
        info.message_count += 1
        link_bearing = {k: message[k] for k in _LINK_FIELDS if k in message}
        links = links_from_node(link_bearing)
        if not links:
            continue
        info.messages_with_links += 1

        ts_raw = message.get("timestamp_ms", message.get("timestamp"))
        sent_at = parse_timestamp(ts_raw) or first_timestamp(message)
        sender = _str(message.get("sender_name")) or _str(message.get("sender"))
        share = message.get("share") if isinstance(message.get("share"), dict) else {}
        owner = _str(share.get("original_content_owner"))
        share_link = parse_url(share["link"]) if isinstance(share.get("link"), str) else None
        share_key = share_link.identity_key if share_link else None
        locator = f"{conv_id}|{ts_raw}|{sender or ''}"
        for link in links:
            is_shared_item = link.identity_key == share_key
            result.references.append(
                ContentReference(
                    url=link,
                    source_type=source_type,
                    source_name=title,
                    source_reference=locator,
                    captured_at=sent_at,
                    sender=sender,
                    creator_username=(owner if is_shared_item else None) or link.username,
                    caption=_str(share.get("share_text")) if is_shared_item else None,
                    conversation_id=conv_id,
                    metadata={
                        "conversation_id": conv_id,
                        "is_group": is_group,
                        "participant_count": len(participants),
                        "export_file": path,
                        "message_index": index,
                    },
                    raw=message,
                )
            )
    return result
