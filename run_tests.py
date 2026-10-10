"""Run the offline suite without reading secrets or contacting model services."""

import argparse
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description="使用临时数据库运行离线测试，不读取.env或调用真实模型。")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--pattern", default="test*.py")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    def audit(event, values):
        if event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
            name = Path(os.fsdecode(values[0])).name.lower()
            if name == ".env" or name.startswith(".env.") or name == "secrets.toml":
                raise RuntimeError("离线测试禁止读取密钥配置文件。")
        if event == "socket.connect":
            raise RuntimeError("离线测试禁止网络调用。")

    sys.addaudithook(audit)
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    for key in tuple(os.environ):
        if key.startswith(("LLM_", "HUNYUAN_", "DEEPSEEK_", "OPENAI_", "WUMA_", "CODE_", "DOCKER_")) or key == "TEACHER_PASSWORD":
            os.environ.pop(key, None)
    os.environ["WUMA_DEMO_MODE"] = "false"
    os.environ["CODE_EXECUTION_ENABLED"] = "false"
    logging.disable(logging.WARNING)
    root = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix="wuma-offline-tests-") as folder:
        os.environ["WUMA_AI_DB_PATH"] = str(Path(folder) / "isolated.db")
        suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=args.pattern)
        import browser_session
        def completed_browser_bridge(*, data, **kwargs):
            # AppTest cannot execute component JavaScript. Simulate the completed
            # empty browser handshake; refresh tests supply their own saved view.
            return {"request": data["request"], "operation": data["operation"],
                    "status": "ready", "token": "", "view": {}}
        with patch.object(browser_session, "_bridge", side_effect=completed_browser_bridge):
            result = unittest.TextTestRunner(verbosity=1 if args.quiet else 2).run(suite)
        passed = result.testsRun - len(result.errors) - len(result.failures) - len(result.skipped)
        print(f"测试{result.testsRun}项：通过{passed}项，失败{len(result.failures)}项，报错{len(result.errors)}项，跳过{len(result.skipped)}项。")
        for test, reason in result.skipped:
            print(f"跳过：{test}；{reason}")
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
