"""Test-only fixture: create real sessions rather than bypass production login."""
import accounts
import database as db
PASSWORD='Test-account-pass-2026'

def authenticate(app,role='学生',student_id='20260001',name='张同学'):
    accounts.init_schema()
    if not accounts.enabled():accounts.bootstrap('fixture-setup','fixture-setup','fixture-admin','管理员',PASSWORD,'测试班级',
                                               teacher_username='fixture-teacher',teacher_name='任课教师',teacher_password=PASSWORD)
    with db.get_connection() as c:
        admin=c.execute("SELECT * FROM accounts WHERE role='admin'").fetchone()
        classroom=c.execute('SELECT * FROM classes ORDER BY id LIMIT 1').fetchone()
        cid=classroom['id']
        teacher=c.execute('SELECT * FROM accounts WHERE id=?',(classroom['teacher_id'],)).fetchone()
        c.execute('INSERT OR IGNORE INTO assignment_classes SELECT id,? FROM assignments',(cid,))
        c.execute('INSERT OR IGNORE INTO legacy_submission_classes SELECT id,? FROM submissions WHERE assignment_id IS NULL',(cid,))
        row=c.execute('SELECT id FROM accounts WHERE student_id=?',(student_id,)).fetchone()
    if role=='学生':
        if not row:accounts.add_students(admin['id'],cid,[{'学号':student_id,'姓名':name,'初始密码':PASSWORD}])
        with db.get_connection() as c:c.execute('UPDATE accounts SET must_change=0 WHERE student_id=?',(student_id,))
        username=student_id
    elif role=='管理员':username=admin['username']
    else:username=teacher['username']
    app.session_state['auth_token']=accounts.login(username,PASSWORD)
    app.session_state['selected_class_id']=cid
    return app


def load_teacher_draft():
    import assignment_drafts
    import access_control
    if not accounts.enabled():return assignment_drafts.load_draft(db.DB_PATH)
    with db.get_connection() as c:
        classroom=c.execute('SELECT * FROM classes ORDER BY id LIMIT 1').fetchone()
        aid=classroom['teacher_id'];cid=classroom['id']
    with access_control.scope(accounts.get_account(aid),cid):return assignment_drafts.load_draft(db.DB_PATH)
