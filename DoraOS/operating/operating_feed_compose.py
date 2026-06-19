#!/usr/bin/env python3
"""Compose a daily Operating Feed note from synced context files."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from common import DEFAULT_ENV_FILE, LOG_DIR, build_logger, ensure_dir, load_env_file, require_env, today_stamp


def _wiki_link(base_dir: Path, note_path: Path) -> str:
    rel = note_path.relative_to(base_dir).with_suffix("")
    return f"[[{rel.as_posix()}]]"


def _wiki_embed(base_dir: Path, note_path: Path, section: str | None = None) -> str:
    rel = note_path.relative_to(base_dir).with_suffix("")
    suffix = f"#{section}" if section else ""
    return f"![[{rel.as_posix()}{suffix}]]"


def _read_text(note_path: Path | None) -> str:
    if not note_path or not note_path.exists():
        return ""
    return note_path.read_text(encoding="utf-8", errors="ignore")


def _extract_heading_block(note_path: Path | None, heading: str | list[str]) -> str:
    text = _read_text(note_path)
    if not text:
        return ""
    headings = [heading] if isinstance(heading, str) else heading
    normalized = {item.strip() for item in headings}
    lines = text.splitlines()
    capture = False
    collected: list[str] = []
    for line in lines:
        if line.strip() in normalized:
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture:
            collected.append(line)
    return "\n".join(collected).strip()


def _markdownish_to_html(text: str) -> str:
    if not text.strip():
        return "<p class=\"muted\">No content.</p>"
    parts: list[str] = []
    in_list = False
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if in_list:
                parts.append("</ul>")
                in_list = False
            continue
        if stripped.startswith("### "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h4>{html.escape(stripped[4:])}</h4>")
            continue
        if stripped.startswith("## "):
            if in_list:
                parts.append("</ul>")
                in_list = False
            parts.append(f"<h3>{html.escape(stripped[3:])}</h3>")
            continue
        if stripped.startswith("- "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{html.escape(stripped[2:])}</li>")
            continue
        if in_list:
            parts.append("</ul>")
            in_list = False
        parts.append(f"<p>{html.escape(stripped)}</p>")
    if in_list:
        parts.append("</ul>")
    return "\n".join(parts)


def _extract_bullets(note_path: Path | None, heading: str | list[str]) -> list[str]:
    block = _extract_heading_block(note_path, heading)
    if not block:
        return []
    return [line.strip()[2:] for line in block.splitlines() if line.strip().startswith("- ")]


def _parse_gmail_messages(note_path: Path | None, limit: int = 4) -> list[dict[str, str]]:
    text = _read_text(note_path)
    if not text:
        return []
    lines = text.splitlines()
    in_section = False
    messages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in lines:
        line = raw.strip()
        if line == "## 郵件列表 | Messages" or line == "## Messages":
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section:
            continue
        if line.startswith("### "):
            if current:
                messages.append(current)
            current = {"subject": line[4:].strip(), "from": "", "snippet": "", "date": "", "labels": ""}
            continue
        if current and line.startswith("- 寄件者 | from:"):
            current["from"] = line.split(":", 1)[1].strip()
        elif current and line.startswith("- 日期 | date:"):
            current["date"] = line.split(":", 1)[1].strip()
        elif current and line.startswith("- 標籤 | labels:"):
            current["labels"] = line.split(":", 1)[1].strip()
        elif current and line.startswith("- 摘要 | snippet:"):
            current["snippet"] = line.split(":", 1)[1].strip()
    if current:
        messages.append(current)
    return messages[:limit]


def _clean_preview(text: str, limit: int = 140) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text)
    cleaned = cleaned.replace("&nbsp;", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _parse_checklist(note_path: Path | None, heading: str | list[str]) -> list[dict[str, str | bool]]:
    block = _extract_heading_block(note_path, heading)
    if not block:
        return []
    items: list[dict[str, str | bool]] = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ] "):
            items.append({"done": False, "text": stripped[6:].strip()})
        elif stripped.startswith("- [x] "):
            items.append({"done": True, "text": stripped[6:].strip()})
    return items


def _parse_daily_brief(note_path: Path | None) -> dict[str, list[str] | str]:
    text = _read_text(note_path)
    sections = {
        "snapshot": "",
        "projects": [],
        "priorities": [],
        "followups": [],
        "actions": [],
        "risks": [],
    }
    if not text:
        return sections
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("## 1. Today’s Snapshot"):
            current = "snapshot"
            continue
        if line.startswith("## 2. Active Projects"):
            current = "projects"
            continue
        if line.startswith("## 3. Messages / Follow-ups"):
            current = "followups"
            continue
        if line.startswith("## 6. Suggested Actions"):
            current = "actions"
            continue
        if line.startswith("## 8. Risks / Blockers"):
            current = "risks"
            continue
        if line.startswith("## 5. Suggested Priorities"):
            current = "priorities"
            continue
        if line.startswith("## "):
            current = None
            continue
        if current == "snapshot" and line.strip():
            sections["snapshot"] = line.strip()
        elif current in {"projects", "priorities", "followups", "actions", "risks"} and line.strip().startswith("- "):
            sections[current].append(line.strip()[2:])
    sections["projects"] = sections["projects"][:3]
    sections["priorities"] = sections["priorities"][:3]
    sections["followups"] = sections["followups"][:2]
    sections["actions"] = sections["actions"][:3]
    sections["risks"] = sections["risks"][:2]
    return sections


def _parse_notion_status(note_path: Path | None) -> dict[str, object]:
    text = _read_text(note_path)
    backed_up = 0
    failures: list[str] = []
    share_required: list[str] = []
    needs_reauth = False
    network_issue = False
    failed_sources = 0
    if not text:
        return {"backed_up": 0, "failures": failures, "share_required": share_required, "needs_reauth": needs_reauth, "network_issue": network_issue, "failed_sources": failed_sources}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") and "[[" in stripped and "Daily Backups" in stripped:
            backed_up += 1
        elif stripped.startswith("- failed_sources:"):
            try:
                failed_sources = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                failed_sources = 0
        elif stripped.startswith("- 需要分享給 integration `dog` 的資料庫"):
            payload = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
            share_required = [re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", item.strip()) for item in payload.split(",") if item.strip()]
        elif "重新登入" in stripped or "re-auth" in stripped:
            needs_reauth = True
        elif "網路 / DNS" in stripped or "network or dns" in stripped.lower():
            network_issue = True
        elif stripped.startswith("- ") and "->" in stripped:
            failures.append(stripped[2:])
    return {
        "backed_up": backed_up,
        "failures": failures,
        "share_required": share_required,
        "needs_reauth": needs_reauth,
        "network_issue": network_issue,
        "failed_sources": failed_sources,
    }


def _parse_research_panel(note_path: Path | None, *, allow_previous: bool = True) -> dict[str, object]:
    text = _read_text(note_path)
    if not text:
        return {
            "items": [],
            "cards": [],
            "rankings": [],
            "message": "今天還沒有研究摘要。| No research digest is available yet.",
            "workspace_hint": "先開 thesis 工作台。| Open the thesis workspace first.",
        }

    picks: list[str] = []
    cards: list[dict[str, str]] = []
    rankings: list[dict[str, str]] = []
    in_picks = False
    in_rankings = False
    current_card: dict[str, str] | None = None
    current_ranking: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line == "## Scholar-like 排序 | Scholar-like Ranking":
            in_rankings = True
            in_picks = False
            continue
        if line in {
            "## 今日推薦 | Today's Picks",
            "## 今天沒有新論文，但這裡有可先讀的舊筆記 | No New Papers Today, Start With These",
        }:
            in_picks = True
            in_rankings = False
            continue
        if (in_picks or in_rankings) and line.startswith("## "):
            break
        if in_rankings and line.startswith("### "):
            if current_ranking:
                rankings.append(current_ranking)
            current_ranking = {"label": line[4:].strip(), "paper": "", "why": "", "note": ""}
            continue
        if in_rankings and current_ranking and line.startswith("- 論文 | paper:"):
            current_ranking["paper"] = line.split(":", 1)[1].strip()
            continue
        if in_rankings and current_ranking and line.startswith("- 理由 | why:"):
            current_ranking["why"] = line.split(":", 1)[1].strip()
            continue
        if in_rankings and current_ranking and line.startswith("- 筆記 | note:"):
            current_ranking["note"] = line.split(":", 1)[1].strip()
            continue
        if not in_picks:
            continue
        if line.startswith("### "):
            continue
        if line.startswith("#### "):
            if current_card:
                cards.append(current_card)
            title = line[5:].strip()
            picks.append(title)
            current_card = {"title": title, "why": "", "summary": "", "difficulty": ""}
            continue
        if current_card and line.startswith("- 難度 | difficulty:"):
            current_card["difficulty"] = line.split(":", 1)[1].strip()
        elif current_card and line.startswith("- 為什麼可能重要 | why it may matter:"):
            current_card["why"] = line.split(":", 1)[1].strip()
        elif current_card and line.startswith("- 摘要 | summary:"):
            current_card["summary"] = line.split(":", 1)[1].strip()

    if current_ranking:
        rankings.append(current_ranking)
    if current_card:
        cards.append(current_card)

    if picks:
        return {
            "items": picks[:3],
            "cards": cards[:3],
            "rankings": rankings[:6],
            "message": "今天先從這些開始，不需要一次讀完。| Start with these today; you do not need to finish everything at once.",
            "workspace_hint": "如果其中一篇真的有感，再補進文獻表。| If one truly matters, add it into the literature table.",
        }

    today_bullets = _extract_bullets(note_path, ["## 今天 | Today", "## 今天沒有新論文，但這裡有可先讀的舊筆記 | No New Papers Today, Start With These"])
    if today_bullets:
        return {
            "items": today_bullets[:3],
            "cards": [],
            "rankings": rankings[:6],
            "message": "今天用低壓模式前進就好。| A low-pressure reading mode is enough today.",
            "workspace_hint": "沒有新論文時，可以改做 thesis mapping。| If there are no new papers, switch to thesis mapping.",
        }

    if allow_previous and note_path and note_path.exists():
        digest_dir = note_path.parent
        previous = sorted(
            [path for path in digest_dir.glob("*.md") if path != note_path],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for candidate in previous:
            parsed = _parse_research_panel(candidate, allow_previous=False)
            if parsed.get("cards") or parsed.get("items"):
                parsed["message"] = "今天即時抓取失敗，先用最近一次成功抓到的研究卡。| Live fetch failed today, so here are the most recent successfully fetched research cards."
                return parsed

    return {
        "items": [],
        "cards": [],
        "rankings": rankings[:6],
        "message": "今天沒有新論文，先做 thesis mapping。| No new papers today; work on thesis mapping first.",
        "workspace_hint": "先補文獻表、量表、變項、研究缺口。| Fill the literature table, scales, variables, and gaps first.",
    }


def _render_research_rankings(rankings: list[dict[str, str]]) -> str:
    if not rankings:
        return ""
    rendered = ['<section class="ranking-grid">']
    for ranking in rankings[:6]:
        rendered.extend(
            [
                '<article class="ranking-card">',
                f"<p class=\"ranking-kicker\">{html.escape(ranking.get('label', ''))}</p>",
                f"<h3>{html.escape(ranking.get('paper', '(untitled)'))}</h3>",
                f"<p>{html.escape(ranking.get('why', ''))}</p>",
                f"<p class=\"tiny-note\">{html.escape(ranking.get('note', ''))}</p>" if ranking.get("note") else "",
                "</article>",
            ]
        )
    rendered.append("</section>")
    return "\n".join(rendered)


def _synthesize_research_rankings(cards: list[dict[str, str]]) -> list[dict[str, str]]:
    if not cards:
        return []

    def _score(text: str, keywords: list[str]) -> int:
        lowered = text.lower()
        return sum(1 for keyword in keywords if keyword in lowered)

    def _difficulty_rank(label: str) -> int:
        if label.startswith("入門"):
            return 0
        if label.startswith("中階"):
            return 1
        return 2

    thesis_keywords = ["adhd", "neurodivergent", "student", "students", "higher education", "college", "university", "generative ai", "chatgpt", "assistant", "learning support", "executive dysfunction", "attention", "emotion regulation"]
    gap_keywords = ["gap", "underexplored", "limited", "few", "lack", "stress", "daily", "everyday", "support"]
    rq_keywords = ["question", "experience", "perception", "acceptance", "use", "interaction", "assistant", "human-ai"]
    meth_keywords = ["survey", "questionnaire", "scale", "inventory", "interview", "qualitative", "experiment", "participants", "validated", "model"]
    contri_keywords = ["framework", "contribution", "implication", "design", "evaluation", "effectiveness", "outcome", "support", "scaffolding"]

    normalized: list[dict[str, str | int]] = []
    for card in cards:
        text = " ".join([card.get("title", ""), card.get("summary", ""), card.get("why", "")])
        normalized.append(
            {
                "title": card.get("title", "(untitled)"),
                "why": card.get("why", ""),
                "text": text,
                "difficulty": card.get("difficulty", ""),
                "thesis": _score(text, thesis_keywords),
                "gap": _score(text, gap_keywords),
                "rq": _score(text, rq_keywords),
                "meth": _score(text, meth_keywords),
                "contri": _score(text, contri_keywords),
            }
        )

    def _pick(key: str, *, prefer_easy: bool = False) -> dict[str, str | int]:
        ranked = sorted(
            normalized,
            key=lambda item: (
                int(item[key]),
                int(item["thesis"]),
                -_difficulty_rank(str(item["difficulty"])) if prefer_easy else int(item["meth"]),
            ),
            reverse=True,
        )
        return ranked[0]

    mapping = [
        ("最像你的 thesis | Best thesis match", _pick("thesis"), "這篇和你的題目核心最接近，最適合先拿來補理論、變項與研究定位。"),
        ("最值得先讀 | Best next read", _pick("thesis", prefer_easy=True), "這篇相對比較容易入口，又足夠貼近你的主題，最適合今天先讀。"),
        ("最可能補 GAP | Best for GAP", _pick("gap"), "這篇最適合幫你看現有研究已經做了什麼、還缺了什麼。"),
        ("最可能補 RQ | Best for RQ", _pick("rq"), "這篇最適合幫你把研究問題講得更清楚、更像一篇論文問題。"),
        ("最可能補 METH | Best for METH", _pick("meth"), "這篇最可能提供你可借鏡的量表、樣本、方法或分析設計。"),
        ("最可能補 CONTRI | Best for CONTRI", _pick("contri"), "這篇最適合幫你想清楚這個題目能帶來什麼理論或實務貢獻。"),
    ]
    return [{"label": label, "paper": str(item["title"]), "why": why, "note": ""} for label, item, why in mapping]


def _render_research_cards(cards: list[dict[str, str]]) -> str:
    if not cards:
        return ""

    def _focus_zh(summary: str, title: str) -> str:
        cleaned = _clean_preview(summary, limit=150)
        if not cleaned:
            return f"這篇主要在看 {title} 的核心問題、互動方式與評估重點。"
        return f"這篇主要在看：{cleaned}"

    def _focus_en(summary: str, title: str) -> str:
        cleaned = _clean_preview(summary, limit=150)
        if not cleaned:
            return f"This paper focuses on the core problem, interaction pattern, and evaluation logic behind {title}."
        return f"This paper studies: {cleaned}"

    def _relevance_zh(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["adhd", "executive dysfunction", "attention", "self-regulation"]):
            return "它直接貼近你的論文核心，因為它碰到注意力管理、執行功能或自我調節，能幫你補理論與變項設計。"
        if any(keyword in combined for keyword in ["human-ai", "llm", "generative ai", "chatbot", "ai assistant"]):
            return "它和你的題目有關，因為它在看人怎麼把生成式 AI 當成日常支持工具，這能幫你界定互動型態與使用情境。"
        if any(keyword in combined for keyword in ["education", "student", "learning", "scaffold"]):
            return "它和你的論文有關，因為它補的是學習支持與認知鷹架這一層，能幫你把 AI 使用和學習結果連起來。"
        if any(keyword in combined for keyword in ["emotion", "stress", "well-being", "mental"]):
            return "它和你的論文有關，因為它補的是情緒調節與壓力支持這一層，這正是你想看的 daily support 面向。"
        if why:
            return why
        return "它不一定完全命中你的題目，但可以先當成周邊文獻，幫你補人機互動、支持工具或學習設計的背景。"

    def _relevance_en(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["adhd", "executive dysfunction", "attention", "self-regulation"]):
            return "It is close to your thesis because it touches attention management, executive functioning, or self-regulation, which helps with theory and variable design."
        if any(keyword in combined for keyword in ["human-ai", "llm", "generative ai", "chatbot", "ai assistant"]):
            return "It matters to your thesis because it looks at how people use generative AI as a day-to-day support tool, which helps define interaction patterns and usage contexts."
        if any(keyword in combined for keyword in ["education", "student", "learning", "scaffold"]):
            return "It matters because it strengthens the learning-support and cognitive-scaffolding side of your topic, helping connect AI use to learning outcomes."
        if any(keyword in combined for keyword in ["emotion", "stress", "well-being", "mental"]):
            return "It matters because it adds the emotional-regulation and stress-support layer that is central to your daily-support framing."
        if why:
            return why
        return "It may not be a perfect match, but it can still serve as background for human-AI interaction, support tools, or learning design."

    def _gap_zh(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["human-ai", "llm", "chatbot", "assistant"]):
            return "這篇提醒你：很多研究會談 AI 效能，但較少細看神經多樣性學生如何把 AI 當成日常支持工具。"
        if any(keyword in combined for keyword in ["adhd", "executive dysfunction", "attention"]):
            return "這篇可幫你看到一個缺口：現有研究常聚焦症狀或學業結果，但較少連到生成式 AI 的日常支持使用。"
        if any(keyword in combined for keyword in ["emotion", "stress", "well-being"]):
            return "這篇可延伸的研究缺口是：情緒支持與壓力調節常被討論，但較少放進高教學生使用生成式 AI 的脈絡。"
        return "可先把它當成背景文獻，再問：現有研究已經回答了什麼？又忽略了哪些學生、情境或支持需求？"

    def _gap_en(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["human-ai", "llm", "chatbot", "assistant"]):
            return "A likely gap here is that many studies evaluate AI performance, but fewer examine how neurodivergent students use AI as an everyday support tool."
        if any(keyword in combined for keyword in ["adhd", "executive dysfunction", "attention"]):
            return "A likely gap is that existing work often focuses on symptoms or outcomes, but less often connects them to day-to-day generative AI support."
        if any(keyword in combined for keyword in ["emotion", "stress", "well-being"]):
            return "A likely gap is that emotional support is discussed, but less often in the higher-education context of generative AI use."
        return "Use it as background, then ask: what has already been answered, and which students, contexts, or support needs are still underexplored?"

    def _rq_zh(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["adhd", "attention", "executive dysfunction"]):
            return "你可以把它轉成這種研究問題：ADHD 或神經多樣性學生是否會把生成式 AI 用來支撐注意力管理、規劃與執行？"
        if any(keyword in combined for keyword in ["emotion", "stress", "well-being"]):
            return "你可以往這個問題靠：學生是否把生成式 AI 當成情緒調節或壓力緩衝的日常工具？"
        if any(keyword in combined for keyword in ["human-ai", "assistant", "llm"]):
            return "你可以問：學生如何理解與接受 AI 助理，以及哪些互動經驗會影響他們持續使用？"
        return "先用自己的話改寫：這篇真正想回答的是什麼？再看能不能拉回你的學生、AI 使用與學習支持脈絡。"

    def _rq_en(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["adhd", "attention", "executive dysfunction"]):
            return "A thesis-friendly question could be: do ADHD or neurodivergent students use generative AI to support attention management, planning, and execution?"
        if any(keyword in combined for keyword in ["emotion", "stress", "well-being"]):
            return "A useful question here is: do students use generative AI as a day-to-day emotional regulation or stress-buffering tool?"
        if any(keyword in combined for keyword in ["human-ai", "assistant", "llm"]):
            return "You could ask: how do students interpret and accept AI assistants, and which interaction experiences shape continued use?"
        return "Rewrite the paper's core question in your own words, then connect it back to students, AI use, and learning support."

    def _method_zh(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["evaluation", "metric", "benchmark"]):
            return "方法上可注意：它怎麼定義成效、互動品質或使用結果，這能幫你設計變項與評估方式。"
        if any(keyword in combined for keyword in ["survey", "acceptance", "tam"]):
            return "方法上可注意：它可能適合你參考量表、問卷結構與 TAM 類變項操作化。"
        if any(keyword in combined for keyword in ["student", "learning", "education"]):
            return "方法上可注意：它是否用學生樣本、訪談、觀察或學習成效指標，這些都能借來搭你的研究設計。"
        return "先看這篇用了什麼樣本、資料來源與分析方式，因為這通常最能直接幫到你的論文方法章。"

    def _method_en(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["evaluation", "metric", "benchmark"]):
            return "Methodologically, watch how it defines effectiveness, interaction quality, or usage outcomes. That can inform your variables and evaluation plan."
        if any(keyword in combined for keyword in ["survey", "acceptance", "tam"]):
            return "Methodologically, it may help with scales, questionnaire structure, and TAM-style operationalization."
        if any(keyword in combined for keyword in ["student", "learning", "education"]):
            return "Methodologically, check whether it uses student samples, interviews, observation, or learning-outcome indicators. Those can transfer into your own design."
        return "Start by checking the sample, data source, and analysis approach, because those often help most when shaping your methods chapter."

    def _contri_zh(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["human-ai", "llm", "assistant"]):
            return "它的價值多半在於補足人機互動或 AI 助理使用的概念框架，幫你寫出理論背景與使用脈絡。"
        if any(keyword in combined for keyword in ["adhd", "attention", "executive dysfunction"]):
            return "它的價值在於補神經多樣性、注意力或執行功能這一層，幫你把支持需求寫得更具體。"
        if any(keyword in combined for keyword in ["education", "learning", "scaffold"]):
            return "它的貢獻多半落在學習支持或鷹架設計，能幫你說明 AI 為什麼不只是工具，而是支持機制。"
        return "先把它視為支撐你論文某一塊拼圖的文獻：可能是背景、方法、變項，或使用情境。"

    def _contri_en(title: str, summary: str, why: str) -> str:
        combined = f"{title} {summary} {why}".lower()
        if any(keyword in combined for keyword in ["human-ai", "llm", "assistant"]):
            return "Its contribution is likely to strengthen your conceptual framing around human-AI interaction and assistant use."
        if any(keyword in combined for keyword in ["adhd", "attention", "executive dysfunction"]):
            return "Its contribution is likely to strengthen the neurodiversity, attention, or executive-functioning side of your thesis."
        if any(keyword in combined for keyword in ["education", "learning", "scaffold"]):
            return "Its contribution is likely in learning support or scaffolding, helping explain AI as a support mechanism rather than just a tool."
        return "Treat it as one useful piece of your thesis puzzle: background, methods, variables, or usage context."

    rendered: list[str] = []
    for card in cards[:3]:
        title = html.escape(card.get("title", "(untitled)"))
        raw_why = card.get("why", "")
        raw_summary = card.get("summary", "")
        why = html.escape(raw_why)
        summary = html.escape(_clean_preview(raw_summary, limit=180))
        difficulty = html.escape(card.get("difficulty", ""))
        focus_zh = html.escape(_focus_zh(raw_summary, card.get("title", "this paper")))
        focus_en = html.escape(_focus_en(raw_summary, card.get("title", "this paper")))
        relevance_zh = html.escape(_relevance_zh(card.get("title", ""), raw_summary, raw_why))
        relevance_en = html.escape(_relevance_en(card.get("title", ""), raw_summary, raw_why))
        rendered.append(
            "\n".join(
                [
                    '<article class="mini-card">',
                    f"<h3>{title}</h3>",
                    f'<p class="meta-line">{difficulty}</p>' if difficulty else "",
                    '<div class="assistant-brief">',
                    '<p class="brief-label">這篇在研究什麼 | What it studies</p>',
                    f"<p><strong>中：</strong>{focus_zh}</p>",
                    f"<p><strong>EN:</strong> {focus_en}</p>",
                    '</div>',
                    '<div class="assistant-brief">',
                    '<p class="brief-label">為什麼跟你的論文有關 | Why it matters to your thesis</p>',
                    f"<p><strong>中：</strong>{relevance_zh}</p>",
                    f"<p><strong>EN:</strong> {relevance_en}</p>",
                    '</div>',
                    f"<p class=\"tiny-note\"><strong>一句話摘要 | Quick take:</strong> {summary}</p>" if summary else "",
                    f"<p class=\"tiny-note\"><strong>補充線索 | Extra note:</strong> {why}</p>" if why else "",
                    '<div class="frame-grid">',
                    '<section class="frame-card">',
                    '<p class="frame-kicker">📁 BG 卡 | Background</p>',
                    f"<p>{focus_zh}</p>",
                    f"<p class=\"tiny-note\"><strong>EN:</strong> {focus_en}</p>",
                    '</section>',
                    '<section class="frame-card">',
                    '<p class="frame-kicker">🔎 GAP 卡 | Research Gap</p>',
                    f"<p>{html.escape(_gap_zh(card.get('title', ''), raw_summary, raw_why))}</p>",
                    f"<p class=\"tiny-note\"><strong>EN:</strong> {html.escape(_gap_en(card.get('title', ''), raw_summary, raw_why))}</p>",
                    '</section>',
                    '<section class="frame-card">',
                    '<p class="frame-kicker">❓ RQ 卡 | Research Question</p>',
                    f"<p>{html.escape(_rq_zh(card.get('title', ''), raw_summary, raw_why))}</p>",
                    f"<p class=\"tiny-note\"><strong>EN:</strong> {html.escape(_rq_en(card.get('title', ''), raw_summary, raw_why))}</p>",
                    '</section>',
                    '<section class="frame-card">',
                    '<p class="frame-kicker">⚙️ METH 卡 | Methodology</p>',
                    f"<p>{html.escape(_method_zh(card.get('title', ''), raw_summary, raw_why))}</p>",
                    f"<p class=\"tiny-note\"><strong>EN:</strong> {html.escape(_method_en(card.get('title', ''), raw_summary, raw_why))}</p>",
                    '</section>',
                    '<section class="frame-card">',
                    '<p class="frame-kicker">🏆 CONTRI 卡 | Contribution</p>',
                    f"<p>{html.escape(_contri_zh(card.get('title', ''), raw_summary, raw_why))}</p>",
                    f"<p class=\"tiny-note\"><strong>EN:</strong> {html.escape(_contri_en(card.get('title', ''), raw_summary, raw_why))}</p>",
                    '</section>',
                    '</div>',
                    "</article>",
                ]
            )
        )
    return "\n".join(rendered)


def _parse_thesis_workspace(note_path: Path | None) -> dict[str, list[str]]:
    text = _read_text(note_path)
    result = {
        "today": [],
        "fill_first": [],
        "gaps": [],
    }
    if not text:
        return result

    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped == "## 今天可直接完成 | What You Can Finish Today":
            current = "today"
            continue
        if stripped == "### 今日先填這些欄位 | Fill These First":
            current = "fill_first"
            continue
        if stripped == "### 研究缺口提示句 | Gap Prompts":
            current = "gaps"
            continue
        if stripped.startswith("## ") or stripped.startswith("### "):
            current = None
            continue
        if current and stripped.startswith("- "):
            result[current].append(stripped[2:].strip())

    result["today"] = result["today"][:3]
    result["fill_first"] = result["fill_first"][:4]
    result["gaps"] = result["gaps"][:2]
    return result


def _render_message_cards(messages: list[dict[str, str]]) -> str:
    if not messages:
        return "<p class=\"muted\">今天沒有可顯示的信件。| No email highlights today.</p>"
    visible = messages[:2]
    cards: list[str] = []
    for message in visible:
        sender = html.escape(message.get("from", ""))
        snippet = html.escape(_clean_preview(message.get("snippet", "")))
        labels = [item.strip() for item in message.get("labels", "").split(",") if item.strip()]
        label_html = ""
        priority_labels = [label for label in labels if label in {"IMPORTANT", "CATEGORY_PERSONAL", "CATEGORY_UPDATES", "CATEGORY_SOCIAL"}]
        if priority_labels:
            label_html = "<p class=\"meta-line\">" + " · ".join(html.escape(label) for label in priority_labels[:2]) + "</p>"
        cards.append(
            "\n".join(
                [
                    "<article class=\"mini-card\">",
                    f"<h3>{html.escape(message.get('subject', '(no subject)'))}</h3>",
                    f"<p class=\"meta-line\">{sender}</p>",
                    label_html,
                    f"<p>{snippet}</p>",
                    "</article>",
                ]
            )
        )
    if len(messages) > len(visible):
        cards.append(
            f"<p class=\"tiny-note\">還有 {len(messages) - len(visible)} 封今天暫時先略過。| {len(messages) - len(visible)} more messages are intentionally hidden for focus.</p>"
        )
    return "\n".join(cards)


def _render_tasks(items: list[dict[str, str | bool]]) -> str:
    if not items:
        return "<p class=\"muted\">今天還沒有手動待辦。| No manual tasks yet.</p>"
    rendered = ["<ul class=\"checklist\">"]
    for item in items:
        cls = "done" if item["done"] else ""
        mark = "✓" if item["done"] else "○"
        rendered.append(f"<li class=\"{cls}\"><span class=\"mark\">{mark}</span>{html.escape(str(item['text']))}</li>")
    rendered.append("</ul>")
    return "\n".join(rendered)


def _render_simple_bullets(items: list[str], empty_text: str) -> str:
    cleaned_items = [item for item in items if str(item).strip()]
    if not cleaned_items:
        return f"<p class=\"muted\">{html.escape(empty_text)}</p>"
    rendered = ["<ul>"]
    for item in cleaned_items:
        rendered.append(f"<li>{html.escape(item)}</li>")
    rendered.append("</ul>")
    return "\n".join(rendered)


def _parse_calendar_panel(note_path: Path | None) -> dict[str, object]:
    text = _read_text(note_path)
    if not text:
        return {"events": [], "status": "missing", "message": "今天沒有日曆資料。| Calendar data is unavailable today."}

    status_mode = _status_mode(note_path)
    status = "fallback" if status_mode == "snapshot" else "error" if status_mode == "error" else "live"
    events: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            events.append(stripped[4:].strip())

    if events:
        return {"events": events[:4], "status": status, "message": ""}

    if status == "fallback":
        return {
            "events": [],
            "status": status,
            "message": "今天日曆資料未驗證，請把今天是否有行程視為待確認。| Today's calendar could not be verified, so treat your schedule as unconfirmed.",
        }
    if status == "error":
        return {
            "events": [],
            "status": status,
            "message": "今天日曆抓取失敗，這不代表沒有行程。| Calendar fetch failed today, so this does not mean you have no events.",
        }

    return {"events": [], "status": status, "message": "目前顯示沒有行程。| No events are shown for today."}


def _parse_google_tasks(note_path: Path | None, limit: int = 12) -> list[dict[str, str | bool]]:
    text = _read_text(note_path)
    if not text or _is_fallback(note_path):
        return []

    tasks: list[dict[str, str | bool]] = []
    in_list = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## 提醒清單") or stripped.startswith("## Reminder List"):
            in_list = True
            continue
        if in_list and stripped.startswith("## "):
            break
        if not in_list or not stripped.startswith("- ["):
            continue
        done = stripped.startswith("- [x]") or stripped.startswith("- [X]")
        body = stripped[5:].strip()
        tasks.append({"text": body, "done": done})
        if len(tasks) >= limit:
            break
    return tasks


def _render_google_tasks_panel(note_path: Path | None, tasks: list[dict[str, str | bool]]) -> str:
    status = _read_scalar(note_path, "- 狀態 | status:") if note_path else None
    if status:
        return (
            f"<p class=\"muted\">Google Tasks 目前沒有連上：{html.escape(status)}。"
            "這不是沒有待辦，而是 Google Tasks API/權限尚未完成。</p>"
        )
    return _render_tasks(tasks)


def _build_html_page(
    *,
    today: str,
    generated_at: str,
    fallback_sources: list[str],
    api_health_note: Path | None,
    weather_note: Path | None,
    market_note: Path | None,
    gmail_note: Path | None,
    calendar_note: Path | None,
    google_tasks_note: Path | None,
    manual_tasks_note: Path | None,
    daily_brief_note: Path | None,
    notion_status_note: Path | None,
    research_digest_note: Path | None,
) -> str:
    api_today = _extract_heading_block(api_health_note, ["## Today", "## 今天 | Today"])
    weather_panel = _extract_heading_block(weather_note, "## Weather | 台北天氣")
    market_panel = _extract_heading_block(market_note, "## News Radar | 今日新聞")
    gmail_messages = _parse_gmail_messages(gmail_note, limit=4)
    calendar_panel = _parse_calendar_panel(calendar_note)
    manual_today = _parse_checklist(manual_tasks_note, ["## 今天 | Today", "## Today"])
    google_tasks = _parse_google_tasks(google_tasks_note)
    google_tasks_status = _read_scalar(google_tasks_note, "- 狀態 | status:") if google_tasks_note else None
    notion_summary = _parse_notion_status(notion_status_note)
    research_panel = _parse_research_panel(research_digest_note)
    if not research_panel.get("rankings") and research_panel.get("cards"):
        research_panel["rankings"] = _synthesize_research_rankings(list(research_panel.get("cards", [])))
    daily_brief_summary = _parse_daily_brief(daily_brief_note)
    thesis_workspace = _parse_thesis_workspace(
        Path("/Users/youxinhua/Documents/New project/DoraOS/obsidian/DoraOS/Resources/Research Sources/Master Thesis/Master Thesis Literature Mapping.md")
    )

    fallback_html = ""
    if fallback_sources:
        badges_html = "".join(
            f'<span style="display:inline-block;background:#FEF3C7;border:1px solid #D97706;'
            f'border-radius:4px;padding:2px 8px;margin:2px;font-size:0.85em;font-weight:600;">'
            f'{html.escape(src)}</span>'
            for src in fallback_sources
        )
        fallback_html = f"""
        <section class="card" style="border-left:4px solid #D97706;background:#FFFBEB;">
          <h2 style="color:#92400E;">⚠️ 備援模式 | Fallback Active</h2>
          <p>以下來源今天未能取得<strong>即時資料</strong>。
             其中有些改用<strong>最近可用的安全快照</strong>，有些則是<strong>明確抓取失敗</strong>。
             這些內容<u>都不代表今日最新狀態</u>。</p>
          <p>The following sources could not fetch live data today. Some are showing the latest safe snapshot, and some are explicit fetch failures:
             {badges_html}</p>
          <p style="font-size:0.85em;color:#78350F;">
            影響 {len(fallback_sources)} 個來源 | {len(fallback_sources)} source(s) affected.
            其他來源正常 | Other sources are live.
          </p>
        </section>
        """

    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DoraOS Today Hub - {html.escape(today)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5fbff;
      --bg-2: #e8f4fb;
      --card: rgba(255, 255, 255, 0.98);
      --card-2: rgba(242, 249, 255, 0.98);
      --text: #334a66;
      --muted: #6f849b;
      --border: rgba(181, 209, 228, 0.8);
      --accent: #76add6;
      --accent-2: #b9daf0;
      --accent-3: #deeffa;
      --mint: #d9efe6;
      --warn: rgba(255, 246, 225, 0.96);
      --shadow: 0 18px 40px rgba(79, 117, 154, 0.1);
      --radius: 26px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Avenir Next", "Nunito", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(185, 218, 240, 0.48), transparent 30%),
        radial-gradient(circle at top right, rgba(222, 239, 250, 0.95), transparent 28%),
        radial-gradient(circle at bottom left, rgba(217, 239, 230, 0.6), transparent 22%),
        linear-gradient(180deg, var(--bg), var(--bg-2));
      color: var(--text);
      margin: 0;
      padding: 18px 14px 42px;
    }}
    body::before {{
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: 0.3;
      background-image:
        radial-gradient(rgba(118,173,214,0.16) 1px, transparent 1px),
        radial-gradient(rgba(255,255,255,0.5) 1px, transparent 1px);
      background-position: 0 0, 16px 16px;
      background-size: 32px 32px;
    }}
    .wrap {{ max-width: 980px; margin: 0 auto; display: grid; gap: 16px; position: relative; z-index: 1; }}
    .hero, .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    .hero {{
      position: relative;
      overflow: hidden;
      padding: 20px;
    }}
    .hero::after {{
      content: "";
      position: absolute;
      width: 220px;
      height: 220px;
      right: -40px;
      top: -60px;
      background: radial-gradient(circle, rgba(232,244,251,0.95) 0%, rgba(255,255,255,0) 72%);
      pointer-events: none;
    }}
    .hero-shell {{
      display: grid;
      grid-template-columns: 1.3fr 0.7fr;
      gap: 16px;
      align-items: stretch;
    }}
    .hero h1 {{ margin: 0 0 8px; font-size: 30px; letter-spacing: 0.01em; }}
    .meta {{ color: var(--muted); font-size: 14px; }}
    .subcopy {{ max-width: 54ch; }}
    .hero-art {{
      border-radius: 22px;
      padding: 18px 16px;
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.98), transparent 42%),
        linear-gradient(145deg, rgba(233, 246, 254, 0.98), rgba(247, 252, 255, 0.96));
      border: 1px solid rgba(181, 209, 228, 0.82);
      display: grid;
      gap: 12px;
      align-content: space-between;
      min-height: 170px;
    }}
    .hero-face {{ line-height: 1; }}
    .hero-art p {{
      margin: 0;
      font-size: 14px;
      color: #5d7c9d;
    }}
    .soft-band {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      background: rgba(244, 250, 255, 0.96);
      color: #5b7da0;
      font-size: 13px;
      width: fit-content;
      border: 1px solid rgba(181, 209, 228, 0.72);
    }}
    .grid {{ display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); }}
    .card h2 {{
      margin-top: 0;
      margin-bottom: 12px;
      font-size: 17px;
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .warning {{ background: linear-gradient(135deg, var(--warn), rgba(255,255,255,0.85)); }}
    .muted {{ color: var(--muted); }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }}
    .chip {{
      padding: 10px 12px;
      border-radius: 999px;
      background: linear-gradient(180deg, rgba(249,253,255,0.98), rgba(232,245,253,0.95));
      border: 1px solid var(--border);
      font-size: 13px;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.8);
      color: #40607f;
    }}
    .chip strong {{ font-weight: 700; }}
    .top-grid {{ display: grid; gap: 16px; grid-template-columns: 1.2fr .8fr; }}
    .summary {{ font-size: 15px; line-height: 1.7; }}
    .mini-card {{
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 14px;
      margin-bottom: 10px;
      background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(241,249,254,0.96));
      box-shadow: 0 10px 22px rgba(79, 117, 154, 0.08);
      color: var(--text);
    }}
    .mini-card h3 {{ margin: 0 0 6px; font-size: 15px; }}
    .mini-card p, .mini-card li, .mini-card strong {{ color: var(--text); }}
    .meta-line {{ color: var(--muted); font-size: 13px; margin: 0 0 6px; }}
    .checklist {{ list-style: none; padding: 0; margin: 0; display: grid; gap: 10px; }}
    .checklist li {{
      display: flex;
      gap: 10px;
      align-items: flex-start;
      padding: 12px 12px;
      border: 1px solid rgba(181, 209, 228, 0.78);
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(244,250,255,0.95));
      color: var(--text);
      font-weight: 600;
    }}
    .checklist li, .checklist li * {{ color: var(--text) !important; }}
    .checklist li:last-child {{ border-bottom: 0; }}
    .checklist .done {{ opacity: 0.62; text-decoration: line-through; }}
    .mark {{
      width: 24px;
      height: 24px;
      flex: 0 0 24px;
      color: white;
      background: linear-gradient(135deg, #76add6, #b9daf0);
      border-radius: 999px;
      display: grid;
      place-items: center;
      font-size: 12px;
      font-weight: 700;
      margin-top: 1px;
    }}
    details {{ border-top: 1px dashed var(--border); padding-top: 10px; margin-top: 10px; }}
    summary {{ cursor: pointer; color: #5f89ac; font-weight: 700; }}
    h3, h4 {{ margin-bottom: 8px; }}
    ul {{ margin-top: 0; padding-left: 18px; }}
    li {{ margin-bottom: 8px; }}
    p {{ line-height: 1.6; }}
    .icon-badge {{
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: linear-gradient(135deg, rgba(185,218,240,0.4), rgba(247,252,255,0.98));
      font-size: 15px;
    }}
    .tiny-note {{
      font-size: 13px;
      color: var(--muted);
      margin-top: 8px;
    }}
    .assistant-brief {{
      margin-top: 10px;
      padding: 12px 12px 10px;
      border-radius: 14px;
      background: linear-gradient(180deg, rgba(250,253,255,0.98), rgba(234,245,252,0.92));
      border: 1px solid rgba(181, 209, 228, 0.72);
    }}
    .brief-label {{
      margin: 0 0 6px;
      font-size: 12px;
      letter-spacing: 0.02em;
      color: #5e81a3 !important;
      font-weight: 700;
    }}
    .frame-grid {{
      display: grid;
      gap: 10px;
      margin-top: 14px;
    }}
    .ranking-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 10px;
      margin: 10px 0 14px;
    }}
    .ranking-card {{
      padding: 12px 13px;
      border-radius: 18px;
      background: linear-gradient(180deg, rgba(255,255,255,0.99), rgba(234,245,252,0.96));
      border: 1px solid rgba(171, 203, 225, 0.82);
      box-shadow: 0 8px 18px rgba(79, 117, 154, 0.06);
    }}
    .ranking-card h3 {{
      margin: 4px 0 6px;
      font-size: 16px;
      line-height: 1.35;
    }}
    .ranking-kicker {{
      font-size: 12px;
      font-weight: 800;
      color: #4d78a1 !important;
      letter-spacing: 0.02em;
      margin: 0 0 8px;
    }}
    .frame-card {{
      padding: 12px 12px 10px;
      border-radius: 16px;
      background: linear-gradient(180deg, rgba(255,255,255,0.99), rgba(240,248,254,0.96));
      border: 1px solid rgba(171, 203, 225, 0.82);
      box-shadow: 0 8px 18px rgba(79, 117, 154, 0.06);
    }}
    .frame-card p {{
      margin: 0 0 6px;
      color: var(--text);
    }}
    .frame-kicker {{
      font-size: 12px;
      font-weight: 800;
      color: #4d78a1 !important;
      letter-spacing: 0.02em;
      margin-bottom: 8px !important;
    }}
    .status-pills {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 10px;
    }}
    .status-pill {{
      font-size: 12px;
      border-radius: 999px;
      padding: 6px 10px;
      background: rgba(247,252,255,0.96);
      border: 1px solid rgba(181, 209, 228, 0.74);
      color: #5c7fa1;
    }}
    .hero-doodle {{
      display: grid;
      justify-items: center;
      gap: 8px;
    }}
    .hero-doodle svg {{
      width: 100%;
      max-width: 180px;
      height: auto;
      filter: drop-shadow(0 10px 18px rgba(79, 117, 154, 0.12));
    }}
    .sticker-row {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .sticker {{
      border-radius: 999px;
      padding: 6px 10px;
      background: #fff;
      border: 1px solid rgba(181, 209, 228, 0.78);
      font-size: 12px;
      color: #6485a5;
    }}
    @media (max-width: 860px) {{
      .top-grid, .hero-shell {{ grid-template-columns: 1fr; }}
      .hero h1 {{ font-size: 24px; }}
      body {{ padding: 14px 12px 34px; }}
      .hero-art {{ min-height: 0; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="hero-shell">
        <div>
          <div class="soft-band">🫧 DoraOS Morning Deck</div>
          <h1>今日總覽 | Today Hub</h1>
          <div class="meta">generated: {html.escape(generated_at)} · date: {html.escape(today)}</div>
          <p class="subcopy">把今天最重要的事留在眼前，背景資訊像小卡片一樣安靜排好。| Keep the important things in front of you, and let the background context rest in gentle little cards.</p>
          <div class="chips">
            <div class="chip"><strong>💌 Gmail</strong> · {"fallback" if "Gmail" in fallback_sources else "live"}</div>
            <div class="chip"><strong>🗓 Calendar</strong> · {html.escape(str(calendar_panel.get("status", "unknown")))}</div>
            <div class="chip"><strong>📚 Research</strong> · {"live" if research_panel.get("items") else "mapping"}</div>
            <div class="chip"><strong>✅ Tasks</strong> · {html.escape(google_tasks_status or str(len(manual_today) + len(google_tasks)) + " items")}</div>
          </div>
        </div>
        <div class="hero-art">
          <div class="hero-doodle">
            <div class="hero-face">
              <svg viewBox="0 0 220 190" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                <rect x="40" y="55" width="140" height="95" rx="28" fill="#FFFDFB" stroke="#9EC6E3" stroke-width="4"/>
                <path d="M78 69c5-18 18-28 33-28 16 0 27 10 31 28" fill="#DCEFFD" stroke="#89B5D8" stroke-width="4" stroke-linecap="round"/>
                <circle cx="88" cy="104" r="10" fill="#5C7694"/>
                <circle cx="132" cy="104" r="10" fill="#5C7694"/>
                <path d="M98 129c8 9 22 9 30 0" stroke="#7AAAD2" stroke-width="5" stroke-linecap="round" fill="none"/>
                <circle cx="68" cy="85" r="12" fill="#FFF0B8" stroke="#DAB969" stroke-width="3"/>
                <path d="M66 76h4M64 84h8M66 92h4" stroke="#B88D3C" stroke-width="3" stroke-linecap="round"/>
                <circle cx="165" cy="78" r="14" fill="#DDF1E8" stroke="#8DBFA8" stroke-width="3"/>
                <path d="M159 78h12M165 72v12" stroke="#5F9A79" stroke-width="3" stroke-linecap="round"/>
                <path d="M60 149c10 16 28 25 50 25h12c20 0 39-9 49-24" fill="none" stroke="#C4DDF0" stroke-width="4" stroke-linecap="round"/>
              </svg>
            </div>
            <p>今天不需要一次讀完所有東西。先看首頁，再挑一件真正重要的。| You do not need to finish everything today. Start here, then choose one meaningful thing.</p>
          </div>
          <div class="sticker-row">
            <span class="sticker">☁️ slow start</span>
            <span class="sticker">🩵 gentle pace</span>
            <span class="sticker">✏️ one step first</span>
          </div>
          <div class="status-pills">
            <span class="status-pill">🫧 soft focus</span>
            <span class="status-pill">❄️ low pressure</span>
            <span class="status-pill">🧺 tidy context</span>
          </div>
        </div>
      </div>
    </section>
    {fallback_html}
    <section class="top-grid">
      <section class="card">
        <h2><span class="icon-badge">🌤️</span> 今天重點 | Focus Today</h2>
        <div class="summary">
          <p>{html.escape(str(daily_brief_summary.get("snapshot", "今天先看手動待辦、信箱和研究。")) or "今天先看手動待辦、信箱和研究。")}</p>
        </div>
        <h3>今日優先 | Priorities</h3>
        {_render_simple_bullets(list(daily_brief_summary.get("priorities", [])), "今天沒有新的優先建議。")}
        <details>
          <summary>查看追蹤訊號 | Follow-ups</summary>
          {_render_simple_bullets(list(daily_brief_summary.get("followups", [])), "今天沒有額外 follow-up。")}
        </details>
      </section>
      <section class="card">
        <h2><span class="icon-badge">🩺</span> 系統健康總覽 | System Health</h2>
        {_markdownish_to_html(api_today)}
        <details>
          <summary>查看 Notion 狀態 | Notion status</summary>
          <p>今天備份成功 | backed up: {notion_summary.get("backed_up", 0)}</p>
          <p>失敗數 | failures: {notion_summary.get("failed_sources", 0)}</p>
          {_render_simple_bullets(
              [f"請分享這些資料庫給 dog：{', '.join(notion_summary.get('share_required', []))}"] if notion_summary.get("share_required") else [],
              "今天沒有 Notion 限制訊息。"
          )}
          {_render_simple_bullets(
              ["Notion connector 需要重新登入" if notion_summary.get("needs_reauth") else "", "今天有網路 / DNS 問題" if notion_summary.get("network_issue") else ""],
              ""
          )}
        </details>
      </section>
    </section>
    <section class="top-grid">
      <section class="card">
        <h2><span class="icon-badge">🌦️</span> 台北天氣 | Weather</h2>
        {_markdownish_to_html(weather_panel)}
      </section>
      <section class="card">
        <h2><span class="icon-badge">📈</span> 今日市場 | Market</h2>
        {_markdownish_to_html(market_panel)}
      </section>
    </section>
    <div class="grid">
      <section class="card">
        <h2><span class="icon-badge">✅</span> 今日待辦 | Today</h2>
        {_render_tasks(manual_today)}
      </section>
      <section class="card">
        <h2><span class="icon-badge">☑️</span> Google 提醒清單 | Reminders</h2>
        {_render_google_tasks_panel(google_tasks_note, google_tasks)}
      </section>
      <section class="card">
        <h2><span class="icon-badge">🗓️</span> 今日日曆 | Calendar</h2>
        {_render_simple_bullets(list(calendar_panel.get("events", [])), str(calendar_panel.get("message", "今天沒有行程。| No events today.")))}
        <p class="tiny-note">先看今天的時間壓力，不要讓行程偷走你的注意力。| See your time pressure first before it steals your attention.</p>
      </section>
      <section class="card">
        <h2><span class="icon-badge">💌</span> 信箱重點 | Gmail</h2>
        {_render_message_cards(gmail_messages)}
      </section>
      <section class="card">
        <h2><span class="icon-badge">📚</span> 研究摘要 | Research Digest</h2>
        <p class="muted">{html.escape(str(research_panel.get("message", "")))}</p>
        {_render_research_rankings(list(research_panel.get("rankings", [])))}
        {_render_research_cards(list(research_panel.get("cards", []))) if research_panel.get("cards") else _render_simple_bullets(list(research_panel.get("items", [])), "今天沒有新論文，先做 thesis mapping。")}
        <h3>接下來直接做 | Do This Next</h3>
        {_render_simple_bullets(list(thesis_workspace.get("today", [])), "先補文獻表、量表、變項、研究缺口。")}
        <p class="tiny-note">{html.escape(str(research_panel.get("workspace_hint", "先補文獻表、量表、變項、研究缺口。| Work on the literature table, scales, variables, and gaps first.")))}</p>
        <h3>先填這些欄位 | Fill These First</h3>
        {_render_simple_bullets(list(thesis_workspace.get("fill_first", [])), "Author / Year / Key findings / Relevance")}
        <details>
          <summary>研究缺口提示 | Gap Prompts</summary>
          {_render_simple_bullets(list(thesis_workspace.get("gaps", [])), "現有研究多集中在 ______，但較少處理 ______。")}
        </details>
      </section>
    </div>
    <section class="card">
      <h2><span class="icon-badge">🪄</span> 今日摘要總覽 | Daily Brief</h2>
      <h3>活躍專案 | Active Projects</h3>
      {_render_simple_bullets(list(daily_brief_summary.get("projects", [])), "今天沒有新的專案摘要。")}
      <h3>建議動作 | Suggested Actions</h3>
      {_render_simple_bullets(list(daily_brief_summary.get("actions", [])), "今天沒有新的建議動作。")}
      <details>
        <summary>風險與提醒 | Risks & Reminders</summary>
        {_render_simple_bullets(list(daily_brief_summary.get("risks", [])), "今天沒有額外風險提醒。")}
      </details>
    </section>
  </main>
</body>
</html>
"""


def _latest_notes(notes_dir: Path, *, limit: int = 3) -> list[Path]:
    if not notes_dir.exists():
        return []
    candidates = [
        path
        for path in notes_dir.glob("*.md")
        if path.is_file() and "Index" not in path.name
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:limit]


def _latest_matching_note(notes_dir: Path, pattern: str) -> Path | None:
    if not notes_dir.exists():
        return None
    candidates = [path for path in notes_dir.glob(pattern) if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _read_scalar(note_path: Path, prefix: str) -> str | None:
    if not note_path.exists():
        return None
    for raw_line in note_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _freshness_label(today_name: str, note_path: Path | None, fallback_label: str) -> str:
    if not note_path:
        return f"- {fallback_label} unavailable."
    status = _read_scalar(note_path, "- 狀態 | status:")
    if status == "fallback":
        return f"- 今日檔案（備援） | today's file (fallback): {note_path.stem}"
    if note_path.name.startswith(today_name):
        return f"- 今天資料 | today's data: {note_path.stem}"
    return f"- 備援資料 | fallback data: {note_path.stem}"


# All fallback status strings the system may emit — must stay in sync with
# build_fallback_digest / build_fallback_calendar / build_digest / fallback_copy
_FALLBACK_STATUSES = frozenset({
    "fallback",
    "backfilled-fallback",
    "low-pressure fallback",
    "api-failed",
    "api-disabled",
    "auth-required",
    "missing-live-data",
})


def _status_value(note_path: Path | None) -> str | None:
    if not note_path or not note_path.exists():
        return None
    scalar = _read_scalar(note_path, "- 狀態 | status:")
    return scalar.strip() if scalar else None


def _status_mode(note_path: Path | None) -> str:
    status = (_status_value(note_path) or "").strip()
    if status in {"fallback", "backfilled-fallback", "low-pressure fallback"}:
        return "snapshot"
    if status in {"api-failed", "api-disabled", "auth-required", "missing-live-data"}:
        return "error"
    if note_path and note_path.exists():
        return "live"
    return "missing"


def _is_fallback(note_path: Path | None) -> bool:
    """Return True if the note was written as any form of fallback, not fresh live data."""
    if not note_path or not note_path.exists():
        return False
    text = note_path.read_text(encoding="utf-8", errors="ignore")
    # Check YAML-style "- 狀態 | status: xxx" lines
    scalar = _status_value(note_path)
    if scalar and scalar in _FALLBACK_STATUSES:
        return True
    # Broad text search for status markers used across different templates
    for marker in (
        "status: fallback",
        "status: backfilled-fallback",
        "status: low-pressure fallback",
        "status: api-failed",
        "status: api-disabled",
        "status: auth-required",
        "status: missing-live-data",
    ):
        if marker in text:
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Compose DoraOS Operating Feed from Gmail and Calendar sync outputs.")
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logger = build_logger("doraos.operating_feed", LOG_DIR / "dora_operating_feed_compose.log", verbose=args.verbose)
    config = load_env_file(Path(args.env_file))
    vault_path = Path(require_env(config, "OBSIDIAN_VAULT_PATH")).expanduser()
    today, generated_at = today_stamp()

    feed_dir = vault_path / "Resources" / "Operating Feed"
    resources_dir = vault_path / "Resources"
    today_hub_path = feed_dir / "Today Hub.md"
    api_health_note = feed_dir / "API Health.md"
    gmail_note = feed_dir / f"{today} Gmail Digest.md"
    calendar_note = feed_dir / f"{today} Calendar View.md"
    google_tasks_note = feed_dir / f"{today} Google Tasks.md"
    weather_note = feed_dir / f"{today} Weather.md"
    market_note = feed_dir / f"{today} Market News.md"
    manual_tasks_note = feed_dir / "Today Manual Tasks.md"
    research_web_dir = resources_dir / "Research Sources" / "Web Articles"
    research_repo_dir = resources_dir / "Research Sources" / "google-research"
    research_digest_dir = resources_dir / "Research Sources" / "Daily Digests"
    research_auto_dir = resources_dir / "Research Sources" / "Auto Papers"
    research_zotero_dir = resources_dir / "Research Sources" / "Zotero"
    brief_dir = resources_dir / "AI Briefs"
    notion_sync_dir = resources_dir / "Notion Sync"
    output_path = feed_dir / f"{today} Operating Feed.md"
    today_hub_html_path = feed_dir / "Today Hub.html"
    dated_hub_html_path = feed_dir / f"{today} Today Hub.html"
    root_today_html_path = vault_path / "Today.html"

    latest_digest_notes = _latest_notes(research_digest_dir, limit=2)
    latest_auto_notes = _latest_notes(research_auto_dir, limit=4)
    latest_web_notes = _latest_notes(research_web_dir, limit=3)
    latest_repo_notes = _latest_notes(research_repo_dir, limit=3)
    latest_zotero_notes = _latest_notes(research_zotero_dir, limit=4)
    latest_repo_notes = [note for note in latest_repo_notes if "[" not in note.stem and "]" not in note.stem]
    latest_gmail_note = gmail_note if gmail_note.exists() else _latest_matching_note(feed_dir, "* Gmail Digest.md")
    latest_calendar_note = calendar_note if calendar_note.exists() else _latest_matching_note(feed_dir, "* Calendar View.md")
    latest_google_tasks_note = google_tasks_note if google_tasks_note.exists() else _latest_matching_note(feed_dir, "* Google Tasks.md")
    latest_weather_note = weather_note if weather_note.exists() else _latest_matching_note(feed_dir, "* Weather.md")
    latest_market_note = market_note if market_note.exists() else _latest_matching_note(feed_dir, "* Market News.md")
    latest_research_digest = latest_digest_notes[0] if latest_digest_notes else None
    latest_daily_brief = _latest_matching_note(brief_dir, "* Daily Brief.md")
    notion_status_note = notion_sync_dir / "Notion Backup Status.md"
    gmail_count = _read_scalar(latest_gmail_note, "- messages:") if latest_gmail_note else None
    if not gmail_count:
        gmail_count = _read_scalar(latest_gmail_note, "- 郵件數量 | messages:") if latest_gmail_note else None
    if not gmail_count and latest_gmail_note:
        parsed_gmail_count = len(_parse_gmail_messages(latest_gmail_note, limit=100))
        gmail_count = str(parsed_gmail_count) if parsed_gmail_count else None
    calendar_count = _read_scalar(latest_calendar_note, "- events:") if latest_calendar_note else None
    if not calendar_count:
        calendar_count = _read_scalar(latest_calendar_note, "- 事件數量 | events:") if latest_calendar_note else None
    google_tasks_count = _read_scalar(latest_google_tasks_note, "- tasks:") if latest_google_tasks_note else None
    if not google_tasks_count:
        google_tasks_count = _read_scalar(latest_google_tasks_note, "- 任務數量 | tasks:") if latest_google_tasks_note else None
    digest_count = latest_research_digest and _read_scalar(latest_research_digest, "- papers:")
    fallback_sources: list[str] = []
    if _is_fallback(latest_gmail_note):
        fallback_sources.append("Gmail")
    if _is_fallback(latest_calendar_note):
        fallback_sources.append("Calendar")
    if _is_fallback(latest_google_tasks_note):
        fallback_sources.append("Google Tasks")
    if _is_fallback(latest_weather_note):
        fallback_sources.append("Weather")
    if _is_fallback(latest_market_note):
        fallback_sources.append("Market")
    if _is_fallback(latest_research_digest):
        fallback_sources.append("Research Digest")

    research_low_pressure = False
    if latest_research_digest and latest_research_digest.exists():
        research_text = latest_research_digest.read_text(encoding="utf-8", errors="ignore")
        research_low_pressure = "status: low-pressure fallback" in research_text
        if research_low_pressure:
            latest_auto_notes = []
            latest_web_notes = []
            latest_repo_notes = []
            latest_zotero_notes = []

    lines = [
        "# 今日總覽 | Today Hub",
        "",
        f"- 生成時間 | generated: {generated_at}",
        f"- 日期 | date: {today}",
        "",
        "## 這是什麼 | What this is",
        "",
        "- 這是你每天只要先看的單一畫面 | this is the single screen to start from each day",
        "- 它是唯讀上下文，不直接替你決定任務 | it is read-only context, not an automatic task decider",
        "- 真正要承諾的事，請手動抄到 `Today Manual Tasks` | copy real commitments into `Today Manual Tasks` by hand",
        "",
    ]
    if api_health_note.exists():
        lines += [
            "## 系統健康總覽 | System Health",
            "",
            f"- {_wiki_link(vault_path, api_health_note)}",
            "",
            _wiki_embed(vault_path, api_health_note, "Today"),
            "",
        ]
    if fallback_sources:
        lines += [
            "## 今日來源警示 | Today's Source Alerts",
            "",
            f"- 今天有 {len(fallback_sources)} 個來源未取得即時資料 | {len(fallback_sources)} sources did not return live data today.",
            f"- 受影響來源 | affected sources: {', '.join(fallback_sources)}",
            f"- 其中有些是最近可用快照，有些是明確抓取失敗；都不要當成今日即時狀態 | Some are latest safe snapshots and some are explicit fetch failures; none should be treated as live today: {', '.join(fallback_sources)}.",
            "",
        ]

    if latest_weather_note:
        lines += [
            "## Weather | 台北天氣",
            "",
            _freshness_label(today, latest_weather_note, "Weather"),
            f"- {_wiki_link(vault_path, latest_weather_note)}",
            "",
            "### 直接看 | Quick view",
            "",
            _wiki_embed(vault_path, latest_weather_note, "Weather | 台北天氣"),
            "",
        ]
    if latest_market_note:
        lines += [
            "## News Radar | 今日新聞",
            "",
            _freshness_label(today, latest_market_note, "Market News"),
            f"- {_wiki_link(vault_path, latest_market_note)}",
            "",
            "### 直接看 | Quick view",
            "",
            _wiki_embed(vault_path, latest_market_note, "News Radar | 今日新聞"),
            "",
        ]

    lines += [
        "## 晨間主畫面 | Morning Console",
        "",
        "- 先看這一頁，不用先翻很多 index | start here instead of bouncing between many indexes",
        f"- 歷史頁面 | dated page: [[Resources/Operating Feed/{today} Operating Feed]]",
        "",
        "## 信箱重點 | Gmail",
        "",
        _freshness_label(today, latest_gmail_note, "Gmail digest"),
        f"- 今日未讀摘要 | unread summary: {gmail_count} 封" if latest_gmail_note and gmail_count and _status_mode(latest_gmail_note) == "live" else ("- 以最近可用快照呈現 | showing the latest safe snapshot." if _status_mode(latest_gmail_note) == "snapshot" else "- 今日抓取失敗，未顯示即時郵件 | today's fetch failed, so no live email summary is shown."),
        f"- {_wiki_link(vault_path, latest_gmail_note)}" if latest_gmail_note else "- Gmail digest not available yet.",
        "",
        "### 直接看 | Quick view",
        "",
        _wiki_embed(vault_path, latest_gmail_note, "Messages") if latest_gmail_note else "- 暫無內容。",
        "",
        "## 今日日曆 | Calendar",
        "",
        _freshness_label(today, latest_calendar_note, "Calendar view"),
        f"- 今日日程事件 | calendar events today: {calendar_count} 個" if latest_calendar_note and calendar_count and _status_mode(latest_calendar_note) == "live" else ("- 以最近可用快照呈現 | showing the latest safe snapshot." if _status_mode(latest_calendar_note) == "snapshot" else "- 今日抓取失敗，不能把空白視為沒行程 | today's fetch failed, so blank does not mean no events."),
        f"- {_wiki_link(vault_path, latest_calendar_note)}" if latest_calendar_note else "- Calendar view not available yet.",
        "",
        "### 直接看 | Quick view",
        "",
        _wiki_embed(vault_path, latest_calendar_note, "Today") if latest_calendar_note else "- 暫無內容。",
        "",
        "## Google 提醒清單 | Reminder List",
        "",
        _freshness_label(today, latest_google_tasks_note, "Google Tasks"),
        f"- 未完成提醒 | open reminders: {google_tasks_count} 個" if latest_google_tasks_note and google_tasks_count and _status_mode(latest_google_tasks_note) == "live" else ("- 以最近可用快照呈現 | showing the latest safe snapshot." if _status_mode(latest_google_tasks_note) == "snapshot" else "- 今日抓取失敗，不能把空白視為沒有提醒 | today's fetch failed, so blank does not mean no reminders."),
        f"- {_wiki_link(vault_path, latest_google_tasks_note)}" if latest_google_tasks_note else "- Google Tasks not available yet.",
        "",
        "### 直接看 | Quick view",
        "",
        _wiki_embed(vault_path, latest_google_tasks_note, "Reminder List") if latest_google_tasks_note else "- 暫無內容。",
        "",
        "## 今日待辦 | Today",
        "",
        f"- {_wiki_link(vault_path, manual_tasks_note)}",
        "- 請手動寫下你今天真正要做的事 | write your real commitments by hand",
        "",
        "### 你今天手打的待辦 | Your manual tasks today",
        "",
        _wiki_embed(vault_path, manual_tasks_note, "Today"),
        "",
        "## 今日摘要總覽 | Daily Brief",
        "",
        _freshness_label(today, latest_daily_brief, "Daily brief"),
        f"- {_wiki_link(vault_path, latest_daily_brief)}" if latest_daily_brief else "- Daily brief not available yet.",
        "",
        "### 直接看 | Quick view",
        "",
        _wiki_embed(vault_path, latest_daily_brief) if latest_daily_brief else "- 暫無內容。",
        "",
        "## 今日研究雷達 | Research Radar",
        "",
    ]

    if research_low_pressure:
        lines += [
            "### Thesis 工作台 | Thesis Workspace",
            "",
            "- [[Resources/Research Sources/Master Thesis/Master Thesis Literature Mapping]]",
            "- 今天先維持 thesis mapping 與概念整理，不要被背景筆記分心 | Stay with thesis mapping and concept framing instead of bouncing into backlog notes.",
            "",
        ]

    if latest_auto_notes:
        lines += [
            "### 自動抓取論文 | Auto Papers",
            "",
        ]
        for note in latest_auto_notes:
            lines.append(f"- {_wiki_link(vault_path, note)}")
        lines.append("")

    if latest_web_notes:
        lines += [
            "### 網頁文章 | Web Articles",
            "",
        ]
        for note in latest_web_notes:
            lines.append(f"- {_wiki_link(vault_path, note)}")
        lines.append("")

    if latest_repo_notes:
        lines += [
            "### 研究筆記 | Research Notes",
            "",
        ]
        for note in latest_repo_notes:
            lines.append(f"- {_wiki_link(vault_path, note)}")
        lines.append("")

    if latest_zotero_notes:
        lines += [
            "### Zotero 筆記 | Zotero Notes",
            "",
        ]
        for note in latest_zotero_notes:
            lines.append(f"- {_wiki_link(vault_path, note)}")
        lines.append("")

    if latest_digest_notes:
        lines += [
            "### 今日研究摘要 | Daily Digests",
            "",
            _freshness_label(today, latest_research_digest, "Research digest"),
            f"- 今日研究雷達 | today's paper radar: {digest_count} 篇" if digest_count else "- 已切到低壓閱讀模式或最近版本 | using low-pressure mode or latest available version",
            "",
            "#### 直接看今天摘要 | Quick view today",
            "",
            _wiki_embed(vault_path, latest_research_digest, "Today") if latest_research_digest else "- 暫無內容。",
            "",
            "#### 原始筆記 | Source notes",
            "",
        ]
        for note in latest_digest_notes:
            lines.append(f"- {_wiki_link(vault_path, note)}")
        lines.append("")

    if not latest_digest_notes and not latest_auto_notes and not latest_web_notes and not latest_repo_notes and not latest_zotero_notes:
        lines += [
            "- 目前還沒有研究重點 | No research highlights yet.",
            "- 之後你存下文章或論文筆記，這裡會自動出現 | After you save article or paper notes, they will appear here automatically.",
            "",
        ]

    lines += [
        "## 下一步 | Next step",
        "",
        "- 先在這一頁完成早晨掃描 | Finish your morning scan on this page first.",
        "- 再把真正承諾事項手動寫進 [[Resources/Operating Feed/Today Manual Tasks]] | Then manually write real commitments into [[Resources/Operating Feed/Today Manual Tasks]].",
        "- 如果今天是閱讀日，再往下看研究摘要，不必一開始就打開很多頁 | If today is a reading day, continue into the research section without opening many separate pages.",
        "",
    ]
    content = "\n".join(lines).rstrip() + "\n"
    html_page = _build_html_page(
        today=today,
        generated_at=generated_at,
        fallback_sources=fallback_sources,
        api_health_note=api_health_note if api_health_note.exists() else None,
        weather_note=latest_weather_note,
        market_note=latest_market_note,
        gmail_note=latest_gmail_note,
        calendar_note=latest_calendar_note,
        google_tasks_note=latest_google_tasks_note,
        manual_tasks_note=manual_tasks_note,
        daily_brief_note=latest_daily_brief,
        notion_status_note=notion_status_note if notion_status_note.exists() else None,
        research_digest_note=latest_research_digest,
    )

    if args.dry_run:
        print(content)
        logger.info("Dry run complete for %s", output_path)
        return 0

    ensure_dir(output_path.parent)
    output_path.write_text(content, encoding="utf-8")
    today_hub_path.write_text(content, encoding="utf-8")
    today_hub_html_path.write_text(html_page, encoding="utf-8")
    dated_hub_html_path.write_text(html_page, encoding="utf-8")
    root_today_html_path.write_text(html_page, encoding="utf-8")
    logger.info("Wrote operating feed to %s", output_path)
    logger.info("Refreshed Today Hub at %s", today_hub_path)
    logger.info("Wrote standalone Today Hub HTML to %s", today_hub_html_path)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
