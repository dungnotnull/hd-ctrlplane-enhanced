"""
KnowledgeUpdater — Research paper and resource crawler for ctrlplane-enhanced.

Sources:
  - ArXiv API: cs.SE, cs.DC categories (deployment risk, CI/CD, anomaly detection)
  - GitHub API: Ctrlplane releases + enhancement issues
  - GitHub Engineering Blog RSS
  - CNCF Blog RSS
  - Semantic Scholar: ICSE, MSR conference papers on deployment intelligence

Schedule: Weekly (Sunday 02:00) via APScheduler.
"""

import asyncio
import hashlib
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

ARXIV_CATEGORIES = ["cs.SE", "cs.DC"]
ARXIV_KEYWORDS = [
    "deployment risk", "CI/CD", "continuous deployment", "rollback prediction",
    "pipeline optimization", "anomaly detection DevOps", "build failure prediction",
    "ML deployment", "AIOps", "SRE automation",
]
ARXIV_MAX_RESULTS = 30
SEMANTIC_SCHOLAR_CONFERENCES = ["ICSE", "MSR", "FSE", "SOSP", "EuroSys"]

RSS_FEEDS = {
    "GitHub Engineering": "https://github.blog/category/engineering/feed/",
    "CNCF Blog": "https://www.cncf.io/blog/feed/",
}

SECOND_KNOWLEDGE_BRAIN_PATH = Path(__file__).parent.parent / "SECOND-KNOWLEDGE-BRAIN.md"


class KnowledgeUpdater:
    def __init__(self, memory=None) -> None:
        self.memory = memory
        self.github_token = os.getenv("GITHUB_TOKEN", "")
        self._new_entries: list[dict] = []

    # ── Public methods ───────────────────────────────────────────────────────

    async def run_update(self) -> int:
        """Run full knowledge crawl. Returns number of new entries added."""
        logger.info("Starting knowledge update crawl...")
        self._new_entries = []

        tasks = [
            self._crawl_arxiv(),
            self._crawl_semantic_scholar(),
            self._crawl_rss_feeds(),
            self._crawl_ctrlplane_releases(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Crawl task failed: %s", r)

        if self._new_entries:
            self._append_to_knowledge_brain(self._new_entries)
            logger.info("Added %d new entries to SECOND-KNOWLEDGE-BRAIN.md", len(self._new_entries))
        else:
            logger.info("No new entries found this cycle.")

        return len(self._new_entries)

    # ── Crawlers ─────────────────────────────────────────────────────────────

    async def _crawl_arxiv(self) -> None:
        loop = asyncio.get_event_loop()
        for category in ARXIV_CATEGORIES:
            try:
                entries = await loop.run_in_executor(None, self._fetch_arxiv, category)
                for entry in entries:
                    if self._is_relevant(entry["title"] + " " + entry["abstract"]):
                        self._add_if_new(entry, source="arxiv")
            except Exception as e:
                logger.warning("ArXiv crawl failed for %s: %s", category, e)

    async def _crawl_semantic_scholar(self) -> None:
        loop = asyncio.get_event_loop()
        for keyword in ARXIV_KEYWORDS[:5]:
            try:
                entries = await loop.run_in_executor(None, self._fetch_semantic_scholar, keyword)
                for entry in entries:
                    self._add_if_new(entry, source="semantic_scholar")
            except Exception as e:
                logger.warning("Semantic Scholar crawl failed for '%s': %s", keyword, e)

    async def _crawl_rss_feeds(self) -> None:
        loop = asyncio.get_event_loop()
        for name, url in RSS_FEEDS.items():
            try:
                entries = await loop.run_in_executor(None, self._fetch_rss, name, url)
                for entry in entries:
                    if self._is_relevant(entry["title"] + " " + entry.get("abstract", "")):
                        self._add_if_new(entry, source=name)
            except Exception as e:
                logger.warning("RSS crawl failed for %s: %s", name, e)

    async def _crawl_ctrlplane_releases(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            entries = await loop.run_in_executor(None, self._fetch_github_releases, "ctrlplanedev/ctrlplane")
            for entry in entries:
                self._add_if_new(entry, source="ctrlplane_releases")
        except Exception as e:
            logger.warning("Ctrlplane releases crawl failed: %s", e)

    # ── Fetchers ──────────────────────────────────────────────────────────────

    def _fetch_arxiv(self, category: str) -> list[dict]:
        ninety_days_ago = (datetime.utcnow() - timedelta(days=90)).strftime("%Y%m%d")
        query_parts = [f"cat:{category}"]
        keyword_query = " OR ".join(f'ti:"{kw}" OR abs:"{kw}"' for kw in ARXIV_KEYWORDS[:5])
        query = f"({keyword_query}) AND {query_parts[0]}"

        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": query,
            "max_results": ARXIV_MAX_RESULTS,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(response.text)
        entries = []
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", "", ns) or "").strip()
            abstract = (entry.findtext("atom:summary", "", ns) or "").strip()
            link = entry.findtext("atom:id", "", ns) or ""
            published = entry.findtext("atom:published", "", ns) or ""
            authors_els = entry.findall("atom:author/atom:name", ns)
            authors = ", ".join(el.text or "" for el in authors_els[:3])
            entries.append({
                "title": title,
                "abstract": abstract[:300],
                "url": link,
                "published": published[:10],
                "authors": authors,
                "type": "paper",
            })
        return entries

    def _fetch_semantic_scholar(self, query: str) -> list[dict]:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "fields": "title,abstract,authors,year,venue,externalIds",
            "limit": 10,
        }
        headers = {}
        ss_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
        if ss_key:
            headers["x-api-key"] = ss_key

        response = requests.get(url, params=params, headers=headers, timeout=30)
        if not response.ok:
            return []

        papers = response.json().get("data", [])
        entries = []
        for p in papers:
            if not any(conf in (p.get("venue") or "") for conf in SEMANTIC_SCHOLAR_CONFERENCES):
                if (p.get("year") or 0) < 2020:
                    continue
            doi = (p.get("externalIds") or {}).get("DOI", "")
            entries.append({
                "title": p.get("title", ""),
                "abstract": (p.get("abstract") or "")[:300],
                "url": f"https://doi.org/{doi}" if doi else "",
                "published": str(p.get("year", "")),
                "authors": ", ".join(a["name"] for a in (p.get("authors") or [])[:3]),
                "type": "paper",
            })
        return [e for e in entries if e["title"]]

    def _fetch_rss(self, name: str, url: str) -> list[dict]:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        entries = []
        for item in root.findall(".//item")[:10]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            description = re.sub(r"<[^>]+>", "", item.findtext("description") or "")[:300]
            pub_date = (item.findtext("pubDate") or "")[:10]
            entries.append({
                "title": title,
                "abstract": description,
                "url": link,
                "published": pub_date,
                "authors": name,
                "type": "blog",
            })
        return entries

    def _fetch_github_releases(self, repo: str) -> list[dict]:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.github_token:
            headers["Authorization"] = f"token {self.github_token}"
        url = f"https://api.github.com/repos/{repo}/releases"
        response = requests.get(url, headers=headers, timeout=15)
        if not response.ok:
            return []
        releases = response.json()[:5]
        entries = []
        for r in releases:
            entries.append({
                "title": f"Ctrlplane Release {r.get('tag_name', '')}",
                "abstract": (r.get("body") or "")[:300],
                "url": r.get("html_url", ""),
                "published": (r.get("published_at") or "")[:10],
                "authors": "ctrlplanedev",
                "type": "release",
            })
        return entries

    # ── Dedup + append ────────────────────────────────────────────────────────

    def _is_relevant(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in ARXIV_KEYWORDS)

    def _add_if_new(self, entry: dict, source: str) -> None:
        url = entry.get("url", entry.get("title", ""))
        entry_hash = hashlib.sha256(url.encode()).hexdigest()[:16]

        already_seen = False
        if self.memory:
            already_seen = self.memory.has_knowledge_hash(entry_hash)
        else:
            brain_text = SECOND_KNOWLEDGE_BRAIN_PATH.read_text(encoding="utf-8") if SECOND_KNOWLEDGE_BRAIN_PATH.exists() else ""
            already_seen = url in brain_text or entry.get("title", "") in brain_text

        if not already_seen:
            entry["hash"] = entry_hash
            entry["source"] = source
            self._new_entries.append(entry)
            if self.memory:
                self.memory.add_knowledge_hash(entry_hash, source, entry.get("title", ""))

    def _append_to_knowledge_brain(self, entries: list[dict]) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        lines = [f"\n\n---\n\n## Knowledge Update Log — {today}\n"]
        for e in entries:
            entry_type = e.get("type", "resource")
            lines.append(
                f"- **[{entry_type.upper()}]** [{e['title']}]({e['url']}) "
                f"— {e['authors']}, {e['published']}. "
                f"{e.get('abstract', '')[:150]}..."
            )
        update_block = "\n".join(lines)

        if SECOND_KNOWLEDGE_BRAIN_PATH.exists():
            existing = SECOND_KNOWLEDGE_BRAIN_PATH.read_text(encoding="utf-8")
            SECOND_KNOWLEDGE_BRAIN_PATH.write_text(existing + update_block, encoding="utf-8")
        else:
            SECOND_KNOWLEDGE_BRAIN_PATH.write_text(update_block, encoding="utf-8")


def schedule_weekly_update() -> None:
    """Register weekly Sunday 02:00 crawl via APScheduler."""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler()

        def run_update():
            updater = KnowledgeUpdater()
            asyncio.run(updater.run_update())

        scheduler.add_job(run_update, "cron", day_of_week="sun", hour=2, minute=0)
        scheduler.start()
        logger.info("Knowledge updater scheduled: weekly Sunday 02:00")
    except ImportError:
        logger.warning("APScheduler not available; weekly schedule not registered")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    updater = KnowledgeUpdater()
    added = asyncio.run(updater.run_update())
    print(f"Knowledge update complete: {added} new entries added.")
