"""Bounded local processes. No dotenv, model credentials, or application data."""

import ctypes
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import time


class ProtectionError(OSError):
    pass


def clean_environment():
    """Pass only OS/runtime variables, never the web service's credentials."""
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATH", "PATHEXT",
               "LANG", "LC_ALL", "LD_LIBRARY_PATH"}
    return {name: os.environ[name] for name in allowed if name in os.environ}


class TemporaryWorkspace(tempfile.TemporaryDirectory):
    """Retry a short Windows executable-file lock after process exit."""

    def __init__(self, *, prefix):
        if prefix != 'wuma_verify_':
            raise ProtectionError('不支持的临时执行目录。')
        self._temp_root = Path(tempfile.gettempdir()).resolve()
        super().__init__(prefix=prefix)
        self._owned_path = Path(self.name).resolve()

    def cleanup(self):
        target = Path(self.name).resolve()
        if target != self._owned_path or target.parent != self._temp_root:
            raise ProtectionError('临时执行目录不匹配，已中止清理。')
        for attempt in range(20):
            try:
                super().cleanup()
                return
            except PermissionError as error:
                if os.name != 'nt' or attempt == 19:
                    raise ProtectionError('临时程序文件仍被系统占用，请稍后重试评测。') from error
                time.sleep(0.05)


class WindowsJob:
    """A Job Object owns the process tree, memory, CPU and process-count limits."""

    def __init__(self, memory_mb=256, process_limit=16):
        from ctypes import wintypes as w
        size_t = ctypes.c_size_t

        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", w.DWORD), ("min_working", size_t), ("max_working", size_t),
                        ("active_processes", w.DWORD), ("affinity", size_t),
                        ("priority", w.DWORD), ("scheduling", w.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", size_t),
                        ("job_memory", size_t), ("peak_process_memory", size_t), ("peak_job_memory", size_t)]

        class CPU(ctypes.Structure):
            _fields_ = [("flags", w.DWORD), ("rate", w.DWORD)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "QueryInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "OpenProcess": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ProtectionError("无法创建Windows执行保护。")
        try:
            limits = Extended()
            limits.basic.flags = 0x2000 | 0x100 | 0x200 | 0x8
            limits.basic.active_processes = process_limit
            limits.process_memory = limits.job_memory = memory_mb * 1024 * 1024
            self._set(9, limits)
            # Windows expresses CpuRate as a fraction of all logical processors.
            self._set(15, CPU(0x1 | 0x4, max(1, 10000 // (os.cpu_count() or 1))))
        except BaseException:
            self.close()
            raise

    def _set(self, kind, value):
        if not self.api.SetInformationJobObject(self.handle, kind, ctypes.byref(value), ctypes.sizeof(value)):
            raise ProtectionError("无法设置Windows资源上限。")

    def assign(self, pid):
        handle = self.api.OpenProcess(0x100 | 0x1, False, pid)
        if not handle:
            raise ProtectionError("无法关联受保护进程。")
        try:
            if not self.api.AssignProcessToJobObject(self.handle, handle):
                raise ProtectionError("无法关联Windows进程组，已停止运行。")
        finally:
            self.api.CloseHandle(handle)

    def _process_handles(self):
        from ctypes import wintypes as w

        capacity = 4096
        for attempt in range(4):
            class ProcessList(ctypes.Structure):
                _fields_ = [("assigned", w.DWORD), ("count", w.DWORD),
                            ("pids", ctypes.c_size_t * capacity)]

            processes = ProcessList()
            queried = self.api.QueryInformationJobObject(
                self.handle, 3, ctypes.byref(processes), ctypes.sizeof(processes), None)
            error = ctypes.get_last_error() if not queried else 0
            if queried and processes.count <= capacity and processes.assigned <= processes.count:
                break
            # A short list or ERROR_MORE_DATA requires another complete snapshot;
            # never use partial process IDs as proof that cleanup succeeded.
            if not queried and error != 234:
                raise ProtectionError(f"无法确认受保护进程树（系统错误{error}），已中止评测。")
            if attempt == 3 or processes.count > capacity or processes.assigned > 65536:
                raise ProtectionError("无法确认完整的受保护进程树，已中止评测。")
            capacity = min(65536, max(capacity * 2, int(processes.assigned)))
            time.sleep(0.005)
        handles = []
        try:
            for pid in processes.pids[:processes.count]:
                handle = self.api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
                if handle:
                    handles.append(handle)
                elif ctypes.get_last_error() != 87:  # ERROR_INVALID_PARAMETER: process already exited
                    raise ProtectionError("无法确认子进程状态，已中止评测。")
            return handles
        except BaseException:
            for handle in handles:
                self.api.CloseHandle(handle)
            raise

    def close(self):
        if not getattr(self, "handle", None):
            return
        handles = []
        deadline = time.monotonic() + 3
        try:
            try:
                handles.extend(self._process_handles())
            finally:
                if not self.api.TerminateJobObject(self.handle, 1):
                    raise ProtectionError("无法终止受保护进程树，已中止评测。")
            # Include any children spawned between the first snapshot and termination.
            handles.extend(self._process_handles())
            for handle in handles:
                left_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if self.api.WaitForSingleObject(handle, left_ms) != 0:
                    raise ProtectionError("子进程尚未完全停止，已中止评测。")
        finally:
            for handle in handles:
                self.api.CloseHandle(handle)
            self.api.CloseHandle(self.handle)
            self.handle = None


def _resume_windows_process(pid):
    """Resume only after assignment; prevents children escaping before protection."""
    from ctypes import wintypes as w

    class ThreadEntry(ctypes.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("thread_id", w.DWORD),
                    ("owner_pid", w.DWORD), ("base_priority", w.LONG),
                    ("delta_priority", w.LONG), ("flags", w.DWORD)]

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, args, result in (
        ("CreateToolhelp32Snapshot", [w.DWORD, w.DWORD], w.HANDLE),
        ("Thread32First", [w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
        ("Thread32Next", [w.HANDLE, ctypes.POINTER(ThreadEntry)], w.BOOL),
        ("OpenThread", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        ("ResumeThread", [w.HANDLE], w.DWORD),
        ("CloseHandle", [w.HANDLE], w.BOOL),
    ):
        getattr(api, name).argtypes, getattr(api, name).restype = args, result
    snapshot = api.CreateToolhelp32Snapshot(0x4, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ProtectionError("无法恢复受保护进程。")
    resumed = False
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        available = api.Thread32First(snapshot, ctypes.byref(entry))
        while available:
            if entry.owner_pid == pid:
                thread = api.OpenThread(0x2, False, entry.thread_id)
                if thread:
                    try:
                        if api.ResumeThread(thread) != 0xFFFFFFFF:
                            resumed = True
                    finally:
                        api.CloseHandle(thread)
            available = api.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    if not resumed:
        raise ProtectionError("无法恢复受保护进程，已停止运行。")


def run_bounded(command, cwd, standard_input, timeout, byte_limit, memory_mb=256, process_limit=16):
    """Drain pipes in bounded chunks; elapsed time includes writing stdin and cleanup.

    Returns a CompletedProcess plus timeout/overflow flags. This limits resources,
    but does not isolate local file or network permissions; use Docker for that.
    """
    started = time.monotonic()
    job = None
    process = None
    overflow = threading.Event()
    reader_error = []
    output = [bytearray(), bytearray()]
    workers = []
    timed_out = False
    try:
        flags = 0
        run_command = list(command)
        if os.name == "nt":
            job = WindowsJob(memory_mb, process_limit)
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | 0x4
        else:
            limiter = shutil.which("prlimit")
            if not limiter:
                raise ProtectionError("本机缺少prlimit资源保护，请使用Docker执行后端。")
            run_command = [limiter, f"--as={memory_mb * 1024 * 1024}",
                           f"--cpu={max(1, math.ceil(timeout) + 1)}", f"--nproc={process_limit}",
                           "--fsize=33554432", "--", *run_command]
        process = subprocess.Popen(run_command, cwd=cwd, env=clean_environment(),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   shell=False, creationflags=flags, start_new_session=os.name != "nt")
        if job:
            job.assign(process.pid)
            _resume_windows_process(process.pid)

        def drain(stream, destination):
            try:
                while chunk := stream.read(4096):
                    remaining = max(0, byte_limit - len(destination))
                    destination.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
            except OSError as error:
                reader_error.append(error)
            finally:
                stream.close()

        def send_input():
            try:
                process.stdin.write(str(standard_input).encode("utf-8"))
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                process.stdin.close()

        workers = [threading.Thread(target=drain, args=(process.stdout, output[0]), daemon=True),
                   threading.Thread(target=drain, args=(process.stderr, output[1]), daemon=True),
                   threading.Thread(target=send_input, daemon=True)]
        for worker in workers:
            worker.start()
        while process.poll() is None:
            if overflow.is_set():
                break
            if time.monotonic() - started >= timeout:
                timed_out = True
                break
            time.sleep(0.01)
    finally:
        # Terminate the whole job and confirm child exit even when the parent exited normally.
        if job:
            job.close()
        elif process is not None and os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ProtectionError("无法确认进程已经停止，已中止评测。") from error
        for worker in workers:
            worker.join(timeout=3)
        if any(worker.is_alive() for worker in workers):
            raise ProtectionError("进程清理未完成，已停止评测。")
    if reader_error:
        raise ProtectionError("无法完整读取运行结果。")
    return subprocess.CompletedProcess(command, process.returncode,
                                       output[0].decode("utf-8", errors="replace"),
                                       output[1].decode("utf-8", errors="replace")), timed_out, overflow.is_set()
