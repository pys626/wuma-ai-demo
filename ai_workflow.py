"""Bounded model waits and reusable, input-specific results; no worker writes to DB/UI."""
from contextvars import copy_context
from datetime import datetime
import queue
import threading
import time
import uuid

import ai_budget
from local_store import fingerprint, read_json, scoped_path, write_json


class WorkflowError(RuntimeError):
    pass


_slots = threading.BoundedSemaphore(2)


class Operation:
    def __init__(self, seconds=ai_budget.OPERATION_SECONDS):
        self.started = time.monotonic()
        self.deadline = self.started + seconds

    def run(self, action, on_tick=None):
        if time.monotonic() >= self.deadline:
            raise WorkflowError("本次操作已到等待上限；已完成的阶段仍保留，可点击原按钮继续。")
        if not _slots.acquire(blocking=False):
            raise WorkflowError("当前已有两项AI请求在运行，请稍后重试；回答和已完成阶段仍保留。")
        request_deadline = min(self.deadline, time.monotonic() + ai_budget.REQUEST_SECONDS)
        result_queue = queue.Queue(maxsize=1)
        context = copy_context()

        def work():
            try:
                with ai_budget.request_deadline(request_deadline):
                    value = action()
                result_queue.put((True, value))
            except Exception as error:
                result_queue.put((False, error))
            finally:
                _slots.release()

        threading.Thread(target=lambda: context.run(work), daemon=True, name="wuma-ai-request").start()
        while True:
            left = request_deadline - time.monotonic()
            if left <= 0:
                # Worker only computes a response. Late responses never advance a question or save a grade.
                raise WorkflowError("本次AI请求已到等待上限；当前回答和已完成阶段仍保留，请重试。")
            if on_tick:
                on_tick(time.monotonic() - self.started, max(0, self.deadline - time.monotonic()))
            try:
                success, value = result_queue.get(timeout=min(0.5, left))
            except queue.Empty:
                continue
            if time.monotonic() >= request_deadline:
                raise WorkflowError("本次AI请求已到等待上限；当前回答仍保留，请重试。")
            if not success:
                raise value
            return value


class Checkpoint:
    def __init__(self, database, identity):
        self.key = fingerprint(identity)
        self.path = scoped_path(database, "ai_checkpoints", identity)
        document = read_json(self.path)
        if document is not None and (not isinstance(document, dict) or document.get("version") != 1
                                     or document.get("key") != self.key or not isinstance(document.get("stages"), dict)):
            raise ValueError("AI阶段记录格式不正确，原文件已保留。")
        self.document = document or {"version": 1, "key": self.key, "stages": {}, "failed_stage": "",
                                     "request_token": uuid.uuid4().hex}
        if document is None:
            self._save()

    def get(self, stage):
        return self.document["stages"].get(stage)

    def save(self, stage, value):
        self.document["stages"][stage] = value
        self.document["failed_stage"] = ""
        self._save()
        return value

    def failed(self, stage):
        self.document["failed_stage"] = stage
        self._save()

    def _save(self):
        self.document["saved_at"] = datetime.now().isoformat(timespec="seconds")
        write_json(self.path, self.document)

    def clear(self):
        self.path.unlink(missing_ok=True)
