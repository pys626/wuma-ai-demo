"""Personal accounts and class roster; all additions leave historical teaching rows intact."""
import csv
import hashlib
import hmac
import io
import re
import secrets
import time
import unicodedata
from pathlib import Path
from contextlib import closing
import database as db

ITERATIONS = 600000
SESSION_SECONDS = 8 * 3600


def init_schema():
    with db.get_connection() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('teacher','student','admin')),
            student_id TEXT UNIQUE, password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            must_change INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS classes (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, teacher_id INTEGER NOT NULL REFERENCES accounts(id));
        CREATE TABLE IF NOT EXISTS enrollments (
            class_id INTEGER REFERENCES classes(id), account_id INTEGER REFERENCES accounts(id),
            PRIMARY KEY(class_id, account_id));
        CREATE TABLE IF NOT EXISTS assignment_classes (
            assignment_id INTEGER PRIMARY KEY REFERENCES assignments(id), class_id INTEGER REFERENCES classes(id));
        CREATE TABLE IF NOT EXISTS legacy_submission_classes (
            submission_id INTEGER PRIMARY KEY REFERENCES submissions(id), class_id INTEGER REFERENCES classes(id));
        CREATE TABLE IF NOT EXISTS account_sessions (
            token_hash TEXT PRIMARY KEY, account_id INTEGER REFERENCES accounts(id), expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS login_limits (
            login_hash TEXT PRIMARY KEY, failures INTEGER NOT NULL, locked_until INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS connector_tokens (
            token_hash TEXT PRIMARY KEY, account_id INTEGER REFERENCES accounts(id), created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS account_audit (
            id INTEGER PRIMARY KEY, actor_id INTEGER, action TEXT NOT NULL, resource TEXT NOT NULL,
            created_at INTEGER NOT NULL);
        """)
        _upgrade_role_schema(c)


def _upgrade_role_schema(connection):
    """Expand the old role constraint atomically; retain all IDs and account data.

    is_admin is retained only as a marker for legacy combined accounts. New
    authorization uses role exclusively; the owner completes the split in UI.
    """
    schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='accounts'").fetchone()[0]
    if "'admin'" in schema:
        return
    connection.commit()
    backup_path=Path(db.current_database_path()).resolve().with_suffix('.before_role_separation.sqlite3')
    if not backup_path.exists():
        # A database-native snapshot keeps password hashes and references intact.
        # The file is local recovery material and must not be exported or logged.
        with closing(db.sqlite3.connect(backup_path)) as snapshot:
            connection.backup(snapshot)
    connection.execute('PRAGMA foreign_keys=OFF')
    try:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute("""CREATE TABLE accounts_role_upgrade (
            id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('teacher','student','admin')),
            student_id TEXT UNIQUE, password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
            must_change INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL)""")
        connection.execute('INSERT INTO accounts_role_upgrade SELECT * FROM accounts')
        connection.execute('DROP TABLE accounts')
        connection.execute('ALTER TABLE accounts_role_upgrade RENAME TO accounts')
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('账号结构升级未通过关联检查，已撤销升级。')
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute('PRAGMA foreign_keys=ON')


def needs_role_split(account):
    return bool(account and account['role'] == 'teacher' and account['is_admin'])


def enabled():
    with db.get_connection() as c:
        present=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='accounts'").fetchone()
        return bool(present and c.execute('SELECT 1 FROM accounts LIMIT 1').fetchone())


def _username(value):
    value=unicodedata.normalize('NFKC',str(value)).strip().casefold()
    if not re.fullmatch(r'[\w.@-]{1,50}', value):
        raise ValueError('账号须为1至50个字母、数字、汉字或 . @ - _。')
    return value


def _text(value, label):
    value=str(value).strip()
    if not 1 <= len(value) <= 50: raise ValueError(label+'须为1至50个字符。')
    return value


def _password(value):
    if not isinstance(value,str) or not 10 <= len(value) <= 256:
        raise ValueError('密码须为10至256个字符。')
    return value


def _hash(value):
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac('sha256',_password(value).encode(),salt,ITERATIONS)
    return f'pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}'


def _verify(value, encoded):
    try:
        _,iterations,salt,digest=encoded.split('$')
        actual=hashlib.pbkdf2_hmac('sha256',str(value).encode(),bytes.fromhex(salt),int(iterations))
        return hmac.compare_digest(actual.hex(),digest)
    except (ValueError,TypeError): return False


def _token_hash(token): return hashlib.sha256(str(token or '').encode()).hexdigest()


def _public(row):
    if row is None:return None
    return {k:row[k] for k in ('id','username','display_name','role','student_id','is_admin','active','must_change')}


def get_account(account_id):
    with db.get_connection() as c:return _public(c.execute('SELECT * FROM accounts WHERE id=?',(account_id,)).fetchone())


def _actor(account_id, admin=False):
    from access_control import current
    p=current()
    if p is not None and int(p['id'])!=int(account_id):raise PermissionError('账号身份不一致。')
    a=get_account(account_id)
    roles = {'admin'} if admin else {'teacher', 'admin'}
    if not a or not a['active'] or a['must_change'] or a['role'] not in roles:
        raise PermissionError('需要有效的'+('管理员' if admin else '教师或管理员')+'账号。')
    if needs_role_split(a):raise PermissionError('请先完成教师与管理员账号分离。')
    return a


def _audit(c, actor, action, resource):
    c.execute('INSERT INTO account_audit(actor_id,action,resource,created_at) VALUES(?,?,?,?)',
              (actor,action,str(resource),int(time.time())))


def bootstrap(setup_password, expected_setup_password, username, name, password, class_name,
              *, teacher_username, teacher_name, teacher_password):
    if not expected_setup_password or not hmac.compare_digest(str(setup_password),str(expected_setup_password)):
        raise ValueError('原教师密码不正确，无法初始化管理员。')
    username=_username(username);name=_text(name,'姓名');class_name=_text(class_name,'班级名称');encoded=_hash(password)
    teacher_username=_username(teacher_username);teacher_name=_text(teacher_name,'教师姓名');teacher_encoded=_hash(teacher_password)
    if teacher_username==username:raise ValueError('管理员和教师须使用不同登录账号。')
    init_schema()
    with db.get_connection() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM accounts LIMIT 1').fetchone():raise ValueError('已完成初始化，请用个人账号登录。')
        aid=c.execute("INSERT INTO accounts(username,display_name,role,password_hash,must_change,created_at) VALUES(?,?,'admin',?,0,?)",
                      (username,name,encoded,int(time.time()))).lastrowid
        teacher_id=c.execute("INSERT INTO accounts(username,display_name,role,password_hash,must_change,created_at) VALUES(?,?,'teacher',?,0,?)",
                             (teacher_username,teacher_name,teacher_encoded,int(time.time()))).lastrowid
        cid=c.execute('INSERT INTO classes(name,teacher_id) VALUES(?,?)',(class_name,teacher_id)).lastrowid
        c.execute('INSERT INTO assignment_classes SELECT id,? FROM assignments',(cid,))
        c.execute('INSERT INTO legacy_submission_classes SELECT id,? FROM submissions WHERE assignment_id IS NULL',(cid,))
        _audit(c,aid,'初始化独立管理员与任课教师',cid)
        # The explicitly configured teacher owns the former single-teacher draft.
        import assignment_drafts
        from local_store import write_json
        try:
            old_draft=assignment_drafts.load_draft(db.current_database_path())
        except assignment_drafts.DraftError:
            old_draft=None
            _audit(c,aid,'旧草稿无法迁移：原文件保留',cid)
        if old_draft:
            destination=assignment_drafts.personal_draft_path(db.current_database_path(),teacher_id,cid)
            if not destination.exists():write_json(destination,old_draft)
    return aid


def split_legacy_admin(token, username, name, password):
    """Keep the old teacher identity/ownership; create an independent admin.

    Credentials are supplied by the authenticated owner, never generated or
    copied from the teacher. Account changes and revocation are one transaction.
    """
    owner=session(token)
    if not owner or owner['must_change'] or not needs_role_split(owner):
        raise PermissionError('请使用待分离的原账号登录。')
    username=_username(username);name=_text(name,'管理员姓名');encoded=_hash(password)
    with db.get_connection() as c:
        c.execute('BEGIN IMMEDIATE')
        current_owner=c.execute('SELECT * FROM accounts WHERE id=?',(owner['id'],)).fetchone()
        valid_session=c.execute('SELECT 1 FROM account_sessions WHERE token_hash=? AND account_id=? AND expires>?',
                                (_token_hash(token),owner['id'],int(time.time()))).fetchone()
        if not valid_session or not current_owner['active'] or not needs_role_split(current_owner):
            raise PermissionError('账号状态已变化，请重新登录。')
        if c.execute('SELECT 1 FROM accounts WHERE username=?',(username,)).fetchone():
            raise ValueError('管理员账号已存在，请填写新的独立账号。')
        admin_id=c.execute("INSERT INTO accounts(username,display_name,role,password_hash,must_change,created_at) VALUES(?,?,'admin',?,0,?)",
                           (username,name,encoded,int(time.time()))).lastrowid
        c.execute('UPDATE accounts SET is_admin=0 WHERE id=?',(owner['id'],))
        c.execute('DELETE FROM account_sessions WHERE account_id=?',(owner['id'],))
        c.execute('DELETE FROM connector_tokens WHERE account_id=?',(owner['id'],))
        _audit(c,owner['id'],'分离独立管理员账号',admin_id)
    return admin_id


def login(username,password,now=None,*,expected_role=None):
    if expected_role not in (None,'teacher','student','admin'):
        raise ValueError('不支持的登录入口。')
    now=int(time.time() if now is None else now)
    try:username=_username(username)
    except ValueError:raise ValueError('账号或密码不正确。')
    key=_token_hash(username)
    with db.get_connection() as c:
        lock=c.execute('SELECT * FROM login_limits WHERE login_hash=?',(key,)).fetchone()
        if lock and lock['locked_until']>now:raise ValueError('尝试次数过多，请15分钟后再试。')
        row=c.execute('SELECT * FROM accounts WHERE username=?',(username,)).fetchone()
        # Equal-cost unknown-user check; no plaintext password or key is persisted.
        encoded=row['password_hash'] if row else f'pbkdf2_sha256${ITERATIONS}$'+('00'*16)+'$'+('00'*32)
        valid=_verify(password,encoded) and row is not None and row['active']
        if not valid:
            failures=(lock['failures'] if lock and lock['locked_until']==0 else 0)+1
            until=now+900 if failures>=5 else 0
            c.execute('INSERT OR REPLACE INTO login_limits VALUES(?,?,?)',(key,failures,until))
        else:
            c.execute('DELETE FROM login_limits WHERE login_hash=?',(key,))
            if expected_role is not None and row['role']!=expected_role:
                _audit(c,row['id'],'登录入口不匹配',expected_role)
            else:
                c.execute('DELETE FROM account_sessions WHERE expires<=?',(now,))
                token=secrets.token_urlsafe(32)
                c.execute('INSERT INTO account_sessions VALUES(?,?,?)',(_token_hash(token),row['id'],now+SESSION_SECONDS))
                _audit(c,row['id'],'登录','会话')
    if not valid:raise ValueError('账号或密码不正确。')
    if expected_role is not None and row['role']!=expected_role:
        target={'teacher':'教师入口','student':'学生入口','admin':'管理员入口'}[row['role']]
        raise ValueError('账号与当前入口不匹配，请切换到'+target+'。')
    return token


def session(token,now=None):
    with db.get_connection() as c:
        row=c.execute('SELECT a.* FROM account_sessions s JOIN accounts a ON a.id=s.account_id WHERE s.token_hash=? AND s.expires>? AND a.active=1',
                      (_token_hash(token),int(time.time() if now is None else now))).fetchone()
    return _public(row)


def logout(token):
    with db.get_connection() as c:c.execute('DELETE FROM account_sessions WHERE token_hash=?',(_token_hash(token),))


def change_password(token,old_password,new_password):
    a=session(token)
    if not a:raise PermissionError('登录已失效。')
    with db.get_connection() as c:
        row=c.execute('SELECT password_hash FROM accounts WHERE id=?',(a['id'],)).fetchone()
        if not _verify(old_password,row['password_hash']):raise ValueError('当前密码不正确。')
        encoded=_hash(new_password)
        if _verify(new_password,row['password_hash']):raise ValueError('新密码应与当前密码不同。')
        c.execute('UPDATE accounts SET password_hash=?,must_change=0 WHERE id=?',(encoded,a['id']))
        c.execute('DELETE FROM account_sessions WHERE account_id=?',(a['id'],))
        c.execute('DELETE FROM connector_tokens WHERE account_id=?',(a['id'],))
        _audit(c,a['id'],'修改密码',a['id'])


def list_classes(actor_id):
    from access_control import current
    principal=current()
    if principal is not None and int(principal['id'])!=int(actor_id):raise PermissionError('账号身份不一致。')
    a=get_account(actor_id)
    if not a or not a['active']:return []
    with db.get_connection() as c:
        if a['role']=='student':
            rows=c.execute('SELECT c.* FROM classes c JOIN enrollments e ON e.class_id=c.id WHERE e.account_id=?',(actor_id,)).fetchall()
        elif a['role']=='admin':rows=c.execute('SELECT * FROM classes ORDER BY id').fetchall()
        else:rows=c.execute('SELECT * FROM classes WHERE teacher_id=? ORDER BY id',(actor_id,)).fetchall()
    return [dict(r) for r in rows]


def require_class(actor_id,class_id):
    _actor(actor_id)
    if not any(r['id']==int(class_id) for r in list_classes(actor_id)):raise PermissionError('无法管理该班级。')


def create_class(actor_id,name,teacher_id=None):
    a=_actor(actor_id)
    if a['role']=='admin' and teacher_id is None:raise ValueError('请为班级指定任课教师。')
    teacher_id=teacher_id or actor_id
    if teacher_id!=actor_id and a['role']!='admin':raise PermissionError('仅管理员可以指定其他教师。')
    target=get_account(teacher_id)
    if not target or target['role']!='teacher' or not target['active'] or needs_role_split(target):raise ValueError('任课教师无效。')
    with db.get_connection() as c:
        cid=c.execute('INSERT INTO classes(name,teacher_id) VALUES(?,?)',(_text(name,'班级名称'),teacher_id)).lastrowid
        _audit(c,actor_id,'创建班级',cid)
    return cid


def assign_class_teacher(actor_id,class_id,teacher_id):
    _actor(actor_id,admin=True);require_class(actor_id,class_id)
    target=get_account(teacher_id)
    if not target or not target['active'] or target['role']!='teacher' or needs_role_split(target):
        raise ValueError('请选择有效的任课教师。')
    with db.get_connection() as c:
        c.execute('UPDATE classes SET teacher_id=? WHERE id=?',(teacher_id,class_id))
        _audit(c,actor_id,'调整任课教师',class_id)


def create_teacher(actor_id,username,name,password):
    _actor(actor_id,admin=True)
    encoded=_hash(password)
    with db.get_connection() as c:
        try:aid=c.execute("INSERT INTO accounts(username,display_name,role,password_hash,created_at) VALUES(?,?,'teacher',?,?)",
                          (_username(username),_text(name,'姓名'),encoded,int(time.time()))).lastrowid
        except db.sqlite3.IntegrityError:raise ValueError('账号已存在。')
        _audit(c,actor_id,'创建教师',aid)
    return aid


def list_teachers(actor_id):
    _actor(actor_id,admin=True)
    with db.get_connection() as c:return [_public(r) for r in c.execute("SELECT * FROM accounts WHERE role='teacher'").fetchall()]


def parse_roster(text):
    if len(text.encode('utf-8'))>200000:raise ValueError('名册文件过大（最多200KB）。')
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if reader.fieldnames!=['学号','姓名','初始密码']:raise ValueError('CSV表头须为：学号,姓名,初始密码。')
    rows=list(reader)
    if not 1<=len(rows)<=200:raise ValueError('每批名册须为1至200名学生。')
    return rows


def add_students(actor_id,class_id,rows):
    require_class(actor_id,class_id)
    if not 1<=len(rows)<=200:raise ValueError('每批须为1至200名学生。')
    prepared=[];seen=set()
    for row in rows:
        sid=_text(row['学号'],'学号');username=_username(sid);name=_text(row['姓名'],'姓名')
        if username in seen:raise ValueError('名册含重复学号。')
        seen.add(username);prepared.append((sid,username,name,_hash(row['初始密码'])))
    with db.get_connection() as c:
        c.execute('BEGIN IMMEDIATE')
        for sid,username,name,encoded in prepared:
            # Never silently attach or overwrite another person's existing identity.
            if c.execute('SELECT 1 FROM accounts WHERE username=? OR student_id=?',(username,sid)).fetchone():
                raise ValueError('学号或账号已存在，请管理员核对身份后使用“将已有学生加入当前班级”。')
            aid=c.execute("INSERT INTO accounts(username,display_name,role,student_id,password_hash,created_at) VALUES(?,?,'student',?,?,?)",
                          (username,name,sid,encoded,int(time.time()))).lastrowid
            c.execute('INSERT INTO enrollments VALUES(?,?)',(class_id,aid))
        _audit(c,actor_id,'导入学生名册',class_id)


def enroll_existing(actor_id,class_id,student_id):
    _actor(actor_id,admin=True);require_class(actor_id,class_id)
    with db.get_connection() as c:
        row=c.execute("SELECT id FROM accounts WHERE student_id=? AND role='student' AND active=1",(_text(student_id,'学号'),)).fetchone()
        if not row:raise ValueError('未找到已确认身份的学生账号。')
        c.execute('INSERT OR IGNORE INTO enrollments VALUES(?,?)',(class_id,row['id']))
        _audit(c,actor_id,'加入已有学生',row['id'])


def roster(actor_id,class_id):
    require_class(actor_id,class_id)
    with db.get_connection() as c:
        return [_public(r) for r in c.execute('SELECT a.* FROM enrollments e JOIN accounts a ON a.id=e.account_id WHERE e.class_id=? ORDER BY a.student_id',(class_id,)).fetchall()]


def reset_password(actor_id,target_id,password):
    a=_actor(actor_id);target=get_account(target_id)
    if not target:raise ValueError('账号不存在。')
    if a['role']!='admin':
        if target['role']!='student':raise PermissionError('只能重置本人班级学生密码。')
        own={x['id'] for x in list_classes(actor_id)}
        with db.get_connection() as c:members={r[0] for r in c.execute('SELECT class_id FROM enrollments WHERE account_id=?',(target_id,))}
        if not members or not members.issubset(own):raise PermissionError('该学生涉及其他教师班级，请由管理员重置密码。')
    encoded=_hash(password)
    with db.get_connection() as c:
        c.execute('UPDATE accounts SET password_hash=?,must_change=1 WHERE id=?',(encoded,target_id))
        c.execute('DELETE FROM account_sessions WHERE account_id=?',(target_id,))
        c.execute('DELETE FROM connector_tokens WHERE account_id=?',(target_id,))
        _audit(c,actor_id,'重置密码',target_id)


def set_active(actor_id,target_id,active):
    _actor(actor_id,admin=True)
    if int(target_id)==int(actor_id):raise ValueError('不能停用当前管理员。')
    with db.get_connection() as c:
        c.execute('UPDATE accounts SET active=? WHERE id=?',(int(bool(active)),target_id))
        c.execute('DELETE FROM account_sessions WHERE account_id=?',(target_id,))
        c.execute('DELETE FROM connector_tokens WHERE account_id=?',(target_id,))
        _audit(c,actor_id,'启停账号',target_id)


def connector_token(actor_id):
    actor=_actor(actor_id)
    if actor['role']!='teacher':raise PermissionError('LearnBuddy教学连接仅供教师账号使用。')
    token=secrets.token_urlsafe(32)
    with db.get_connection() as c:
        c.execute('DELETE FROM connector_tokens WHERE account_id=?',(actor_id,))
        c.execute('INSERT INTO connector_tokens VALUES(?,?,?)',(_token_hash(token),actor_id,int(time.time())))
        _audit(c,actor_id,'更新只读连接令牌',actor_id)
    return token


def connector_account(token):
    with db.get_connection() as c:
        return _public(c.execute("SELECT a.* FROM connector_tokens t JOIN accounts a ON a.id=t.account_id WHERE t.token_hash=? AND a.active=1 AND a.role='teacher' AND a.is_admin=0 AND a.must_change=0",(_token_hash(token),)).fetchone())


def manage_teacher(actor_id,target_id,active,password=''):
    _actor(actor_id,admin=True)
    if int(target_id)==int(actor_id):raise ValueError('不能在此修改当前管理员。')
    target=get_account(target_id)
    if not target or target['role']!='teacher':raise ValueError('教师账号不存在。')
    encoded=_hash(password) if password else None
    with db.get_connection() as c:
        c.execute('UPDATE accounts SET active=? WHERE id=?',(int(bool(active)),target_id))
        if encoded:c.execute('UPDATE accounts SET password_hash=?,must_change=1 WHERE id=?',(encoded,target_id))
        c.execute('DELETE FROM account_sessions WHERE account_id=?',(target_id,))
        c.execute('DELETE FROM connector_tokens WHERE account_id=?',(target_id,))
        _audit(c,actor_id,'管理教师账号',target_id)


def migration_warning(actor_id):
    _actor(actor_id,admin=True)
    with db.get_connection() as c:
        return bool(c.execute("SELECT 1 FROM account_audit WHERE actor_id=? AND action='旧草稿无法迁移：原文件保留'",(actor_id,)).fetchone())
