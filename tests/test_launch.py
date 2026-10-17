from auth_helpers import authenticate
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import database as db
import runtime_config


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "synthetic.db"
        self.config = Path(self.temp.name) / "connector.json"
        self.root = Path(__file__).resolve().parents[1]

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, mode="local", synthetic=True):
        args = [sys.executable, "-B", str(self.root / "launch.py"), "--mode", mode,
                "--database", str(self.path), "--config", str(self.config), "--prepare-only"]
        if synthetic:
            args.append("--synthetic")
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=20)

    def query(self, config, tool, arguments=None):
        server = config['mcpServers']['wuma-ai']
        result = subprocess.run(
            [server['command'], *server['args']],
            input=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": tool, "arguments": arguments or {}}}) + "\n",
            text=True, encoding="utf-8", capture_output=True,
            env=dict(os.environ, **server['env']), check=True, timeout=20,
        )
        response = json.loads(result.stdout.strip())
        self.assertFalse(response['result']['isError'])
        return json.loads(response['result']['content'][0]['text'])

    def test_full_rehearsal_exports_same_source_and_web_review_is_seen_by_mcp(self):
        prepared = self.prepare()
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        config = json.loads(self.config.read_text(encoding='utf-8'))
        server = config['mcpServers']['wuma-ai']
        self.assertEqual(server['env']['WUMA_AI_DB_PATH'], str(self.path.resolve()))
        self.assertEqual(server['env']['WUMA_DEMO_MODE'], 'false')
        self.assertEqual(set(server['env']), {'WUMA_AI_DB_PATH', 'WUMA_DEMO_MODE', 'WUMA_DEMO_READ_ONLY', 'PYTHONUTF8'})
        overview = self.query(config, 'wuma_teacher_overview')
        self.assertEqual(overview['数据快照']['数据源标识'], runtime_config.database_id(self.path))
        self.assertEqual(overview['数据快照']['最新提交编号'], 3)
        with patch.object(db, 'DB_PATH', self.path), patch.dict(os.environ, {'WUMA_DEMO_MODE': 'false'}):
            app = AppTest.from_file(str(self.root / 'app.py'))
            app.session_state['auth_role'] = '教师'
            app.session_state['auth_name'] = '演练教师'
            app.session_state['current_page'] = '教师工作台'
            authenticate(app, '教师')
            import accounts
            server['env']['WUMA_TEACHER_TOKEN'] = accounts.connector_token(accounts.session(app.session_state['auth_token'])['id'])
            app.run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            self.assertTrue(any(runtime_config.database_id(self.path) in item.value for item in app.caption))
            next(button for button in app.button if button.label == '保存教师复核').click().run(timeout=20)
            self.assertEqual(len(app.exception), 0)
            self.assertIsNotNone(db.get_teacher_review(3))
            diagnosis = self.query(config, 'wuma_student_diagnosis', {'submission_id': 3})
            self.assertEqual(diagnosis['教师复核']['状态'], '已复核')
            self.assertEqual(self.query(config, 'wuma_teacher_overview')['教师待办总数'], len(db.list_teacher_tasks()))

    def test_demo_exports_demo_source_and_can_be_restarted_without_duplicate_records(self):
        for _ in range(2):
            result = self.prepare(mode='demo', synthetic=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(config['mcpServers']['wuma-ai']['env']['WUMA_DEMO_MODE'], 'true')
        overview = self.query(config, 'wuma_teacher_overview')
        self.assertEqual(overview['数据快照']['最新提交编号'], 3)
        with patch.object(db, 'DB_PATH', self.path):
            self.assertEqual(len(db.list_submissions()), 3)

    def test_synthetic_option_refuses_existing_unmarked_files_without_changes(self):
        config_path = self.config
        self.config = self.path
        self.assertNotEqual(self.prepare().returncode, 0)
        self.assertFalse(self.path.exists())
        self.config = config_path
        self.path.write_bytes(b'an existing business file')
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertFalse(self.config.exists())


if __name__ == '__main__':
    unittest.main()
