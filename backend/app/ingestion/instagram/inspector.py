"""Read-only inspection of an export → structured report. No database required."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from app.ingestion.deduplication import group_by_identity
from app.ingestion.instagram.archive import ExportArchive
from app.ingestion.instagram.models import (
    ContentReference,
    ConversationInfo,
    FileCategory,
    ParseIssue,
)
from app.ingestion.instagram.parsers.metadata import ExportMetadata, describe_export
from app.ingestion.instagram.reader import iter_parse
from app.ingestion.url_parser import MEDIA_KINDS, UrlKind


@dataclass
class InspectionReport:
    export: ExportMetadata
    files_by_category: dict[str, int] = field(default_factory=dict)
    parsed_files: list[str] = field(default_factory=list)
    saved_posts: int = 0
    saved_records_without_url: int = 0
    collections: dict[str, int] = field(default_factory=dict)
    conversations: list[ConversationInfo] = field(default_factory=list)
    url_occurrences: int = 0
    urls_by_kind: dict[str, int] = field(default_factory=dict)
    unique_content_items: int = 0
    duplicate_content_items: int = 0
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    unresolved_share_links: list[str] = field(default_factory=list)
    unsupported_files: list[str] = field(default_factory=list)
    unrecognised_json_files: list[str] = field(default_factory=list)
    rejected_members: list[dict[str, str]] = field(default_factory=list)
    errors: list[ParseIssue] = field(default_factory=list)
    warnings: list[ParseIssue] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["conversation_summary"] = {
            "total": len(self.conversations),
            "group": sum(1 for c in self.conversations if c.is_group),
            "direct": sum(1 for c in self.conversations if not c.is_group),
            "with_instagram_links": sum(1 for c in self.conversations if c.messages_with_links),
        }
        return data


def inspect_export(archive: ExportArchive, duplicate_sample: int = 50) -> InspectionReport:
    listing = archive.listing()
    report = InspectionReport(export=describe_export(listing.members))
    report.rejected_members = [{"path": r.path, "reason": r.reason} for r in listing.rejected]

    categories: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    all_refs: list[ContentReference] = []
    conversations: dict[str, ConversationInfo] = {}

    for result in iter_parse(archive):
        categories[result.category.value] += 1
        if result.category is FileCategory.HTML:
            report.unsupported_files.append(result.path)
        elif (result.category is FileCategory.OTHER_JSON and result.parser is None
              and not result.issues):
            report.unrecognised_json_files.append(result.path)
        if result.parser:
            report.parsed_files.append(result.path)

        for issue in result.issues:
            (report.errors if issue.severity == "error" else report.warnings).append(issue)

        if result.category is FileCategory.SAVED_POSTS:
            report.saved_posts += result.records_seen - len(result.unresolved_records)
            report.saved_records_without_url += len(result.unresolved_records)
        for name, count in result.collections.items():
            report.collections[name] = report.collections.get(name, 0) + count
        if result.conversation:
            c = result.conversation
            existing = conversations.get(c.conversation_id)
            if existing:  # threads split across message_1.json, message_2.json, ...
                existing.message_count += c.message_count
                existing.messages_with_links += c.messages_with_links
            else:
                conversations[c.conversation_id] = c

        for ref in result.references:
            kinds[ref.url.kind.value] += 1
            all_refs.append(ref)

    report.files_by_category = dict(categories)
    report.conversations = list(conversations.values())
    report.url_occurrences = len(all_refs)
    report.urls_by_kind = dict(kinds)

    groups = group_by_identity(r for r in all_refs if r.url.kind in MEDIA_KINDS
                               or r.url.kind is UrlKind.SHARE_LINK)
    report.unique_content_items = len(groups)
    dupes = [g for g in groups.values() if g.source_count > 1]
    report.duplicate_content_items = len(dupes)
    report.duplicates = [
        {
            "canonical_url": g.url.canonical_url,
            "occurrences": g.source_count,
            "sources": sorted({f"{r.source_type.value}:{r.source_name}" for r in g.references}),
        }
        for g in sorted(dupes, key=lambda g: -g.source_count)[:duplicate_sample]
    ]
    report.unresolved_share_links = sorted(
        {r.url.canonical_url for r in all_refs if r.url.kind is UrlKind.SHARE_LINK}
    )
    return report


def format_report(report: InspectionReport, max_items: int = 20) -> str:
    d = report.to_dict()
    e = report.export
    lines = [
        "InstaBrain — Instagram export inspection",
        "=" * 44,
        f"Layout: {e.layout}   Root folder: {e.root_prefix or '(none)'}",
        f"Files discovered: {e.total_files} ({e.total_bytes:,} bytes)  formats={e.formats}",
        f"Files by category: {report.files_by_category}",
        f"Parsed files: {len(report.parsed_files)}",
        "",
        f"Saved posts identified: {report.saved_posts}"
        + (f"  (+{report.saved_records_without_url} without URL)"
           if report.saved_records_without_url else ""),
        f"Collections identified: {len(report.collections)}",
    ]
    for name, count in sorted(report.collections.items())[:max_items]:
        lines.append(f"  - {name}: {count} item(s)")
    cs = d["conversation_summary"]
    lines += [
        f"Conversations identified: {cs['total']} (group={cs['group']}, direct={cs['direct']}, "
        f"with Instagram links={cs['with_instagram_links']})",
        "",
        f"Instagram URLs extracted: {report.url_occurrences}  by kind={report.urls_by_kind}",
        f"Unique content items: {report.unique_content_items}",
        f"Duplicate content items (seen in >1 source): {report.duplicate_content_items}",
    ]
    for dup in report.duplicates[:max_items]:
        lines.append(f"  - {dup['canonical_url']} ×{dup['occurrences']}  {dup['sources']}")
    if report.unresolved_share_links:
        lines.append(f"Unresolved share links (need network resolution): "
                     f"{len(report.unresolved_share_links)}")

    def _section(title: str, items: list[str]) -> None:
        lines.append(f"{title}: {len(items)}")
        lines.extend(f"  - {i}" for i in items[:max_items])
        if len(items) > max_items:
            lines.append(f"  … {len(items) - max_items} more")

    lines.append("")
    _section("Unsupported files (HTML export — request JSON format)", report.unsupported_files)
    _section("Other JSON files (not import-relevant, not parsed)", report.unrecognised_json_files)
    _section("Rejected archive members",
             [f"{r['path']} ({r['reason']})" for r in report.rejected_members])
    _section("Parsing errors", [f"{i.path}: {i.message}" for i in report.errors])
    _section("Parsing warnings",
             [f"{i.locator or i.path}: {i.message}" for i in report.warnings])
    return "\n".join(lines)
