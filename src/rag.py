"""知识库 / 向量检索 RAG / LLM Wiki —— 文档上传、分片、向量检索与结构化词条。

能力分层（可插拔扩展）：
- 文档源：扫描 docs/**/*.md + 运行时上传文档（add_document / upload_document）。
- 文本分片：按标题 / 空行 / 长度三级切片，超长块保留重叠行。
- 向量检索：VectorStore 把切片编码为哈希 TF-IDF 向量（无需第三方依赖），余弦相似度召回。
- LLM 扩展：抽象 LLMProvider，默认 OpenAI 兼容实现；无密钥时纯本地抽取式作答。
- 知识图谱扩展：抽象 KnowledgeGraph，默认 SimpleKnowledgeGraph（从切片抽取实体共现）。
- 可审计：答案 / 词条携带来源、标题、得分；无可靠依据时明确转人工。

对外接口（保持向后兼容）：
    KnowledgeBase / get_kb() / answer() / wiki() / MIN_RELEVANCE
新增接口：
    VectorStore / LLMProvider / OpenAICompatLLM / KnowledgeGraph /
    SimpleKnowledgeGraph / add_document() / upload_document() / vector_search()
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.request
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# src/rag.py -> 项目根/docs
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"

# 检索阈值与切片参数（保持原值，兼容既有行为）
MIN_RELEVANCE = 2.5      # 低于此得分视为无可靠依据，转人工
CHUNK_MAX = 300          # 单个切片最大字符数
OVERLAP_LINES = 1        # 超长切片切分时的重叠行数
VECTOR_DIM = 512         # 哈希向量维度（2 的幂，便于取模）

_KEEP = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]+")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")


# ============================================================
# 基础文本处理
# ============================================================
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


# ============================================================
# 扩展接口 1：LLM 提供方（可替换任意大模型后端）
# ============================================================
class LLMProvider(ABC):
    """LLM 抽象接口。实现 generate(question, context) 即可接入任意大模型。"""

    @abstractmethod
    def available(self) -> bool:
        """当前提供方是否可用（如密钥是否配置）。"""

    @abstractmethod
    def generate(self, question: str, context: str) -> Optional[str]:
        """依据给定知识上下文回答问题；失败返回 None 以触发本地降级。"""

    @property
    def name(self) -> str:
        return self.__class__.__name__


class OpenAICompatLLM(LLMProvider):
    """OpenAI 兼容 chat/completions 实现（DeepSeek 等均兼容）。"""

    def __init__(self, api_key: Optional[str] = None,
                 base_url: Optional[str] = None, model: Optional[str] = None,
                 timeout: int = 20):
        self.api_key = api_key if api_key is not None else os.getenv("LLM_API_KEY", "")
        self.base_url = base_url or os.getenv(
            "LLM_BASE_URL", "https://api.deepseek.com/v1/chat/completions"
        )
        self.model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def generate(self, question: str, context: str) -> Optional[str]:
        if not self.available():
            return None
        try:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是酒店房态协同助手，只依据给定知识回答；不确定的一律说明需转人工。"},
                    {"role": "user", "content": f"知识：\n{context}\n\n问题：{question}"},
                ],
                "temperature": 0.2,
            }
            req = urllib.request.Request(
                self.base_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip()
        except Exception:
            return None


# ============================================================
# 扩展接口 2：知识图谱（可替换 Neo4j / RDF 等实现）
# ============================================================
class KnowledgeGraph(ABC):
    """知识图谱抽象接口。预留实体/关系扩展点，默认提供轻量实现。"""

    @abstractmethod
    def build(self, chunks: Sequence[Dict]) -> None:
        """从切片构建图谱。"""

    @abstractmethod
    def related(self, question: str, top_k: int = 5) -> List[Dict]:
        """按问题返回相关实体及其关系上下文。"""

    @property
    def name(self) -> str:
        return self.__class__.__name__


class SimpleKnowledgeGraph(KnowledgeGraph):
    """默认图谱：从切片标题与要点抽取实体，基于共现建立轻量关联（零依赖）。"""

    def __init__(self):
        self.entities: Dict[str, Set[str]] = defaultdict(set)  # 实体 -> 关联实体
        self.chunk_index: Dict[str, Dict] = {}

    def build(self, chunks: Sequence[Dict]) -> None:
        self.entities.clear()
        self.chunk_index.clear()
        # 实体过滤：仅纯中文二字词或英文字母词，排除时间戳/数字噪音
        ent_pattern = re.compile(r"^([\u4e00-\u9fff]{2,4}|[a-z]{3,})$")
        for c in chunks:
            key = f"{c['source']}::{c['heading']}"
            self.chunk_index[key] = c
            toks = [t for t in set(_tokenize(c["text"])) if ent_pattern.match(t)]
            # 按词频优先取前 12 个作为该切片的实体集合
            freq = Counter(_tokenize(c["text"]))
            toks.sort(key=lambda t: freq[t], reverse=True)
            ents = toks[:12]
            for e in ents:
                self.entities[e].update(x for x in ents if x != e)

    def related(self, question: str, top_k: int = 5) -> List[Dict]:
        q = set(_tokenize(question))
        scored: List[Tuple[int, str]] = []
        for ent, neighbors in self.entities.items():
            if ent in q:
                scored.append((len(neighbors), ent))
        scored.sort(reverse=True)
        out = []
        for weight, ent in scored[:top_k]:
            ctx_key = next((k for k in self.chunk_index if ent in k), "")
            c = self.chunk_index.get(ctx_key, {})
            out.append({
                "entity": ent,
                "relations": sorted(self.entities[ent])[:8],
                "source": c.get("source", ""),
                "weight": weight,
            })
        return out


# ============================================================
# 向量检索：哈希 TF-IDF 向量 + 余弦相似度
# ============================================================
class VectorStore:
    """轻量向量库：把文本编码为固定维度哈希向量，余弦相似度检索。

    - 采用 hashing trick + TF-IDF 权重，无需 sklearn / numpy / 向量数据库。
    - 可通过替换 _encode 接入真实 embedding（如 LLMProvider 提供的向量）。
    """

    def __init__(self, dim: int = VECTOR_DIM):
        self.dim = dim
        self.vectors: List[Dict[int, float]] = []   # 每个切片的稀疏向量 {idx: weight}
        self.norms: List[float] = []
        self.df: Counter = Counter()

    @staticmethod
    def _hashes(token: str, dim: int) -> List[int]:
        """一个 token 映射到 2 个哈希位，降低冲突。"""
        h = hashlib.md5(token.encode("utf-8")).hexdigest()
        return [int(h[:8], 16) % dim, int(h[8:16], 16) % dim]

    def _fit_df(self, token_sets: Iterable[Set[str]]) -> None:
        self.df.clear()
        for toks in token_sets:
            self.df.update(toks)

    def _encode(self, text: str) -> Dict[int, float]:
        """文本 -> 归一化 TF-IDF 稀疏向量。"""
        tf = Counter(_tokenize(text))
        if not tf:
            return {}
        total = max(1, len(self.vectors) + 1)
        vec: Dict[int, float] = defaultdict(float)
        for tok, count in tf.items():
            idf = math.log(1 + total / (1 + self.df.get(tok, 0)))
            w = (1 + math.log(count)) * idf
            for idx in self._hashes(tok, self.dim):
                vec[idx] += w
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {i: v / norm for i, v in vec.items()}

    def build(self, texts: Sequence[str]) -> None:
        """根据全部切片文本拟合 IDF 并编码向量。"""
        token_sets = [set(_tokenize(t)) for t in texts]
        self._fit_df(token_sets)
        self.vectors, self.norms = [], []
        for t in texts:
            vec = self._encode(t)
            self.vectors.append(vec)
            self.norms.append(math.sqrt(sum(v * v for v in vec.values())) or 1.0)

    def add(self, text: str) -> None:
        """增量加入单个切片（IDF 近似更新）。"""
        toks = set(_tokenize(text))
        self.df.update(toks)
        vec = self._encode(text)
        self.vectors.append(vec)
        self.norms.append(math.sqrt(sum(v * v for v in vec.values())) or 1.0)

    def search(self, query: str, top_k: int = 3) -> List[Tuple[int, float]]:
        """余弦相似度召回，返回 [(切片下标, 相似度)]，相似度范围约 0~1。"""
        if not self.vectors:
            return []
        qvec = self._encode(query)
        if not qvec:
            return []
        qnorm = math.sqrt(sum(v * v for v in qvec.values())) or 1.0
        scored: List[Tuple[int, float]] = []
        for i, vec in enumerate(self.vectors):
            dot = sum(w * vec.get(idx, 0.0) for idx, w in qvec.items())
            sim = dot / (qnorm * (self.norms[i] or 1.0))
            if sim > 0:
                scored.append((i, round(sim, 6)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:max(1, top_k)]


# ============================================================
# 知识库主体
# ============================================================
class KnowledgeBase:
    """从 docs 下的 Markdown + 运行时上传文档加载、分片并建立索引。"""

    def __init__(self, docs_dir: Path = DOCS_DIR,
                 llm: Optional[LLMProvider] = None,
                 kg: Optional[KnowledgeGraph] = None):
        self.docs_dir = Path(docs_dir)
        self.chunks: List[Dict] = []      # {text, source, title, heading, boost, body_len}
        self._df: Counter = Counter()     # 文档频率（供兼容打分使用）
        self.runtime_docs: List[Tuple[str, str]] = []  # 运行时上传 (source, text)
        self.llm: LLMProvider = llm if llm is not None else OpenAICompatLLM()
        self.kg: KnowledgeGraph = kg if kg is not None else SimpleKnowledgeGraph()
        self.vector_store = VectorStore()
        self.load()

    # ---------- 加载与分片 ----------
    def load(self) -> None:
        """重新扫描 docs 目录 + 运行时文档，构建索引与向量（可重复调用）。"""
        self.chunks.clear()
        self._df.clear()
        if self.docs_dir.exists():
            for md in sorted(self.docs_dir.rglob("*.md")):
                try:
                    raw = md.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                boost = 1.5 if "knowledge_base" in md.parts else 1.0
                self._ingest(raw, md.name, md.stem, boost)
        for source, raw in self.runtime_docs:
            self._ingest(raw, source, Path(source).stem, 1.5)
        self._rebuild_indexes()

    def _ingest(self, raw: str, source: str, title: str, boost: float) -> None:
        """对单篇文档执行分片并写入 chunks。"""
        heading = ""
        para: List[str] = []

        def flush() -> None:
            text = "\n".join(x for x in para if x).strip()
            para.clear()
            if text:
                self._split_and_add(text, source, title, heading, boost)

        for line in raw.splitlines():
            m = _HEADING.match(line)
            if m:
                flush()
                heading = m.group(2).strip()
                self._add_chunk(heading, source, title, heading, boost, len(heading))
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

    def _rebuild_indexes(self) -> None:
        """重建向量索引与知识图谱（文档变更后调用）。"""
        self.vector_store.build([c["text"] for c in self.chunks])
        self.kg.build(self.chunks)

    # ---------- 上传 ----------
    def add_document(self, text: str, source: str = "upload.md") -> int:
        """运行时上传文档：分片入库并重建向量/图谱索引，返回新增切片数。"""
        if not text or not text.strip():
            return 0
        self.runtime_docs.append((source, text))
        before = len(self.chunks)
        self.load()
        return len(self.chunks) - before

    # ---------- 检索 ----------
    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """兼容版检索：TF-IDF 加权打分（与历史行为一致），返回片段列表。"""
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
            score = score / math.sqrt(chunk["body_len"] + 1) * chunk["boost"]
            coverage = len(matched) / len(q_tokens)
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

    def vector_search(self, query: str, top_k: int = 3) -> List[Dict]:
        """向量检索：余弦相似度召回，返回带 similarity 的片段。"""
        hits = self.vector_store.search(query, top_k=top_k)
        results = []
        for i, sim in hits:
            c = self.chunks[i]
            results.append({
                "text": c["text"], "source": c["source"], "title": c["title"],
                "heading": c["heading"], "similarity": sim,
            })
        return results

    def related_entities(self, question: str, top_k: int = 5) -> List[Dict]:
        """知识图谱关联查询（扩展接口）。"""
        return self.kg.related(question, top_k=top_k)


# ---------------- 单例 ----------------
_KB: Optional[KnowledgeBase] = None


def get_kb() -> KnowledgeBase:
    """单例知识库：首次调用时加载，之后复用；重载用 KnowledgeBase() 重建。"""
    global _KB
    if _KB is None:
        _KB = KnowledgeBase()
    return _KB


def add_document(text: str, source: str = "upload.md") -> int:
    """向单例知识库上传文档的便捷函数，返回新增切片数。"""
    return get_kb().add_document(text, source)


def upload_document(text: str, source: str = "upload.md") -> Dict:
    """上传文档并返回结构化结果（供 API 层直接调用）。"""
    added = add_document(text, source)
    return {"ok": True, "source": source, "chunks_added": added,
            "total_chunks": len(get_kb().chunks)}


def vector_search(query: str, top_k: int = 3) -> List[Dict]:
    """向量检索便捷函数。"""
    return get_kb().vector_search(query, top_k=top_k)


# ---------------- 问答与 Wiki（行为保持兼容） ----------------
def _llm_answer(question: str, passages: List[Dict], llm: Optional[LLMProvider] = None) -> Optional[str]:
    """调用 LLM 增强；任何异常返回 None 触发本地回退。"""
    provider = llm if llm is not None else OpenAICompatLLM()
    if not provider.available():
        return None
    context = "\n\n".join(f"[{p['source']}] {p['text']}" for p in passages)
    return provider.generate(question, context)


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
            if s.startswith("["):
                s = s.strip("[] ")
            lines.append(f"{s}（来源：{p['source']}）")
    text = "\n".join(lines[:8])
    return text or "知识库命中内容无法解析为要点，需转人工处理。"


def answer(question: str, top_k: int = 3) -> Dict:
    """RAG 问答：检索 -> 相关性判定 -> LLM/本地作答 -> 携带来源。"""
    kb = get_kb()
    passages = kb.search(question, top_k=top_k)
    if not passages or passages[0]["score"] < MIN_RELEVANCE:
        return {
            "question": question,
            "answer": "知识库中没有可靠相关内容，此问题需转人工处理。",
            "sources": [], "mode": "local", "need_human": True,
        }
    llm = _llm_answer(question, passages, kb.llm)
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
        tags = list(dict.fromkeys([p["heading"]] + [b[:8] for b in bullets[:2] if b]))
        entries.append({
            "title": p["title"], "heading": p["heading"],
            "summary": bullets[0] if bullets else p["text"][:80],
            "key_points": bullets[:6], "tags": [t for t in tags if t],
            "source": p["source"], "score": p["score"],
        })
    return {
        "topic": question, "found": True, "entries": entries, "count": len(entries),
        "note": "仅汇总知识库已有内容；异常与冲突场景须转人工确认。",
    }
