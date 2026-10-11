"""Latest v1.4 UI with private synthetic workspaces and explicit case playback."""
from pathlib import Path
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
ROLES = {'学生甲': ('20260001', 'student', 0), '学生乙': ('20260002', 'student', 1),
         '教师': ('demo_teacher', 'teacher', None), '管理员': ('demo_admin', 'admin', None)}

def create_workspace():
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix='visitor_', dir=WORKSPACE_ROOT)).resolve()
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
    return {'path': str(path), 'password': password}

def validate_workspace(workspace):
    path = Path(workspace['path']).resolve()
    if path.parent.parent != WORKSPACE_ROOT.resolve() or not path.parent.name.startswith('visitor_'):
        raise ValueError('演示空间路径无效。')
    demo_mode.validate_demo_database(path)
    return path

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
            completed = next((row for row in records if db.get_report(row['id'])), None)
            if completed:
                app.load_submission_session(completed['id'])
    st.rerun()

def reset_workspace(workspace):
    old_folder = validate_workspace(workspace).parent
    new_workspace = create_workspace()
    for key in list(st.session_state):
        del st.session_state[key]
    st.session_state[KEY] = new_workspace
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
    st.caption('学生甲：遗漏后补答；学生乙：持续误解与教师重答要求；教师：发布与复核；管理员：账号和班级归属。刷新会开启新的演示空间。')
    if st.button('恢复初始演示数据', key='demo_reset'):
        reset_workspace(workspace)

def main():
    st.set_page_config(page_title='悟码AI · 交互Demo', page_icon=':material/code:', layout='wide')
    workspace = st.session_state.get(KEY)
    if not workspace:
        workspace = create_workspace()
        st.session_state[KEY] = workspace
    path = validate_workspace(workspace)
    with db.database_scope(path), demo_playback.scope():
        app.init_state()
        app.add_styles()
        actor = accounts.session(st.session_state.get('auth_token', ''))
        if not actor:
            render_role_picker(workspace)
            return
        account_ui.bind(actor)
        classes = accounts.list_classes(actor['id'])
        if st.session_state.get('selected_class_id') not in {row['id'] for row in classes}:
            st.session_state.selected_class_id = classes[0]['id'] if classes else None
        with st.expander('演示说明与切换身份'):
            st.caption('真实界面与保存流程，私有合成数据；AI评价和判题为固定案例结果回放。修改实验或任意新回答不会获得套用的分数。')
            for column, label in zip(st.columns(4), ROLES):
                if column.button('切换到'+label, key='demo_switch_'+label, width='stretch'):
                    switch_role(workspace, label)
            if st.button('恢复初始演示数据', key='demo_reset'):
                reset_workspace(workspace)
        with access_control.scope(actor, st.session_state.selected_class_id):
            app.render_authenticated_pages(actor)
