import os
import shutil
import subprocess
import unittest
from unittest.mock import patch

import code_verifier as verifier
import database as db


class CodeVerifierTests(unittest.TestCase):
    def test_output_comparison_ignores_line_endings_and_trailing_spaces(self):
        self.assertEqual(
            verifier.normalize_output("8  \r\n\r\n"),
            verifier.normalize_output("8\n"),
        )
        self.assertNotEqual(
            verifier.normalize_output("  缩进\n"),
            verifier.normalize_output("缩进\n"),
        )

    def test_output_difference_counts_missing_and_extra_spaces(self):
        missing = verifier.analyze_output_difference("1   2", "1 2")
        extra = verifier.analyze_output_difference("1 2", "1   2")
        self.assertEqual(missing["kind"], "缺少空格")
        self.assertEqual(missing["line"], 1)
        self.assertEqual(missing["column"], 3)
        self.assertIn("2个空格", missing["detail"])
        self.assertEqual(extra["kind"], "多余空格")
        self.assertIn("2个空格", extra["detail"])

    def test_output_difference_locates_newline_and_content(self):
        newline = verifier.analyze_output_difference("1\n2", "1 2")
        content = verifier.analyze_output_difference("12\n", "13\n")
        self.assertEqual(newline["kind"], "缺少换行")
        self.assertEqual((newline["line"], newline["column"]), (1, 2))
        self.assertEqual(content["kind"], "内容不同")
        self.assertEqual((content["line"], content["column"]), (1, 2))

    def test_output_difference_respects_verifier_normalization(self):
        difference = verifier.analyze_output_difference("8\n", "8   \r\n\r\n")
        self.assertFalse(difference["different"])
        self.assertEqual(difference["kind"], "没有差异")

    def test_execution_is_disabled_by_default(self):
        with patch.dict(os.environ, {"CODE_EXECUTION_ENABLED": "false"}):
            result = verifier.verify_cpp_code(
                db.DEFAULT_STARTER_CODE,
                db.DEFAULT_TEST_CASES,
            )
        self.assertEqual(result["overall_status"], "未启用")
        self.assertFalse(result["enabled"])

    def test_execution_backend_can_be_replaced_through_stable_interface(self):
        class FakeBackend:
            def runtime_status(self):
                return {"available": True, "label": "测试后端", "compiler": "fake"}

            def verify_cpp_code(self, code, test_cases, cpp_standard="auto"):
                return {"overall_status": "测试判定", "code": code}

            def run_cpp_code(self, code, standard_input="", cpp_standard="auto"):
                return {"overall_status": "测试运行", "input": standard_input}

        original = verifier.get_execution_backend()
        try:
            verifier.set_execution_backend(FakeBackend())
            self.assertEqual(verifier.runtime_status()["label"], "测试后端")
            self.assertEqual(
                verifier.verify_cpp_code("code", [])["overall_status"],
                "测试判定",
            )
            self.assertEqual(
                verifier.run_cpp_code("code", "input")["input"],
                "input",
            )
            with self.assertRaisesRegex(TypeError, "缺少必要接口"):
                verifier.set_execution_backend(object())
        finally:
            verifier.set_execution_backend(original)

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_correct_cpp_code_passes_all_cases(self):
        with patch.dict(
            os.environ,
            {"CODE_EXECUTION_ENABLED": "true", "CXX_COMPILER": "g++"},
        ):
            result = verifier.verify_cpp_code(
                db.DEFAULT_STARTER_CODE,
                db.DEFAULT_TEST_CASES,
            )
        self.assertEqual(result["compile"]["status"], "成功")
        self.assertEqual(result["overall_status"], "全部通过")
        self.assertEqual(result["passed"], 3)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["max_score"], 100)
        self.assertTrue(all(item["verdict_code"] == "AC" for item in result["cases"]))

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_weighted_cases_produce_partial_score_and_group_summary(self):
        code = "#include <iostream>\nint main(){std::cout << 8; return 0;}"
        with patch.dict(
            os.environ,
            {"CODE_EXECUTION_ENABLED": "true", "CXX_COMPILER": "g++"},
        ):
            result = verifier.verify_cpp_code(code, db.DEFAULT_TEST_CASES)
        self.assertEqual(result["overall_status"], "部分通过")
        self.assertEqual(result["score"], 40)
        self.assertEqual(result["max_score"], 100)
        self.assertEqual(result["cases"][0]["verdict_code"], "AC")
        self.assertEqual(result["cases"][1]["verdict_code"], "WA")
        group_scores = {item["group"]: item for item in result["group_scores"]}
        self.assertEqual(group_scores["基础功能"]["score"], 40)
        self.assertEqual(group_scores["边界情况"]["max_score"], 60)

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_compile_error_is_saved_as_evidence(self):
        with patch.dict(
            os.environ,
            {"CODE_EXECUTION_ENABLED": "true", "CXX_COMPILER": "g++"},
        ):
            result = verifier.verify_cpp_code(
                "int main( {",
                db.DEFAULT_TEST_CASES,
            )
        self.assertEqual(result["overall_status"], "编译失败")
        self.assertEqual(result["compile"]["status"], "失败")
        self.assertTrue(result["compile"]["message"])

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_infinite_loop_is_stopped_by_timeout(self):
        code = "int main(){while(true){} return 0;}"
        with patch.dict(
            os.environ,
            {
                "CODE_EXECUTION_ENABLED": "true",
                "CXX_COMPILER": "g++",
                "CODE_EXECUTION_TIMEOUT_SECONDS": "0.5",
            },
        ):
            result = verifier.verify_cpp_code(
                code,
                [{"name": "超时检查", "input": "", "expected_output": "0"}],
            )
        self.assertEqual(result["cases"][0]["status"], "超时")

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_custom_run_returns_actual_output_without_expected_answer(self):
        with patch.dict(
            os.environ,
            {"CODE_EXECUTION_ENABLED": "true", "CXX_COMPILER": "g++"},
        ):
            result = verifier.run_cpp_code(
                db.DEFAULT_STARTER_CODE,
                "4\n-8 3 12 5\n",
            )
        self.assertEqual(result["overall_status"], "运行成功")
        self.assertEqual(result["cases"][0]["status"], "运行成功")
        self.assertEqual(verifier.normalize_output(result["cases"][0]["actual_output"]), "12")

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_auto_standard_falls_back_for_an_old_compiler(self):
        original_run = subprocess.run

        def old_compiler_run(arguments, **kwargs):
            if arguments[1] in {
                "-std=c++17",
                "-std=c++1z",
                "-std=c++14",
                "-std=c++1y",
            }:
                return subprocess.CompletedProcess(
                    arguments,
                    1,
                    stdout="",
                    stderr=(
                        "g++.exe: error: unrecognized command line option "
                        f"'{arguments[1]}'"
                    ),
                )
            return original_run(arguments, **kwargs)

        with (
            patch.dict(
                os.environ,
                {"CODE_EXECUTION_ENABLED": "true", "CXX_COMPILER": "g++"},
            ),
            patch.object(verifier.subprocess, "run", side_effect=old_compiler_run),
        ):
            result = verifier.run_cpp_code("int main(){return 0;}")

        self.assertEqual(result["overall_status"], "运行成功")
        self.assertEqual(result["effective_cpp_standard"], "c++11")
        self.assertEqual(result["compile"]["standard_flag"], "-std=c++11")
        self.assertEqual(result["cases"][0]["actual_output"], "")

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_unsupported_explicit_standard_is_environment_error(self):
        unsupported = subprocess.CompletedProcess(
            ["g++", "-std=c++17"],
            1,
            stdout="",
            stderr=(
                "g++.exe: error: unrecognized command line option '-std=c++17'"
            ),
        )
        with (
            patch.dict(
                os.environ,
                {"CODE_EXECUTION_ENABLED": "true", "CXX_COMPILER": "g++"},
            ),
            patch.object(verifier.subprocess, "run", return_value=unsupported),
        ):
            result = verifier.run_cpp_code(
                "int main(){return 0;}",
                cpp_standard="c++17",
            )

        self.assertEqual(result["overall_status"], "编译环境不兼容")
        self.assertEqual(result["compile"]["status"], "环境不兼容")

    def test_backend_is_selected_from_environment(self):
        try:
            verifier.reset_execution_backend()
            with patch.dict(
                os.environ,
                {"CODE_EXECUTION_BACKEND": "docker"},
            ):
                self.assertIsInstance(
                    verifier.get_execution_backend(),
                    verifier.DockerCppExecutionBackend,
                )
            with patch.dict(
                os.environ,
                {"CODE_EXECUTION_BACKEND": "unsupported"},
            ):
                status = verifier.runtime_status()
                self.assertFalse(status["available"])
                self.assertEqual(status["backend_id"], "invalid")
                self.assertIn("local或docker", status["detail"])
        finally:
            verifier.reset_execution_backend()

    def test_docker_status_distinguishes_missing_command(self):
        backend = verifier.DockerCppExecutionBackend()
        with (
            patch.dict(
                os.environ,
                {
                    "CODE_EXECUTION_ENABLED": "true",
                    "CODE_EXECUTION_BACKEND": "docker",
                },
            ),
            patch.object(verifier, "_docker_executable_path", return_value=None),
        ):
            status = backend.runtime_status()
        self.assertFalse(status["available"])
        self.assertEqual(status["label"], "未找到Docker命令")
        self.assertIn("Docker Desktop", status["detail"])

    def test_docker_status_distinguishes_missing_image(self):
        backend = verifier.DockerCppExecutionBackend()
        responses = [
            subprocess.CompletedProcess(["docker", "info"], 0, "26.1", ""),
            subprocess.CompletedProcess(
                ["docker", "image", "inspect"],
                1,
                "",
                "No such image",
            ),
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "CODE_EXECUTION_ENABLED": "true",
                    "CODE_EXECUTION_BACKEND": "docker",
                    "DOCKER_CPP_IMAGE": "gcc:test",
                },
            ),
            patch.object(
                verifier,
                "_docker_executable_path",
                return_value="docker",
            ),
            patch.object(verifier.subprocess, "run", side_effect=responses),
        ):
            status = backend.runtime_status()
        self.assertFalse(status["available"])
        self.assertEqual(status["label"], "尚未下载Docker C++镜像")
        self.assertIn("docker pull gcc:test", status["detail"])

    def test_docker_command_contains_required_isolation_limits(self):
        command = verifier._docker_container_command(
            "docker",
            "gcc:test",
            "wuma_test",
            ".",
            writable=False,
        )
        joined = " ".join(command)
        self.assertIn("--pull never", joined)
        self.assertIn("--init", command)
        self.assertIn("--network none", joined)
        self.assertIn("--ipc none", joined)
        self.assertIn("--memory 256m", joined)
        self.assertIn("--memory-swap 256m", joined)
        self.assertIn("--cpus 1", joined)
        self.assertIn("--pids-limit 64", joined)
        self.assertIn("--cap-drop ALL", joined)
        self.assertIn("--security-opt no-new-privileges", joined)
        self.assertIn("--read-only", command)
        self.assertIn("nodev,nosuid,noexec", joined)
        self.assertIn("readonly", joined)
        self.assertIn("--user 65534:65534", joined)
        self.assertNotIn("-i", command)

    def test_docker_runtime_command_keeps_stdin_open(self):
        command = verifier._docker_container_command(
            "docker",
            "gcc:test",
            "wuma_stdin_test",
            ".",
            writable=False,
            interactive=True,
        )
        self.assertIn("-i", command)
        self.assertLess(command.index("-i"), command.index("gcc:test"))

    def test_docker_judge_passes_interactive_command_to_runtime_monitor(self):
        from pathlib import Path

        captured = {}
        runtime = {
            "available": True,
            "label": "Docker隔离代码验证可用",
            "detail": "测试环境",
            "compiler": "gcc:test / g++",
        }

        def fake_monitor(
            command,
            standard_input,
            stdout_path,
            stderr_path,
            *_args,
        ):
            captured["command"] = command
            captured["input"] = standard_input
            stdout_path.write_text("1\n", encoding="utf-8")
            stderr_path.write_text(
                "__WUMA_STUDENT_EXIT_CODE__=0\n",
                encoding="utf-8",
            )
            return {
                "process": subprocess.CompletedProcess(command, 0),
                "timed_out": False,
                "output_too_long": False,
                "launch_error": "",
            }

        with (
            patch.dict(os.environ, {"CODE_EXECUTION_ENABLED": "true"}),
            patch.object(verifier, "_docker_runtime_status", return_value=runtime),
            patch.object(verifier, "_docker_executable_path", return_value="docker"),
            patch.object(
                verifier.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, "", ""),
            ),
            patch.object(Path, "chmod", return_value=None),
            patch.object(verifier, "_monitor_docker_program", side_effect=fake_monitor),
        ):
            result = verifier._verify_cpp_code_docker(
                "int main(){return 0;}",
                [{"name": "输入回归", "input": "1\n", "expected_output": "1\n"}],
            )

        self.assertEqual(result["overall_status"], "全部通过")
        self.assertEqual(captured["input"], "1\n")
        self.assertIn("-i", captured["command"])

    def test_judged_output_limit_expands_to_expected_output_size(self):
        self.assertEqual(
            verifier._case_output_limit(
                {"expected_output": "x" * 25000},
                4000,
            ),
            26024,
        )
        self.assertEqual(
            verifier._case_output_limit(
                {"expected_output": "", "compare_output": False},
                4000,
            ),
            4000,
        )

    @unittest.skipUnless(shutil.which("g++"), "当前测试环境没有g++")
    def test_large_legitimate_output_is_judged_and_evidence_is_bounded(self):
        amount = 25000
        code = (
            "#include <iostream>\n"
            f"int main(){{for(int i=0;i<{amount};++i)std::cout<<'x';}}"
        )
        with patch.dict(
            os.environ,
            {
                "CODE_EXECUTION_ENABLED": "true",
                "CXX_COMPILER": "g++",
                "CODE_EXECUTION_MAX_OUTPUT_CHARS": "4000",
            },
        ):
            result = verifier.verify_cpp_code(
                code,
                [{"name": "大输出", "input": "", "expected_output": "x" * amount}],
            )
        self.assertEqual(result["overall_status"], "全部通过")
        self.assertEqual(len(result["cases"][0]["actual_output"]), 20000)
        self.assertTrue(result["cases"][0]["evidence_truncated"])

    def test_docker_infrastructure_failure_is_not_compile_failure(self):
        runtime = {
            "available": True,
            "label": "Docker隔离代码验证可用",
            "detail": "测试环境",
            "compiler": "gcc:test / g++",
        }
        failed = subprocess.CompletedProcess(
            ["docker", "run"],
            125,
            "",
            "docker: error response from daemon",
        )
        with (
            patch.dict(
                os.environ,
                {"CODE_EXECUTION_ENABLED": "true"},
            ),
            patch.object(verifier, "_docker_runtime_status", return_value=runtime),
            patch.object(
                verifier,
                "_docker_executable_path",
                return_value="docker",
            ),
            patch.object(verifier.subprocess, "run", return_value=failed),
        ):
            result = verifier._verify_cpp_code_docker(
                "int main(){return 0;}",
                [{"name": "测试", "input": "", "expected_output": ""}],
            )
        self.assertEqual(result["overall_status"], "环境不可用")
        self.assertEqual(result["compile"]["status"], "未执行")
        self.assertNotEqual(result["overall_status"], "编译失败")

    def test_student_exit_marker_is_removed_from_stderr(self):
        exit_code, stderr = verifier._extract_student_exit_code(
            "runtime warning\n__WUMA_STUDENT_EXIT_CODE__=7\n"
        )
        self.assertEqual(exit_code, 7)
        self.assertEqual(stderr, "runtime warning")

    def test_internal_exit_marker_does_not_consume_output_limit(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stderr.txt"
            path.write_text(
                "12345\n__WUMA_STUDENT_EXIT_CODE__=0\n",
                encoding="utf-8",
            )
            stderr, too_long, exit_code = verifier._read_docker_stderr(path, 5)
        self.assertEqual(stderr, "12345")
        self.assertFalse(too_long)
        self.assertEqual(exit_code, 0)

    def test_docker_process_monitor_transfers_input_and_captures_exit_marker(self):
        import sys
        import tempfile
        from pathlib import Path

        script = (
            "import sys; data=sys.stdin.read(); print(data.strip()); "
            "print('__WUMA_STUDENT_EXIT_CODE__=0', file=sys.stderr)"
        )
        with tempfile.TemporaryDirectory() as folder:
            stdout_path = Path(folder) / "stdout.txt"
            stderr_path = Path(folder) / "stderr.txt"
            monitored = verifier._monitor_docker_program(
                [sys.executable, "-c", script],
                "sample input\n",
                stdout_path,
                stderr_path,
                2,
                4000,
                "docker",
                "unused_container_name",
            )
            stdout, stdout_too_long = verifier._read_limited(stdout_path, 4000)
            stderr, stderr_too_long, exit_code = verifier._read_docker_stderr(
                stderr_path,
                4000,
            )
        self.assertFalse(monitored["timed_out"])
        self.assertFalse(monitored["output_too_long"])
        self.assertEqual(monitored["process"].returncode, 0)
        self.assertEqual(stdout.strip(), "sample input")
        self.assertFalse(stdout_too_long)
        self.assertFalse(stderr_too_long)
        self.assertEqual(stderr, "")
        self.assertEqual(exit_code, 0)
