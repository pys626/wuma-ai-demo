import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import accounts
import database as db
from auth_helpers import authenticate,PASSWORD

ROOT=Path(__file__).resolve().parents[1]
class AuthenticationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.old=db.DB_PATH;db.DB_PATH=Path(self.temp.name)/'db.sqlite'
        db.init_db();accounts.init_schema()
    def tearDown(self):db.DB_PATH=self.old;self.temp.cleanup()
    def app(self):return AppTest.from_file(str(ROOT/'app.py'))
    def ready(self):
        a=self.app();authenticate(a,'学生','20261234','李同学');return a
    def test_personal_login_uses_canonical_identity_and_logout_revokes_token(self):
        self.ready();a=self.app().run(timeout=20)
        next(i for i in a.text_input if i.label=='学生学号').set_value('20261234')
        next(i for i in a.text_input if i.label=='密码').set_value(PASSWORD)
        next(b for b in a.button if b.label=='进入学生端').click().run(timeout=20)
        self.assertFalse(a.exception);token=a.session_state['auth_token']
        self.assertEqual(a.session_state['auth_name'],'李同学')
        labels=[b.label for b in a.button];self.assertIn('实验提交',labels);self.assertNotIn('系统设置',labels)
        next(b for b in a.button if b.label=='退出登录').click().run(timeout=20)
        self.assertIsNone(accounts.session(token));self.assertFalse(a.exception)
    def test_forged_role_without_session_does_not_open_teacher_routes(self):
        self.ready();a=self.app();a.session_state['auth_role']='教师';a.session_state['current_page']='教师工作台'
        a.run(timeout=20);self.assertFalse(a.exception)
        self.assertTrue(any(i.label=='学生学号' for i in a.text_input));self.assertFalse(any(b.label=='实验管理' for b in a.button))
    def test_first_setup_requires_existing_teacher_secret_and_preserves_assignments(self):
        before=db.list_assignments()
        with patch.dict(os.environ,{'TEACHER_PASSWORD':'fixture-setup'}):
            a=self.app().run(timeout=20)
            values={'原教师密码':'wrong','管理员账号':'admin-one','管理员姓名':'管理员','管理员新密码':PASSWORD,'确认管理员新密码':PASSWORD,
                    '首位教师账号':'teacher-one','首位教师姓名':'王老师','首位教师密码':PASSWORD,'确认首位教师密码':PASSWORD,'首个班级名称':'软件一班'}
            for i in a.text_input:
                if i.label in values:i.set_value(values[i.label])
            next(b for b in a.button if b.label=='建立管理员并保留旧记录').click().run(timeout=20)
            self.assertFalse(accounts.enabled());self.assertTrue(a.error)
            next(i for i in a.text_input if i.label=='原教师密码').set_value('fixture-setup')
            next(b for b in a.button if b.label=='建立管理员并保留旧记录').click().run(timeout=20)
        self.assertFalse(a.exception);self.assertTrue(accounts.enabled());self.assertEqual(db.list_assignments(),before)
    def test_initial_password_must_change_before_teaching_pages(self):
        accounts.bootstrap('setup','setup','admin','管理员',PASSWORD,'一班',teacher_username='teacher-one',teacher_name='王老师',teacher_password=PASSWORD);accounts.add_students(1,1,[{'学号':'20260001','姓名':'张同学','初始密码':PASSWORD}])
        a=self.app();a.session_state['auth_token']=accounts.login('20260001',PASSWORD);a.run(timeout=20)
        self.assertTrue(any(t.value=='首次登录－修改初始密码' for t in a.title));self.assertFalse(any(b.label=='实验提交' for b in a.button))
        for i in a.text_input:i.set_value(PASSWORD if i.label=='当前密码' else 'New-password-2026')
        next(b for b in a.button if b.label=='修改密码并重新登录').click().run(timeout=20)
        self.assertFalse(a.exception);self.assertFalse(accounts.session(accounts.login('20260001','New-password-2026'))['must_change'])
    def test_teacher_home_and_settings_hide_diagnostics_by_default(self):
        a=self.app();authenticate(a,'教师');a.run(timeout=20)
        self.assertFalse(a.exception);self.assertIn('班级与学生管理',[b.label for b in a.button])
        self.assertTrue(any(i.value=='① 配置实验与评分点' for i in a.info))
        next(b for b in a.button if b.label=='班级与学生管理').click().run(timeout=20)
        self.assertFalse(a.exception);self.assertFalse(any(e.label=='AI评价对照测试' for e in a.expander))
        self.assertFalse(any(i.label=='显示高级诊断' for i in a.checkbox))
        self.assertFalse(any(e.label=='教师账号管理' for e in a.expander))
    def test_missing_setup_secret_cannot_create_admin(self):
        with patch.dict(os.environ,{'TEACHER_PASSWORD':''}):a=self.app().run(timeout=20)
        self.assertFalse(a.exception);self.assertTrue(any('尚未配置教师密码' in w.value for w in a.warning))
        self.assertTrue(next(b for b in a.button if b.label=='建立管理员并保留旧记录').disabled)

    def test_expired_session_clears_previous_personal_page_cache(self):
        app=self.ready();old=app.session_state['auth_token'];accounts.logout(old)
        app.session_state['submission']={'student_id':'another','code':'private'}
        app.session_state['current_page']='AI答辩';app.run(timeout=20)
        self.assertFalse(app.exception);self.assertIsNone(app.session_state['submission'])
        self.assertTrue(any('会话已过期' in x.value for x in app.info))
    def test_first_admin_adopts_old_draft_and_keeps_source_file(self):
        import assignment_drafts
        import access_control
        assignment_drafts.save_draft(db.DB_PATH,{'title':'旧实验草稿','description':'尚未发布'})
        oldpath=assignment_drafts.draft_path(db.DB_PATH)
        aid=accounts.bootstrap('setup','setup','admin','管理员',PASSWORD,'首个班',teacher_username='teacher-one',teacher_name='王老师',teacher_password=PASSWORD)
        classroom=accounts.list_classes(aid)[0];cid=classroom['id']
        with access_control.scope(accounts.get_account(classroom['teacher_id']),cid):
            self.assertEqual(assignment_drafts.load_draft(db.DB_PATH)['data']['title'],'旧实验草稿')
            self.assertNotEqual(assignment_drafts.draft_path(db.DB_PATH),oldpath)
        self.assertTrue(oldpath.exists());self.assertEqual(db.list_assignments()[0]['title'],'求数组中的最大值')

    def test_teacher_student_provisioning_and_class_creation_work_through_ui(self):
        app=self.app();authenticate(app,'管理员');app.session_state['current_page']='账号与系统管理';app.run(timeout=20)
        for label,value in {'新教师账号':'teacher-two','新教师姓名':'李老师','教师初始密码':PASSWORD}.items():
            next(i for i in app.text_input if i.label==label).set_value(value)
        next(b for b in app.button if b.label=='开通教师').click().run(timeout=20)
        self.assertFalse(app.exception);self.assertEqual(len(accounts.list_teachers(1)),2)
        target=next(t['id'] for t in accounts.list_teachers(1) if t['username']=='teacher-two')
        selector=next(i for i in app.selectbox if i.label=='任课教师');selector.set_value(target)
        next(i for i in app.text_input if i.label=='班级名称').set_value('算法二班')
        next(b for b in app.button if b.label=='保存班级').click().run(timeout=20)
        self.assertFalse(app.exception);self.assertEqual(accounts.list_classes(target)[0]['name'],'算法二班')
        for label,value in {'学生学号':'20265555','学生姓名':'陈同学','学生初始密码':PASSWORD}.items():
            next(i for i in app.text_input if i.label==label).set_value(value)
        next(b for b in app.button if b.label=='开通学生').click().run(timeout=20)
        self.assertFalse(app.exception);self.assertTrue(any(x['student_id']=='20265555' for x in accounts.roster(1,1)))
        token=accounts.login('20265555',PASSWORD);self.assertTrue(accounts.session(token)['must_change'])
    def test_corrupt_legacy_draft_does_not_block_personal_setup_or_remove_source(self):
        import assignment_drafts
        path=assignment_drafts.draft_path(db.DB_PATH);path.write_text('invalid-json',encoding='utf-8')
        aid=accounts.bootstrap('setup','setup','admin','管理员',PASSWORD,'一班',teacher_username='teacher-one',teacher_name='王老师',teacher_password=PASSWORD)
        self.assertTrue(accounts.enabled());self.assertTrue(accounts.migration_warning(aid))
        self.assertEqual(path.read_text(encoding='utf-8'),'invalid-json')

    def test_student_credentials_cannot_create_teacher_entry_session(self):
        self.ready()
        app=self.app().run(timeout=20)
        next(r for r in app.radio if r.label=='请选择登录入口').set_value('教师入口').run(timeout=20)
        next(i for i in app.text_input if i.label=='教师账号').set_value('20261234')
        next(i for i in app.text_input if i.label=='密码').set_value(PASSWORD)
        with db.get_connection() as c:before=c.execute('SELECT COUNT(*) FROM account_sessions').fetchone()[0]
        next(b for b in app.button if b.label=='进入教师端').click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(any('请切换到学生入口' in e.value for e in app.error))
        self.assertNotIn('auth_token',app.session_state)
        with db.get_connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM account_sessions').fetchone()[0],before)
        next(r for r in app.radio if r.label=='请选择登录入口').set_value('学生入口').run(timeout=20)
        self.assertEqual(next(i for i in app.text_input if i.label=='学生学号').value,'')
        self.assertEqual(next(i for i in app.text_input if i.label=='密码').value,'')
        next(i for i in app.text_input if i.label=='学生学号').set_value('20261234')
        next(i for i in app.text_input if i.label=='密码').set_value(PASSWORD)
        next(b for b in app.button if b.label=='进入学生端').click().run(timeout=20)
        self.assertFalse(app.exception);self.assertEqual(app.session_state['auth_role'],'学生')

    def test_admin_uses_independent_entry_and_cannot_use_student_entry(self):
        self.ready()
        app=self.app().run(timeout=20)
        next(i for i in app.text_input if i.label=='学生学号').set_value('fixture-admin')
        next(i for i in app.text_input if i.label=='密码').set_value(PASSWORD)
        next(b for b in app.button if b.label=='进入学生端').click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(any('请切换到管理员入口' in e.value for e in app.error))
        self.assertNotIn('auth_token',app.session_state)
        next(r for r in app.radio if r.label=='请选择登录入口').set_value('管理员入口').run(timeout=20)
        next(i for i in app.text_input if i.label=='管理员账号').set_value('fixture-admin')
        next(i for i in app.text_input if i.label=='密码').set_value(PASSWORD)
        next(b for b in app.button if b.label=='进入管理端').click().run(timeout=20)
        self.assertFalse(app.exception);self.assertEqual(app.session_state['auth_role'],'管理员')
        self.assertIn('账号与系统管理',[b.label for b in app.button])
        self.assertNotIn('教师工作台',[b.label for b in app.button])

    def test_entry_does_not_disclose_role_before_password_is_verified(self):
        self.ready()
        with self.assertRaisesRegex(ValueError,'账号或密码不正确'):
            accounts.login('20261234','incorrect-password',expected_role='teacher')
        with self.assertRaisesRegex(ValueError,'不支持的登录入口'):
            accounts.login('20261234',PASSWORD,expected_role='unknown')
