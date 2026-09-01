"""
ADR parsing primitives — shared by scan_project (server.py) and the ADR
index builder (adr_index.py).

These three functions were originally private helpers inside server.py. They
were lifted out when adr_index.py was added so the two consumers cannot
drift: if scan_project and the Decisions view disagree about what an ADR is,
the view silently under-reports and nobody notices.

Nothing here touches state. Pure text in, structured data out.
"""

import re
from pathlib import Path
from typing import Optional


def load_decisions(project_dir: Path) -> Optional[tuple[str, str]]:
    """Load ADR text for a project, split layout first, legacy single file second.

    Split layout is ``docs/project_notes/decisions/ADR-*.md`` (one ADR per
    file). Legacy is a single ``docs/project_notes/decisions.md``. When a repo
    has migrated, decisions.md remains as a generated index whose rows are
    table cells, not ``## ADR-`` headings, so reading it would yield zero ADRs
    and silently report that nothing needs a ticket. Preferring the directory
    is what stops that.

    Returns ``(content, source_description)``, or None when neither exists.
    """
    notes_dir = project_dir / "docs" / "project_notes"
    split_dir = notes_dir / "decisions"
    single_file = notes_dir / "decisions.md"

    if split_dir.is_dir():
        def _adr_sort_key(p: Path) -> tuple[int, str]:
            m = re.match(r"ADR-(\d+)", p.name)
            return (int(m.group(1)) if m else 10**9, p.name)

        files = sorted(split_dir.glob("ADR-*.md"), key=_adr_sort_key)
        if files:
            content = "\n\n".join(
                f.read_text(encoding="utf-8") for f in files
            )
            return content, f"{split_dir} ({len(files)} files)"

    if single_file.exists():
        return single_file.read_text(encoding="utf-8"), str(single_file)

    return None


def load_decisions_per_file(project_dir: Path) -> list[tuple[str, str]]:
    """Same discovery as load_decisions, but keeps per-file provenance.

    load_decisions concatenates the split layout into one blob, which is fine
    for scan_project (it only counts ADRs) but loses which file an ADR came
    from. The Decisions view wants to deep-link to the source, so this variant
    returns ``[(content, source_path), ...]`` instead.

    For the legacy single-file layout this is a one-element list, so callers
    can treat both layouts identically.
    """
    notes_dir = project_dir / "docs" / "project_notes"
    split_dir = notes_dir / "decisions"
    single_file = notes_dir / "decisions.md"

    if split_dir.is_dir():
        def _adr_sort_key(p: Path) -> tuple[int, str]:
            m = re.match(r"ADR-(\d+)", p.name)
            return (int(m.group(1)) if m else 10**9, p.name)

        files = sorted(split_dir.glob("ADR-*.md"), key=_adr_sort_key)
        if files:
            return [(f.read_text(encoding="utf-8"), str(f)) for f in files]

    if single_file.exists():
        return [(single_file.read_text(encoding="utf-8"), str(single_file))]

    return []


# Heading separators seen in the wild: 597 use ": ", 14 use " — " (em-dash,
# all in virtual_power_plant), 3 use a bare space. The original pattern
# required a colon, so virtual_power_plant's 14 ADRs parsed as zero and
# scan_project reported the repo had no decisions at all. The separator is
# optional here so every dialect is read.
ADR_HEADING_RE = re.compile(
    r"^#{2,3} (ADR-\d+|ADR-XXX)\s*[:—–-]?\s*(.+?)"
    r"(?:\s*\((\d{4}-\d{2}-\d{2})\))?\s*$",
    re.MULTILINE,
)


def parse_adrs(content: str) -> list[dict]:
    """Parse ADR entries from decisions text (single file or concatenated split files)."""
    adr_pattern = ADR_HEADING_RE

    matches = list(adr_pattern.finditer(content))
    adrs = []

    for i, match in enumerate(matches):
        adr_id = match.group(1)
        title = match.group(2).strip()
        date = match.group(3) or ""

        # Skip the template entry
        if adr_id == "ADR-XXX":
            continue

        # Extract body until next ADR or end of file
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()

        # Parse sections
        sections = parse_adr_sections(body)

        # Fall back to **Date**: field if header didn't have a date
        if not date and "date" in sections:
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", sections["date"])
            if date_match:
                date = date_match.group(0)

        adrs.append({
            "id": adr_id,
            "title": title,
            "date": date,
            "body": body,
            "sections": sections,
        })

    return adrs


def parse_adr_sections(body: str) -> dict[str, str]:
    """Split an ADR body into named sections. Handles both **Bold:** and ### Header styles."""
    # Match **Bold:** markers OR ### subsection headers
    section_pattern = re.compile(
        r"(?:^\*\*(.+?):?\*\*\s*$|^### (.+?)\s*$)",
        re.MULTILINE,
    )
    matches = list(section_pattern.finditer(body))
    sections = {}

    for i, match in enumerate(matches):
        # group(1) is **Bold**, group(2) is ### Header
        name = (match.group(1) or match.group(2)).strip().rstrip(":")
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[name.lower()] = body[start:end].strip()

    return sections


# Back-compat aliases. server.py referred to these by their underscore names
# in several places; keeping the aliases means the extraction is a pure move
# with no behaviour change at the call sites.
_load_decisions = load_decisions
_parse_adrs = parse_adrs
_parse_adr_sections = parse_adr_sections
