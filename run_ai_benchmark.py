"""Run synthetic live comparisons using process variables only; never read .env/secrets."""
import argparse
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="真实AI对照；只使用当前进程配置，禁止读取密钥文件。")
    parser.add_argument("--live", action="store_true", help="明确运行真实模型请求")
    parser.add_argument("--database", default="data/wuma_ai_rehearsal.db")
    parser.add_argument("--repeats", type=int, choices=[1, 2], default=2)
    parser.add_argument("--scope", choices=["all", "focused", "complex"], default="all")
    parser.add_argument("--steps", type=int, default=26)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("真实对照需使用--live；离线程序验收请运行run_tests.py。")
    if not 1 <= args.steps <= 100:
        parser.error("--steps需在1至100之间。")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    def audit(event, values):
        if event == "open" and isinstance(values[0], (str, bytes, os.PathLike)):
            name = Path(os.fsdecode(values[0])).name.lower()
            if name == ".env" or name.startswith(".env.") or name == "secrets.toml":
                raise RuntimeError("真实对照命令禁止读取密钥文件；请在已配置的网页教师端运行。")
    sys.addaudithook(audit)
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    os.environ["WUMA_DEMO_MODE"] = "false"
    database = Path(args.database).resolve()
    os.environ["WUMA_AI_DB_PATH"] = str(database)
    import ai_workflow
    import evaluation_benchmark as benchmark
    import llm_service as service
    if not service.is_configured():
        print("当前进程没有可用的模型配置。未读取.env、未发送API请求；请在悟码AI系统设置－高级诊断的“AI评价对照测试”运行。")
        return 2
    metadata = {"provider": service.provider_name(), "model": service.model_name(),
                "evaluation_version": service.EVALUATION_VERSION}
    run = benchmark.load_latest(database) if args.resume else None
    if run and run["model"] != metadata:
        print("模型或评价规则已改变，请开始新一轮对照。")
        return 2
    run = run or benchmark.create_run(database, metadata, args.repeats, args.scope)
    performed = 0
    try:
        with benchmark.claim(run["id"]):
            for item in run["plan"]:
                if item["id"] in run["results"]:
                    continue
                if performed >= args.steps:
                    break
                print(f"第{item['round']}轮：{item['case']} · {item.get('number', '初评')}", flush=True)
                started = time.monotonic()
                try:
                    result = ai_workflow.Operation(seconds=60).run(lambda: benchmark.evaluate(item, service))
                    run["results"][item["id"]] = {"evaluation": result, "seconds": round(time.monotonic() - started, 2)}
                    run["failed_item"] = ""
                    benchmark.save_run(database, run)
                except Exception:
                    run["failed_item"] = item["id"]
                    benchmark.save_run(database, run)
                    raise
                performed += 1
    except (service.LLMServiceError, ai_workflow.WorkflowError, OSError, ValueError) as error:
        print("对照中断，已完成结果保留：" + str(error))
        return 1
    rows = benchmark.rows_for(run)
    print(f"已完成{len(run['results'])}/{len(run['plan'])}条请求；逐点一致{sum(row['是否一致']=='是' for row in rows)}/{len(rows)}。")
    print("结果文件：" + str(benchmark.folder(database) / (run["id"] + ".json")))
    for row in rows:
        if row["需核查"]:
            print(f"需核查：{row['案例']} · {row['考查要点']} · 教师预期{row['教师预期']} · AI判断{row['AI判断']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
