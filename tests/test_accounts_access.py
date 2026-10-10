import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import accounts as a
import access_control as acl
import assignment_drafts
import database as db
from auth_helpers import PASSWORD

Q=[{'dimension':'程序逻辑理解','question':'说明算法','reference_points':['循环']}]
class AccountAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.old=db.DB_PATH;db.DB_PATH=Path(self.tmp.name)/'db.sqlite'
        db.init_db();a.init_schema();self.admin=a.bootstrap('setup','setup','admin','管理员',PASSWORD,'初始班',
                                                         teacher_username='first-teacher',teacher_name='初始教师',teacher_password=PASSWORD)
        self.t1=a.create_teacher(self.admin,'t1','李老师',PASSWORD);self.t2=a.create_teacher(self.admin,'t2','王老师',PASSWORD)
        with db.get_connection() as c:c.execute('UPDATE accounts SET must_change=0')
        self.c1=a.create_class(self.admin,'一班',self.t1);self.c2=a.create_class(self.admin,'二班',self.t2)
        a.add_students(self.admin,self.c1,[{'学号':'S1','姓名':'甲同学','初始密码':PASSWORD}])
        a.add_students(self.admin,self.c2,[{'学号':'S2','姓名':'乙同学','初始密码':PASSWORD}])
        with db.get_connection() as c:c.execute('UPDATE accounts SET must_change=0')
        self.p1=a.session(a.login('s1',PASSWORD));self.p2=a.session(a.login('s2',PASSWORD))
        self.tp1=a.get_account(self.t1);self.tp2=a.get_account(self.t2)
        with acl.scope(self.tp1,self.c1):self.as1=db.create_assignment('一班实验','说明',['要求'],['重点'],'int main(){}')
        with acl.scope(self.tp2,self.c2):self.as2=db.create_assignment('二班实验','说明',['要求'],['重点'],'int main(){}')
        with acl.scope(self.p1):self.s1=db.create_submission_with_questions('S1','伪造姓名','错误题名','','int main(){}','2026-10-08',Q,db.get_assignment(self.as1))
        with acl.scope(self.p2):self.s2=db.create_submission_with_questions('S2','乙同学','二班实验','','int main(){}','2026-10-08',Q,db.get_assignment(self.as2))
    def tearDown(self):db.DB_PATH=self.old;self.tmp.cleanup()
    def test_student_queries_and_writes_deny_forged_ids(self):
        with acl.scope(self.p1):
            self.assertEqual([x['id'] for x in db.list_assignments()], [self.as1])
            self.assertEqual([x['id'] for x in db.list_submissions()], [self.s1])
            self.assertEqual(db.get_submission(self.s1)['name'],'甲同学')
            self.assertEqual(db.get_submission(self.s1)['problem'],'一班实验')
            for fn,args in [(db.get_submission,(self.s2,)),(db.get_qa_records,(self.s2,)),(db.get_report,(self.s2,)),(db.get_assignment,(self.as2,)),(db.list_submissions,(None,'S2')),(db.save_initial_result,(self.s2,0,'答案','',{},True)),(db.update_assignment,(self.as1,'篡改','',[],[],''))]:
                with self.subTest(fn=fn.__name__),self.assertRaises(PermissionError):fn(*args)
            with self.assertRaises(PermissionError):db.create_submission_with_questions('S2','乙同学','题','','x','now',Q,db.get_assignment(self.as1))
    def test_teacher_scope_applies_to_lists_single_and_exports(self):
        with acl.scope(self.tp1,self.c1):
            self.assertEqual([x['id'] for x in db.list_submissions()],[self.s1])
            self.assertEqual([x['id'] for x in db.list_assignments()],[self.as1])
            for fn,args in [(db.get_submission,(self.s2,)),(db.save_teacher_review,(self.s2,'伪造','调整理解度结论',80,'')),(db.set_assignment_status,(self.as2,'已归档'))]:
                with self.subTest(fn=fn.__name__),self.assertRaises(PermissionError):fn(*args)
            self.assertTrue(all(t['submission_id']==self.s1 for t in db.list_teacher_tasks()))
    def test_assignment_class_binding_and_forged_snapshot(self):
        with acl.scope(self.tp1,self.c2),self.assertRaises(PermissionError):db.create_assignment('越权','',[],[],'x')
        with acl.scope(self.p1):
            forged={'id':self.as1,'title':'篡改','explanation_required':False,'rubric':[]}
            sid=db.create_submission_with_questions('S1','篡改','篡改','','x','now',Q,forged)
            snapshot=db.get_submission(sid)['assignment_snapshot']
            self.assertEqual(snapshot['title'],'一班实验');self.assertTrue(snapshot['explanation_required'])
    def test_roster_conflict_is_atomic_and_cross_class_attach_requires_admin(self):
        before=a.roster(self.admin,self.c1)
        with self.assertRaises(ValueError):a.add_students(self.t1,self.c1,[{'学号':'S3','姓名':'丙','初始密码':PASSWORD},{'学号':'S2','姓名':'乙','初始密码':PASSWORD}])
        self.assertEqual(a.roster(self.admin,self.c1),before)
        with self.assertRaises(PermissionError):a.add_students(self.t1,self.c2,[{'学号':'S4','姓名':'丁','初始密码':PASSWORD}])
        with self.assertRaises(PermissionError):a.enroll_existing(self.t1,self.c1,'S2')
        a.enroll_existing(self.admin,self.c1,'S2');self.assertEqual(len(a.roster(self.admin,self.c1)),2)
    def test_password_hash_session_expiry_reset_and_lockout(self):
        token=a.login('s1',PASSWORD,now=1000)
        self.assertIsNotNone(a.session(token,now=1001));self.assertIsNone(a.session(token,now=1000+a.SESSION_SECONDS))
        live=a.login('s1',PASSWORD)
        a.reset_password(self.t1,self.p1['id'],'Reset-pass-2026');self.assertIsNone(a.session(live))
        self.assertTrue(a.session(a.login('s1','Reset-pass-2026'))['must_change'])
        for _ in range(5):
            with self.assertRaises(ValueError):a.login('s2','bad',now=2000)
        with self.assertRaisesRegex(ValueError,'15分钟'):a.login('s2',PASSWORD,now=2001)
        self.assertIsNotNone(a.session(a.login('s2',PASSWORD,now=2901),now=2902))
        with db.get_connection() as c:
            text=' '.join(str(tuple(x)) for x in c.execute('SELECT * FROM accounts'))
            audit=' '.join(str(tuple(x)) for x in c.execute('SELECT * FROM account_audit'))
        self.assertNotIn(PASSWORD,text+audit);self.assertIn('pbkdf2_sha256$600000',text)
    def test_connector_rejects_old_config_and_filters_valid_teacher(self):
        from learnbuddy import mcp_server as m
        msg={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'wuma_teacher_overview','arguments':{}}}
        with patch.dict(m.os.environ,{'WUMA_TEACHER_TOKEN':'','WUMA_DEMO_MODE':'false'}):
            result=m.reply(msg)
        self.assertTrue(result['result']['isError'])
        token=a.connector_token(self.t1)
        with patch.dict(m.os.environ,{'WUMA_TEACHER_TOKEN':token,'WUMA_DEMO_MODE':'false'}):
            result=m.reply(msg)
        payload=json.loads(result['result']['content'][0]['text']);self.assertEqual(payload['历史提交数'],1)
        a.set_active(self.admin,self.t1,False);self.assertIsNone(a.connector_account(token))
    def test_context_resets_and_drafts_are_teacher_and_class_isolated(self):
        with acl.scope(self.tp1,self.c1):
            assignment_drafts.save_draft(db.DB_PATH,{'title':'私有草稿'});path=assignment_drafts.draft_path(db.DB_PATH)
        self.assertIsNone(acl.current())
        with acl.scope(self.tp2,self.c2):self.assertIsNone(assignment_drafts.load_draft(db.DB_PATH));self.assertNotEqual(assignment_drafts.draft_path(db.DB_PATH),path)
        try:
            with acl.scope(self.tp1,self.c1):raise RuntimeError('模拟失败')
        except RuntimeError:pass
        self.assertIsNone(acl.current())
    def test_regular_teacher_has_no_advanced_diagnostic(self):
        token=a.login('t1',PASSWORD);app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'))
        app.session_state['auth_token']=token;app.session_state['current_page']='系统设置';app.run(timeout=20)
        self.assertFalse(app.exception);self.assertFalse(any(i.label=='显示高级诊断' for i in app.checkbox))
        self.assertFalse(any(e.label=='AI评价对照测试' for e in app.expander))
    def test_bootstrap_does_not_rewrite_historical_teaching_rows(self):
        with db.get_connection() as c:
            before={t:[tuple(x) for x in c.execute('SELECT * FROM '+t)] for t in ('assignments','submissions','qa_records')}
        a.init_schema()
        with self.assertRaises(ValueError):a.bootstrap('setup','setup','other','其他',PASSWORD,'二班',teacher_username='other-teacher',teacher_name='其他教师',teacher_password=PASSWORD)
        with db.get_connection() as c:
            after={t:[tuple(x) for x in c.execute('SELECT * FROM '+t)] for t in before}
        self.assertEqual(before,after)
