"""Bounded public-paper lookup for benchmark scoring evidence."""
from __future__ import annotations

from io import BytesIO
import hashlib
from pathlib import Path
import re
from urllib.parse import urlparse

import requests
from pypdf import PdfReader


class PublicPaperTools:
    def __init__(self, source_snapshot: Path):
        self.source_snapshot = source_snapshot.resolve()
        self.allowed: dict[str, str] = {}
        self.documents: list[dict[str, str]] = []

    def search_paper(self, title: str) -> list[dict[str, str]]:
        """Find an open paper by title; the agent must verify the title itself."""
        if not isinstance(title, str) or not 10 <= len(title) <= 300:
            raise ValueError("Paper title must be between 10 and 300 characters")
        response = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search/match",
            params={"query": title, "fields": "title,openAccessPdf,externalIds"},
            timeout=15,
        )
        response.raise_for_status()
        results = []
        for item in response.json().get("data", [])[:3]:
            arxiv_id = item.get("externalIds", {}).get("ArXiv")
            pdf = (item.get("openAccessPdf") or {}).get("url")
            if not isinstance(arxiv_id, str) or not re.fullmatch(r"\d{4}\.\d{4,5}(v\d+)?", arxiv_id):
                continue
            if not isinstance(pdf, str) or urlparse(pdf).netloc != "arxiv.org" or not pdf.startswith("https://arxiv.org/pdf/"):
                continue
            paper_title = item.get("title")
            if not isinstance(paper_title, str) or not paper_title:
                continue
            self.allowed[pdf] = arxiv_id
            results.append({"title": paper_title, "pdf_url": pdf, "arxiv_id": arxiv_id})
        return results

    def read_paper(self, pdf_url: str, start_page: int = 1, page_count: int = 2) -> dict[str, str]:
        """Read at most three pages of a paper returned by search_paper."""
        if pdf_url not in self.allowed:
            raise ValueError("Paper URL must come from search_paper in this run")
        if not 1 <= page_count <= 3 or start_page < 1:
            raise ValueError("Paper page range is outside tool limits")
        response = requests.get(pdf_url, timeout=30)
        response.raise_for_status()
        payload = response.content
        if len(payload) > 15_000_000 or not payload.startswith(b"%PDF"):
            raise ValueError("Paper is not a bounded PDF")
        paper = PdfReader(BytesIO(payload))
        if start_page > len(paper.pages):
            raise ValueError("Paper page does not exist")
        all_text = "\n\n".join(
            f"[page {number + 1}]\n{page.extract_text() or ''}"
            for number, page in enumerate(paper.pages)
        )
        selected = "\n\n".join(
            f"[page {number + 1}]\n{paper.pages[number].extract_text() or ''}"
            for number in range(start_page - 1, min(len(paper.pages), start_page - 1 + page_count))
        )[:15000]
        arxiv_id = self.allowed[pdf_url]
        directory = self.source_snapshot / ".lladar-papers"
        directory.mkdir(exist_ok=True)
        relative = f".lladar-papers/{arxiv_id}.txt"
        target = directory / f"{arxiv_id}.txt"
        target.write_text(all_text, encoding="utf-8")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        descriptor = {"url": pdf_url, "path": relative, "sha256": digest}
        if descriptor not in self.documents:
            self.documents.append(descriptor)
        return {"source_path": relative, "text": selected}