"""Start one explicit Web mode and export its matching, secret-free MCP config."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import runtime_config


def connector_config(database, demo=False):
    return {
        "mcpServers": {
            "wuma-ai": {
                "type": "stdio",
                "command": str(Path(sys.executable).resolve()),
                "args": [
                    "-X", "utf8", "-B",
                    str(runtime_config.PROJECT_ROOT / "learnbuddy" / "mcp_server.py"),
                ],
                "env": {
                    "WUMA_AI_DB_PATH": str(Path(database).resolve()),
                    "WUMA_DEMO_MODE": "true" if demo else "false",
                    "WUMA_DEMO_READ_ONLY": "true",
                    "PYTHONUTF8": "1",
                },
                "description": "悟码AI教师诊断只读连接器",
                "disabled": False,
            }
        }
    }


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="统一启动悟码AI并生成同库的LearnBuddy连接器配置。")
    parser.add_argument("--mode", choices=["local", "demo"], default="local")
    parser.add_argument("--database", help="独立数据库路径；相对路径以项目目录为准。")
    parser.add_argument("--synthetic", action="store_true", help="为完整本地演练准备合成数据，默认使用独立的rehearsal数据库。")
    parser.add_argument("--prepare-only", action="store_true", help="只准备数据和连接器配置，不启动网页。")
    parser.add_argument("--config", type=Path, default=runtime_config.PROJECT_ROOT / "learnbuddy" / "mcp_config.generated.json")
    args = parser.parse_args(argv)
    demo = args.mode == "demo"
    if args.database:
        database = Path(args.database).expanduser()
        if not database.is_absolute():
            database = runtime_config.PROJECT_ROOT / database
    elif demo:
        database = runtime_config.DEFAULT_DEMO_DB
    elif args.synthetic:
        database = runtime_config.PROJECT_ROOT / "data" / "wuma_ai_rehearsal.db"
    else:
        database = runtime_config.DEFAULT_BUSINESS_DB
    database = database.resolve()
    config_path = args.config.expanduser().resolve()
    if config_path.suffix.lower() != ".json" or config_path == database:
        parser.error("连接器配置必须写入独立的.json文件，不能覆盖数据库或密钥配置。")
    os.environ["WUMA_DEMO_MODE"] = "true" if demo else "false"
    os.environ["WUMA_DEMO_READ_ONLY"] = "true"
    os.environ["WUMA_AI_DB_PATH"] = str(database)
    if demo:
        os.environ["CODE_EXECUTION_ENABLED"] = "false"

    # Imported after selecting the mode; no .env or model module is imported here.
    import database as db
    import demo_mode
    db.DB_PATH = database
    try:
        if demo or args.synthetic:
            demo_mode.prepare_demo_database(database)
        db.init_db()
        if demo or args.synthetic:
            demo_mode.ensure_demo_data(force=args.synthetic)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(connector_config(database, demo), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"启动已停止：{error}", file=sys.stderr)
        return 1
    print(f"运行模式：{'赛事只读Demo' if demo else '完整本地版'}")
    print(f"数据库：{database}")
    print(f"数据源标识：{runtime_config.database_id(database)}")
    print(f"LearnBuddy配置：{config_path}")
    if args.prepare_only:
        return 0
    if importlib.util.find_spec("streamlit") is None:
        print("当前Python未安装Streamlit，请使用已安装项目依赖的虚拟环境运行。", file=sys.stderr)
        return 1
    return subprocess.call(
        [sys.executable, "-X", "utf8", "-m", "streamlit", "run",
         str(runtime_config.PROJECT_ROOT / "app.py")],
        cwd=runtime_config.PROJECT_ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
