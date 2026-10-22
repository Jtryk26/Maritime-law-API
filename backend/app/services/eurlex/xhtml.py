"""Cellar XHTML structure, retaining source anchors and exact stored-text offsets.

Observed against 32009L0016's ELI subdivisions, nested list/layout tables and
annexes. No Danish paragraph parser or generic flat-HTML fallback is used.
Formex requires a separate adapter and is rejected rather than misinterpreted.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from app.services.retsinformation.base import PermanentSourceError


def local(node):
    return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""


def node_text(node):
    return " ".join("".join(node.itertext()).split())


@dataclass(frozen=True)
class ParsedAct:
    content: str
    structure: dict
    language: str | None
    identifiers: tuple[str, ...]


def parse_xhtml(body: bytes) -> ParsedAct:
    if any(marker in body.lower() for marker in (b"awswaf", b"aws-waf", b"captcha", b"verify you are human", b"<!entity")):
        raise PermanentSourceError("Cellar returned an access challenge or unsupported XML entity declarations")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise PermanentSourceError("Cellar did not return well-formed XHTML") from exc
    if local(root) != "html":
        raise PermanentSourceError("Only XHTML is supported; Formex is not parsed as HTML")
    if any(local(node) in {"img", "svg", "math", "object", "iframe"} for node in root.iter()):
        raise PermanentSourceError("XHTML contains unsupported non-text legal content; manual format review is required")
    body_node = next((node for node in root if local(node) == "body"), None)
    if body_node is None:
        raise PermanentSourceError("Cellar did not return an XHTML body")
    language = root.get("lang") or root.get("{http://www.w3.org/XML/1998/namespace}lang")
    if not language:
        language = next((node_text(node) for node in body_node.iter() if "oj-hd-lg" in node.get("class", "").split()), None)
    if not language:
        reference = next((node_text(node) for node in body_node.iter() if "reference" in node.get("class", "").split()), "")
        match = re.search(r"^0[0-9]{4}[RL][0-9]{4}\s*—\s*(EN|DA)\s*—", reference)
        language = match[1] if match else None
    if language:
        language = {"eng": "en", "dan": "da"}.get(language.lower(), language.lower().split("-")[0])
    identifiers = tuple(node.get("content", "") for node in root.iter()
                        if local(node) == "meta" and node.get("name", "").lower() in {"dc.identifier", "dc.identifier.uri"})
    parts, units, tables, ranges = [], [], [], {}
    position = 0
    last = ""

    def append(value):
        nonlocal position, last
        if value:
            parts.append(value)
            position += len(value)
            last = value[-1]

    def boundary():
        if position and last != "\n":
            append("\n")

    def emit(value):
        value = " ".join((value or "").split())
        if value:
            if position and not last.isspace():
                append(" ")
            append(value)

    def render(node, owner=None, parent_table=None):
        tag = local(node)
        if tag in {"head", "script", "style"}:
            return
        block = tag in {"body", "div", "p", "table", "tr", "li", "h1", "h2", "h3", "br"}
        if block:
            boundary()
        start = position
        source_id = node.get("id", "")
        kind = ("article" if re.fullmatch(r"art_[0-9]+[a-z]?", source_id) else
                "annex" if re.fullmatch(r"anx_(?:[IVXLCDM]+|[0-9]+)", source_id) else
                "recital" if re.fullmatch(r"rct_[0-9]+", source_id) else None)
        unit = None
        if kind:
            title_node = next((child for child in node.iter() if local(child) == "p"), node)
            unit = {"kind": kind, "id": source_id, "parent_id": owner, "heading": node_text(title_node),
                    "char_start": start}
            units.append(unit)
            owner = source_id
        table = None
        if tag == "table":
            table = {"id": source_id or f"table_{len(tables)+1}", "owner_id": owner,
                     "parent_table_id": parent_table, "char_start": start}
            tables.append(table)
            parent_table = table["id"]
        emit(node.text)
        seen_cell = False
        for child in node:
            if tag == "tr" and local(child) in {"td", "th"}:
                if seen_cell:
                    append(" | ")
                seen_cell = True
            render(child, owner, parent_table)
            emit(child.tail)
        end = position
        ranges[id(node)] = (start, end)
        if unit is not None:
            unit["char_end"] = end
            unit["paragraphs"] = [
                {"id": child.get("id") or f"{source_id}.p_{i}", "char_start": ranges[id(child)][0],
                 "char_end": ranges[id(child)][1]}
                for i, child in enumerate((n for n in node.iter() if local(n) == "p"), 1)]
        if table is not None:
            table["char_end"] = end
            def rows(parent):
                for child in parent:
                    if local(child) == "tr":
                        yield child
                    elif local(child) != "table":
                        yield from rows(child)
            table["rows"] = [[{"char_start": ranges[id(cell)][0], "char_end": ranges[id(cell)][1],
                "rowspan": cell.get("rowspan", "1"), "colspan": cell.get("colspan", "1")}
                for cell in row if local(cell) in {"td", "th"}] for row in rows(node)]
        if block:
            boundary()
    render(body_node)
    content = "".join(parts)  # Keep the exact string to which every range points.
    if len(content.strip()) < 200:
        raise PermanentSourceError("Cellar XHTML has no substantive legal text")
    return ParsedAct(content=content, language=language, identifiers=identifiers,
        structure={"format": "cellar_xhtml", "offset_scope": "document_version_content",
                   "normalization": "Whitespace normalized; block boundaries and table cell separators retained. Consult original XHTML for layout.",
                   "units": units, "tables": tables})
