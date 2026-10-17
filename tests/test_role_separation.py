"""Independent roles and legacy upgrade, using only synthetic databases."""
import ast
import copy
import hashlib
import tempfile
import time
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest
import accounts
import access_control as acl
import assignment_drafts
import database as db
from auth_helpers import PASSWORD, authenticate

ROOT = Path(__file__).resolve().parents[1]


class RoleSeparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / 'isolated.db'
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.previous
        self.tmp.cleanup()

    def fresh(self):
        self.admin = accounts.bootstrap('setup', 'setup', 'admin', '管理员', PASSWORD, '一班',
                                       teacher_username='teacher', teacher_name='李老师', teacher_password=PASSWORD)
        self.classroom = accounts.list_classes(self.admin)[0]
        self.teacher = self.classroom['teacher_id']
        accounts.add_students(self.admin, self.classroom['id'], [{'学号':'S1','姓名':'甲同学','初始密码':PASSWORD}])
        with db.get_connection() as c:
            c.execute('UPDATE accounts SET must_change=0')
        self.student = accounts.session(accounts.login('s1', PASSWORD))
        assignment = db.list_assignments()[0]
        self.assignment = assignment['id']
        self.submission = db.create_submission_with_questions('S1','甲同学',assignment['title'],'说明','int main(){}','2026-10-10',
            [{'dimension':'程序逻辑理解','question':'解释循环','reference_points':['循环']}], assignment)

    def legacy(self):
        with db.get_connection() as c:
            c.execute("""CREATE TABLE accounts (
                id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('teacher','student')),
                student_id TEXT UNIQUE, password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
                must_change INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL)""")
            c.execute("INSERT INTO accounts VALUES(1,'old-teacher','原教师','teacher',NULL,?,1,1,0,?)", (accounts._hash(PASSWORD),int(time.time())))
            c.execute('CREATE TABLE classes (id INTEGER PRIMARY KEY,name TEXT NOT NULL,teacher_id INTEGER NOT NULL REFERENCES accounts(id))')
            c.execute("INSERT INTO classes VALUES(1,'原班级',1)")
            before = tuple(c.execute('SELECT * FROM accounts WHERE id=1').fetchone())
        accounts.init_schema()
        with db.get_connection() as c:
            c.execute('INSERT INTO assignment_classes SELECT id,1 FROM assignments')
        return before

    def test_bootstrap_creates_two_roles_and_teacher_owns_initial_class(self):
        self.fresh()
        self.assertEqual(accounts.get_account(self.admin)['role'], 'admin')
        self.assertEqual(accounts.get_account(self.teacher)['role'], 'teacher')
        self.assertNotEqual(self.admin, self.teacher)
        self.assertEqual(accounts.list_teachers(self.admin)[0]['id'], self.teacher)
        with self.assertRaises(ValueError):accounts.create_class(self.admin,'未指定教师')
        with self.assertRaises(ValueError):accounts.create_class(self.admin,'管理员任课',self.admin)

    def test_admin_cannot_read_or_write_teaching_records_even_with_forged_class(self):
        self.fresh()
        with acl.scope(accounts.get_account(self.admin), self.classroom['id']):
            for fn, args in [(db.list_assignments,()),(db.list_submissions,()),(db.get_submission,(self.submission,)),
                             (db.create_assignment,('越权','','',[],'x')),
                             (db.save_teacher_review,(self.submission,'管理员','认可AI诊断',100,'')),
                             (db.process_student_feedback,(1,'答复','管理员','now'))]:
                with self.subTest(operation=fn.__name__), self.assertRaises(PermissionError):fn(*args)
            self.assertFalse(acl.assignment_allowed(acl.current(),self.assignment))
            self.assertFalse(acl.submission_allowed(acl.current(),self.submission))
        with self.assertRaises(PermissionError):accounts.connector_token(self.admin)

    def test_teacher_cannot_gain_admin_permissions_or_access_another_class(self):
        self.fresh()
        other=accounts.create_teacher(self.admin,'other','王老师',PASSWORD)
        with db.get_connection() as c:c.execute('UPDATE accounts SET must_change=0 WHERE id=?',(other,))
        other_class=accounts.create_class(self.admin,'二班',other)
        for fn,args in [(accounts.create_teacher,(self.teacher,'bad','越权',PASSWORD)),
                        (accounts.list_teachers,(self.teacher,)),(accounts.enroll_existing,(self.teacher,other_class,'S1')),
                        (accounts.assign_class_teacher,(self.teacher,other_class,self.teacher))]:
            with self.subTest(operation=fn.__name__),self.assertRaises(PermissionError):fn(*args)
        with acl.scope(accounts.get_account(self.teacher),other_class),self.assertRaises(PermissionError):
            db.create_assignment('越权','说明',['要求'],['重点'],'x')

    def test_transfer_class_keeps_records_and_changes_teacher_access(self):
        self.fresh()
        other=accounts.create_teacher(self.admin,'other','王老师',PASSWORD)
        with db.get_connection() as c:c.execute('UPDATE accounts SET must_change=0 WHERE id=?',(other,))
        original=db.get_submission(self.submission)
        accounts.assign_class_teacher(self.admin,self.classroom['id'],other)
        self.assertEqual(db.get_submission(self.submission),original)
        with acl.scope(accounts.get_account(self.teacher)),self.assertRaises(PermissionError):db.get_submission(self.submission)
        with acl.scope(accounts.get_account(other)):self.assertEqual(db.get_submission(self.submission)['id'],self.submission)

    def test_admin_login_is_separate_and_wrong_entry_creates_no_session(self):
        self.fresh()
        with db.get_connection() as c:count=c.execute('SELECT COUNT(*) FROM account_sessions').fetchone()[0]
        with self.assertRaisesRegex(ValueError,'管理员入口'):accounts.login('admin',PASSWORD,expected_role='teacher')
        with db.get_connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM account_sessions').fetchone()[0],count)
        self.assertEqual(accounts.session(accounts.login('admin',PASSWORD,expected_role='admin'))['role'],'admin')
        with self.assertRaisesRegex(ValueError,'教师入口'):accounts.login('teacher',PASSWORD,expected_role='admin')

    def test_schema_upgrade_preserves_ids_hashes_and_class_references_and_is_repeatable(self):
        before=self.legacy()
        with db.get_connection() as c:
            self.assertEqual(tuple(c.execute('SELECT * FROM accounts WHERE id=1').fetchone()),before)
            self.assertEqual(c.execute('SELECT teacher_id FROM classes').fetchone()[0],1)
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
        accounts.init_schema()
        self.assertEqual(accounts.get_account(1)['username'],'old-teacher')

    def test_legacy_split_keeps_teacher_draft_and_history_revokes_old_credentials(self):
        self.legacy()
        original=accounts.get_account(1)
        assignment_drafts.personal_draft_path(db.DB_PATH,1,1).write_text('{"version":1,"data":{"title":"旧草稿"}}',encoding='utf-8')
        draft=assignment_drafts.personal_draft_path(db.DB_PATH,1,1);draft_bytes=draft.read_bytes()
        before=db.list_assignments()
        token=accounts.login('old-teacher',PASSWORD,expected_role='teacher')
        old_connector='old-token'
        with db.get_connection() as c:c.execute('INSERT INTO connector_tokens VALUES(?,?,?)',(accounts._token_hash(old_connector),1,int(time.time())))
        self.assertIsNone(accounts.connector_account(old_connector))
        with self.assertRaises(PermissionError):
            with acl.scope(original,1):pass
        aid=accounts.split_legacy_admin(token,'new-admin','新管理员','Independent-admin-2026')
        self.assertEqual(accounts.get_account(1)['id'],original['id'])
        self.assertFalse(accounts.needs_role_split(accounts.get_account(1)))
        self.assertEqual(accounts.get_account(aid)['role'],'admin')
        self.assertEqual(accounts.list_classes(1)[0]['teacher_id'],1)
        self.assertEqual(db.list_assignments(),before)
        self.assertEqual(draft.read_bytes(),draft_bytes)
        self.assertIsNone(accounts.session(token));self.assertIsNone(accounts.connector_account(old_connector))
        teacher=accounts.session(accounts.login('old-teacher',PASSWORD,expected_role='teacher'))
        with acl.scope(teacher,1):self.assertEqual(len(db.list_assignments()),len(before))
        connector=accounts.connector_token(1)
        self.assertEqual(accounts.connector_account(connector)['id'],1)

    def test_legacy_split_collision_rolls_back_and_student_cannot_split(self):
        self.legacy()
        token=accounts.login('old-teacher',PASSWORD)
        with self.assertRaises(ValueError):accounts.split_legacy_admin(token,'old-teacher','管理员',PASSWORD)
        self.assertIsNotNone(accounts.session(token));self.assertTrue(accounts.needs_role_split(accounts.get_account(1)))
        with db.get_connection() as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM accounts WHERE role='admin'").fetchone()[0],0)
        with self.assertRaises(PermissionError):accounts.split_legacy_admin('invalid','admin','管理员',PASSWORD)
        with db.get_connection() as c:
            c.execute("INSERT INTO accounts(username,display_name,role,student_id,password_hash,must_change,created_at) VALUES('student','测试学生','student','S1',?,0,0)", (accounts._hash(PASSWORD),))
        student_token=accounts.login('student',PASSWORD,expected_role='student')
        with self.assertRaises(PermissionError):
            accounts.split_legacy_admin(student_token,'student-admin','管理员',PASSWORD)

    def test_legacy_upgrade_ui_only_offers_split_and_finishes_without_teacher_id_change(self):
        self.legacy()
        app=AppTest.from_file(str(ROOT/'app.py'))
        app.session_state['auth_token']=accounts.login('old-teacher',PASSWORD)
        app.run(timeout=20)
        self.assertFalse(app.exception)
        self.assertFalse(any(b.label=='教师工作台' for b in app.button))
        values={'独立管理员账号':'new-admin','管理员姓名':'系统管理员','管理员密码':'Independent-admin-2026','确认管理员密码':'Independent-admin-2026'}
        for i in app.text_input:
            if i.label in values:i.set_value(values[i.label])
        next(b for b in app.button if b.label=='建立独立管理员并保留教师账号').click().run(timeout=20)
        self.assertFalse(app.exception)
        self.assertFalse(accounts.needs_role_split(accounts.get_account(1)))
        self.assertTrue(any('账号已分离' in item.value for item in app.success))
        app.run(timeout=20)
        self.assertTrue(any('账号已分离' in item.value for item in app.success))
        self.assertEqual(next(r.value for r in app.radio if r.label=='请选择登录入口'),'教师入口')
        self.assertTrue(any(i.label=='教师账号' for i in app.text_input))

    def test_role_routes_and_settings_sections_are_independent(self):
        teacher=AppTest.from_file(str(ROOT/'app.py'));authenticate(teacher,'教师')
        teacher.session_state['current_page']='班级与学生管理';teacher.run(timeout=20)
        self.assertFalse(teacher.exception)
        self.assertTrue(any(e.label=='LearnBuddy－个人只读连接' for e in teacher.expander))
        self.assertFalse(any(e.label=='教师账号管理' for e in teacher.expander))
        admin=AppTest.from_file(str(ROOT/'app.py'));authenticate(admin,'管理员')
        admin.session_state['current_page']='教师工作台';admin.run(timeout=20)
        self.assertFalse(admin.exception);self.assertEqual(admin.session_state['current_page'],'首页')
        self.assertFalse(any(b.label=='教师工作台' for b in admin.button))
        next(b for b in admin.button if b.label=='账号与系统管理').click().run(timeout=20)
        self.assertFalse(admin.exception)
        self.assertTrue(any(e.label=='教师账号管理' for e in admin.expander))
        self.assertFalse(any(e.label=='LearnBuddy－个人只读连接' for e in admin.expander))
        next(i for i in admin.checkbox if i.label=='显示高级诊断').check().run(timeout=20)
        self.assertFalse(admin.exception);self.assertTrue(any(e.label=='AI评价对照测试' for e in admin.expander))

    def test_focus_markup_preserves_every_original_item_and_escapes_html(self):
        from html.parser import HTMLParser
        source=(ROOT/'app.py').read_text(encoding='utf-8')
        helper=next(node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name=='teaching_focus_html')
        import html,re
        namespace={'html':html,'re':re}
        exec(compile(ast.Module(body=[helper],type_ignores=[]),'focus-helper','exec'),namespace)
        points=['candidate<dist[v]。零权环使用<=会重复入队。','INF=max/4 足够。\r\n第二行','<script>不要执行</script>','普通BFS不适用。']
        original=copy.deepcopy(points)
        class FocusParser(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.actual={};self.number='';self.text='';self.mode=None;self.code_count=0;self.scripts=0
            def handle_starttag(self,tag,attrs):
                classes=dict(attrs).get('class','').split()
                if 'wuma-focus-number' in classes:self.mode='number';self.number=''
                if 'wuma-focus-text' in classes:self.mode='text';self.text=''
                if tag=='code':self.code_count+=1
                if tag=='script':self.scripts+=1
            def handle_data(self,data):
                if self.mode=='number':self.number+=data
                if self.mode=='text':self.text+=data
            def handle_endtag(self,tag):
                if tag=='span' and self.mode=='number':self.mode=None
                if tag=='div' and self.mode=='text':self.actual[int(self.number)]=self.text;self.mode=None
        parser=FocusParser();parser.feed(namespace['teaching_focus_html'](points));actual=parser.actual
        self.assertEqual(actual,{i+1:p for i,p in enumerate(points)})
        self.assertEqual(points,original);self.assertEqual(parser.scripts,0);self.assertGreater(parser.code_count,0)
