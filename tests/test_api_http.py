"""API 端到端冒烟测试：真实启动服务，验证 JWT 权限 + RAG 接口联动。

运行：python tests/test_api_http.py
依赖：仅标准库（urllib），需先 pip install -r src/requirements.txt
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8140
BASE = f"http://127.0.0.1:{PORT}"


def req(path, method="GET", data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data is not None else None
    r = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    # 用独立端口与干净数据库启动
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "src.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    ok = 0
    try:
        for _ in range(40):
            try:
                urllib.request.urlopen(BASE + "/docs", timeout=1)
                break
            except Exception:
                time.sleep(0.25)

        # 1. 登录
        code, admin = req("/login", "POST", {"username": "admin", "password": "admin123"})
        assert code == 200, f"admin login failed: {code} {admin}"
        code, user = req("/login", "POST", {"username": "user", "password": "user123"})
        assert code == 200, f"user login failed: {code} {user}"
        at, ut = admin["access_token"], user["access_token"]
        print("[ok] JWT 登录 admin/user 均成功")
        ok += 1

        # 2. 权限：user 读 /logs 被拒
        code, _ = req("/logs", token=ut)
        assert code == 403, f"user /logs expected 403, got {code}"
        print("[ok] 普通用户访问 /logs 返回 403")
        ok += 1

        # 3. 知识库统计
        code, stats = req("/knowledge/stats", token=ut)
        assert code == 200 and stats["chunks"] > 50, stats
        print(f"[ok] 知识库统计：{stats['chunks']} 个切片")
        ok += 1

        # 4. RAG 问答（相关）
        code, ans = req("/rag/ask", "POST", {
            "question": "房间可以入住的前置条件", "top_k": 3}, token=ut)
        assert code == 200 and not ans["need_human"] and ans["sources"], ans
        print(f"[ok] RAG 相关问答：mode={ans['mode']}, 来源数={len(ans['sources'])}")
        ok += 1

        # 5. RAG 问答（无关转人工）
        code, ans = req("/rag/ask", "POST", {"question": "股票今日行情"}, token=ut)
        assert code == 200 and ans["need_human"], ans
        print("[ok] RAG 无关问题正确转人工")
        ok += 1

        # 6. LLM Wiki
        code, wiki = req("/rag/wiki", "POST", {"question": "角色与权限"}, token=ut)
        assert code == 200 and wiki["found"] and wiki["count"] > 0, wiki
        print(f"[ok] LLM Wiki：生成 {wiki['count']} 条词条")
        ok += 1

        # 7. 知识库重载（仅 admin）
        code, reload_res = req("/knowledge/reload", token=at)
        assert code == 200 and reload_res["chunks"] > 50, reload_res
        print(f"[ok] 管理员重载知识库：{reload_res['chunks']} 个切片")
        ok += 1

        # 8. 输入校验：空问题 / 超限 top_k
        code, _ = req("/rag/ask", "POST", {"question": "", "top_k": 3}, token=ut)
        assert code == 422, f"empty question expected 422, got {code}"
        code, _ = req("/rag/ask", "POST", {"question": "入住", "top_k": 99}, token=ut)
        assert code == 422, f"top_k=99 expected 422, got {code}"
        print("[ok] 输入校验：空问题与超限 top_k 返回 422")
        ok += 1

        print(f"\n全部 {ok} 项端到端检查通过")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        db = ROOT / "hotel.db"
        if db.exists():
            db.unlink()


if __name__ == "__main__":
    main()
