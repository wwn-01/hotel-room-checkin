"""Harness 测试调度器 —— 一键编排单元测试与接口测试，输出结构化结果。

供 DeepSeek Harness / CI / 人工调用，纯标准库，无需 pytest。

用法（在项目根目录执行）：
    python -m tests.harness_runner                 # 运行全部（unit + http）
    python -m tests.harness_runner --suite unit    # 只跑单元测试
    python -m tests.harness_runner --suite http    # 只跑接口测试
    python -m tests.harness_runner --json report.json   # 额外把报告写入文件

输出：
- 人类可读的摘要打印到 stdout；
- 一行机器可读 JSON（前缀 REPORT_JSON=）便于 Harness 解析；
- 退出码 0 表示全部通过，1 表示有失败。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _run_unit() -> dict:
    """运行 tests/ 下 test_*.py 中的所有 unittest 用例，收集结构化结果。"""
    import importlib.util

    class _Result(unittest.TextTestResult):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.passed = 0

        def addSuccess(self, test):
            super().addSuccess(test)
            self.passed += 1

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for mod in ("tests.test_rag", "tests.test_auth"):
        # 模块尚不存在时跳过（如 test_auth 尚未创建），避免生成失败占位用例
        spec = importlib.util.find_spec(mod)
        if spec is not None:
            suite.addTests(loader.loadTestsFromName(mod))

    buf = io.StringIO()
    runner = unittest.TextTestRunner(stream=buf, verbosity=1, resultclass=_Result)
    with redirect_stdout(buf), redirect_stderr(buf):
        result = runner.run(suite)

    failures = len(result.failures)
    errors = len(result.errors)
    detail = [f"{t.id()}: {tb}" for t, tb in (result.failures + result.errors)]
    return {
        "name": "单元测试",
        "total": result.testsRun,
        "passed": result.passed,
        "failures": failures,
        "errors": errors,
        "ok": failures == 0 and errors == 0 and result.testsRun > 0,
        "detail": detail,
        "log": buf.getvalue().strip(),
    }


def _run_http() -> dict:
    """运行接口端到端测试脚本（子进程），依据退出码判定。"""
    import subprocess

    script = TESTS_DIR / "test_api_http.py"
    if not script.exists():
        return {"name": "接口测试", "ok": False, "total": 0, "passed": 0,
                "failures": 1, "errors": 0, "detail": [f"脚本不存在: {script}"], "log": ""}

    proc = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=180,
        env={**dict(__import__("os").environ), "PYTHONIOENCODING": "utf-8"},
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    # 从输出里提取 "全部 N 项端到端检查通过" 的 N
    total = 0
    for line in out.splitlines():
        if "项端到端检查通过" in line:
            digits = "".join(ch for ch in line if ch.isdigit())
            if digits:
                total = int(digits)
    ok = proc.returncode == 0
    return {
        "name": "接口测试",
        "total": total,
        "passed": total if ok else 0,
        "failures": 0 if ok else 1,
        "errors": 0 if ok else 1,
        "ok": ok,
        "detail": [] if ok else [out.strip()[-800:]],
        "log": out.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Harness 测试调度器")
    parser.add_argument("--suite", choices=("unit", "http", "all"), default="all",
                        help="要运行的测试套件（默认 all）")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="将完整报告写入指定 JSON 文件")
    parser.add_argument("--quiet", action="store_true", help="不打印人类可读摘要")
    args = parser.parse_args()

    results = []
    if args.suite in ("unit", "all"):
        results.append(_run_unit())
    if args.suite in ("http", "all"):
        results.append(_run_http())

    all_ok = all(r["ok"] for r in results) and bool(results)
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "suite": args.suite,
        "all_ok": all_ok,
        "suites": results,
        "summary": {
            "total": sum(r["total"] for r in results),
            "passed": sum(r["passed"] for r in results),
        },
    }

    if not args.quiet:
        print("=" * 56)
        print("Harness 测试调度报告")
        print("=" * 56)
        for r in results:
            flag = "PASS" if r["ok"] else "FAIL"
            print(f"[{flag}] {r['name']}: {r['passed']}/{r['total']} 通过"
                  f"（失败 {r['failures']} / 错误 {r['errors']}）")
            for d in r["detail"]:
                print(f"       - {d}")
        print("-" * 56)
        print(f"总计: {report['summary']['passed']}/{report['summary']['total']} 通过"
              f" | 结论: {'全部通过' if all_ok else '存在失败'}")

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if not args.quiet:
            print(f"报告已写入: {args.json_path}")

    # 机器可读单行 JSON，便于 Harness 解析
    print("REPORT_JSON=" + json.dumps(
        {"all_ok": all_ok, "summary": report["summary"]}, ensure_ascii=False
    ))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
