"""Latest v1.4 UI with private synthetic workspaces and explicit case playback."""
from pathlib import Path
import json
import re
import secrets
import shutil
import tempfile
import time
import streamlit as st
import accounts
import account_ui
import access_control
import app
import database as db
import demo_mode
import demo_playback
import finals_demo_data

WORKSPACE_ROOT = Path(tempfile.gettempdir()) / 'wuma_interactive_finals_v1'
KEY = '_interactive_workspace'
QUERY_KEY = 'demo'
WORKSPACE_TTL = 24 * 60 * 60
_RESUME_TOKEN = re.compile(r'[A-Za-z0-9_-]{43}\Z')
ROLES = {'学生甲': ('20260001', 'student', 0), '学生乙': ('20260002', 'student', 1),
         '教师': ('demo_teacher', 'teacher', None), '管理员': ('demo_admin', 'admin', None)}

def create_workspace():
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    resume_token = secrets.token_urlsafe(32)
    folder = (WORKSPACE_ROOT / ('visitor_' + resume_token)).resolve()
    folder.mkdir(mode=0o700)
    path = folder / 'synthetic.db'
    password = secrets.token_urlsafe(24)
    with db.database_scope(path):
        demo_mode.prepare_demo_database(path)
        db.init_db()
        accounts.init_schema()
        finals_demo_data.seed_finals_cases()
        encoded = accounts._hash(password)
        now = int(time.time())
        with db.get_connection() as connection:
            rows = [('demo_admin', '演示管理员', 'admin', None, 0, 1),
                    ('demo_teacher', '演示教师', 'teacher', None, 0, 1),
                    ('demo_teacher_2', '另一班教师', 'teacher', None, 0, 1),
                    ('20260001', '案例学生甲', 'student', '20260001', 0, 1),
                    ('20260002', '案例学生乙', 'student', '20260002', 0, 1)]
            rows += [(str(20260000+n), f'演示学生{n:02d}', 'student', str(20260000+n),
                      int(n%4 == 0), int(n%9 != 0)) for n in range(3, 36)]
            for username, name, role, student_id, must_change, active in rows:
                connection.execute('INSERT INTO accounts(username,display_name,role,student_id,password_hash,is_admin,active,must_change,created_at) VALUES(?,?,?,?,?,0,?,?,?)',
                                   (username, name, role, student_id, encoded, active, must_change, now))
            connection.execute("INSERT INTO classes(id,name,teacher_id) VALUES(1,'演示一班',2),(2,'演示二班',3)")
            connection.execute("INSERT INTO enrollments SELECT 1,id FROM accounts WHERE role='student'")
            connection.execute('INSERT INTO assignment_classes SELECT id,1 FROM assignments')
            connection.execute('INSERT INTO legacy_submission_classes SELECT id,1 FROM submissions WHERE assignment_id IS NULL')
            connection.execute("INSERT INTO wuma_demo_metadata VALUES('interactive_workspace','frozen-v1.4-20261011')")
    workspace = {'path': str(path), 'password': password, 'resume_token': resume_token,
                 'expires_at': int(time.time()) + WORKSPACE_TTL}
    persist_workspace(workspace)
    return workspace

def validate_workspace(workspace):
    path = Path(workspace['path']).resolve()
    if path.parent.parent != WORKSPACE_ROOT.resolve() or not path.parent.name.startswith('visitor_'):
        raise ValueError('演示空间路径无效。')
    demo_mode.validate_demo_database(path)
    return path


def persist_workspace(workspace):
    """Keep the synthetic login password on the server, never in the URL."""
    path = validate_workspace(workspace)
    token = workspace.get('resume_token', '')
    if not _RESUME_TOKEN.fullmatch(token) or path.parent.name != 'visitor_' + token:
        raise ValueError('演示空间恢复标识无效。')
    metadata = path.parent / 'workspace.json'
    data = json.dumps(workspace, ensure_ascii=False)
    if metadata.exists() and metadata.read_text(encoding='utf-8') == data:
        return
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.workspace_', suffix='.tmp', delete=False) as stream:
        stream.write(data)
        temporary = Path(stream.name)
    try:
        temporary.replace(metadata)
    finally:
        temporary.unlink(missing_ok=True)


def restore_workspace(resume_token):
    """An unguessable expiring handle can resume only its marked synthetic DB."""
    if not isinstance(resume_token, str) or not _RESUME_TOKEN.fullmatch(resume_token):
        return None
    folder = WORKSPACE_ROOT / ('visitor_' + resume_token)
    try:
        workspace = json.loads((folder / 'workspace.json').read_text(encoding='utf-8'))
        if (workspace.get('resume_token') != resume_token
                or workspace['expires_at'] <= time.time()
                or Path(workspace['path']).resolve() != (folder / 'synthetic.db').resolve()):
            return None
        validate_workspace(workspace)
        if not isinstance(workspace['password'], str):
            return None
        return workspace
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def current_workspace():
    requested = st.query_params.get(QUERY_KEY, '')
    current = st.session_state.get(KEY)
    token = current.get('resume_token', '') if current else ''
    workspace = None
    if token and (not requested or requested == token):
        # Validate the saved handle even for a live session, so reset/expiry is explicit.
        workspace = restore_workspace(token)
        if workspace:
            workspace.update(current)
    if not workspace:
        workspace = restore_workspace(requested)
    if not workspace:
        workspace = create_workspace()
        if requested or current:
            st.session_state['_demo_space_recreated'] = True
    if current and current.get('path') != workspace['path']:
        for key in list(st.session_state):
            if key != '_demo_space_recreated':
                del st.session_state[key]
    st.session_state[KEY] = workspace
    if requested != workspace['resume_token']:
        st.query_params[QUERY_KEY] = workspace['resume_token']
    return workspace

def switch_role(workspace, label):
    username, role, case_index = ROLES[label]
    try:
        token = accounts.login(username, workspace['password'], expected_role=role)
    except ValueError:
        st.warning('这个演示账号已被修改或停用，请恢复初始演示数据后再进入。')
        return
    if st.session_state.get('auth_token'):
        accounts.logout(st.session_state.auth_token)
    for key in list(st.session_state):
        del st.session_state[key]
    st.session_state[KEY] = workspace
    st.session_state['_interactive_case'] = case_index
    st.session_state.auth_token = token
    app.init_state()
    actor = accounts.session(token)
    account_ui.bind(actor)
    if role == 'student':
        classes = accounts.list_classes(actor['id'])
        st.session_state.selected_class_id = classes[0]['id'] if classes else None
        with access_control.scope(actor, st.session_state.selected_class_id):
            selected = next((row for row in db.list_assignments() if row['title'] == demo_playback.fixture()['assignment']['title']), None)
            if selected:
                st.session_state.selected_assignment_id = selected['id']
            records = db.list_submissions(student_id=actor['student_id'])
            target = workspace.get('last_teacher_submission_id')
            completed = next((row for row in records if row['id'] == target and db.get_report(row['id'])), None)
            if completed is None:
                completed = next((row for row in records if db.get_report(row['id'])), None)
            if completed:
                app.load_submission_session(completed['id'])
                if completed['id'] == target:
                    st.session_state.current_page = '学生报告'
    st.rerun()

def reset_workspace(workspace):
    old_folder = validate_workspace(workspace).parent
    new_workspace = create_workspace()
    for key in list(st.session_state):
        del st.session_state[key]
    st.session_state[KEY] = new_workspace
    st.query_params[QUERY_KEY] = new_workspace['resume_token']
    # validate_workspace restricts deletion to this visitor's own temporary folder.
    shutil.rmtree(old_folder)
    st.rerun()

def render_role_picker(workspace):
    st.markdown('''<style>[data-testid="stMainBlockContainer"]{max-width:none;width:min(100%,calc(82% + 4rem));margin-inline:auto}@media(max-width:640px){[data-testid="stMainBlockContainer"]{width:100%}}</style>''', unsafe_allow_html=True)
    app.render_brand_lockup()
    st.title('体验悟码AI')
    st.write('基于最新 v1.4 的交互演示。可以管理班级和实验、填写提交与答辩、保存教师复核意见。')
    st.info('账号和教学数据均为合成数据，每个访客使用独立空间。AI与判题回放固定案例的已保存真实结果；不会评价任意新代码或新回答。')
    for column, label in zip(st.columns(4), ROLES):
        if column.button('体验'+label+'端', width='stretch', type='primary' if label == '学生甲' else 'secondary'):
            switch_role(workspace, label)
    st.caption('学生甲：遗漏后补答；学生乙：持续误解与教师重答要求；教师：发布与复核；管理员：账号和班级归属。刷新后仍可进入本次演示空间。')
    if st.button('恢复初始演示数据', key='demo_reset'):
        reset_workspace(workspace)

def main():
    st.set_page_config(page_title='悟码AI · 交互Demo', page_icon=':material/code:', layout='wide')
    workspace = current_workspace()
    path = validate_workspace(workspace)
    with db.database_scope(path), demo_playback.scope():
        app.init_state()
        app.add_styles()
        st.caption('复核同步版 · 当前演示空间 ' + workspace['resume_token'][:8])
        if st.session_state.pop('_demo_space_recreated', False):
            st.warning('原演示空间已过期或被清理，已建立新的演示空间。此前的临时操作不会出现在新空间中。')
        actor = accounts.session(st.session_state.get('auth_token', ''))
        if not actor:
            render_role_picker(workspace)
            return
        account_ui.bind(actor)
        target = st.session_state.get('teacher_selected_submission_id')
        if actor['role'] == 'teacher' and isinstance(target, int) and not isinstance(target, bool):
            workspace['last_teacher_submission_id'] = target
        persist_workspace(workspace)
        classes = accounts.list_classes(actor['id'])
        if st.session_state.get('selected_class_id') not in {row['id'] for row in classes}:
            st.session_state.selected_class_id = classes[0]['id'] if classes else None
        with st.expander('演示说明与切换身份'):
            st.caption('真实界面与保存流程，私有合成数据；AI评价和判题为固定案例结果回放。修改实验或任意新回答不会获得套用的分数。')
            st.caption('刷新保留当前链接对应的临时空间。教师切到对应学生时，打开教师刚选中的报告；“恢复初始演示数据”会清空本次操作。')
            for column, label in zip(st.columns(4), ROLES):
                if column.button('切换到'+label, key='demo_switch_'+label, width='stretch'):
                    switch_role(workspace, label)
            if st.button('恢复初始演示数据', key='demo_reset'):
                reset_workspace(workspace)
        with access_control.scope(actor, st.session_state.selected_class_id):
            app.render_authenticated_pages(actor)
