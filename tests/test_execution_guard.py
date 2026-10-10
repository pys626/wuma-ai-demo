import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import execution_guard as guard


def process_alive(pid):
    if os.name == "nt":
        from ctypes import wintypes as w
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes, api.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        api.WaitForSingleObject.argtypes, api.WaitForSingleObject.restype = [w.HANDLE, w.DWORD], w.DWORD
        api.CloseHandle.argtypes, api.CloseHandle.restype = [w.HANDLE], w.BOOL
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class ExecutionGuardTests(unittest.TestCase):
    def run_python(self, code, timeout=2, limit=65536, standard_input="", process_limit=16):
        with tempfile.TemporaryDirectory() as folder:
            return guard.run_bounded([sys.executable, "-I", "-c", code], Path(folder),
                                     standard_input, timeout, limit, process_limit=process_limit)

    def test_only_os_variables_are_passed_to_programs(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "test-secret", "TEACHER_PASSWORD": "test-secret"}):
            result, _, _ = self.run_python("import os; print('LLM_API_KEY' in os.environ, 'TEACHER_PASSWORD' in os.environ)")
        self.assertEqual(result.stdout.strip(), "False False")

    def test_timeout_stops_process_and_next_program_still_runs(self):
        started = time.monotonic()
        result, timeout, _ = self.run_python("while True: pass", timeout=0.5)
        self.assertTrue(timeout)
        self.assertLess(time.monotonic() - started, 4)
        result, timeout, _ = self.run_python("print(42)")
        self.assertFalse(timeout)
        self.assertEqual(result.stdout.strip(), "42")

    def test_infinite_stdout_is_stopped_with_bounded_capture(self):
        result, timeout, overflow = self.run_python("import sys\nwhile True: sys.stdout.write('x'*4096); sys.stdout.flush()", limit=8192)
        self.assertTrue(overflow)
        self.assertLessEqual(len(result.stdout.encode()), 8192)
        self.assertFalse(timeout)

    def test_infinite_stderr_is_stopped_with_bounded_capture(self):
        result, _, overflow = self.run_python("import sys\nwhile True: sys.stderr.write('x'*4096); sys.stderr.flush()", limit=8192)
        self.assertTrue(overflow)
        self.assertLessEqual(len(result.stderr.encode()), 8192)

    def test_timeout_also_covers_blocked_stdin_writer(self):
        started = time.monotonic()
        _, timeout, _ = self.run_python("import time; time.sleep(20)", timeout=0.5, standard_input="x" * 200000)
        self.assertTrue(timeout)
        self.assertLess(time.monotonic() - started, 4)

    def test_children_are_killed_even_when_parent_exits_normally(self):
        code = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(20)']); print(p.pid,flush=True)"
        result, timeout, _ = self.run_python(code)
        self.assertFalse(timeout)
        self.assertFalse(process_alive(int(result.stdout.strip())))

    def test_timeout_removes_parent_and_child(self):
        code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(20)']); print(p.pid,flush=True); time.sleep(20)"
        result, timeout, _ = self.run_python(code, timeout=0.5)
        self.assertTrue(timeout)
        self.assertFalse(process_alive(int(result.stdout.strip())))

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_memory_limit_rejects_large_allocation(self):
        code = "try:\n data=bytearray(384*1024*1024)\n print('unbounded')\nexcept MemoryError:\n print('limited')"
        result, timeout, _ = self.run_python(code)
        self.assertFalse(timeout)
        self.assertEqual(result.stdout.strip(), "limited")

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_process_count_rejects_more_children_and_cleans_existing_children(self):
        code = "import subprocess,sys\nchildren=[]\ntry:\n for i in range(10):\n  children.append(subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(20)']))\nexcept OSError:\n print(len(children),flush=True)\n print(','.join(str(child.pid) for child in children),flush=True)"
        result, timeout, _ = self.run_python(code, process_limit=3)
        self.assertFalse(timeout)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "2")
        self.assertTrue(all(not process_alive(int(pid)) for pid in lines[1].split(",")))

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_guard_failure_never_starts_unprotected_process(self):
        with patch.object(guard, "WindowsJob", side_effect=guard.ProtectionError("guard failed")), patch.object(guard.subprocess, "Popen") as spawn:
            with self.assertRaises(guard.ProtectionError):
                self.run_python("print(42)")
            spawn.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_cleanup_timeout_never_returns_success_and_releases_handles(self):
        job = guard.WindowsJob.__new__(guard.WindowsJob)
        job.handle = 10
        job.api = Mock()
        job.api.TerminateJobObject.return_value = True
        job.api.WaitForSingleObject.return_value = 258
        with patch.object(job, "_process_handles", side_effect=[[20], [21]]):
            with self.assertRaisesRegex(guard.ProtectionError, "尚未完全停止"):
                job.close()
        self.assertIsNone(job.handle)
        self.assertEqual([call.args[0] for call in job.api.CloseHandle.call_args_list], [20, 21, 10])

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_cleanup_snapshot_failure_still_terminates_the_job(self):
        job = guard.WindowsJob.__new__(guard.WindowsJob)
        job.handle = 10
        job.api = Mock()
        job.api.TerminateJobObject.return_value = True
        with patch.object(job, "_process_handles", side_effect=guard.ProtectionError("snapshot failed")):
            with self.assertRaises(guard.ProtectionError):
                job.close()
        job.api.TerminateJobObject.assert_called_once_with(10, 1)
        job.api.CloseHandle.assert_called_once_with(10)
        self.assertIsNone(job.handle)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_partial_snapshot_is_requeried_before_opening_process_handles(self):
        job = guard.WindowsJob.__new__(guard.WindowsJob)
        job.handle = 10
        job.api = Mock()
        job.api.OpenProcess.return_value = 20
        snapshots = [(2, [101]), (1, [102])]

        def query(handle, kind, buffer, size, length):
            address = ctypes.cast(buffer, ctypes.c_void_p).value
            counts = (ctypes.c_uint32 * 2).from_address(address)
            assigned, pids = snapshots.pop(0)
            counts[0], counts[1] = assigned, len(pids)
            array = (ctypes.c_size_t * len(pids)).from_address(address + 8)
            for index, pid in enumerate(pids):
                array[index] = pid
            return True

        job.api.QueryInformationJobObject.side_effect = query
        with patch.object(guard.time, "sleep"):
            self.assertEqual(job._process_handles(), [20])
        self.assertEqual(job.api.QueryInformationJobObject.call_count, 2)
        job.api.OpenProcess.assert_called_once_with(0x100000, False, 102)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_more_data_snapshot_grows_buffer_and_requires_successful_query(self):
        job = guard.WindowsJob.__new__(guard.WindowsJob)
        job.handle = 10
        job.api = Mock()
        job.api.QueryInformationJobObject.side_effect = [False, True]
        with patch.object(guard.ctypes, "get_last_error", return_value=234), patch.object(guard.time, "sleep"):
            self.assertEqual(job._process_handles(), [])
        calls = job.api.QueryInformationJobObject.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertGreater(calls[1].args[3], calls[0].args[3])
        job.api.OpenProcess.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_incomplete_snapshots_stop_after_bounded_retries(self):
        job = guard.WindowsJob.__new__(guard.WindowsJob)
        job.handle = 10
        job.api = Mock()

        def query(handle, kind, buffer, size, length):
            address = ctypes.cast(buffer, ctypes.c_void_p).value
            counts = (ctypes.c_uint32 * 2).from_address(address)
            counts[0], counts[1] = 1, 0
            return True

        job.api.QueryInformationJobObject.side_effect = query
        with patch.object(guard.time, "sleep"):
            with self.assertRaisesRegex(guard.ProtectionError, "无法确认完整"):
                job._process_handles()
        self.assertEqual(job.api.QueryInformationJobObject.call_count, 4)
        job.api.OpenProcess.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_access_denied_snapshot_is_not_retried_or_accepted(self):
        job = guard.WindowsJob.__new__(guard.WindowsJob)
        job.handle = 10
        job.api = Mock()
        job.api.QueryInformationJobObject.return_value = False
        with patch.object(guard.ctypes, "get_last_error", return_value=5):
            with self.assertRaisesRegex(guard.ProtectionError, "系统错误5"):
                job._process_handles()
        job.api.QueryInformationJobObject.assert_called_once()
        job.api.OpenProcess.assert_not_called()


class TemporaryWorkspaceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows executable file locks")
    def test_brief_executable_lock_is_retried_and_owned_directory_removed(self):
        workspace=guard.TemporaryWorkspace(prefix='wuma_verify_')
        path=Path(workspace.name);original=workspace._rmtree;attempts=[]
        def delayed(*args,**kwargs):
            attempts.append(1)
            if len(attempts)==1:raise PermissionError('brief file lock')
            return original(*args,**kwargs)
        with patch.object(workspace,'_rmtree',side_effect=delayed), patch.object(guard.time,'sleep'):
            workspace.cleanup()
        self.assertEqual(len(attempts),2);self.assertFalse(path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows executable file locks")
    def test_persistent_file_lock_stops_after_bounded_retries(self):
        workspace=guard.TemporaryWorkspace(prefix='wuma_verify_')
        try:
            with patch.object(workspace,'_rmtree',side_effect=PermissionError('locked')) as remove, patch.object(guard.time,'sleep'):
                with self.assertRaisesRegex(guard.ProtectionError,'仍被系统占用'):workspace.cleanup()
            self.assertEqual(remove.call_count,20)
        finally:workspace.cleanup()

    def test_changed_path_is_never_deleted(self):
        workspace=guard.TemporaryWorkspace(prefix='wuma_verify_');original=workspace.name
        try:
            workspace.name=str(Path(original).parent/'unrelated')
            with patch.object(workspace,'_rmtree') as remove:
                with self.assertRaisesRegex(guard.ProtectionError,'目录不匹配'):workspace.cleanup()
            remove.assert_not_called()
        finally:workspace.name=original;workspace.cleanup()

    def test_cleanup_error_becomes_environment_status_in_both_local_routes(self):
        import code_verifier as verifier
        backend=verifier.LocalCppExecutionBackend()
        with patch.object(verifier,'_verify_cpp_code_local',side_effect=guard.ProtectionError('文件占用')):
            result=backend.verify_cpp_code('code',[])
        self.assertEqual(result['overall_status'],'环境不可用');self.assertIn('文件占用',result['message'])
        with patch.object(verifier,'_run_cpp_code_local',side_effect=guard.ProtectionError('文件占用')):
            result=backend.run_cpp_code('code')
        self.assertEqual(result['overall_status'],'环境不可用')


if __name__ == "__main__":
    unittest.main()
