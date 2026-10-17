"""Navigation and large-roster workflows against synthetic databases only."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import accounts
import browser_session
import database as db
from auth_helpers import PASSWORD

ROOT = Path(__file__).resolve().parents[1]


class StudentManagementNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.temp.name) / 'isolated.db'
        db.init_db()
        self.admin = accounts.bootstrap('fixture', 'fixture', 'qa-admin', '测试管理员', PASSWORD, '算法一班',
                                        teacher_username='qa-teacher', teacher_name='测试教师', teacher_password=PASSWORD)
        self.first = accounts.list_classes(self.admin)[0]
        self.teacher = self.first['teacher_id']
        self.second = accounts.create_class(self.admin, '算法二班', self.teacher)
        self.empty = accounts.create_class(self.admin, '空班级', self.teacher)
        encoded = accounts._hash(PASSWORD)
        with db.get_connection() as connection:
            connection.execute('UPDATE accounts SET must_change=0')
            for number in range(1, 106):
                sid = f'2099{number:04d}'
                name = '同名学生' if number in (8, 9) else f'测试学生{number:03d}'
                target = connection.execute("INSERT INTO accounts(username,display_name,role,student_id,password_hash,active,must_change,created_at) VALUES(?,?,'student',?,?,?,?,?)",
                                            (sid, name, sid, encoded, int(number % 5 != 0), int(number % 4 == 0), int(time.time()))).lastrowid
                connection.execute('INSERT INTO enrollments VALUES(?,?)', (self.first['id'], target))
            target = connection.execute("INSERT INTO accounts(username,display_name,role,student_id,password_hash,must_change,created_at) VALUES('20992001','二班学生','student','20992001',?,0,?)",
                                        (encoded, int(time.time()))).lastrowid
            connection.execute('INSERT INTO enrollments VALUES(?,?)', (self.second, target))

    def tearDown(self):
        db.DB_PATH = self.old_path
        self.temp.cleanup()

    def app(self, role='admin', page=None):
        app = AppTest.from_file(str(ROOT / 'app.py'))
        username = 'qa-admin' if role == 'admin' else 'qa-teacher'
        app.session_state['auth_token'] = accounts.login(username, PASSWORD, expected_role=role)
        app.session_state['selected_class_id'] = self.first['id']
        if page:
            app.session_state['current_page'] = page
        app.run(timeout=30)
        self.assertFalse(app.exception)
        return app

    @staticmethod
    def field(app, label):
        return next(item for item in [*app.text_input, *app.selectbox] if item.label == label)

    @staticmethod
    def button(app, label):
        return next(item for item in app.button if item.label == label)

    def settings(self, role='admin'):
        return self.app(role, '账号与系统管理' if role == 'admin' else '班级与学生管理')

    def test_home_shortcut_and_sidebar_keep_same_valid_admin_session(self):
        for entry in ('进入账号与系统管理', '账号与系统管理'):
            with self.subTest(entry=entry):
                app = self.app()
                original = app.session_state['auth_token']
                self.button(app, entry).click().run(timeout=30)
                self.assertFalse(app.exception)
                self.assertEqual(app.session_state['current_page'], '账号与系统管理')
                self.assertEqual(app.session_state['auth_token'], original)
                self.assertEqual(accounts.session(original)['role'], 'admin')
                self.assertTrue(any(title.value == '账号与系统管理' for title in app.title))

    def test_revoked_session_navigation_has_explanation_and_rejects_access(self):
        app = self.app()
        accounts.logout(app.session_state['auth_token'])
        self.button(app, '进入账号与系统管理').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertTrue(any('会话已过期或被撤销' in message.value for message in app.info))
        self.assertTrue(any(title.value == '登录悟码AI' for title in app.title))
        self.assertFalse(any(title.value == '账号与系统管理' for title in app.title))

    def test_refresh_restores_admin_settings_without_new_login(self):
        original = accounts.login('qa-admin', PASSWORD, expected_role='admin')
        view = {'page': '账号与系统管理', 'class_id': self.first['id']}
        app = AppTest.from_file(str(ROOT / 'app.py'))
        def exchange(database, operation, *args):
            return {'token': original, 'view': browser_session.sanitize_view(view)} if operation == 'read' else None
        with patch.object(browser_session, 'exchange', side_effect=exchange):
            app.run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['auth_token'], original)
        self.assertEqual(app.session_state['current_page'], '账号与系统管理')

    def test_login_form_waits_for_restore_receipt_then_stops_reading_again(self):
        app = AppTest.from_file(str(ROOT / 'app.py'))
        with patch.object(browser_session, '_bridge', return_value=None):
            app.run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.radio), 0)
        self.assertTrue(any('正在恢复登录状态' in item.value for item in app.caption))
        app.run(timeout=30)
        self.assertTrue(app.session_state['_browser_read_completed'])
        entry = next(item for item in app.radio if item.label == '请选择登录入口')
        entry.set_value('教师入口').run(timeout=30)
        self.assertFalse(app.exception)
        self.assertTrue(any(item.label == '教师账号' for item in app.text_input))

    def test_logout_waits_for_clear_and_does_not_restore_old_browser_state(self):
        app = self.app()
        original = app.session_state['auth_token']
        with patch.object(browser_session, '_bridge', return_value=None):
            self.button(app, '退出登录').click().run(timeout=30)
        self.assertIsNone(accounts.session(original))
        self.assertEqual(len(app.radio), 0)
        self.assertTrue(app.session_state['_browser_clear_pending'])
        self.assertTrue(app.session_state['_browser_read_completed'])
        app.run(timeout=30)
        self.assertFalse(app.exception)
        self.assertNotIn('_browser_clear_pending', app.session_state)
        with patch.object(browser_session, '_bridge', side_effect=AssertionError('A completed logout must not read again')):
            next(item for item in app.radio if item.label == '请选择登录入口').set_value('管理员入口').run(timeout=30)
        self.assertTrue(any(item.label == '管理员账号' for item in app.text_input))

    def test_identity_and_roster_are_collapsed_in_both_roles(self):
        for role in ('admin', 'teacher'):
            with self.subTest(role=role):
                app = self.settings(role)
                personal = next(item for item in app.expander if item.label == '我的账号')
                roster = next(item for item in app.expander if item.label == '查看与管理班级学生（共105人）')
                self.assertFalse(personal.proto.expanded)
                self.assertFalse(roster.proto.expanded)
                self.assertFalse(any('当前账号：' in item.value or '登录账号：' in item.value for item in app.caption))
                self.assertTrue(any(item.value.startswith('姓名：') for item in app.markdown))
                self.assertTrue(any(item.value.startswith('登录用户名：') for item in app.markdown))
                self.assertTrue(any(item.value.startswith('角色：') for item in app.markdown))
                self.assertEqual(len(app.dataframe[0].value), 10)

    def test_pagination_limits_table_and_operation_candidates(self):
        app = self.settings()
        self.assertEqual(len(self.field(app, '选择本页学生进行账号操作').options), 10)
        self.assertTrue(self.button(app, '上一页').disabled)
        self.button(app, '下一页').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.iloc[0]['学号'], '20990011')
        self.field(app, '每页人数').set_value(50).run(timeout=30)
        self.assertEqual(len(app.dataframe[0].value), 50)
        self.assertEqual(app.dataframe[0].value.iloc[0]['学号'], '20990001')
        self.button(app, '下一页').click().run(timeout=30)
        self.button(app, '下一页').click().run(timeout=30)
        self.assertEqual(len(app.dataframe[0].value), 5)
        self.assertTrue(self.button(app, '下一页').disabled)

    def test_search_status_combination_and_empty_results(self):
        app = self.settings('teacher')
        self.field(app, '搜索姓名或学号').set_value('同名学生').run(timeout=30)
        self.assertEqual(set(app.dataframe[0].value['学号']), {'20990008', '20990009'})
        self.field(app, '学生状态').set_value('初始密码待修改').run(timeout=30)
        self.assertEqual(list(app.dataframe[0].value['学号']), ['20990008'])
        self.field(app, '搜索姓名或学号').set_value(' 不存在的学生 ').run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 0)
        self.assertFalse(any(item.label == '选择本页学生进行账号操作' for item in app.selectbox))
        self.assertTrue(any('没有符合条件的学生' in item.value for item in app.info))

    def test_search_and_page_changes_clear_target_and_unsent_password(self):
        app = self.settings()
        selector = self.field(app, '选择本页学生进行账号操作')
        target = accounts.roster(self.admin, self.first['id'])[0]['id']
        selector.set_value(target).run(timeout=30)
        self.field(app, '新的学生初始密码').set_value('Unsubmitted-password-2026').run(timeout=30)
        self.button(app, '下一页').click().run(timeout=30)
        self.assertFalse(app.exception)
        self.assertIsNone(self.field(app, '选择本页学生进行账号操作').value)
        prefix = f'class_students_{self.admin}_{self.first["id"]}_password_'
        self.assertFalse(any(key.startswith(prefix) for key in app.session_state.filtered_state))
        self.field(app, '搜索姓名或学号').set_value('20990001').run(timeout=30)
        self.assertEqual(len(app.dataframe[0].value), 1)
        self.assertIsNone(self.field(app, '选择本页学生进行账号操作').value)

    def test_switching_class_clears_filters_target_password_and_keeps_auth(self):
        app = self.settings('teacher')
        original = app.session_state['auth_token']
        prefix = f'class_students_{self.teacher}_{self.first["id"]}'
        self.field(app, '搜索姓名或学号').set_value('20990001').run(timeout=30)
        target = accounts.roster(self.teacher, self.first['id'])[0]['id']
        self.field(app, '选择本页学生进行账号操作').set_value(target).run(timeout=30)
        self.field(app, '新的学生初始密码').set_value('Unsubmitted-password-2026').run(timeout=30)
        self.field(app, '当前班级').set_value(self.second).run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['auth_token'], original)
        self.assertEqual(app.session_state['current_page'], '班级与学生管理')
        self.assertFalse(any(key.startswith(prefix) for key in app.session_state.filtered_state))
        self.assertEqual(self.field(app, '搜索姓名或学号').value, '')
        self.assertIsNone(self.field(app, '选择本页学生进行账号操作').value)
        self.assertEqual(list(app.dataframe[0].value['学号']), ['20992001'])

    def test_empty_class_shows_guidance_and_has_no_reset_target(self):
        app = self.settings()
        self.field(app, '当前班级').set_value(self.empty).run(timeout=30)
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe), 0)
        self.assertTrue(any('该班级尚无学生' in item.value for item in app.info))
        self.assertFalse(any(item.label == '重置并要求修改' for item in app.button))

    def test_filtered_password_reset_affects_only_explicitly_selected_student(self):
        for role in ('admin', 'teacher'):
            with self.subTest(role=role):
                app = self.settings(role)
                self.field(app, '搜索姓名或学号').set_value('同名学生').run(timeout=30)
                matches = [student for student in accounts.roster(self.admin, self.first['id']) if student['display_name'] == '同名学生']
                target = matches[1]
                self.field(app, '选择本页学生进行账号操作').set_value(target['id']).run(timeout=30)
                self.assertTrue(any('20990009' in item.value and '处理学生' in item.value for item in app.markdown))
                self.assertTrue(any('所属班级：算法一班' in item.value for item in app.caption))
                new_password = f'Reset-{role}-password-2026'
                self.field(app, '新的学生初始密码').set_value(new_password)
                self.button(app, '重置并要求修改').click().run(timeout=30)
                self.assertFalse(app.exception)
                self.assertTrue(any('同名学生（20990009）' in item.value for item in app.success))
                self.assertTrue(accounts.session(accounts.login('20990009', new_password))['must_change'])
                self.assertIsNotNone(accounts.session(accounts.login('20990008', PASSWORD)))


if __name__ == '__main__':
    unittest.main()
