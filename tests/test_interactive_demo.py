import concurrent.futures
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import access_control
import accounts
import code_verifier as verifier
import database as db
import demo_playback
import interactive_demo
import llm_service as llm


class InteractiveDemoTests(unittest.TestCase):
    def setUp(self):
        self.workspaces = []
        self.env = patch.dict(os.environ, {'WUMA_DEMO_MODE': 'false', 'WUMA_INTERACTIVE_DEMO': 'true'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        for workspace in {item['path']: item for item in self.workspaces}.values():
            if not Path(workspace['path']).exists():
                continue
            folder = interactive_demo.validate_workspace(workspace).parent
            shutil.rmtree(folder)

    def workspace(self):
        workspace = interactive_demo.create_workspace()
        self.workspaces.append(workspace)
        return workspace

    def page(self, query_params=None):
        page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'competition_demo.py'))
        page.query_params.update(query_params or {})
        page.run(timeout=20)
        self.assertEqual(len(page.exception), 0)
        self.workspaces.append(page.session_state[interactive_demo.KEY])
        return page

    def click(self, page, label):
        next(button for button in page.button if button.label == label).click().run(timeout=20)
        self.assertEqual(len(page.exception), 0)

    def test_concurrent_visitors_keep_separate_databases_and_default_path(self):
        left, right = self.workspace(), self.workspace()
        default = db.current_database_path()
        def write(workspace, name):
            with db.database_scope(workspace['path']):
                db.create_submission_with_questions('20260001', name, 'isolated', '', '', '2026-10-11', [])
                return db.current_database_path(), [row['name'] for row in db.list_submissions()]
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            a, b = list(executor.map(lambda item: write(*item), [(left, '访客甲'), (right, '访客乙')]))
        self.assertNotEqual(a[0], b[0])
        self.assertIn('访客甲', a[1]); self.assertNotIn('访客乙', a[1])
        self.assertIn('访客乙', b[1]); self.assertNotIn('访客甲', b[1])
        self.assertEqual(db.current_database_path(), default)

    def test_roles_use_canonical_separate_permissions(self):
        workspace = self.workspace()
        with db.database_scope(workspace['path']):
            teacher = accounts.get_account(2)
            admin = accounts.get_account(1)
            with access_control.scope(teacher, 1):
                self.assertEqual(len(accounts.list_classes(2)), 1)
                with self.assertRaises(PermissionError):
                    accounts.create_class(2, '越权班级', teacher_id=3)
            with access_control.scope(admin, 1):
                self.assertEqual(len(accounts.list_classes(1)), 2)
                self.assertGreater(accounts.create_class(1, '新演示班级', teacher_id=2), 2)
                with self.assertRaises(PermissionError):
                    db.get_report(1)

    def test_exact_playback_and_rejection_never_call_provider_or_backend(self):
        data = demo_playback.fixture()
        assignment = data['assignment']
        with demo_playback.scope(), patch('llm_provider.chat_text', side_effect=AssertionError('network')), patch.object(verifier, 'get_execution_backend', side_effect=AssertionError('execution')):
            self.assertTrue(llm.is_configured())
            result = verifier.verify_cpp_code(data['code'], verifier.formal_test_cases(assignment), assignment['cpp_standard'])
            self.assertEqual(result['passed'], 11)
            self.assertEqual(result['execution_backend'], 'saved-case-playback')
            changed = verifier.verify_cpp_code(data['code']+'\nint changed;', verifier.formal_test_cases(assignment), assignment['cpp_standard'])
            self.assertEqual(changed['overall_status'], '未执行')
            self.assertEqual(verifier.run_cpp_code(data['code'])['overall_status'], '未执行')
            for case in data['cases']:
                for qa in case['qa']:
                    self.assertEqual(llm.evaluate_initial_answer(assignment['title'], data['code'], qa, qa['answer']), qa['initial_evaluation'])
                    if qa['follow_up_answer']:
                        self.assertEqual(llm.evaluate_follow_up(assignment['title'], data['code'], qa, qa['answer'], qa['follow_up_question'], qa['follow_up_answer'], qa['initial_evaluation']), qa['final_evaluation'])
                self.assertEqual(llm.generate_report({'problem': assignment['title'], 'code': data['code']}, case['qa'])['overall'], case['report']['overall'])
            with self.assertRaisesRegex(llm.LLMServiceError, '没有对应的预存评价'):
                llm.evaluate_initial_answer(assignment['title'], data['code'], data['cases'][0]['qa'][0], '任意新答案')
            with self.assertRaisesRegex(llm.LLMServiceError, '不发送新的模型请求'):
                llm.test_connection()
        self.assertFalse(demo_playback.active())

    def test_student_can_submit_complete_defense_and_report_in_real_ui(self):
        page = self.page()
        self.click(page, '体验学生甲端')
        self.click(page, '实验提交')
        self.click(page, '填入演示材料')
        self.click(page, '载入本例已保存的评测证据')
        self.click(page, '进入案例答辩（结果回放）')
        self.assertGreater(page.session_state.submission_id, 3)
        for question_index in range(3):
            self.click(page, '填入示例回答')
            self.click(page, '提交并查看已保存评价')
            if question_index == 1:
                self.click(page, '填入示例回答')
                self.click(page, '提交追问回答')
        workspace = page.session_state[interactive_demo.KEY]
        with db.database_scope(workspace['path']):
            self.assertEqual(db.get_report(page.session_state.submission_id)['overall'], 100)
        self.click(page, '切换到教师')
        self.click(page, '教师工作台')
        self.assertEqual(page.session_state[interactive_demo.KEY], workspace)
        self.click(page, '切换到学生甲')
        self.click(page, '学生报告')
        self.assertGreater(page.session_state.submission_id, 3)
        self.assertTrue(page.session_state.defense_completed)

    def test_admin_navigation_class_switch_and_logout_preserve_workspace(self):
        page = self.page()
        workspace = page.session_state[interactive_demo.KEY]
        self.click(page, '体验管理员端')
        self.click(page, '进入账号与系统管理')
        self.assertTrue(any('查看与管理班级学生' in item.label for item in page.expander))
        next(item for item in page.selectbox if item.label == '当前班级').select(2).run(timeout=20)
        self.assertEqual(len(page.exception), 0)
        self.assertEqual(page.session_state[interactive_demo.KEY], workspace)
        self.click(page, '退出登录')
        self.assertEqual(page.session_state[interactive_demo.KEY], workspace)
        self.assertTrue(any(button.label == '体验管理员端' for button in page.button))

    def test_mutated_login_has_actionable_reset_instead_of_exception(self):
        page = self.page()
        workspace = page.session_state[interactive_demo.KEY]
        with db.database_scope(workspace['path']), db.get_connection() as connection:
            connection.execute('UPDATE accounts SET active=0 WHERE id=2')
        self.click(page, '体验教师端')
        self.assertTrue(any('恢复初始演示数据' in item.value for item in page.warning))

    def test_teacher_saves_review_and_student_sees_same_record(self):
        page = self.page()
        self.click(page, '体验教师端')
        self.click(page, '教师工作台')
        next(item for item in page.selectbox if item.label == '筛选学生状态').select('全部记录').run(timeout=20)
        selector = next(item for item in page.selectbox if item.label == '选择要处理的提交')
        selector.select(next(option for option in selector.options if '提交编号：1 ·' in option)).run(timeout=20)
        next(item for item in page.text_area if item.label == '教师复核意见（选填）').set_value('私有演示空间的复核意见')
        self.click(page, '保存教师复核')
        self.click(page, '切换到学生甲')
        self.click(page, '学生报告')
        self.assertEqual(page.session_state.submission_id, 1)
        self.assertTrue(any('私有演示空间的复核意见' in item.value for item in page.info))

    def test_saved_review_survives_lost_session_using_same_demo_link(self):
        page = self.page()
        self.click(page, '体验教师端')
        self.click(page, '教师工作台')
        next(item for item in page.selectbox if item.label == '筛选学生状态').select('全部记录').run(timeout=20)
        selector = next(item for item in page.selectbox if item.label == '选择要处理的提交')
        selector.select(next(option for option in selector.options if '提交编号：1 ·' in option)).run(timeout=20)
        next(item for item in page.text_area if item.label == '教师复核意见（选填）').set_value('刷新后仍应显示的新意见')
        self.click(page, '保存教师复核')
        original_workspace = page.session_state[interactive_demo.KEY]
        refreshed = self.page(dict(page.query_params))
        self.click(refreshed, '体验学生甲端')
        self.click(refreshed, '学生报告')
        self.assertTrue(any('刷新后仍应显示的新意见' in item.value for item in refreshed.info))
        self.assertEqual(refreshed.session_state[interactive_demo.KEY]['path'], original_workspace['path'])

    def test_teacher_role_switch_opens_selected_older_submission(self):
        page = self.page()
        workspace = page.session_state[interactive_demo.KEY]
        fixture = demo_playback.fixture()
        case = fixture['cases'][0]
        with db.database_scope(workspace['path']):
            assignment = next(row for row in db.list_assignments() if row['title'] == fixture['assignment']['title'])
            questions = [{'dimension': qa['dimension'], 'question': qa['question'],
                          'reference_points': qa['reference_points'], 'reason': qa.get('question_reason', '')}
                         for qa in case['qa']]
            newer_id = db.create_submission_with_questions('20260001', '案例学生甲', assignment['title'], '',
                        fixture['code'], '2026-10-11 22:00', questions, assignment=assignment)
            db.save_report(newer_id, case['report'], '2026-10-11 22:01')
        self.assertGreater(newer_id, 1)
        self.click(page, '体验教师端')
        self.click(page, '教师工作台')
        next(item for item in page.selectbox if item.label == '筛选学生状态').select('全部记录').run(timeout=20)
        selector = next(item for item in page.selectbox if item.label == '选择要处理的提交')
        selector.select(next(option for option in selector.options if '提交编号：1 ·' in option)).run(timeout=20)
        next(item for item in page.text_area if item.label == '教师复核意见（选填）').set_value('旧提交的新复核意见')
        self.click(page, '保存教师复核')
        self.click(page, '切换到学生甲')
        self.click(page, '学生报告')
        self.assertEqual(page.session_state.submission_id, 1)
        self.assertTrue(any('旧提交的新复核意见' in item.value for item in page.info))

    def test_resume_handles_keep_visitors_separate_and_reject_invalid_or_expired_handles(self):
        left, right = self.workspace(), self.workspace()
        self.assertNotEqual(left['resume_token'], right['resume_token'])
        self.assertEqual(interactive_demo.restore_workspace(left['resume_token'])['path'], left['path'])
        self.assertEqual(interactive_demo.restore_workspace(right['resume_token'])['path'], right['path'])
        for invalid in ['../../outside', '', 'a' * 44, left['resume_token'] + '/..']:
            self.assertIsNone(interactive_demo.restore_workspace(invalid))
        with patch('interactive_demo.time.time', return_value=left['expires_at'] + 1):
            self.assertIsNone(interactive_demo.restore_workspace(left['resume_token']))

    def test_reset_invalidates_old_link_and_returns_explicit_new_space_notice(self):
        page = self.page()
        original_query = dict(page.query_params)
        original = page.session_state[interactive_demo.KEY]
        self.click(page, '恢复初始演示数据')
        replacement = page.session_state[interactive_demo.KEY]
        self.workspaces.append(replacement)
        self.assertNotEqual(replacement['path'], original['path'])
        self.assertIsNone(interactive_demo.restore_workspace(original['resume_token']))
        expired_page = self.page(original_query)
        self.assertTrue(any('已建立新的演示空间' in item.value for item in expired_page.warning))

    def test_resumed_student_cannot_open_other_students_selected_report(self):
        page = self.page()
        self.click(page, '体验教师端')
        self.click(page, '教师工作台')
        next(item for item in page.selectbox if item.label == '筛选学生状态').select('全部记录').run(timeout=20)
        selector = next(item for item in page.selectbox if item.label == '选择要处理的提交')
        selector.select(next(option for option in selector.options if '提交编号：2 ·' in option)).run(timeout=20)
        self.click(page, '切换到学生甲')
        self.click(page, '学生报告')
        self.assertEqual(page.session_state.submission_id, 1)

    def test_review_uses_beijing_time_and_survives_logout_roundtrip(self):
        page = self.page()
        workspace = page.session_state[interactive_demo.KEY]
        self.click(page, '体验学生甲端')
        self.click(page, '学生报告')
        self.click(page, '退出登录')
        self.assertEqual(page.session_state[interactive_demo.KEY], workspace)
        self.click(page, '体验教师端')
        self.click(page, '教师工作台')
        next(item for item in page.selectbox if item.label == '筛选学生状态').select('全部记录').run(timeout=20)
        selector = next(item for item in page.selectbox if item.label == '选择要处理的提交')
        selector.select(next(option for option in selector.options if '提交编号：1 ·' in option)).run(timeout=20)
        comment = '北京时间复核验收'
        next(item for item in page.text_area if item.label == '教师复核意见（选填）').set_value(comment)

        class UTCServerClock(datetime):
            @classmethod
            def now(cls, tz=None):
                value = datetime(2026, 10, 11, 13, 49, tzinfo=timezone.utc)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        with patch('app_clock.datetime', UTCServerClock):
            self.click(page, '保存教师复核')
        with db.database_scope(workspace['path']):
            review = db.get_teacher_review(1)
            self.assertEqual(review['comment'], comment)
            self.assertEqual(review['reviewed_at'], '2026-10-11 21:49')
            with patch('app_clock.datetime', UTCServerClock):
                db.save_teacher_review(1, '演示教师', '认可AI诊断', 100, comment)
            self.assertEqual(db.get_teacher_review(1)['reviewed_at'], '2026-10-11 21:49')
        self.click(page, '退出登录')
        for key in ('path', 'password', 'resume_token'):
            self.assertEqual(page.session_state[interactive_demo.KEY][key], workspace[key])
        self.click(page, '体验学生甲端')
        self.click(page, '学生报告')
        self.assertEqual(page.session_state.submission_id, 1)
        self.assertTrue(any(comment in item.value for item in page.info))
        self.assertTrue(any('复核时间：2026-10-11 21:49' in item.value for item in page.caption))
