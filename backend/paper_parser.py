"""Research-paper parsing and section-aware chunking.

The parser keeps the original document upload pipeline untouched. It reuses the
same three-level chunk fields that SuperMew needs for auto-merging while adding
paper-specific metadata such as section title, page range, and chunk type.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any

import docx2txt
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader


SECTION_ALIASES = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "related work": "Related Work",
    "background": "Background",
    "method": "Method",
    "methods": "Method",
    "methodology": "Methodology",
    "approach": "Approach",
    "framework": "Framework",
    "experiments": "Experiments",
    "experiment": "Experiments",
    "evaluation": "Evaluation",
    "ablation study": "Ablation Study",
    "discussion": "Discussion",
    "conclusion": "Conclusion",
    "conclusions": "Conclusion",
    "references": "References",
    "bibliography": "References",
    "appendix": "Appendix",
}

SECTION_PATTERN = re.compile(
    r"^\s*(?:(?:\d+|[IVX]+)(?:\.\d+)*\.?\s+)?"
    r"(Abstract|Introduction|Related\s+Work|Background|Methods?|Methodology|"
    r"Approach|Framework|Experiments?|Evaluation|Ablation\s+Study|Discussion|"
    r"Conclusions?|References|Bibliography|Appendix)\s*$",
    re.IGNORECASE,
)

SUBSECTION_PATTERN = re.compile(r"^\s*(\d+\.\d+(?:\.\d+)*)\s+(.{3,120})$")
FIGURE_PATTERN = re.compile(r"^\s*(fig\.|figure)\s*\d+[:.\s-]", re.IGNORECASE)
TABLE_PATTERN = re.compile(r"^\s*table\s*\d+[:.\s-]", re.IGNORECASE)
REFERENCE_PATTERN = re.compile(r"^\s*(\[\d+\]|\d+\.\s+).+")
FORMULA_NUMBER_PATTERN = re.compile(r"\(\s*\d+(?:\.\d+)?\s*\)\s*$")


class ResearchPaperParser:
    """Parse PDF/DOCX/TXT papers into section-aware three-level chunks."""

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50):
        level_1_size = max(1200, chunk_size * 2)
        level_1_overlap = max(240, chunk_overlap * 2)
        level_2_size = max(600, chunk_size)
        level_2_overlap = max(120, chunk_overlap)
        level_3_size = max(300, chunk_size // 2)
        level_3_overlap = max(60, chunk_overlap // 2)
        separators = ["\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ", ""]
        self._splitter_level_1 = RecursiveCharacterTextSplitter(
            chunk_size=level_1_size,
            chunk_overlap=level_1_overlap,
            add_start_index=True,
            separators=separators,
        )
        self._splitter_level_2 = RecursiveCharacterTextSplitter(
            chunk_size=level_2_size,
            chunk_overlap=level_2_overlap,
            add_start_index=True,
            separators=separators,
        )
        self._splitter_level_3 = RecursiveCharacterTextSplitter(
            chunk_size=level_3_size,
            chunk_overlap=level_3_overlap,
            add_start_index=True,
            separators=separators,
        )

    def parse_file(
        self,
        file_path: str,
        filename: str,
        paper_id: int,
        owner_id: int,
        paper_title: str = "",
    ) -> list[dict[str, Any]]:
        """Parse a supported paper file into database-ready chunk dictionaries."""
        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf":
            pages = self._load_pdf_pages(file_path)
        elif suffix == ".docx":
            pages = self._load_docx_pages(file_path)
        elif suffix == ".txt":
            pages = self._load_txt_pages(file_path)
        else:
            raise ValueError(f"Unsupported paper file type: {filename}")

        cleaned_pages = self._remove_repeated_headers_footers(pages)
        sections = self._segment_sections(cleaned_pages)
        if not sections:
            sections = [{
                "section_title": "Unknown",
                "subsection_title": "",
                "chunk_type": "unknown",
                "page_start": pages[0]["page_number"] if pages else None,
                "page_end": pages[-1]["page_number"] if pages else None,
                "text": "\n\n".join(page["text"] for page in cleaned_pages).strip(),
            }]
        return self._split_sections_to_three_levels(
            sections=sections,
            filename=filename,
            paper_id=paper_id,
            owner_id=owner_id,
            paper_title=paper_title,
        )

    def _load_pdf_pages(self, file_path: str) -> list[dict[str, Any]]:
        """Extract page-numbered text from a PDF with pypdf."""
        reader = PdfReader(file_path)
        pages = []
        for idx, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            pages.append({"page_number": idx, "text": self._clean_page_text(text)})
        return pages

    def _load_docx_pages(self, file_path: str) -> list[dict[str, Any]]:
        """Load DOCX as one logical page for stage-8 fallback parsing."""
        text = docx2txt.process(file_path) or ""
        return [{"page_number": 1, "text": self._clean_page_text(text)}]

    def _load_txt_pages(self, file_path: str) -> list[dict[str, Any]]:
        """Load TXT as one logical page for stage-8 fallback parsing."""
        text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        return [{"page_number": 1, "text": self._clean_page_text(text)}]

    def _clean_page_text(self, text: str) -> str:
        """Merge PDF line breaks while preserving captions, references, and formulas."""
        text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
        raw_lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
        lines = [line for line in raw_lines if line]
        merged: list[str] = []
        for line in lines:
            if not merged:
                merged.append(line)
                continue
            prev = merged[-1]
            if self._should_keep_line_break(prev, line):
                merged.append(line)
            elif prev.endswith("-") and line[:1].islower():
                merged[-1] = prev[:-1] + line
            else:
                merged[-1] = prev + " " + line
        return "\n".join(merged)

    def _should_keep_line_break(self, prev: str, current: str) -> bool:
        """Decide whether two extracted PDF lines should remain separated."""
        if SECTION_PATTERN.match(current) or SUBSECTION_PATTERN.match(current):
            return True
        if FIGURE_PATTERN.match(current) or TABLE_PATTERN.match(current):
            return True
        if REFERENCE_PATTERN.match(current):
            return True
        if FORMULA_NUMBER_PATTERN.search(prev):
            return True
        if prev.endswith((".", "?", "!", ":", ";")):
            return True
        if current[:1].isupper() and len(prev) < 90:
            return True
        return False

    def _remove_repeated_headers_footers(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove simple repeated first/last lines that look like page headers/footers."""
        if len(pages) < 3:
            return pages
        candidates = []
        for page in pages:
            lines = [line.strip() for line in page["text"].split("\n") if line.strip()]
            if lines:
                candidates.append(lines[0])
                candidates.append(lines[-1])
        repeated = {
            line for line, count in Counter(candidates).items()
            if count >= max(3, len(pages) // 2) and len(line) < 140
        }
        cleaned = []
        for page in pages:
            lines = [line for line in page["text"].split("\n") if line.strip() not in repeated]
            cleaned.append({**page, "text": "\n".join(lines).strip()})
        return cleaned

    def _segment_sections(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Assign paragraphs to recognized sections; fallback section is Unknown."""
        sections = []
        current = self._new_section("Unknown", "", "unknown", None)
        for page in pages:
            page_no = page["page_number"]
            for line in [item.strip() for item in page["text"].split("\n") if item.strip()]:
                section_title = self._match_section_title(line)
                if section_title:
                    self._flush_section(sections, current)
                    current = self._new_section(section_title, "", self._chunk_type_for_section(section_title), page_no)
                    continue

                subsection_title = self._match_subsection_title(line)
                if subsection_title:
                    current["subsection_title"] = subsection_title
                    continue

                chunk_type = self._detect_chunk_type(line, current["section_title"])
                if chunk_type != "text":
                    self._flush_section(sections, current)
                    caption = self._new_section(current["section_title"], current["subsection_title"], chunk_type, page_no)
                    caption["parts"].append(line)
                    caption["page_end"] = page_no
                    self._flush_section(sections, caption)
                    current = self._new_section(current["section_title"], current["subsection_title"], self._chunk_type_for_section(current["section_title"]), page_no)
                    continue

                if current["page_start"] is None:
                    current["page_start"] = page_no
                current["page_end"] = page_no
                current["parts"].append(line)
        self._flush_section(sections, current)
        return sections

    def _new_section(self, title: str, subsection: str, chunk_type: str, page_start: int | None) -> dict[str, Any]:
        return {
            "section_title": title or "Unknown",
            "subsection_title": subsection or "",
            "chunk_type": chunk_type or "text",
            "page_start": page_start,
            "page_end": page_start,
            "parts": [],
        }

    def _flush_section(self, sections: list[dict[str, Any]], section: dict[str, Any]) -> None:
        text = "\n".join(section.get("parts", [])).strip()
        if not text:
            return
        sections.append({
            "section_title": section.get("section_title") or "Unknown",
            "subsection_title": section.get("subsection_title") or "",
            "chunk_type": section.get("chunk_type") or "unknown",
            "page_start": section.get("page_start"),
            "page_end": section.get("page_end"),
            "text": text,
        })

    def _match_section_title(self, line: str) -> str | None:
        match = SECTION_PATTERN.match(line.strip())
        if not match:
            return None
        normalized = re.sub(r"\s+", " ", match.group(1).lower()).strip()
        return SECTION_ALIASES.get(normalized, match.group(1).title())

    def _match_subsection_title(self, line: str) -> str:
        match = SUBSECTION_PATTERN.match(line)
        return match.group(2).strip() if match else ""

    def _chunk_type_for_section(self, section_title: str) -> str:
        return "reference" if (section_title or "").lower() == "references" else "text"

    def _detect_chunk_type(self, line: str, section_title: str) -> str:
        if (section_title or "").lower() == "references":
            return "reference"
        if FIGURE_PATTERN.match(line):
            return "figure_caption"
        if TABLE_PATTERN.match(line):
            return "table_caption"
        return "text"

    def _split_sections_to_three_levels(
        self,
        sections: list[dict[str, Any]],
        filename: str,
        paper_id: int,
        owner_id: int,
        paper_title: str,
    ) -> list[dict[str, Any]]:
        """Split section text into L1/L2/L3 chunks while preserving parent links."""
        chunks: list[dict[str, Any]] = []
        global_idx = 0
        for section_idx, section in enumerate(sections):
            base = {
                "paper_id": paper_id,
                "owner_id": owner_id,
                "filename": filename,
                "paper_title": paper_title or "",
                "section_title": section.get("section_title") or "Unknown",
                "subsection_title": section.get("subsection_title") or "",
                "page_start": section.get("page_start"),
                "page_end": section.get("page_end"),
                "chunk_type": section.get("chunk_type") or "unknown",
            }
            level_1_docs = self._splitter_level_1.create_documents([section["text"]])
            for l1_idx, l1_doc in enumerate(level_1_docs):
                l1_text = (l1_doc.page_content or "").strip()
                if not l1_text:
                    continue
                l1_id = self._build_chunk_id(filename, paper_id, section_idx, 1, l1_idx)
                chunks.append({**base, "text": l1_text, "chunk_id": l1_id, "parent_chunk_id": "", "root_chunk_id": l1_id, "chunk_level": 1, "chunk_idx": global_idx})
                global_idx += 1
                level_2_docs = self._splitter_level_2.create_documents([l1_text])
                for l2_idx, l2_doc in enumerate(level_2_docs):
                    l2_text = (l2_doc.page_content or "").strip()
                    if not l2_text:
                        continue
                    l2_id = self._build_chunk_id(filename, paper_id, section_idx, 2, l1_idx, l2_idx)
                    chunks.append({**base, "text": l2_text, "chunk_id": l2_id, "parent_chunk_id": l1_id, "root_chunk_id": l1_id, "chunk_level": 2, "chunk_idx": global_idx})
                    global_idx += 1
                    level_3_docs = self._splitter_level_3.create_documents([l2_text])
                    for l3_idx, l3_doc in enumerate(level_3_docs):
                        l3_text = (l3_doc.page_content or "").strip()
                        if not l3_text:
                            continue
                        l3_id = self._build_chunk_id(filename, paper_id, section_idx, 3, l1_idx, l2_idx, l3_idx)
                        chunks.append({**base, "text": l3_text, "chunk_id": l3_id, "parent_chunk_id": l2_id, "root_chunk_id": l1_id, "chunk_level": 3, "chunk_idx": global_idx})
                        global_idx += 1
        return chunks

    @staticmethod
    def _build_chunk_id(filename: str, paper_id: int, section_idx: int, level: int, *indexes: int) -> str:
        index_path = ".".join(str(index) for index in indexes)
        return f"paper:{paper_id}:{filename}:s{section_idx}:l{level}:{index_path}"
