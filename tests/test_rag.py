"""RAG / 知识库 / LLM Wiki 单元测试（纯标准库，无第三方依赖）。

运行：python -m unittest tests.test_rag -v
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import rag  # noqa: E402


class TestTokenize(unittest.TestCase):
    def test_chinese_bigrams(self):
        toks = rag._tokenize("入住")
        self.assertIn("入", toks)
        self.assertIn("住", toks)
        self.assertIn("入住", toks)

    def test_english_whole_word(self):
        self.assertIn("fastapi", rag._tokenize("FastAPI"))

    def test_noise_filter(self):
        self.assertTrue(rag._is_noise("2026-11-19T09:00:00 入住"))
        self.assertTrue(rag._is_noise("访谈者："))
        self.assertFalse(rag._is_noise("房间需要质检后才能入住"))


class TestKnowledgeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = rag.KnowledgeBase()

    def test_chunks_loaded(self):
        self.assertGreater(len(self.kb.chunks), 50)

    def test_chunk_shape(self):
        c = self.kb.chunks[0]
        for k in ("text", "source", "title", "heading", "boost", "body_len"):
            self.assertIn(k, c)

    def test_relevant_ranking(self):
        res = self.kb.search("房间可以入住的前置条件", top_k=3)
        self.assertTrue(res)
        self.assertGreaterEqual(res[0]["score"], rag.MIN_RELEVANCE)

    def test_irrelevant_low_score(self):
        res = self.kb.search("股票今日行情", top_k=3)
        self.assertLess(res[0]["score"], rag.MIN_RELEVANCE)

    def test_custom_docs_dir(self):
        kb = rag.KnowledgeBase(docs_dir=Path("/nonexistent"))
        self.assertEqual(kb.chunks, [])


class TestAnswer(unittest.TestCase):
    def test_relevant_has_sources(self):
        d = rag.answer("维修房如何解封", top_k=3)
        self.assertFalse(d["need_human"])
        self.assertTrue(d["sources"])
        self.assertIn(d["mode"], ("local", "llm"))
        self.assertIn("来源", d["answer"])

    def test_irrelevant_need_human(self):
        d = rag.answer("股票今日行情")
        self.assertTrue(d["need_human"])
        self.assertEqual(d["sources"], [])

    def test_llm_key_missing_fallback(self):
        key = os.environ.pop("LLM_API_KEY", None)
        try:
            d = rag.answer("什么情况下房间可以入住")
            self.assertEqual(d["mode"], "local")
        finally:
            if key is not None:
                os.environ["LLM_API_KEY"] = key

    def test_llm_bad_endpoint_fallback(self):
        old = (os.environ.get("LLM_API_KEY"), os.environ.get("LLM_BASE_URL"))
        os.environ["LLM_API_KEY"] = "test-key"
        os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/invalid"
        try:
            d = rag.answer("什么情况下房间可以入住")
            self.assertEqual(d["mode"], "local")
        finally:
            for k, v in zip(("LLM_API_KEY", "LLM_BASE_URL"), old):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class TestWiki(unittest.TestCase):
    def test_wiki_entries(self):
        d = rag.wiki("角色与权限")
        self.assertTrue(d["found"])
        self.assertGreater(d["count"], 0)
        e = d["entries"][0]
        for k in ("title", "heading", "summary", "key_points", "tags", "source", "score"):
            self.assertIn(k, e)

    def test_wiki_unrelated(self):
        d = rag.wiki("股票今日行情")
        self.assertFalse(d["found"])
        self.assertEqual(d["entries"], [])


class TestVectorStore(unittest.TestCase):
    def test_build_and_search(self):
        Texts = ["房间入住需要质检通过", "宠物入住需缴纳押金", "维修中的房间不可入住"]
        vs = rag.VectorStore()
        vs.build(Texts)
        hits = vs.search("宠物押金", top_k=2)
        self.assertTrue(hits)
        self.assertGreaterEqual(hits[0][1], 0.0)
        self.assertLessEqual(hits[0][1], 1.0)  # 余弦相似度范围

    def test_vector_search_wrapper(self):
        kb = rag.KnowledgeBase()
        res = kb.vector_search("房间入住条件", top_k=3)
        self.assertTrue(res)
        self.assertIn("similarity", res[0])
        self.assertIn("source", res[0])


class TestUpload(unittest.TestCase):
    def test_add_document(self):
        kb = rag.KnowledgeBase()
        before = len(kb.chunks)
        doc = "# 测试规则\n宠物可以入住，需缴纳押金 200 元，导盲犬免费。"
        added = kb.add_document(doc, source="test_upload.md")
        self.assertGreater(added, 0)
        self.assertEqual(len(kb.chunks), before + added)
        # 上传内容应可被检索到
        hits = kb.vector_search("宠物押金", top_k=3)
        self.assertTrue(any(h["source"] == "test_upload.md" for h in hits))

    def test_upload_empty(self):
        kb = rag.KnowledgeBase()
        self.assertEqual(kb.add_document("   ", "empty.md"), 0)

    def test_upload_document_result(self):
        res = rag.upload_document("# 新规则\n质检由主管复核。", source="result.md")
        self.assertTrue(res["ok"])
        self.assertGreater(res["chunks_added"], 0)
        self.assertIn("total_chunks", res)


class TestExtensions(unittest.TestCase):
    def test_llm_provider_interface(self):
        self.assertTrue(issubclass(rag.OpenAICompatLLM, rag.LLMProvider))
        llm = rag.OpenAICompatLLM(api_key="")  # 无密钥 -> 不可用
        self.assertFalse(llm.available())
        self.assertIsNone(llm.generate("q", "ctx"))

    def test_kg_interface(self):
        self.assertTrue(issubclass(rag.SimpleKnowledgeGraph, rag.KnowledgeGraph))
        kb = rag.KnowledgeBase()
        ents = kb.related_entities("入住 质检", top_k=3)
        self.assertIsInstance(ents, list)
        if ents:
            self.assertIn("entity", ents[0])
            self.assertIn("relations", ents[0])

    def test_custom_llm_injection(self):
        class FakeLLM(rag.LLMProvider):
            def available(self):
                return True

            def generate(self, question, context):
                return "这是测试 LLM 的答案"

        kb = rag.KnowledgeBase(llm=FakeLLM())
        self.assertEqual(kb.llm.name, "FakeLLM")
        self.assertEqual(kb.llm.generate("q", "c"), "这是测试 LLM 的答案")


if __name__ == "__main__":
    unittest.main(verbosity=2)
