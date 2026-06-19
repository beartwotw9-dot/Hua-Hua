#!/usr/bin/env python3
"""Fetch a daily research digest aligned to DoraOS research directions."""

from __future__ import annotations

import argparse
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, List
from urllib.error import HTTPError, URLError

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, clip_text, ensure_dir, load_env_file, normalize_space, read_json, require_env, retry_call, today_stamp, wait_for_network, write_json


ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_RSS = "https://rss.arxiv.org/rss"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"
STATE_PATH = Path(__file__).resolve().parents[1] / "operating" / "research_digest_state.json"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config" / "research_digest.config.example.json"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
AUTO_START = "<!-- DORAOS_AUTO_PAPER_START -->"
AUTO_END = "<!-- DORAOS_AUTO_PAPER_END -->"


def _retry_settings(config: Dict[str, str]) -> tuple[int, float]:
    retries = int(config.get("SYNC_RETRY_ATTEMPTS", "") or "3")
    delay = float(config.get("SYNC_RETRY_BACKOFF_SECONDS", "") or "2.0")
    return retries, delay


def _is_retryable_network_error(exc: Exception) -> bool:
    if isinstance(exc, HTTPError) and exc.code == 429:
        return False
    if isinstance(exc, (HTTPError, URLError, TimeoutError)):
        return True
    lowered = str(exc).lower()
    markers = [
        "failed to resolve",
        "nodename nor servname provided",
        "temporarily unavailable",
        "connection aborted",
        "remote end closed connection without response",
        "remotedisconnected",
        "max retries exceeded",
        "name resolution",
        "timed out",
        "timeout",
        "connection reset",
    ]
    return any(marker in lowered for marker in markers)


def _is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, HTTPError) and exc.code == 429


@dataclass
class TopicConfig:
    name: str
    query: str
    rss_categories: List[str]
    must_match_any: List[str]
    exclude_if_any: List[str]


@dataclass
class PaperItem:
    paper_id: str
    title: str
    summary: str
    published: str
    updated: str
    url: str
    topic: str
    matched_terms: List[str]


@dataclass
class ExistingNoteItem:
    title: str
    topic: str
    updated: str
    summary: str
    note_path: str
    difficulty: str
    matched_terms: List[str]


@dataclass
class RankedRecommendation:
    label_zh: str
    label_en: str
    title: str
    reason_zh: str
    reason_en: str
    note_ref: str


def classify_difficulty(item: PaperItem) -> str:
    text = f"{item.title} {item.summary}".lower()
    hard_markers = [
        "theorem",
        "regret bound",
        "minimax",
        "optimization",
        "bayesian",
        "neurosymbolic",
        "reinforcement learning",
        "variance-aware",
        "benchmark",
    ]
    gentle_markers = [
        "review",
        "survey",
        "framework",
        "design",
        "case study",
        "qualitative",
        "interview",
        "accessibility",
        "learning",
        "workplace",
        "older adult",
        "adult learning",
        "educational",
    ]
    if any(marker in text for marker in hard_markers):
        return "進階 | Advanced"
    if any(marker in text for marker in gentle_markers):
        return "入門 | Gentle"
    return "中階 | Intermediate"


def topic_priority(topic: str) -> int:
    if topic.startswith("方向一"):
        return 0
    if topic.startswith("方向二"):
        return 1
    if topic.startswith("方向三"):
        return 2
    if topic.startswith("心理學理論"):
        return 3
    if topic.startswith("方向四"):
        return 4
    return 9


def difficulty_rank(label: str) -> int:
    if label.startswith("入門"):
        return 0
    if label.startswith("中階"):
        return 1
    return 2


def _paper_text(item: PaperItem) -> str:
    return " ".join([item.title, item.summary, item.topic, " ".join(item.matched_terms)]).lower()


def _note_text(item: ExistingNoteItem) -> str:
    return " ".join([item.title, item.summary, item.topic, " ".join(item.matched_terms)]).lower()


def _score_keywords(text: str, keywords: list[str]) -> int:
    return sum(1 for keyword in keywords if keyword in text)


def _score_thesis_match(text: str) -> int:
    return _score_keywords(
        text,
        [
            "adhd",
            "neurodivergent",
            "neurodiversity",
            "autistic",
            "autism",
            "student",
            "students",
            "higher education",
            "college",
            "university",
            "generative ai",
            "chatgpt",
            "assistant",
            "learning support",
            "executive dysfunction",
            "attention",
            "emotion regulation",
        ],
    )


def _score_gap_signal(text: str) -> int:
    return _score_keywords(text, ["gap", "underexplored", "limited", "few", "lack", "less", "rarely", "support", "daily", "everyday", "well-being", "stress"])


def _score_rq_signal(text: str) -> int:
    return _score_keywords(text, ["question", "experience", "perception", "acceptance", "use", "interaction", "adoption", "human-ai", "assistant", "students"])


def _score_method_signal(text: str) -> int:
    return _score_keywords(text, ["survey", "questionnaire", "scale", "inventory", "interview", "qualitative", "mixed-method", "mixed method", "experiment", "participants", "validated", "measurement", "model"])


def _score_contribution_signal(text: str) -> int:
    return _score_keywords(text, ["framework", "contribution", "implication", "design", "evaluation", "effectiveness", "outcome", "support", "scaffolding", "acceptance"])


def _best_index(scores: list[tuple[int, int, int]]) -> int:
    ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
    return ranked[0][0]


def build_ranked_recommendations(
    surfaced_items: List[PaperItem],
    fallback_items: List[ExistingNoteItem],
    today: str,
) -> List[RankedRecommendation]:
    fresh_candidates: list[dict[str, str]] = []
    for item in surfaced_items:
        fresh_candidates.append(
            {
                "title": item.title,
                "text": _paper_text(item),
                "difficulty": classify_difficulty(item),
                "note_ref": f"[[Resources/Research Sources/Auto Papers/{_safe_name(item.topic)}/{today} {_paper_slug(item)} - {_safe_name(item.title)}]]",
            }
        )
    fallback_candidates: list[dict[str, str]] = []
    for item in fallback_items:
        fallback_candidates.append(
            {
                "title": item.title,
                "text": _note_text(item),
                "difficulty": item.difficulty,
                "note_ref": f"[[{item.note_path}]]",
            }
        )
    candidates = fresh_candidates or fallback_candidates
    if not candidates:
        return []

    thesis_scores = [(_score_thesis_match(c["text"]), -difficulty_rank(c["difficulty"]), len(c["title"])) for c in candidates]
    read_scores = [(-difficulty_rank(c["difficulty"]), _score_thesis_match(c["text"]), _score_method_signal(c["text"])) for c in candidates]
    gap_scores = [(_score_gap_signal(c["text"]), _score_thesis_match(c["text"]), -difficulty_rank(c["difficulty"])) for c in candidates]
    rq_scores = [(_score_rq_signal(c["text"]), _score_thesis_match(c["text"]), -difficulty_rank(c["difficulty"])) for c in candidates]
    method_scores = [(_score_method_signal(c["text"]), _score_thesis_match(c["text"]), -difficulty_rank(c["difficulty"])) for c in candidates]
    contribution_scores = [(_score_contribution_signal(c["text"]), _score_thesis_match(c["text"]), -difficulty_rank(c["difficulty"])) for c in candidates]

    def _rec(index: int, label_zh: str, label_en: str, reason_zh: str, reason_en: str) -> RankedRecommendation:
        candidate = candidates[index]
        return RankedRecommendation(
            label_zh=label_zh,
            label_en=label_en,
            title=candidate["title"],
            reason_zh=reason_zh,
            reason_en=reason_en,
            note_ref=candidate["note_ref"],
        )

    return [
        _rec(_best_index(thesis_scores), "最像你的 thesis", "Best thesis match", "這篇和你的題目核心最接近，最適合先拿來補理論、變項與研究定位。", "This one is closest to your thesis and is the best starting point for theory, variables, and positioning."),
        _rec(_best_index(read_scores), "最值得先讀", "Best next read", "這篇相對比較容易入口，又足夠貼近你的主題，最適合今天先讀。", "This one is the easiest strong match to read next without creating extra pressure."),
        _rec(_best_index(gap_scores), "最可能補 GAP", "Best for GAP", "這篇最適合幫你看現有研究已經做了什麼、還缺了什麼。", "This one is most helpful for spotting what the literature already covers and what is still missing."),
        _rec(_best_index(rq_scores), "最可能補 RQ", "Best for RQ", "這篇最適合幫你把研究問題講得更清楚、更像一篇論文問題。", "This one is best for sharpening your research question into something thesis-ready."),
        _rec(_best_index(method_scores), "最可能補 METH", "Best for METH", "這篇最可能提供你可借鏡的量表、樣本、方法或分析設計。", "This one is most likely to offer reusable methods, scales, samples, or analysis ideas."),
        _rec(_best_index(contribution_scores), "最可能補 CONTRI", "Best for CONTRI", "這篇最適合幫你想清楚這個題目能帶來什麼理論或實務貢獻。", "This one is most useful for clarifying the theoretical or practical contribution of your thesis."),
    ]


def select_surface_items(items: List[PaperItem], daily_limit: int) -> List[PaperItem]:
    ranked = sorted(
        items,
        key=lambda item: (
            topic_priority(item.topic),
            difficulty_rank(classify_difficulty(item)),
            -len(item.matched_terms),
            item.title.lower(),
        ),
    )
    return ranked[:daily_limit]


def select_surface_existing_notes(items: List[ExistingNoteItem], daily_limit: int) -> List[ExistingNoteItem]:
    ranked = sorted(
        items,
        key=lambda item: (
            topic_priority(item.topic),
            difficulty_rank(item.difficulty),
            item.title.lower(),
        ),
    )
    return ranked[:daily_limit]


def _topic_match_terms(topic: TopicConfig | None, text: str) -> List[str]:
    if not topic or not topic.must_match_any:
        return []
    haystack = text.lower()
    return [term for term in topic.must_match_any if term in haystack]


def _topic_excluded(topic: TopicConfig | None, text: str) -> bool:
    if not topic or not topic.exclude_if_any:
        return False
    haystack = text.lower()
    return any(term in haystack for term in topic.exclude_if_any)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|#]+', "-", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .-")
    return cleaned or "Untitled Paper"


def _paper_slug(item: PaperItem) -> str:
    source = item.url.rstrip("/").split("/")[-1] or item.title
    source = re.sub(r"[^A-Za-z0-9._-]+", "-", source)
    return source.strip("-") or "paper"


def _parse_frontmatter_value(lines: List[str], key: str) -> str:
    prefix = f"{key}:"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped[len(prefix):].strip().strip('"')
    return ""


def _extract_note_summary(text: str) -> str:
    marker = "## 一句話判讀"
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1]
    for line in tail.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            return stripped[2:].strip()
    return ""


def load_existing_note_items(vault_path: Path, output_subdir: str, topic_configs: Dict[str, TopicConfig]) -> List[ExistingNoteItem]:
    root = vault_path / output_subdir
    if not root.exists():
        return []
    items: List[ExistingNoteItem] = []
    for path in root.rglob("*.md"):
        if "Archive" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        title = _parse_frontmatter_value(lines, "title") or path.stem
        topic = _parse_frontmatter_value(lines, "topic") or path.parent.name
        updated = _parse_frontmatter_value(lines, "updated") or path.stat().st_mtime_ns.__str__()
        summary = _extract_note_summary(text) or ""
        topic_config = topic_configs.get(topic)
        if not topic_config:
            continue
        matched_terms = _topic_match_terms(topic_config, f"{title} {summary}")
        if topic_config.must_match_any and not matched_terms:
            continue
        difficulty = classify_difficulty(PaperItem("", title, summary, "", updated, "", topic, []))
        items.append(
            ExistingNoteItem(
                title=title,
                topic=topic,
                updated=updated,
                summary=summary,
                note_path=path.relative_to(vault_path).with_suffix("").as_posix(),
                difficulty=difficulty,
                matched_terms=matched_terms,
            )
        )
    return items


def load_digest_config(path: Path) -> Dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"Research digest config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def parse_topics(payload: Dict[str, object]) -> List[TopicConfig]:
    topics: List[TopicConfig] = []
    for raw in payload.get("topics", []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "")).strip()
        query = str(raw.get("query", "")).strip()
        if name and query:
            rss_categories = [str(value).strip() for value in raw.get("rss_categories", []) if str(value).strip()]
            must_match_any = [str(value).strip().lower() for value in raw.get("must_match_any", []) if str(value).strip()]
            exclude_if_any = [str(value).strip().lower() for value in raw.get("exclude_if_any", []) if str(value).strip()]
            topics.append(
                TopicConfig(
                    name=name,
                    query=query,
                    rss_categories=rss_categories,
                    must_match_any=must_match_any,
                    exclude_if_any=exclude_if_any,
                )
            )
    return topics


def arxiv_search(query: str, max_results: int) -> bytes:
    params = {
        "search_query": f"all:{query}",
        "start": "0",
        "max_results": str(max_results),
        "sortBy": "lastUpdatedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "DoraOS-Research-Digest/0.1"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def arxiv_rss_fetch(category: str) -> bytes:
    url = f"{ARXIV_RSS}/{urllib.parse.quote(category)}"
    req = urllib.request.Request(url, headers={"User-Agent": "DoraOS-Research-Digest/0.1"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def semantic_scholar_search(query: str, max_results: int, api_key: str = "") -> bytes:
    """Search Semantic Scholar graph API.

    Uses the /graph/v1/paper/search endpoint with relevance-based ranking.
    If *api_key* is provided (via SEMANTIC_SCHOLAR_API_KEY env var), the rate
    limit is 100 req/s instead of 1 req/s — strongly recommended for
    multi-topic daily runs.

    Query format: quoted phrases ("ADHD students") and OR/AND operators are
    supported.  We strip outer boolean noise that confuses the endpoint when
    every word is an operator keyword.
    """
    # Strip leading/trailing OR/AND that can appear when topics have many alternatives
    clean_query = re.sub(r"^\s*(OR|AND)\s+", "", query.strip(), flags=re.I)
    clean_query = re.sub(r"\s+(OR|AND)\s*$", "", clean_query, flags=re.I)
    params = {
        "query": clean_query,
        "limit": str(max_results),
        "fields": "title,abstract,url,year,publicationDate,externalIds",
    }
    url = f"{SEMANTIC_SCHOLAR_API}?{urllib.parse.urlencode(params)}"
    headers: Dict[str, str] = {"User-Agent": "DoraOS-Research-Digest/0.1"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def _extract_terms(query: str) -> List[str]:
    quoted = re.findall(r'"([^"]+)"', query)
    query_without_quoted = re.sub(r'"[^"]+"', " ", query)
    bare = re.findall(r"\b[A-Za-z][A-Za-z0-9-]{2,}\b", query_without_quoted)
    terms: List[str] = []
    broad_terms = {
        "learning",
        "adult",
        "adults",
        "user",
        "users",
        "behavior",
        "behaviour",
        "product",
        "products",
        "technology",
        "tools",
        "design",
        "digital",
        "older",
        "outcomes",
    }
    for item in quoted + bare:
        lowered = item.strip().lower()
        if lowered in {"and", "or", "not", "all", "the"}:
            continue
        if lowered in broad_terms:
            continue
        if lowered not in terms:
            terms.append(lowered)
    return terms[:8]


def parse_arxiv_feed(topic: TopicConfig, xml_bytes: bytes, *, days_back: int) -> List[PaperItem]:
    root = ET.fromstring(xml_bytes)
    terms = _extract_terms(topic.query)
    threshold = datetime.now(UTC) - timedelta(days=days_back)
    items: List[PaperItem] = []
    for entry in root.findall("atom:entry", ATOM_NS):
        paper_id = (entry.findtext("atom:id", default="", namespaces=ATOM_NS) or "").strip()
        title = " ".join((entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").split())
        summary = " ".join((entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").split())
        published = (entry.findtext("atom:published", default="", namespaces=ATOM_NS) or "").strip()
        updated = (entry.findtext("atom:updated", default="", namespaces=ATOM_NS) or "").strip()
        updated_dt = None
        try:
            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            updated_dt = None
        if updated_dt and updated_dt < threshold:
            continue
        match_source = f"{title} {summary}".lower()
        if _topic_excluded(topic, match_source):
            continue
        matched_terms = [term for term in terms if term in match_source]
        if terms and not matched_terms:
            continue
        matched_topic_terms = _topic_match_terms(topic, f"{title} {summary}")
        if topic.must_match_any and not matched_topic_terms:
            continue
        items.append(
            PaperItem(
                paper_id=paper_id,
                title=title or "(untitled)",
                summary=summary or "(no summary)",
                published=published,
                updated=updated,
                url=paper_id,
                topic=topic.name,
                matched_terms=matched_topic_terms or matched_terms,
            )
        )
    return items


def parse_arxiv_rss(topic: TopicConfig, xml_bytes: bytes, *, days_back: int) -> List[PaperItem]:
    root = ET.fromstring(xml_bytes)
    terms = _extract_terms(topic.query)
    threshold = datetime.now(UTC) - timedelta(days=days_back)
    items: List[PaperItem] = []
    for item in root.findall("./channel/item"):
        title = " ".join((item.findtext("title", default="") or "").split())
        summary = " ".join((item.findtext("description", default="") or "").split())
        link = (item.findtext("link", default="") or "").strip()
        pub_date = (item.findtext("pubDate", default="") or "").strip()
        updated = pub_date
        updated_dt = None
        try:
            updated_dt = parsedate_to_datetime(pub_date).astimezone(UTC)
        except Exception:
            updated_dt = None
        if updated_dt and updated_dt < threshold:
            continue
        match_source = f"{title} {summary}".lower()
        if _topic_excluded(topic, match_source):
            continue
        matched_terms = [term for term in terms if term in match_source]
        if terms and not matched_terms:
            continue
        matched_topic_terms = _topic_match_terms(topic, f"{title} {summary}")
        if topic.must_match_any and not matched_topic_terms:
            continue
        paper_id = link or title
        items.append(
            PaperItem(
                paper_id=paper_id,
                title=title or "(untitled)",
                summary=summary or "(no summary)",
                published=pub_date,
                updated=updated,
                url=link,
                topic=topic.name,
                matched_terms=matched_topic_terms or matched_terms,
            )
        )
    return items


def parse_semantic_scholar(topic: TopicConfig, payload: bytes, *, days_back: int) -> List[PaperItem]:
    data = json.loads(payload.decode("utf-8"))
    threshold = datetime.now(UTC) - timedelta(days=days_back)
    items: List[PaperItem] = []
    for raw in data.get("data", []) or []:
        title = normalize_space(str(raw.get("title", "") or ""))
        summary = normalize_space(str(raw.get("abstract", "") or ""))
        url = str(raw.get("url", "") or "")
        published = str(raw.get("publicationDate", "") or raw.get("year", "") or "")
        updated = published
        updated_dt = None
        try:
            if published and len(published) == 4:
                updated_dt = datetime(int(published), 1, 1, tzinfo=UTC)
            elif published:
                updated_dt = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            updated_dt = None
        if updated_dt and updated_dt < threshold:
            continue
        match_source = f"{title} {summary}".lower()
        if _topic_excluded(topic, match_source):
            continue
        matched_topic_terms = _topic_match_terms(topic, f"{title} {summary}")
        terms = _extract_terms(topic.query)
        matched_terms = [term for term in terms if term in match_source]
        if terms and not matched_terms and topic.must_match_any and not matched_topic_terms:
            continue
        if topic.must_match_any and not matched_topic_terms:
            continue
        # Prefer a stable Semantic Scholar paper ID if available
        ss_paper_id = str(raw.get("paperId", "") or "")
        doi = ""
        ext_ids = raw.get("externalIds") or {}
        if isinstance(ext_ids, dict):
            doi = str(ext_ids.get("DOI", "") or "")
        paper_id = ss_paper_id or doi or url or title
        items.append(
            PaperItem(
                paper_id=paper_id,
                title=title or "(untitled)",
                summary=summary or "(no abstract)",
                published=published,
                updated=updated,
                url=url,
                topic=topic.name,
                matched_terms=matched_topic_terms or matched_terms,
            )
        )
    return items


def dedupe_items(items: List[PaperItem]) -> List[PaperItem]:
    seen: set[str] = set()
    out: List[PaperItem] = []
    for item in items:
        key = item.paper_id or item.title.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def fetch_topic_items(
    topic: TopicConfig,
    max_results: int,
    days_back: int,
    logger,
    *,
    source: str,
    retries: int,
    retry_delay: float,
    scholar_api_key: str = "",
) -> tuple[List[PaperItem], bool]:
    source = source.strip().lower()
    if source in {"semantic_scholar", "scholar", "mixed"}:
        try:
            scholar_payload = retry_call(
                lambda: semantic_scholar_search(topic.query, max_results=max_results, api_key=scholar_api_key),
                retries=retries,
                base_delay=retry_delay,
                should_retry=_is_retryable_network_error,
                on_retry=lambda exc, attempt, wait: logger.warning("Semantic Scholar retry %s for %s after error: %s", attempt, topic.name, exc),
            )
            scholar_items = parse_semantic_scholar(topic, scholar_payload, days_back=days_back)
            if scholar_items:
                return scholar_items, False
        except Exception as exc:
            if not _is_retryable_network_error(exc) and not _is_rate_limited(exc):
                raise
            if _is_rate_limited(exc):
                logger.warning("Semantic Scholar rate-limited for %s; falling back to arXiv/RSS.", topic.name)
            else:
                logger.warning("Semantic Scholar fetch failed for %s: %s", topic.name, exc)

    try:
        xml_payload = retry_call(
            lambda: arxiv_search(topic.query, max_results=max_results),
            retries=retries,
            base_delay=retry_delay,
            should_retry=_is_retryable_network_error,
            on_retry=lambda exc, attempt, wait: logger.warning("Research API retry %s for %s after error: %s", attempt, topic.name, exc),
        )
        api_items = parse_arxiv_feed(topic, xml_payload, days_back=days_back)
        if api_items:
            return api_items, False
    except Exception as exc:
        if not _is_retryable_network_error(exc):
            raise
        logger.warning("API fetch failed for %s: %s", topic.name, exc)

    rss_items: List[PaperItem] = []
    rss_failed = False
    for category in topic.rss_categories:
        try:
            rss_payload = retry_call(
                lambda category=category: arxiv_rss_fetch(category),
                retries=retries,
                base_delay=retry_delay,
                should_retry=_is_retryable_network_error,
                on_retry=lambda rss_exc, attempt, wait, category=category: logger.warning(
                    "Research RSS retry %s for %s category %s after error: %s",
                    attempt,
                    topic.name,
                    category,
                    rss_exc,
                ),
            )
            rss_items.extend(parse_arxiv_rss(topic, rss_payload, days_back=days_back))
        except Exception as rss_exc:
            rss_failed = True
            logger.warning("RSS fetch failed for %s category %s: %s", topic.name, category, rss_exc)
    return rss_items, True if rss_failed or not rss_items else False


def build_digest(
    all_items: List[PaperItem],
    surfaced_items: List[PaperItem],
    fallback_items: List[ExistingNoteItem],
    generated_at: str,
    today: str,
    state: Dict[str, object],
    *,
    fetch_failed: bool = False,
) -> str:
    lines = [
        "# 研究摘要 | Research Digest",
        "",
        f"- 生成時間 | generated: {generated_at}",
        f"- 日期 | date: {today}",
        f"- 狀態 | status: {'fallback' if fetch_failed else 'live'}",
        f"- 收集到的論文 | collected papers: {len(all_items)}",
        f"- 今天實際推送 | surfaced today: {len(surfaced_items)}",
        "",
        "## 規則 | Rule",
        "",
        "- 這是一個閱讀雷達，不是強制閱讀清單 | this is a reading radar, not a mandatory reading list",
        "- 只升級你真的在意的論文 | only promote papers you actually care about",
        "- 如果某篇真的重要，再把它整理成正式研究筆記 | if something matters, convert it into a real research note later",
        "- 系統會多收集、少打擾，先給你比較容易入口的內容 | the system collects broadly but surfaces gently, starting with easier-entry papers",
        "",
    ]

    if fetch_failed and all_items:
        lines += [
            "## 低壓備援 | Low-Pressure Fallback",
            "",
            "- 今天有些 topic 的 live fetch 失敗，所以這份 digest 混合了 live 結果與 arXiv/RSS 備援，不要把它當成完整雷達。 | Some topic fetches failed today, so this digest mixes live results with arXiv/RSS fallback and should not be treated as a full radar.",
            "- 如果注意力不適合再開新分頁，先回 thesis mapping 工作台也算有效前進。 | If opening new reading loops feels costly today, staying in the thesis mapping workspace is still valid progress.",
            "",
            "## Thesis 工作台 | Thesis Workspace",
            "",
            "- [[Resources/Research Sources/Master Thesis/Master Thesis Literature Mapping]]",
            "- 可以先補文獻表、變項、量表、研究缺口與概念圖，不用勉強追完整批新論文。 | You can work on the literature table, variables, scales, research gaps, and concept map instead of forcing a full paper sweep.",
            "",
        ]

    if fetch_failed and not all_items:
        lines += [
            "## 今天 | Today",
            "",
            "- 今天的研究抓取失敗，所以先不要硬找新東西 | Today's research fetch failed, so do not force new reading.",
            "- 先改做 thesis mapping 整理也算有效前進 | Thesis mapping is still meaningful progress for today.",
            "",
            "## Thesis 工作台 | Thesis Workspace",
            "",
            "- [[Resources/Research Sources/Master Thesis/Master Thesis Literature Mapping]]",
            "- 你可以先補：文獻表、量表、變項、研究缺口、概念圖 | You can work on the literature table, scales, variables, gaps, and concept map first.",
            "",
            "## 訊號 | Signal",
            "",
            "- 今日新進候選 | newly collected today: 0",
            "- 狀態 | status: low-pressure fallback",
            "- 先維持 thesis mapping 與概念整理，不要被背景筆記分心 | Stay with thesis mapping and concept framing instead of bouncing into backlog notes.",
            "",
        ]
        return "\n".join(lines).rstrip() + "\n"

    if not all_items and not fallback_items:
        lines += [
            "## 今天 | Today",
            "",
            "- 目前時間窗內沒有找到新的符合論文 | No new matched papers found in the current time window.",
            "- 先不要硬找新東西，改做 thesis mapping 整理也算有效前進 | Do not force new reading today; thesis mapping is still meaningful progress.",
            "",
            "## Thesis 工作台 | Thesis Workspace",
            "",
            "- [[Resources/Research Sources/Master Thesis/Master Thesis Literature Mapping]]",
            "- 你可以先補：文獻表、量表、變項、研究缺口、概念圖 | You can work on the literature table, scales, variables, gaps, and concept map first.",
            "",
        ]
        return "\n".join(lines).rstrip() + "\n"

    if not all_items and fallback_items:
        ranked_recs = build_ranked_recommendations([], fallback_items, today)
        lines += [
            "## 今天沒有新論文，但這裡有可先讀的舊筆記 | No New Papers Today, Start With These",
            "",
            "- 今天沒有抓到新的符合論文，所以系統改為推薦背景資料庫裡較容易入口的內容 | No newly matched papers were collected today, so the system is recommending gentler backlog notes from your background library.",
            "- 不需要全部看完，先選 1 篇就好 | You do not need to finish everything; pick just one.",
            "",
        ]
        if ranked_recs:
            lines += [
                "## Scholar-like 排序 | Scholar-like Ranking",
                "",
            ]
            for rec in ranked_recs:
                lines += [
                    f"### {rec.label_zh} | {rec.label_en}",
                    f"- 論文 | paper: {rec.title}",
                    f"- 理由 | why: {rec.reason_zh}",
                    f"- EN: {rec.reason_en}",
                    f"- 筆記 | note: {rec.note_ref}",
                    "",
                ]
        grouped_existing: Dict[str, List[ExistingNoteItem]] = {}
        for item in fallback_items:
            grouped_existing.setdefault(item.topic, []).append(item)
        for topic, topic_items in grouped_existing.items():
            lines += [
                f"### {topic}",
                "",
            ]
            for item in topic_items:
                lines += [
                    f"#### {item.title}",
                    f"- 難度 | difficulty: {item.difficulty}",
                    f"- 更新時間 | updated: {item.updated or '(unknown)'}",
                    f"- 命中主題 | matched topic terms: {', '.join(item.matched_terms) if item.matched_terms else '(backlog)'}",
                    f"- 筆記 | note: [[{item.note_path}]]",
                    f"- 摘要 | summary: {clip_text(item.summary or '(no summary)', 360)}",
                    "",
                ]
        lines += [
            "## 背景資料庫概況 | Background Queue Snapshot",
            "",
            f"- 今天新進候選 0 篇 | 0 new candidates collected today.",
            f"- 改為推薦背景資料庫中的 {len(fallback_items)} 篇 | Surfacing {len(fallback_items)} backlog notes instead.",
            "",
            "## Thesis 工作台 | Thesis Workspace",
            "",
            "- [[Resources/Research Sources/Master Thesis/Master Thesis Literature Mapping]]",
            "- 如果今天不想讀新論文，可以改做文獻對照表與概念整理 | If today is not a reading day, work on the literature mapping table and concept framing instead.",
            "",
            "## 訊號 | Signal",
            "",
            "- 今日新進候選 | newly collected today: 0",
            "- 今天先維持低壓閱讀模式，選 1 篇就好 | Stay in low-pressure reading mode today; choose just one.",
            "",
        ]
        return "\n".join(lines).rstrip() + "\n"

    grouped: Dict[str, List[PaperItem]] = {}
    for item in surfaced_items:
        grouped.setdefault(item.topic, []).append(item)
    ranked_recs = build_ranked_recommendations(surfaced_items, [], today)

    lines += [
        "## 今天先看這些 | Start Here Today",
        "",
        f"- 今天總共抓到 {len(all_items)} 篇，但只先推給你 {len(surfaced_items)} 篇 | Collected {len(all_items)} papers today, but only surfaced {len(surfaced_items)} for you first.",
        "- 如果今天精神普通，只看入門項目就好 | If your energy is average today, only read the gentle items.",
        "- 其他內容先留在背景資料庫，不需要一次看完 | The rest stays in the background database; you do not need to finish everything at once.",
        "",
    ]
    if ranked_recs:
        lines += [
            "## Scholar-like 排序 | Scholar-like Ranking",
            "",
        ]
        for rec in ranked_recs:
            lines += [
                f"### {rec.label_zh} | {rec.label_en}",
                f"- 論文 | paper: {rec.title}",
                f"- 理由 | why: {rec.reason_zh}",
                f"- EN: {rec.reason_en}",
                f"- 筆記 | note: {rec.note_ref}",
                "",
            ]

    lines += [
        "## 今日推薦 | Today's Picks",
        "",
    ]
    for topic, topic_items in grouped.items():
        lines += [
            f"### {topic}",
            "",
        ]
        for item in topic_items:
            matched = ", ".join(item.matched_terms) if item.matched_terms else "broad match"
            difficulty = classify_difficulty(item)
            lines += [
                f"#### {item.title}",
                f"- 難度 | difficulty: {difficulty}",
                f"- 更新時間 | updated: {item.updated or item.published or '(unknown)'}",
                f"- 來源 | source: {item.url}",
                f"- 為什麼可能重要 | why it may matter: matched `{matched}`",
                f"- 筆記 | note: [[Resources/Research Sources/Auto Papers/{_safe_name(item.topic)}/{today} {_paper_slug(item)} - {_safe_name(item.title)}]]",
                f"- 摘要 | summary: {clip_text(item.summary, 360)}",
                "",
            ]

    backlog_by_topic: Dict[str, int] = {}
    for item in all_items:
        backlog_by_topic[item.topic] = backlog_by_topic.get(item.topic, 0) + 1

    lines += [
        "## 背景資料庫概況 | Background Queue Snapshot",
        "",
        f"- 今天背景資料庫新增候選 {len(all_items)} 篇 | {len(all_items)} candidates entered the background queue today.",
        f"- 今天先推送 {len(surfaced_items)} 篇 | surfaced {len(surfaced_items)} today first.",
        f"- 其餘暫不打擾：{max(len(all_items) - len(surfaced_items), 0)} 篇 | held back for later: {max(len(all_items) - len(surfaced_items), 0)}.",
        "",
    ]
    for topic, count in sorted(backlog_by_topic.items(), key=lambda pair: (topic_priority(pair[0]), pair[0])):
        lines.append(f"- {topic}: {count} 篇 | {count} papers")
    lines.append("")

    previous_seen = set(state.get("seen_ids", []))
    new_ids = [item.paper_id for item in all_items if item.paper_id and item.paper_id not in previous_seen]
    lines += [
        "## 訊號 | Signal",
        "",
        f"- 今日新進候選 | newly collected today: {len(new_ids)}",
        "- 如果有哪篇真的重要，就把它升級成正式閱讀筆記 | if one paper looks important, turn it into a proper reading note",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def build_paper_note(item: PaperItem, today: str, generated_at: str) -> str:
    matched = "、".join(item.matched_terms) if item.matched_terms else "broad match"
    safe_title = item.title.replace('"', '\\"')
    lines = [
        "---",
        f'title: "{safe_title}"',
        "type: research_note",
        "source_type: academic_search",
        "status: inbox",
        f'topic: "{item.topic}"',
        f'source_url: "{item.url}"',
        f'published: "{item.published}"' if item.published else 'published: ""',
        f'updated: "{item.updated}"' if item.updated else 'updated: ""',
        "tags:",
        "  - research",
        "  - academic-search",
        "  - doraos",
        f"  - {item.topic.lower().replace(' ', '-')}",
        "---",
        "",
        f"# 研究卡：{item.title}",
        "",
        AUTO_START,
        "## 一句話判讀",
        "",
        f"- {clip_text(item.summary, 180)}",
        "",
        "## 為什麼今天浮上來",
        "",
        f"- 研究方向：{item.topic}",
        f"- 命中關鍵字：{matched}",
        f"- 更新時間：{item.updated or item.published or '未知'}",
        "",
        "## 來源",
        "",
        f"- search source：{item.url}",
        "",
        "## 摘要",
        "",
        item.summary or "(no summary)",
        "",
        "## 建議怎麼讀",
        "",
        "- 先判斷：這篇是在解哪個問題。",
        "- 再判斷：它和你現在的 DoraOS / agent / memory / RAG 研究到底有沒有關。",
        "- 如果值得追，再把真正的洞見寫進下方手動區塊。",
        "",
        "## Source Log",
        "",
        "- source: academic search auto capture",
        f"- generated_at: {generated_at}",
        f"- topic: {item.topic}",
        f"- matched_terms: {matched}",
        AUTO_END,
        "",
        "## 你的閱讀筆記",
        "",
        "- ",
        "",
    ]
    return "\n".join(lines)


def merge_with_existing(path: Path, content: str) -> str:
    if not path.exists():
        return content
    existing = path.read_text(encoding="utf-8")
    if AUTO_START in existing and AUTO_END in existing and AUTO_END in content:
        _, tail = existing.split(AUTO_END, 1)
        head, _ = content.split(AUTO_END, 1)
        return (head + AUTO_END + tail).rstrip() + "\n"
    return content


def write_paper_notes(vault_path: Path, today: str, generated_at: str, items: List[PaperItem], output_subdir: str, logger, dry_run: bool) -> int:
    count = 0
    for item in items:
        topic_dir = vault_path / output_subdir / _safe_name(item.topic)
        note_name = f"{today} {_paper_slug(item)} - {_safe_name(item.title)}.md"
        note_path = topic_dir / note_name
        content = merge_with_existing(note_path, build_paper_note(item, today, generated_at))
        if not dry_run:
            ensure_dir(topic_dir)
            note_path.write_text(content, encoding="utf-8")
        count += 1
    logger.info("%s auto paper notes %s", "Would write" if dry_run else "Wrote", count)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch a daily research digest for DoraOS.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = build_logger("doraos.research_digest", LOG_DIR / "dora_research_digest_sync.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    vault_path = Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()
    digest_config = load_digest_config(Path(args.config))
    if not digest_config.get("enabled", True):
        logger.info("Research digest disabled in config.")
        return 0

    topics = parse_topics(digest_config)
    topic_config_map = {topic.name: topic for topic in topics}
    max_results = int(digest_config.get("max_results_per_topic", 3))
    days_back = int(digest_config.get("days_back", 14))
    daily_surface_limit = int(digest_config.get("daily_surface_limit", 3))
    auto_capture_notes = bool(digest_config.get("auto_capture_notes", True))
    note_output_subdir = str(digest_config.get("note_output_subdir", "Resources/Research Sources/Auto Papers"))
    source = str(digest_config.get("source", "semantic_scholar"))
    retries, retry_delay = _retry_settings(config)

    network_timeout = float(config.get("NETWORK_WARMUP_TIMEOUT", "120") or "120")
    wait_for_network(timeout=network_timeout, logger=logger)

    today, _generated_at = today_stamp()
    state = read_json(STATE_PATH, {"seen_ids": []})

    scholar_api_key = config.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
    if scholar_api_key:
        logger.info("Using Semantic Scholar API key (higher rate limit active)")
    else:
        logger.info("No SEMANTIC_SCHOLAR_API_KEY set — using anonymous Semantic Scholar access (1 req/s limit)")

    collected: List[PaperItem] = []
    fetch_failed = False
    for topic in topics:
        logger.info("Fetching topic: %s", topic.name)
        topic_items, topic_failed = fetch_topic_items(
            topic,
            max_results=max_results,
            days_back=days_back,
            logger=logger,
            source=source,
            retries=retries,
            retry_delay=retry_delay,
            scholar_api_key=scholar_api_key,
        )
        collected.extend(topic_items)
        fetch_failed = fetch_failed or topic_failed

    if fetch_failed:
        logger.warning(
            "Research fetch partially/fully failed for this run. "
            "Collected %s items across %s topics. "
            "Fallback content will be used where available.",
            len(collected), len(topics),
        )
    else:
        logger.info("Research fetch complete. Collected %s raw items across %s topics.", len(collected), len(topics))
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    items = dedupe_items(collected)
    surfaced_items = select_surface_items(items, daily_surface_limit)
    existing_items = load_existing_note_items(vault_path, note_output_subdir, topic_config_map)
    fallback_items = select_surface_existing_notes(existing_items, daily_surface_limit)
    content = build_digest(items, surfaced_items, fallback_items, generated_at, today, state, fetch_failed=fetch_failed)
    output_dir = vault_path / "Resources" / "Research Sources" / "Daily Digests"
    output_path = output_dir / f"{today} Research Digest.md"

    if auto_capture_notes:
        write_paper_notes(vault_path, today, generated_at, surfaced_items, note_output_subdir, logger, args.dry_run)

    if args.dry_run:
        print(content)
        logger.info("Dry run complete for %s", output_path)
        return 0

    ensure_dir(output_dir)
    output_path.write_text(content, encoding="utf-8")
    state["seen_ids"] = dedupe_ids(list(state.get("seen_ids", [])) + [item.paper_id for item in items if item.paper_id])[-400:]
    state["last_run"] = generated_at
    write_json(STATE_PATH, state)
    logger.info("Wrote research digest to %s", output_path)
    print(output_path)
    return 0


def dedupe_ids(values: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
