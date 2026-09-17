"""C++代码验证引擎。

支持兼容旧版本的本机g++后端，以及限制网络和资源的Docker隔离后端。Docker
模式适合评审演示和受控课堂部署，但仍不等同于经过安全审计的生产级判题集群。
"""

from __future__ import annotations

import math
import os
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_COMPILE_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_OUTPUT_CHARS = 4000
MAX_JUDGED_OUTPUT_CHARS = 2_000_000
MAX_CUSTOM_INPUT_CHARS = 200_000
EVIDENCE_PREVIEW_CHARS = 20_000
DEFAULT_DOCKER_IMAGE = "gcc:13-bookworm"
DOCKER_STATUS_TIMEOUT_SECONDS = 5.0

CPP_STANDARD_LABELS = {
    "auto": "自动选择",
    "c++11": "C++11",
    "c++14": "C++14",
    "c++17": "C++17",
}


def normalize_cpp_standard(value):
    """规范实验指定的C++标准，旧任务默认使用自动兼容。"""
    standard = str(value or "auto").strip().lower()
    if standard not in CPP_STANDARD_LABELS:
        raise ValueError("C++编译标准只能是自动选择、C++11、C++14或C++17。")
    return standard


def cpp_standard_label(value):
    return CPP_STANDARD_LABELS[normalize_cpp_standard(value)]


def _standard_attempts(requested_standard):
    """同时兼容现代标准名称与旧版GCC使用的草案名称。"""
    aliases = {
        "c++17": ["c++17", "c++1z"],
        "c++14": ["c++14", "c++1y"],
        "c++11": ["c++11", "c++0x"],
    }
    standards = (
        ["c++17", "c++14", "c++11"]
        if requested_standard == "auto"
        else [requested_standard]
    )
    return [
        (standard, alias)
        for standard in standards
        for alias in aliases[standard]
    ]


def _unsupported_standard_option(message):
    lowered = str(message or "").lower()
    option_error = any(
        phrase in lowered
        for phrase in (
            "unrecognized command line option",
            "unknown argument",
            "unsupported option",
            "invalid value",
        )
    )
    return option_error and ("-std=" in lowered or "language standard" in lowered)


def _env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def execution_enabled():
    """是否允许执行学生代码；默认关闭。"""
    return _env_flag("CODE_EXECUTION_ENABLED", False)


def compiler_name():
    return os.getenv("CXX_COMPILER", "g++").strip() or "g++"


def execution_backend_name():
    """读取代码执行后端；旧配置继续默认使用本机g++。"""
    return os.getenv("CODE_EXECUTION_BACKEND", "local").strip().lower() or "local"


def docker_command():
    return os.getenv("DOCKER_COMMAND", "docker").strip() or "docker"


def docker_cpp_image():
    return os.getenv("DOCKER_CPP_IMAGE", DEFAULT_DOCKER_IMAGE).strip() or DEFAULT_DOCKER_IMAGE


def _compiler_path():
    configured = compiler_name()
    path = Path(configured)
    if path.is_file():
        return str(path)
    return shutil.which(configured)


def _local_runtime_status():
    """返回页面可直接展示的运行环境状态。"""
    path = _compiler_path()
    if not execution_enabled():
        return {
            "available": False,
            "label": "本机代码验证未启用",
            "detail": "在.env中设置CODE_EXECUTION_ENABLED=true后启用。",
            "compiler": compiler_name(),
        }
    if not path:
        return {
            "available": False,
            "label": "未找到C++编译器",
            "detail": "请安装g++并加入PATH，或通过CXX_COMPILER指定完整路径。",
            "compiler": compiler_name(),
        }
    return {
        "available": True,
        "label": "本机代码验证可用",
        "detail": f"编译器：{path}",
        "compiler": path,
    }


def normalize_output(value):
    """忽略行末空格和首尾空行，保留行内空格与换行结构。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def analyze_output_difference(expected_output, actual_output):
    """定位标准化后的首个输出差异，并给出适合学生阅读的说明。"""
    expected = normalize_output(expected_output)
    actual = normalize_output(actual_output)
    if expected == actual:
        return {
            "different": False,
            "kind": "没有差异",
            "line": None,
            "column": None,
            "detail": "标准化后的实际输出与预期输出一致。",
        }

    index = 0
    common_length = min(len(expected), len(actual))
    while index < common_length and expected[index] == actual[index]:
        index += 1

    expected_char = expected[index] if index < len(expected) else None
    actual_char = actual[index] if index < len(actual) else None
    position_text = expected[:index] if expected_char is not None else actual[:index]
    line = position_text.count("\n") + 1
    column = len(position_text.rsplit("\n", 1)[-1]) + 1

    if expected_char == "\n" and actual_char != "\n":
        kind = "缺少换行"
        detail = "实际输出在此处缺少换行。"
    elif actual_char == "\n" and expected_char != "\n":
        kind = "多余换行"
        detail = "实际输出在此处多了一次换行。"
    elif expected_char == "\t" and actual_char != "\t":
        kind = "缺少制表符"
        detail = "实际输出在此处缺少制表符。"
    elif actual_char == "\t" and expected_char != "\t":
        kind = "多余制表符"
        detail = "实际输出在此处多了一个制表符。"
    elif expected_char == " " and actual_char != " ":
        count = 0
        while index + count < len(expected) and expected[index + count] == " ":
            count += 1
        kind = "缺少空格"
        detail = f"实际输出在此处缺少{count}个空格。"
    elif actual_char == " " and expected_char != " ":
        count = 0
        while index + count < len(actual) and actual[index + count] == " ":
            count += 1
        kind = "多余空格"
        detail = f"实际输出在此处多了{count}个空格。"
    elif expected_char is None:
        kind = "多余内容"
        detail = "预期输出已经结束，但实际输出仍有多余内容。"
    elif actual_char is None:
        kind = "输出不完整"
        detail = "实际输出提前结束，缺少后续内容。"
    else:
        kind = "内容不同"
        detail = (
            f"预期字符为“{expected_char}”，实际字符为“{actual_char}”。"
        )

    return {
        "different": True,
        "kind": kind,
        "line": line,
        "column": column,
        "detail": detail,
    }


def _bounded_float_env(name, default, minimum, maximum):
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _bounded_int_env(name, default, minimum, maximum):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _execution_limits(timeout_seconds, max_output_chars):
    """POSIX下附加资源上限；Windows公开部署仍必须使用独立沙箱。"""
    if os.name != "posix":
        return None

    def apply_limits():
        import resource

        cpu_seconds = max(1, math.ceil(timeout_seconds) + 1)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        memory_bytes = 256 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        file_bytes = max(65536, max_output_chars * 8)
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))

    return apply_limits


def _creation_flags():
    if os.name != "nt":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess,
        "CREATE_NEW_PROCESS_GROUP",
        0,
    )


def _stop_process(process):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (ProcessLookupError, OSError):
        process.kill()
    process.wait()


def _read_limited(path, maximum):
    with path.open("r", encoding="utf-8", errors="replace") as file:
        text = file.read(maximum + 1)
    if len(text) > maximum:
        return text[:maximum], True
    return text, False


def _case_output_limit(case, configured_limit):
    """按标准答案长度放宽合法输出，同时保留全局硬上限。"""
    if not case.get("compare_output", True):
        return configured_limit
    expected_length = len(str(case.get("expected_output", "")))
    return min(
        MAX_JUDGED_OUTPUT_CHARS,
        max(configured_limit, expected_length + 1024),
    )


def _balanced_weights(amount, total=100):
    if amount <= 0:
        return []
    base, remainder = divmod(total, amount)
    return [base + (1 if index < remainder else 0) for index in range(amount)]


def _case_scoring_metadata(test_cases):
    """兼容旧样例；正式隐藏测试使用教师权重，公开样例自动平均。"""
    comparable_indices = [
        index
        for index, case in enumerate(test_cases)
        if case.get("compare_output", True)
    ]
    weights = [0] * len(test_cases)
    explicit = []
    missing = []
    for index in comparable_indices:
        raw_weight = test_cases[index].get("weight")
        try:
            weight = int(raw_weight)
        except (TypeError, ValueError):
            weight = 0
        if weight > 0:
            explicit.append((index, weight))
            weights[index] = weight
        else:
            missing.append(index)

    if comparable_indices and len(missing) == len(comparable_indices):
        for index, weight in zip(
            comparable_indices,
            _balanced_weights(len(comparable_indices)),
        ):
            weights[index] = weight
    elif missing:
        remaining = max(0, 100 - sum(weight for _, weight in explicit))
        distributed = _balanced_weights(len(missing), remaining)
        for index, weight in zip(missing, distributed):
            weights[index] = weight

    return [
        {
            "group": str(case.get("group") or "未分组")[:40],
            "weight": weights[index],
        }
        for index, case in enumerate(test_cases)
    ]


def _verdict_code(status):
    return {
        "通过": "AC",
        "未通过": "WA",
        "超时": "TLE",
        "运行错误": "RE",
        "输出过长": "OLE",
        "运行成功": "RUN",
    }.get(status, "UNKNOWN")


def _group_score_summary(case_results):
    groups = {}
    for item in case_results:
        group = item.get("group") or "未分组"
        summary = groups.setdefault(
            group,
            {"group": group, "score": 0, "max_score": 0, "passed": 0, "total": 0},
        )
        summary["score"] += int(item.get("score", 0) or 0)
        summary["max_score"] += int(item.get("weight", 0) or 0)
        summary["passed"] += int(item.get("status") == "通过")
        summary["total"] += 1
    return list(groups.values())


def _base_result(status, message, compiler, requested_standard="auto"):
    requested_standard = normalize_cpp_standard(requested_standard)
    return {
        "enabled": False,
        "overall_status": status,
        "message": message,
        "compiler": compiler,
        "requested_cpp_standard": requested_standard,
        "effective_cpp_standard": "",
        "compile": {
            "status": "未执行",
            "message": message,
            "duration_ms": 0,
            "requested_standard": requested_standard,
            "effective_standard": "",
            "standard_flag": "",
        },
        "passed": 0,
        "total": 0,
        "score": 0,
        "max_score": 0,
        "group_scores": [],
        "cases": [],
        "executed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _verify_cpp_code_local(code, test_cases, cpp_standard="auto"):
    """编译并运行C++代码，返回可序列化的客观证据。"""
    requested_standard = normalize_cpp_standard(cpp_standard)
    status = _local_runtime_status()
    compiler = status["compiler"]
    if not execution_enabled():
        return _base_result("未启用", status["detail"], compiler, requested_standard)
    if not status["available"]:
        return _base_result("环境不可用", status["detail"], compiler, requested_standard)
    if not str(code).strip():
        return _base_result("未执行", "代码为空，无法验证。", compiler, requested_standard)
    if not test_cases:
        return _base_result(
            "未执行",
            "当前实验没有配置测试样例。",
            compiler,
            requested_standard,
        )

    timeout_seconds = _bounded_float_env(
        "CODE_EXECUTION_TIMEOUT_SECONDS",
        DEFAULT_TIMEOUT_SECONDS,
        0.5,
        5.0,
    )
    max_output = _bounded_int_env(
        "CODE_EXECUTION_MAX_OUTPUT_CHARS",
        DEFAULT_MAX_OUTPUT_CHARS,
        500,
        MAX_JUDGED_OUTPUT_CHARS,
    )
    compile_timeout = _bounded_float_env(
        "CODE_COMPILE_TIMEOUT_SECONDS",
        DEFAULT_COMPILE_TIMEOUT_SECONDS,
        3.0,
        30.0,
    )

    result = _base_result("未执行", "", compiler, requested_standard)
    result["enabled"] = True
    scoring_metadata = _case_scoring_metadata(test_cases)
    result["max_score"] = sum(item["weight"] for item in scoring_metadata)
    empty_group_cases = [
        {
            "group": item["group"],
            "weight": item["weight"],
            "score": 0,
            "status": "未执行",
        }
        for item in scoring_metadata
    ]
    result["group_scores"] = _group_score_summary(empty_group_cases)
    with tempfile.TemporaryDirectory(prefix="wuma_verify_") as folder:
        workdir = Path(folder)
        source_path = workdir / "main.cpp"
        program_path = workdir / ("main.exe" if os.name == "nt" else "main")
        source_path.write_text(str(code), encoding="utf-8")

        compile_started = time.perf_counter()
        completed = None
        effective_standard = ""
        standard_flag = ""
        unsupported_messages = []
        for candidate_standard, candidate_alias in _standard_attempts(requested_standard):
            candidate_flag = f"-std={candidate_alias}"
            try:
                candidate_result = subprocess.run(
                    [
                        compiler,
                        candidate_flag,
                        "-O2",
                        str(source_path),
                        "-o",
                        str(program_path),
                    ],
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=compile_timeout,
                    shell=False,
                    creationflags=_creation_flags(),
                )
            except subprocess.TimeoutExpired:
                result["overall_status"] = "编译超时"
                result["message"] = "编译超过时间限制。"
                result["compile"].update(
                    {
                        "status": "超时",
                        "message": result["message"],
                        "duration_ms": round(
                            (time.perf_counter() - compile_started) * 1000
                        ),
                        "standard_flag": candidate_flag,
                    }
                )
                return result
            except OSError as error:
                result["overall_status"] = "环境不可用"
                result["message"] = f"无法启动编译器：{error}"
                result["compile"].update(
                    {
                        "status": "未执行",
                        "message": result["message"][:1000],
                        "duration_ms": round(
                            (time.perf_counter() - compile_started) * 1000
                        ),
                    }
                )
                return result

            candidate_message = (
                candidate_result.stderr or candidate_result.stdout or "编译成功。"
            )[:4000]
            if (
                candidate_result.returncode != 0
                and _unsupported_standard_option(candidate_message)
            ):
                unsupported_messages.append(candidate_message)
                continue

            completed = candidate_result
            effective_standard = candidate_standard
            standard_flag = candidate_flag
            break

        compile_duration = round((time.perf_counter() - compile_started) * 1000)
        if completed is None:
            result["overall_status"] = "编译环境不兼容"
            result["message"] = (
                "当前编译器不支持实验要求的C++标准，请更换编译器或调整实验编译标准。"
            )
            result["compile"].update(
                {
                    "status": "环境不兼容",
                    "message": (unsupported_messages[-1] if unsupported_messages else result["message"]),
                    "duration_ms": compile_duration,
                }
            )
            return result

        compile_message = (completed.stderr or completed.stdout or "编译成功。")[:4000]
        result["effective_cpp_standard"] = effective_standard
        result["compile"] = {
            "status": "成功" if completed.returncode == 0 else "失败",
            "message": compile_message,
            "duration_ms": compile_duration,
            "requested_standard": requested_standard,
            "effective_standard": effective_standard,
            "standard_flag": standard_flag,
        }
        if completed.returncode != 0:
            result["overall_status"] = "编译失败"
            result["message"] = (
                f"编译器已接受{cpp_standard_label(effective_standard)}标准选项，"
                "但代码没有通过编译，请根据下方编译信息检查代码。"
            )
            return result

        if requested_standard == "auto" and effective_standard != "c++17":
            result["compile"]["message"] = (
                f"编译成功：当前编译器不支持更高标准，已自动兼容为"
                f"{cpp_standard_label(effective_standard)}（{standard_flag}）。"
            )

        case_results = []
        for index, (case, scoring) in enumerate(
            zip(test_cases, scoring_metadata),
            start=1,
        ):
            case_output_limit = _case_output_limit(case, max_output)
            stdout_path = workdir / f"stdout_{index}.txt"
            stderr_path = workdir / f"stderr_{index}.txt"
            started = time.perf_counter()
            with (
                stdout_path.open("w", encoding="utf-8") as stdout_file,
                stderr_path.open("w", encoding="utf-8") as stderr_file,
            ):
                try:
                    process = subprocess.Popen(
                        [str(program_path)],
                        cwd=workdir,
                        stdin=subprocess.PIPE,
                        stdout=stdout_file,
                        stderr=stderr_file,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        shell=False,
                        start_new_session=os.name == "posix",
                        preexec_fn=_execution_limits(
                            timeout_seconds,
                            case_output_limit,
                        ),
                        creationflags=_creation_flags(),
                    )
                    try:
                        process.communicate(
                            input=str(case.get("input", "")),
                            timeout=timeout_seconds,
                        )
                        timed_out = False
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        _stop_process(process)
                except OSError as error:
                    process = None
                    timed_out = False
                    launch_error = str(error)
                else:
                    launch_error = ""

            actual_output, stdout_too_long = _read_limited(
                stdout_path,
                case_output_limit,
            )
            stderr_output, stderr_too_long = _read_limited(
                stderr_path,
                case_output_limit,
            )
            duration_ms = round((time.perf_counter() - started) * 1000)
            expected_output = str(case.get("expected_output", ""))

            if launch_error:
                case_status = "运行错误"
                message = f"无法启动程序：{launch_error}"
                exit_code = None
            elif timed_out:
                case_status = "超时"
                message = f"运行超过{timeout_seconds:g}秒限制。"
                exit_code = None
            elif stdout_too_long or stderr_too_long:
                case_status = "输出过长"
                message = f"程序输出超过{case_output_limit}个字符限制。"
                exit_code = process.returncode
            elif process.returncode != 0:
                case_status = "运行错误"
                message = stderr_output or f"程序异常退出，返回码{process.returncode}。"
                exit_code = process.returncode
            elif not case.get("compare_output", True):
                case_status = "运行成功"
                message = "程序正常结束；本次仅展示输出，不进行答案判定。"
                exit_code = process.returncode
            elif normalize_output(actual_output) == normalize_output(expected_output):
                case_status = "通过"
                message = "实际输出与预期输出一致。"
                exit_code = process.returncode
            else:
                case_status = "未通过"
                message = "实际输出与预期输出不一致。"
                exit_code = process.returncode

            case_results.append(
                {
                    "name": str(case.get("name") or f"样例{index}")[:80],
                    "group": scoring["group"],
                    "weight": scoring["weight"],
                    "score": scoring["weight"] if case_status == "通过" else 0,
                    "verdict_code": _verdict_code(case_status),
                    "status": case_status,
                    "input": str(case.get("input", ""))[:EVIDENCE_PREVIEW_CHARS],
                    "expected_output": expected_output[:EVIDENCE_PREVIEW_CHARS],
                    "actual_output": actual_output[:EVIDENCE_PREVIEW_CHARS],
                    "stderr": stderr_output[:EVIDENCE_PREVIEW_CHARS],
                    "evidence_truncated": any(
                        len(value) > EVIDENCE_PREVIEW_CHARS
                        for value in (
                            str(case.get("input", "")),
                            expected_output,
                            actual_output,
                            stderr_output,
                        )
                    ),
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                    "message": message[:1000],
                }
            )

        passed = sum(
            item["status"] in {"通过", "运行成功"}
            for item in case_results
        )
        total = len(case_results)
        score = sum(item["score"] for item in case_results)
        max_score = sum(item["weight"] for item in case_results)
        result.update(
            {
                "passed": passed,
                "total": total,
                "score": score,
                "max_score": max_score,
                "group_scores": _group_score_summary(case_results),
                "cases": case_results,
                "overall_status": (
                    "全部通过"
                    if passed == total
                    else "全部未通过"
                    if passed == 0
                    else "部分通过"
                ),
                "message": (
                    f"共执行{total}组测试，通过{passed}组；"
                    f"客观得分{score}/{max_score}分。"
                    if max_score
                    else f"共执行{total}组测试，通过{passed}组。"
                ),
            }
        )
        return result


def _run_cpp_code_local(code, standard_input="", cpp_standard="auto"):
    """编译并使用学生自定义输入运行一次，不与标准答案比较。"""
    result = _verify_cpp_code_local(
        code,
        [
            {
                "name": "自定义运行",
                "input": str(standard_input or "")[:MAX_CUSTOM_INPUT_CHARS],
                "expected_output": "",
                "compare_output": False,
            }
        ],
        cpp_standard=cpp_standard,
    )
    if result.get("cases") and result["cases"][0].get("status") == "运行成功":
        result["overall_status"] = "运行成功"
        result["message"] = "程序已使用自定义输入正常运行。"
    return result


class LocalCppExecutionBackend:
    """本机C++执行后端；公开部署时可替换为容器或远程判题服务。"""

    backend_id = "local_cpp"
    display_name = "本机C++执行器"

    def runtime_status(self):
        status = _local_runtime_status()
        return {**status, "backend_id": self.backend_id}

    def verify_cpp_code(self, code, test_cases, cpp_standard="auto"):
        result = _verify_cpp_code_local(code, test_cases, cpp_standard)
        result["execution_backend"] = self.backend_id
        return result

    def run_cpp_code(self, code, standard_input="", cpp_standard="auto"):
        result = _run_cpp_code_local(code, standard_input, cpp_standard)
        result["execution_backend"] = self.backend_id
        return result


def _docker_executable_path():
    configured = docker_command()
    path = Path(configured)
    if path.is_file():
        return str(path)
    return shutil.which(configured)


def _docker_runtime_status():
    """检查Docker命令、守护进程和预先下载的C++镜像。"""
    image = docker_cpp_image()
    executable = _docker_executable_path()
    compiler = f"{image} / g++"
    if not execution_enabled():
        return {
            "available": False,
            "label": "Docker代码验证未启用",
            "detail": (
                "在.env中设置CODE_EXECUTION_ENABLED=true并选择"
                "CODE_EXECUTION_BACKEND=docker后启用。"
            ),
            "compiler": compiler,
        }
    if not executable:
        return {
            "available": False,
            "label": "未找到Docker命令",
            "detail": "请先安装Docker Desktop，并确认docker命令已加入PATH。",
            "compiler": compiler,
        }

    try:
        info = subprocess.run(
            [executable, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DOCKER_STATUS_TIMEOUT_SECONDS,
            shell=False,
            creationflags=_creation_flags(),
        )
    except subprocess.TimeoutExpired:
        return {
            "available": False,
            "label": "Docker服务响应超时",
            "detail": "请确认Docker Desktop已经完成启动后再重试。",
            "compiler": compiler,
        }
    except OSError as error:
        return {
            "available": False,
            "label": "Docker环境不可用",
            "detail": f"无法启动Docker命令：{error}",
            "compiler": compiler,
        }

    if info.returncode != 0:
        detail = (info.stderr or info.stdout or "Docker服务没有响应。").strip()
        return {
            "available": False,
            "label": "Docker服务未启动",
            "detail": (
                "请启动Docker Desktop后再重试。环境信息：" + detail[:500]
            ),
            "compiler": compiler,
        }

    try:
        inspected = subprocess.run(
            [executable, "image", "inspect", image],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DOCKER_STATUS_TIMEOUT_SECONDS,
            shell=False,
            creationflags=_creation_flags(),
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        return {
            "available": False,
            "label": "无法检查Docker C++镜像",
            "detail": f"请检查Docker服务后重试：{error}",
            "compiler": compiler,
        }

    if inspected.returncode != 0:
        return {
            "available": False,
            "label": "尚未下载Docker C++镜像",
            "detail": f"请在PowerShell执行：docker pull {image}",
            "compiler": compiler,
        }

    server_version = (info.stdout or "").strip()
    return {
        "available": True,
        "label": "Docker隔离代码验证可用",
        "detail": (
            f"镜像：{image}；Docker {server_version or '服务已连接'}；"
            "已限制网络、CPU、内存、进程数、运行时间和输出量。"
        ),
        "compiler": compiler,
    }


def _docker_container_command(
    executable,
    image,
    container_name,
    workdir,
    writable=False,
    interactive=False,
):
    """生成固定的容器安全参数；返回值可独立测试。"""
    mount = f"type=bind,source={Path(workdir).resolve()},target=/workspace"
    if not writable:
        mount += ",readonly"
    command = [
        executable,
        "run",
        "--rm",
        "--pull",
        "never",
        "--init",
        "--name",
        container_name,
        "--network",
        "none",
        "--ipc",
        "none",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--cpus",
        "1",
        "--pids-limit",
        "64",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nodev,nosuid,noexec,size=64m",
        "--ulimit",
        "nofile=64:64",
        "--ulimit",
        "fsize=33554432:33554432",
        "--mount",
        mount,
        "--workdir",
        "/workspace",
    ]
    if interactive:
        # docker run默认不会把宿主stdin转发给容器；正式运行必须显式保持stdin。
        command.append("-i")
    if not writable:
        command.extend(["--user", "65534:65534"])
    command.append(image)
    return command


def _force_remove_container(executable, container_name):
    try:
        subprocess.run(
            [executable, "rm", "-f", container_name],
            capture_output=True,
            timeout=DOCKER_STATUS_TIMEOUT_SECONDS,
            shell=False,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _docker_environment_error(returncode, message):
    lowered = str(message or "").lower()
    markers = (
        "cannot connect to the docker daemon",
        "error response from daemon",
        "is the docker daemon running",
        "unable to find image",
        "permission denied while trying to connect",
        "docker: error",
    )
    return returncode == 125 or any(marker in lowered for marker in markers)


def _extract_student_exit_code(stderr_output):
    marker = "__WUMA_STUDENT_EXIT_CODE__="
    marker_index = stderr_output.rfind(marker)
    if marker_index < 0:
        return None, stderr_output
    value_start = marker_index + len(marker)
    value_end = stderr_output.find("\n", value_start)
    if value_end < 0:
        value_end = len(stderr_output)
    try:
        exit_code = int(stderr_output[value_start:value_end].strip())
    except ValueError:
        return None, stderr_output
    cleaned = (stderr_output[:marker_index] + stderr_output[value_end:]).rstrip()
    return exit_code, cleaned


def _read_docker_stderr(path, maximum):
    """剥离内部退出码标记，避免标记本身占用学生输出限额。"""
    with path.open("r", encoding="utf-8", errors="replace") as file:
        raw = file.read(maximum + 256)
    exit_code, cleaned = _extract_student_exit_code(raw)
    return cleaned[:maximum], len(cleaned) > maximum, exit_code


def _monitor_docker_program(
    command,
    standard_input,
    stdout_path,
    stderr_path,
    timeout_seconds,
    max_output,
    executable,
    container_name,
):
    """运行容器并从宿主侧监控超时与输出文件大小。"""
    process = None
    timed_out = False
    output_too_long = False
    launch_error = ""
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_file,
        stderr_path.open("w", encoding="utf-8") as stderr_file,
    ):
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                creationflags=_creation_flags(),
            )
            try:
                process.stdin.write(str(standard_input or ""))
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

            deadline = time.monotonic() + timeout_seconds
            byte_limit = max(65536, max_output * 4)
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if (
                    stdout_path.stat().st_size > byte_limit
                    or stderr_path.stat().st_size > byte_limit
                ):
                    output_too_long = True
                    break
                time.sleep(0.02)

            if timed_out or output_too_long:
                _force_remove_container(executable, container_name)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _stop_process(process)
        except OSError as error:
            launch_error = str(error)

    return {
        "process": process,
        "timed_out": timed_out,
        "output_too_long": output_too_long,
        "launch_error": launch_error,
    }


def _finish_verification_result(result, case_results):
    passed = sum(
        item["status"] in {"通过", "运行成功"}
        for item in case_results
    )
    total = len(case_results)
    score = sum(item["score"] for item in case_results)
    max_score = sum(item["weight"] for item in case_results)
    result.update(
        {
            "passed": passed,
            "total": total,
            "score": score,
            "max_score": max_score,
            "group_scores": _group_score_summary(case_results),
            "cases": case_results,
            "overall_status": (
                "全部通过"
                if passed == total
                else "全部未通过"
                if passed == 0
                else "部分通过"
            ),
            "message": (
                f"共执行{total}组测试，通过{passed}组；"
                f"客观得分{score}/{max_score}分。"
                if max_score
                else f"共执行{total}组测试，通过{passed}组。"
            ),
        }
    )
    return result


def _verify_cpp_code_docker(code, test_cases, cpp_standard="auto"):
    requested_standard = normalize_cpp_standard(cpp_standard)
    status = _docker_runtime_status()
    compiler = status["compiler"]
    if not execution_enabled():
        return _base_result("未启用", status["detail"], compiler, requested_standard)
    if not status["available"]:
        return _base_result("环境不可用", status["detail"], compiler, requested_standard)
    if not str(code).strip():
        return _base_result("未执行", "代码为空，无法验证。", compiler, requested_standard)
    if not test_cases:
        return _base_result(
            "未执行",
            "当前实验没有配置测试样例。",
            compiler,
            requested_standard,
        )

    timeout_seconds = _bounded_float_env(
        "CODE_EXECUTION_TIMEOUT_SECONDS",
        DEFAULT_TIMEOUT_SECONDS,
        0.5,
        5.0,
    )
    max_output = _bounded_int_env(
        "CODE_EXECUTION_MAX_OUTPUT_CHARS",
        DEFAULT_MAX_OUTPUT_CHARS,
        500,
        MAX_JUDGED_OUTPUT_CHARS,
    )
    compile_timeout = _bounded_float_env(
        "CODE_COMPILE_TIMEOUT_SECONDS",
        DEFAULT_COMPILE_TIMEOUT_SECONDS,
        3.0,
        30.0,
    )
    executable = _docker_executable_path()
    image = docker_cpp_image()
    scoring_metadata = _case_scoring_metadata(test_cases)
    result = _base_result("未执行", "", compiler, requested_standard)
    result["enabled"] = True
    result["max_score"] = sum(item["weight"] for item in scoring_metadata)
    result["group_scores"] = _group_score_summary(
        [
            {
                "group": item["group"],
                "weight": item["weight"],
                "score": 0,
                "status": "未执行",
            }
            for item in scoring_metadata
        ]
    )

    with tempfile.TemporaryDirectory(prefix="wuma_docker_verify_") as folder:
        workdir = Path(folder)
        source_path = workdir / "main.cpp"
        source_path.write_text(str(code), encoding="utf-8")
        compile_started = time.perf_counter()
        completed = None
        effective_standard = ""
        standard_flag = ""
        unsupported_messages = []

        for candidate_standard, candidate_alias in _standard_attempts(requested_standard):
            candidate_flag = f"-std={candidate_alias}"
            container_name = "wuma_compile_" + uuid.uuid4().hex[:16]
            command = _docker_container_command(
                executable,
                image,
                container_name,
                workdir,
                writable=True,
            ) + ["g++", candidate_flag, "-O2", "main.cpp", "-o", "main"]
            try:
                candidate_result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=compile_timeout,
                    shell=False,
                    creationflags=_creation_flags(),
                )
            except subprocess.TimeoutExpired:
                _force_remove_container(executable, container_name)
                result["overall_status"] = "编译超时"
                result["message"] = "Docker内编译超过时间限制。"
                result["compile"].update(
                    {
                        "status": "超时",
                        "message": result["message"],
                        "duration_ms": round(
                            (time.perf_counter() - compile_started) * 1000
                        ),
                        "standard_flag": candidate_flag,
                    }
                )
                return result
            except OSError as error:
                result["overall_status"] = "环境不可用"
                result["message"] = f"无法启动Docker：{error}"
                result["compile"].update(
                    {
                        "status": "未执行",
                        "message": result["message"][:1000],
                        "duration_ms": round(
                            (time.perf_counter() - compile_started) * 1000
                        ),
                    }
                )
                return result

            candidate_message = (
                candidate_result.stderr or candidate_result.stdout or "编译成功。"
            )[:4000]
            if _docker_environment_error(candidate_result.returncode, candidate_message):
                result["overall_status"] = "环境不可用"
                result["message"] = "Docker执行环境发生错误，请教师检查服务和镜像。"
                result["compile"].update(
                    {
                        "status": "未执行",
                        "message": candidate_message,
                        "duration_ms": round(
                            (time.perf_counter() - compile_started) * 1000
                        ),
                        "standard_flag": candidate_flag,
                    }
                )
                return result
            if (
                candidate_result.returncode != 0
                and _unsupported_standard_option(candidate_message)
            ):
                unsupported_messages.append(candidate_message)
                continue
            completed = candidate_result
            effective_standard = candidate_standard
            standard_flag = candidate_flag
            break

        compile_duration = round((time.perf_counter() - compile_started) * 1000)
        if completed is None:
            result["overall_status"] = "编译环境不兼容"
            result["message"] = "Docker镜像不支持实验要求的C++标准，请更换镜像或调整标准。"
            result["compile"].update(
                {
                    "status": "环境不兼容",
                    "message": (
                        unsupported_messages[-1]
                        if unsupported_messages
                        else result["message"]
                    ),
                    "duration_ms": compile_duration,
                }
            )
            return result

        compile_message = (completed.stderr or completed.stdout or "编译成功。")[:4000]
        result["effective_cpp_standard"] = effective_standard
        result["compile"] = {
            "status": "成功" if completed.returncode == 0 else "失败",
            "message": compile_message,
            "duration_ms": compile_duration,
            "requested_standard": requested_standard,
            "effective_standard": effective_standard,
            "standard_flag": standard_flag,
        }
        if completed.returncode != 0:
            result["overall_status"] = "编译失败"
            result["message"] = (
                f"Docker内的编译器已接受{cpp_standard_label(effective_standard)}标准选项，"
                "但代码没有通过编译，请根据下方编译信息检查代码。"
            )
            return result

        # 编译容器可能以root身份生成文件；运行容器改用无特权用户，只需读取和执行。
        try:
            workdir.chmod(0o755)
            (workdir / "main").chmod(0o755)
        except OSError as error:
            result["overall_status"] = "环境不可用"
            result["message"] = f"无法准备Docker运行文件权限：{error}"
            return result

        case_results = []
        for index, (case, scoring) in enumerate(
            zip(test_cases, scoring_metadata),
            start=1,
        ):
            case_output_limit = _case_output_limit(case, max_output)
            stdout_path = workdir / f"stdout_{index}.txt"
            stderr_path = workdir / f"stderr_{index}.txt"
            container_name = "wuma_run_" + uuid.uuid4().hex[:16]
            command = _docker_container_command(
                executable,
                image,
                container_name,
                workdir,
                writable=False,
                interactive=True,
            ) + [
                "sh",
                "-c",
                (
                    "./main; code=$?; printf '\\n__WUMA_STUDENT_EXIT_CODE__=%s\\n' "
                    '"$code" >&2; exit 0'
                ),
            ]
            started = time.perf_counter()
            monitored = _monitor_docker_program(
                command,
                str(case.get("input", "")),
                stdout_path,
                stderr_path,
                timeout_seconds,
                case_output_limit,
                executable,
                container_name,
            )
            actual_output, stdout_too_long = _read_limited(
                stdout_path,
                case_output_limit,
            )
            stderr_output, stderr_too_long, exit_code = _read_docker_stderr(
                stderr_path,
                case_output_limit,
            )
            process = monitored["process"]
            docker_returncode = process.returncode if process is not None else None
            duration_ms = round((time.perf_counter() - started) * 1000)
            expected_output = str(case.get("expected_output", ""))

            if monitored["launch_error"]:
                result["overall_status"] = "环境不可用"
                result["message"] = f"无法启动Docker：{monitored['launch_error']}"
                result["cases"] = case_results
                return result
            if (
                not monitored["timed_out"]
                and not monitored["output_too_long"]
                and _docker_environment_error(docker_returncode, stderr_output)
            ):
                result["overall_status"] = "环境不可用"
                result["message"] = (
                    "Docker在运行测试时发生环境错误，请教师检查服务和镜像。"
                )
                result["cases"] = case_results
                return result

            if monitored["timed_out"]:
                case_status = "超时"
                message = f"运行超过{timeout_seconds:g}秒限制。"
                exit_code = None
            elif monitored["output_too_long"] or stdout_too_long or stderr_too_long:
                case_status = "输出过长"
                message = f"程序输出超过{case_output_limit}个字符限制。"
            elif docker_returncode != 0 or exit_code is None:
                result["overall_status"] = "环境不可用"
                result["message"] = (
                    "Docker没有返回完整的运行状态，请教师检查执行环境。"
                )
                result["cases"] = case_results
                return result
            elif exit_code != 0:
                case_status = "运行错误"
                message = stderr_output or f"程序异常退出，返回码{exit_code}。"
            elif not case.get("compare_output", True):
                case_status = "运行成功"
                message = "程序正常结束；本次仅展示输出，不进行答案判定。"
            elif normalize_output(actual_output) == normalize_output(expected_output):
                case_status = "通过"
                message = "实际输出与预期输出一致。"
            else:
                case_status = "未通过"
                message = "实际输出与预期输出不一致。"

            case_results.append(
                {
                    "name": str(case.get("name") or f"样例{index}")[:80],
                    "group": scoring["group"],
                    "weight": scoring["weight"],
                    "score": scoring["weight"] if case_status == "通过" else 0,
                    "verdict_code": _verdict_code(case_status),
                    "status": case_status,
                    "input": str(case.get("input", ""))[:EVIDENCE_PREVIEW_CHARS],
                    "expected_output": expected_output[:EVIDENCE_PREVIEW_CHARS],
                    "actual_output": actual_output[:EVIDENCE_PREVIEW_CHARS],
                    "stderr": stderr_output[:EVIDENCE_PREVIEW_CHARS],
                    "evidence_truncated": any(
                        len(value) > EVIDENCE_PREVIEW_CHARS
                        for value in (
                            str(case.get("input", "")),
                            expected_output,
                            actual_output,
                            stderr_output,
                        )
                    ),
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                    "message": message[:1000],
                }
            )

        return _finish_verification_result(result, case_results)


def _run_cpp_code_docker(code, standard_input="", cpp_standard="auto"):
    result = _verify_cpp_code_docker(
        code,
        [
            {
                "name": "自定义运行",
                "input": str(standard_input or "")[:MAX_CUSTOM_INPUT_CHARS],
                "expected_output": "",
                "compare_output": False,
            }
        ],
        cpp_standard=cpp_standard,
    )
    if result.get("cases") and result["cases"][0].get("status") == "运行成功":
        result["overall_status"] = "运行成功"
        result["message"] = "程序已在Docker隔离环境中使用自定义输入正常运行。"
    return result


class DockerCppExecutionBackend:
    """Docker隔离执行后端；适合评审演示和受控课堂部署。"""

    backend_id = "docker_cpp"
    display_name = "Docker隔离C++执行器"

    def __init__(self):
        self._cached_status = None
        self._cached_status_at = 0.0
        self._cached_status_key = None

    def runtime_status(self):
        cache_key = (execution_enabled(), docker_command(), docker_cpp_image())
        now = time.monotonic()
        if (
            self._cached_status is None
            or self._cached_status_key != cache_key
            or now - self._cached_status_at > 3.0
        ):
            self._cached_status = _docker_runtime_status()
            self._cached_status_at = now
            self._cached_status_key = cache_key
        return {**self._cached_status, "backend_id": self.backend_id}

    def verify_cpp_code(self, code, test_cases, cpp_standard="auto"):
        result = _verify_cpp_code_docker(code, test_cases, cpp_standard)
        result["execution_backend"] = self.backend_id
        return result

    def run_cpp_code(self, code, standard_input="", cpp_standard="auto"):
        result = _run_cpp_code_docker(code, standard_input, cpp_standard)
        result["execution_backend"] = self.backend_id
        return result


class InvalidExecutionBackend:
    """将配置错误明确归类为环境问题，避免误判学生代码。"""

    backend_id = "invalid"
    display_name = "无效代码执行器配置"

    def __init__(self, configured_name):
        self.configured_name = configured_name

    def runtime_status(self):
        return {
            "available": False,
            "label": "代码执行后端配置错误",
            "detail": (
                f"CODE_EXECUTION_BACKEND={self.configured_name!r}无效；"
                "可选值为local或docker。"
            ),
            "compiler": "未配置",
            "backend_id": self.backend_id,
        }

    def verify_cpp_code(self, code, test_cases, cpp_standard="auto"):
        status = self.runtime_status()
        result = _base_result(
            "环境不可用",
            status["detail"],
            status["compiler"],
            cpp_standard,
        )
        result["execution_backend"] = self.backend_id
        return result

    def run_cpp_code(self, code, standard_input="", cpp_standard="auto"):
        return self.verify_cpp_code(code, [], cpp_standard)


_BACKENDS = {
    "local": LocalCppExecutionBackend(),
    "docker": DockerCppExecutionBackend(),
}
_execution_backend = None


def _configured_execution_backend():
    name = execution_backend_name()
    return _BACKENDS.get(name, InvalidExecutionBackend(name))


def set_execution_backend(backend):
    """测试或部署时注入兼容后端；传入后优先于.env配置。"""
    global _execution_backend
    required = {"runtime_status", "verify_cpp_code", "run_cpp_code"}
    if not all(callable(getattr(backend, name, None)) for name in required):
        raise TypeError("代码执行后端缺少必要接口。")
    _execution_backend = backend


def reset_execution_backend():
    """取消测试注入，恢复由CODE_EXECUTION_BACKEND选择后端。"""
    global _execution_backend
    _execution_backend = None


def get_execution_backend():
    return _execution_backend or _configured_execution_backend()


def runtime_status():
    return get_execution_backend().runtime_status()


def verify_cpp_code(code, test_cases, cpp_standard="auto"):
    return get_execution_backend().verify_cpp_code(code, test_cases, cpp_standard)


def run_cpp_code(code, standard_input="", cpp_standard="auto"):
    return get_execution_backend().run_cpp_code(code, standard_input, cpp_standard)
