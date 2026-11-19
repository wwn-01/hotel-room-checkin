"""知识库 / RAG / LLM Wiki —— 文档切片、检索、问答与结构化词条。

设计目标：
- 零第三方 NLP 依赖：中文 1-gram + 2-gram，英文整词，TF-IDF 风格打分。
- 无密钥可用：默认本地检索 + 抽取式作答；配置 LLM_API_KEY 后自动升级 LLM 增强，
  接口不可用时静默回退到本地答案。
- 可审计：每条答案/词条都带来源文件、标题与检索得分；无可靠依据时明确转人工。

对外接口：KnowledgeBase / get_kb() / answer() / wiki() / MIN_RELEVANCE。
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set

# src/rag.py -> 项目根/docs
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# 检索相关阈值与切片参数
MIN_RELEVANCE = 2.5      # 低于此得分视为无可靠依据，转人工
CHUNK_MAX = 300          # 单个切片最大字符数
OVERLAP_LINES = 1        # 超长切片切分时的重叠行数，保留上下文

_KEEP = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")


def _tokenize(text: str) -> List[str]:
    """轻量分词：英文/数字整词 + 中文单字与相邻二字词，无需 jieba。"""
    tokens: List[str] = []
    for seg in _KEEP.findall(text.lower()):
        if re.match(r"^[a-z0-9]", seg):
            tokens.append(seg)
        else:
            tokens.extend(list(seg))
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return tokens


def _is_noise(line: str) -> bool:
    """过滤访谈时间戳、分隔线等噪音行。"""
    s = line.strip()
    if not s:
        return True
    if re.match(r"^\d{4}-\d{2}-\d{2}T", s):
        return True
    if s.startswith("访谈者：") or re.match(r"^[-=]{3,}$", s):
        return True
    return False


def _clean(line: str) -> str:
    """去掉简单 HTML 标签与列表符号，保留可读正文。"""
    s = re.sub(r"<[^>]+>", "", line).strip()
    return s.strip(" -•\t")


class KnowledgeBase:
    """从 docs 下的 Markdown 加载、切片并建立词频索引。"""

    def __init__(self, docs_dir: Path = DOCS_DIR):
        self.docs_dir = Path(docs_dir)
        self.chunks: List[Dict] = []      # {text, source, title, heading, boost, body_len}
        self._df: Counter = Counter()     # 文档频率
        self.load()

    def load(self) -> None:
        """重新扫描 docs 目录并构建索引（可重复调用）。"""
        self.chunks.clear()
        self._df.clear()
        if not self.docs_dir.exists():
            return
        for md in sorted(self.docs_dir.rglob("*.md")):
            try:
                raw = md.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            # knowledge_base 目录是权威知识源，检索加权
            boost = 1.5 if "knowledge_base" in md.parts else 1.0
            heading = ""
            para: List[str] = []

            def flush() -> None:
                text = "\n".join(x for x in para if x).strip()
                para.clear()
                if text:
                    self._split_and_add(text, md.name, md.stem, heading, boost)

            for line in raw.splitlines():
                m = _HEADING.match(line)
                if m:
                    flush()
                    heading = m.group(2).strip()
                    # 标题本身作为独立短切片，标题命中是强信号
                    self._add_chunk(heading, md.name, md.stem, heading, boost, len(heading))
                    continue
                if _is_noise(line):
                    flush()
                    continue
                cleaned = _clean(line)
                if cleaned:
                    para.append(cleaned)
                if len("\n".join(para)) >= CHUNK_MAX:
                    flush()
            flush()

    def _split_and_add(self, text: str, source: str, title: str, heading: str, boost: float) -> None:
        """段落超长时按行切分，并保留重叠行避免语义割裂。"""
        if len(text) <= CHUNK_MAX:
            self._add_chunk(self._with_heading(text, heading), source, title, heading, boost, len(text))
            return
        buf: List[str] = []
        for line in text.splitlines():
            if buf and len("\n".join(buf)) + len(line) > CHUNK_MAX:
                joined = "\n".join(buf)
                self._add_chunk(self._with_heading(joined, heading), source, title, heading, boost, len(joined))
                buf = buf[-OVERLAP_LINES:] if OVERLAP_LINES else []
            buf.append(line)
        if buf:
            joined = "\n".join(buf)
            self._add_chunk(self._with_heading(joined, heading), source, title, heading, boost, len(joined))

    @staticmethod
    def _with_heading(text: str, heading: str) -> str:
        """把所属标题拼进切片文本，让'章节语义'参与检索。"""
        return f"[{heading}]\n{text}" if heading else text

    def _add_chunk(self, text: str, source: str, title: str, heading: str,
                   boost: float, body_len: int) -> None:
        toks: Set[str] = set(_tokenize(text))
        if not toks:
            return
        self.chunks.append({
            "text": text, "source": source, "title": title,
            "heading": heading, "boost": boost, "body_len": body_len,
        })
        self._df.update(toks)

    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """TF-IDF 风格检索：返回按得分降序的片段（含来源、标题、覆盖率）。"""
        if not self.chunks:
            return []
        q_tokens = set(_tokenize(query))
        if not q_tokens:
            return []
        total = len(self.chunks)
        scored: List[tuple] = []
        for i, chunk in enumerate(self.chunks):
            tf = Counter(_tokenize(chunk["text"]))
            matched = [t for t in q_tokens if t in tf]
            if not matched:
                continue
            score = 0.0
            for t in matched:
                idf = math.log(1 + total / (1 + self._df.get(t, 0)))
                score += tf[t] * idf
            # 按正文长度归一，避免长文档刷分；knowledge_base 目录加权
            score = score / math.sqrt(chunk["body_len"] + 1) * chunk["boost"]
            coverage = len(matched) / len(q_tokens)  # 查询词覆盖率（仅作参考，不参与得分）
            scored.append((score, i, coverage))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for score, i, coverage in scored[:max(1, top_k)]:
            c = self.chunks[i]
            results.append({
                "text": c["text"], "source": c["source"], "title": c["title"],
                "heading": c["heading"], "score": round(score, 3),
                "coverage": round(coverage, 3),
            })
        return results


_KB: Optional[KnowledgeBase] = None


def get_kb() -> KnowledgeBase:
    """单例知识库：首次调用时加载，之后复用；重载用 KnowledgeBase() 重建。"""
    global _KB
    if _KB is None:
        _KB = KnowledgeBase()
    return _KB


def _llm_available() -> bool:
    return bool(os.getenv("LLM_API_KEY"))


def _llm_answer(question: str, passages: List[Dict]) -> Optional[str]:
    """可选 LLM 增强：OpenAI 兼容接口；任何异常返回 None 触发本地回退。"""
    if not _llm_available():
        return None
    try:
        api_key = os.getenv("LLM_API_KEY")
        base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions")
        model = os.getenv("LLM_MODEL", "deepseek-chat")
        context = "\n\n".join(f"[{p['source']}] {p['text']}" for p in passages)
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是酒店房态协同助手，只依据给定知识回答；不确定的一律说明需转人工。"},
                {"role": "user", "content": f"知识：\n{context}\n\n问题：{question}"},
            ],
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            base,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None


def _local_answer(passages: List[Dict]) -> str:
    """本地抽取式降级：从命中片段挑要点句，附来源标注。"""
    lines: List[str] = []
    for p in passages:
        for ln in p["text"].splitlines():
            s = ln.strip(" -•")
            if len(s) < 8 or not re.search(r"[\u4e00-\u9fff]", s):
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}T", s) or "访谈者：" in s:
                continue
            if s.startswith("["):  # 标题前缀行单独保留
                s = s.strip("[] ")
            lines.append(f"{s}（来源：{p['source']}）")
    text = "\n".join(lines[:8])
    return text or "知识库命中内容无法解析为要点，需转人工处理。"


def answer(question: str, top_k: int = 3) -> Dict:
    """RAG 问答：检索 -> 相关性判定 -> LLM/本地作答 -> 携带来源。"""
    passages = get_kb().search(question, top_k=top_k)
    if not passages or passages[0]["score"] < MIN_RELEVANCE:
        return {
            "question": question,
            "answer": "知识库中没有可靠相关内容，此问题需转人工处理。",
            "sources": [], "mode": "local", "need_human": True,
        }
    llm = _llm_answer(question, passages)
    return {
        "question": question,
        "answer": llm if llm else _local_answer(passages),
        "sources": [{
            "source": p["source"], "title": p["title"],
            "heading": p["heading"], "score": p["score"],
        } for p in passages],
        "mode": "llm" if llm else "local",
        "need_human": False,
    }


def wiki(question: str) -> Dict:
    """LLM Wiki：把检索结果整理成结构化词条（标题/摘要/要点/标签/来源）。"""
    passages = get_kb().search(question, top_k=5)
    if not passages or passages[0]["score"] < MIN_RELEVANCE:
        return {"topic": question, "found": False, "entries": [],
                "note": "知识库无相关词条，需人工补充。"}
    entries = []
    for p in passages:
        bullets = [
            ln.strip(" -•")
            for ln in p["text"].splitlines()
            if len(ln.strip(" -•")) >= 4 and not ln.strip().startswith("#")
        ]
        bullets = [b for b in bullets if not _is_noise(b)]
        tags = list(dict.fromkeys(
            [p["heading"]] + [b[:8] for b in bullets[:2] if b]
        ))
        entries.append({
            "title": p["title"],
            "heading": p["heading"],
            "summary": bullets[0] if bullets else p["text"][:80],
            "key_points": bullets[:6],
            "tags": [t for t in tags if t],
            "source": p["source"],
            "score": p["score"],
        })
    return {
        "topic": question, "found": True, "entries": entries,
        "count": len(entries),
        "note": "仅汇总知识库已有内容；异常与冲突场景须转人工确认。",
    }
