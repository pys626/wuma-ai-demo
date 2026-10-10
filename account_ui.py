"""Personal login and teacher settings; secrets are entered only by the user."""
import json
import html
import sqlite3
import math
import streamlit as st
import accounts
import access_control
import database as db
import browser_session
from ui_uploads import chinese_file_uploader


def login(setup_secret):
    st.title('登录悟码AI')
    if not accounts.enabled():
        st.subheader('首次设置－建立管理员与教师账号')
        st.info('管理员负责账号与系统，教师负责教学。请分别设置两个账号；旧实验、提交及报告保留并归入首个教师班级。')
        if not setup_secret:
            st.warning('尚未配置教师密码，请先配置原 TEACHER_PASSWORD，才能初始化管理员。')
        with st.form('account_setup'):
            old=st.text_input('原教师密码',type='password')
            user=st.text_input('管理员账号')
            name=st.text_input('管理员姓名')
            password=st.text_input('管理员新密码',type='password',help='至少10个字符。')
            confirm=st.text_input('确认管理员新密码',type='password')
            teacher_user=st.text_input('首位教师账号')
            teacher_name=st.text_input('首位教师姓名')
            teacher_password=st.text_input('首位教师密码',type='password',help='至少10个字符。')
            teacher_confirm=st.text_input('确认首位教师密码',type='password')
            classroom=st.text_input('首个班级名称')
            ok=st.form_submit_button('建立管理员并保留旧记录',disabled=not bool(setup_secret))
        if ok:
            try:
                if password!=confirm:raise ValueError('两次新密码不一致。')
                if teacher_password!=teacher_confirm:raise ValueError('两次教师密码不一致。')
                accounts.bootstrap(old,setup_secret,user,name,password,classroom,
                                   teacher_username=teacher_user,teacher_name=teacher_name,teacher_password=teacher_password)
                st.rerun()
            except (ValueError,sqlite3.Error) as e:st.error(str(e))
        return
    if st.session_state.get('_role_split_completed',False):
        st.success('账号已分离。原账号从教师入口登录，新管理员账号从管理员入口登录。')
    entry=st.radio('请选择登录入口',['学生入口','教师入口','管理员入口'],
                   index=1 if st.session_state.get('_role_split_completed',False) else 0,
                   horizontal=True,key='login_entry')
    role={'学生入口':'student','教师入口':'teacher','管理员入口':'admin'}[entry]
    st.subheader(entry)
    if role=='student':
        st.caption('使用本班教师开通的学号和密码登录。首次登录须修改初始密码。')
    elif role=='teacher':
        st.caption('使用个人教师账号登录，处理本人班级的实验、答辩与复核。')
    else:
        st.caption('使用独立管理员账号登录，管理账号、班级归属与系统诊断。')
    with st.form('personal_login_'+role):
        user=st.text_input({'student':'学生学号','teacher':'教师账号','admin':'管理员账号'}[role],key='login_'+role+'_username')
        password=st.text_input('密码',type='password',key='login_'+role+'_password')
        ok=st.form_submit_button({'student':'进入学生端','teacher':'进入教师端','admin':'进入管理端'}[role],type='primary',width='stretch')
    if ok:
        try:
            token=accounts.login(user,password,expected_role=role)
            for key in list(st.session_state):del st.session_state[key]
            st.session_state.auth_token=token
            st.session_state.current_page='首页'
            st.rerun()
        except (ValueError,PermissionError) as e:st.error(str(e))
    if role=='student':
        st.caption('尚无账号或忘记密码，请联系本班教师。')
    elif role=='teacher':
        st.caption('教师账号由管理员开通或重置。升级前的兼任账号请从教师入口登录，完成账号分离。')
    else:
        st.caption('管理员忘记密码，请联系系统维护人员。')


def role_split_form(a):
    st.title('升级设置：分离教师与管理员账号')
    st.info('原账号保留为教师账号，班级、实验、草稿与历史教学记录继续保留。请设置一个独立管理员账号，用于账号与系统管理。')
    st.caption(f"教师姓名：{a['display_name']} · 登录用户名：{a['username']}")
    with st.form('split_legacy_admin'):
        username=st.text_input('独立管理员账号',help='须与原教师账号及已有账号不同。')
        name=st.text_input('管理员姓名')
        password=st.text_input('管理员密码',type='password',help='至少10个字符。')
        confirm=st.text_input('确认管理员密码',type='password')
        ok=st.form_submit_button('建立独立管理员并保留教师账号',type='primary')
    if ok:
        try:
            if password!=confirm:raise ValueError('两次管理员密码不一致。')
            accounts.split_legacy_admin(st.session_state.auth_token,username,name,password)
            browser_session.clear_state()
            st.session_state['_role_split_completed']=True
            st.session_state['login_entry']='教师入口'
            st.rerun()
        except (ValueError,PermissionError,sqlite3.Error) as e:st.error(str(e))


def bind(a):
    st.session_state.auth_role={'teacher':'教师','student':'学生','admin':'管理员'}[a['role']]
    st.session_state.auth_name=a['display_name']
    st.session_state.auth_student_id=a['student_id'] or ''
    st.session_state.form_name=a['display_name']
    st.session_state.form_student_id=a['student_id'] or ''


def password_form(a,required=False):
    if required:
        st.title('首次登录－修改初始密码')
        st.info('修改初始密码后，再进入本账号的工作区。')
    with st.form('personal_password_change'):
        old=st.text_input('当前密码',type='password')
        password=st.text_input('新密码',type='password',help='至少10个字符。')
        confirm=st.text_input('确认新密码',type='password')
        ok=st.form_submit_button('修改密码并重新登录')
    if ok:
        try:
            if password!=confirm:raise ValueError('两次新密码不一致。')
            accounts.change_password(st.session_state.auth_token,old,password)
            browser_session.clear_state()
            st.rerun()
        except (ValueError,PermissionError) as e:st.error(str(e))


def _action(fn,*args,success_message=None):
    try:
        fn(*args)
        messages = {
            'create_class': '班级设置已保存。',
            'add_students': '学生账号已开通并加入该班级。',
            'enroll_existing': '已有学生账号已加入该班级。',
            'reset_password': '该账号的密码已重置，下次登录须修改。',
            'create_teacher': '教师账号已开通。',
            'manage_teacher': '该教师账号的设置已保存。',
            'assign_class_teacher': '班级任课教师已更新，实验和历史记录继续保留。',
        }
        st.session_state.account_settings_flash=success_message or messages.get(fn.__name__, '账号或班级设置已保存。')
        st.rerun()
    except (ValueError,PermissionError,sqlite3.Error) as e:st.error(str(e))


def _roster_uploader():
    return chinese_file_uploader(
        '导入班级名册CSV', file_type='csv', key='roster_csv',
        limit_text='仅支持CSV，每个名册最多200KB、200名学生',
        help='将CSV名册拖到上传框，或点击“选择文件”。支持UTF-8编码；每个文件最多200KB、200名学生。',
    )


def teacher_identity_label(teacher):
    return {'teacher':'教师','admin':'管理员','student':'学生'}[teacher['role']]


def teacher_selection_labels(teachers):
    """普通选择只显示姓名，同名时用登录用户名明确区分；值仍使用账号ID。"""
    counts = {}
    for teacher in teachers:
        name = teacher['display_name']
        counts[name] = counts.get(name, 0) + 1
    return {
        teacher['id']: (f"{teacher['display_name']}（登录用户名：{teacher['username']}）"
                        if counts[teacher['display_name']] > 1 else teacher['display_name'])
        for teacher in teachers
    }


def teacher_accounts_table_html(teachers):
    headers = ['教师姓名', '登录用户名', '角色', '账号状态']
    rows = []
    for teacher in teachers:
        identity = teacher_identity_label(teacher)
        role_kind = 'admin' if teacher['role']=='admin' else 'teacher'
        status = '启用' if teacher['active'] else '停用'
        status_kind = 'active' if teacher['active'] else 'inactive'
        values = [html.escape(str(teacher['display_name'])), html.escape(str(teacher['username'])),
                  f'<span class="wuma-account-tag wuma-account-{role_kind}">{identity}</span>',
                  f'<span class="wuma-account-tag wuma-account-{status_kind}">{status}</span>']
        cells = ''.join(f'<td data-label="{label}">{value}</td>' for label, value in zip(headers, values))
        rows.append(f'<tr>{cells}</tr>')
    return ('<div class="wuma-table-wrapper"><table class="wuma-data-table wuma-evidence-table wuma-account-table">'
            + '<thead><tr>' + ''.join(f'<th>{label}</th>' for label in headers) + '</tr></thead>'
            + '<tbody>' + ''.join(rows) + '</tbody></table></div>')


def filter_students(roster, query='', status='全部'):
    """Filter a permission-scoped roster; combined conditions keep their meaning."""
    query = query.strip().casefold()
    return [student for student in roster
            if (not query or query in str(student['student_id']).casefold()
                or query in student['display_name'].casefold())
            and (status == '全部'
                 or status == '正常' and student['active']
                 or status == '停用' and not student['active']
                 or status == '初始密码待修改' and student['must_change'])]


def clear_student_choice(prefix):
    st.session_state[prefix + '_selected'] = None
    clear_student_password(prefix)


def reset_student_view(prefix):
    st.session_state[prefix + '_page'] = 1
    clear_student_choice(prefix)


def move_student_page(prefix, direction, page_count):
    current = st.session_state.get(prefix + '_page', 1)
    st.session_state[prefix + '_page'] = max(1, min(page_count, current + direction))
    clear_student_choice(prefix)


def clear_student_password(prefix):
    for key in list(st.session_state):
        if key.startswith(prefix + '_password_'):
            del st.session_state[key]


def student_management(a, cid, class_name, roster):
    """Bounded roster and student actions share one search and class context."""
    prefix = f"class_students_{a['id']}_{cid}"
    active = sum(bool(student['active']) for student in roster)
    pending = sum(bool(student['must_change']) for student in roster)
    st.write(f"学生共 **{len(roster)}** 人 · 正常 **{active}** 人 · 停用 **{len(roster)-active}** 人 · 初始密码待修改 **{pending}** 人")
    with st.expander(f'查看与管理班级学生（共{len(roster)}人）', expanded=False):
        st.caption(f'当前班级：{class_name}')
        if not roster:
            st.info('该班级尚无学生，可在下方“开通学生账号”中添加。')
            return
        search_col, status_col = st.columns([3, 2])
        with search_col:
            query = st.text_input('搜索姓名或学号', key=prefix + '_query',
                                  placeholder='输入姓名或学号',
                                  on_change=reset_student_view, args=(prefix,))
        with status_col:
            status = st.selectbox('学生状态', ['全部', '正常', '停用', '初始密码待修改'],
                                  key=prefix + '_status', on_change=reset_student_view, args=(prefix,))
        filtered = filter_students(roster, query, status)
        if not filtered:
            clear_student_choice(prefix)
            st.info('没有符合条件的学生，请调整姓名、学号或状态筛选。')
            return
        size_col, previous_col, page_col, next_col = st.columns([2, 1, 2, 1], vertical_alignment='bottom')
        with size_col:
            page_size = st.selectbox('每页人数', [10, 20, 50], key=prefix + '_size',
                                     on_change=reset_student_view, args=(prefix,))
        page_count = math.ceil(len(filtered) / page_size)
        page_number = st.session_state.get(prefix + '_page', 1)
        if page_number > page_count or page_number < 1:
            page_number = 1
            reset_student_view(prefix)
        with previous_col:
            st.button('上一页', key=prefix + '_previous', disabled=page_number == 1,
                      on_click=move_student_page, args=(prefix, -1, page_count), width='stretch')
        with page_col:
            st.caption(f'找到{len(filtered)}人 · 第{page_number}/{page_count}页')
        with next_col:
            st.button('下一页', key=prefix + '_next', disabled=page_number == page_count,
                      on_click=move_student_page, args=(prefix, 1, page_count), width='stretch')
        start = (page_number - 1) * page_size
        page_students = filtered[start:start + page_size]
        st.dataframe([{'学号':student['student_id'], '姓名':student['display_name'],
                       '账号状态':'正常' if student['active'] else '停用',
                       '初始密码状态':'待修改' if student['must_change'] else '已修改'}
                      for student in page_students], hide_index=True, width='stretch',
                     height=min(430, (len(page_students) + 1) * 35 + 3),
                     column_config={'初始密码状态': st.column_config.TextColumn('初始密码', help='学生是否已修改初始密码。')})
        mapping = {student['id']: f"{student['display_name']} · {student['student_id']}"
                   for student in page_students}
        selection_key = prefix + '_selected'
        if st.session_state.get(selection_key) not in mapping:
            st.session_state[selection_key] = None
        target = st.selectbox('选择本页学生进行账号操作', list(mapping), index=None,
                              format_func=mapping.get, placeholder='选择学生', key=selection_key,
                              on_change=clear_student_password, args=(prefix,))
        if target is not None:
            selected = next(student for student in page_students if student['id'] == target)
            st.write(f"处理学生：**{selected['display_name']}** · 学号：**{selected['student_id']}**")
            st.caption(f'所属班级：{class_name} · 重置后原密码失效，学生下次登录须修改初始密码。')
            with st.form(prefix + f'_reset_{target}'):
                password = st.text_input('新的学生初始密码', type='password',
                                          key=prefix + f'_password_{target}', help='至少10个字符。')
                if st.form_submit_button('重置并要求修改'):
                    _action(accounts.reset_password, a['id'], target, password,
                            success_message=f"已重置{class_name}学生{selected['display_name']}（{selected['student_id']}）的密码，下次登录须修改。")


def settings(a,benchmark):
    a=accounts._actor(a['id'])
    is_admin=a['role']=='admin'
    st.title('账号与系统管理' if is_admin else '班级与学生管理')
    st.subheader('班级管理')
    if st.session_state.get('account_settings_flash'):
        st.success(st.session_state.pop('account_settings_flash'))
    if is_admin and accounts.migration_warning(a['id']):
        st.warning('升级前的实验草稿格式损坏，未自动载入；原文件仍保留，请核查草稿备份。')
    classes=accounts.list_classes(a['id']);cid=st.session_state.get('selected_class_id')
    st.caption('管理员维护账号和班级归属，教学复核由任课教师完成。' if is_admin else '教师管理本人班级的学生和教学；跨班级账号问题请联系管理员。')
    with st.expander('创建班级'):
        with st.form('create_class'):
            name=st.text_input('班级名称')
            tid=a['id']
            if is_admin:
                teachers=accounts.list_teachers(a['id']);mapping=teacher_selection_labels([x for x in teachers if x['active'] and not accounts.needs_role_split(x)])
                tid=st.selectbox('任课教师',list(mapping),format_func=mapping.get) if mapping else None
                if not mapping:st.caption('请先在教师账号管理中开通教师，再创建班级。')
            if st.form_submit_button('保存班级',disabled=tid is None):_action(accounts.create_class,a['id'],name,tid)
    if cid:
        class_name=next((x['name'] for x in classes if x['id']==cid),'')
        st.write('当前班级：'+class_name)
        if is_admin:
            with st.expander('调整班级任课教师'):
                with st.form('assign_class_teacher'):
                    teachers=accounts.list_teachers(a['id']);mapping=teacher_selection_labels([x for x in teachers if x['active'] and not accounts.needs_role_split(x)])
                    owner=next((x['teacher_id'] for x in classes if x['id']==cid),None)
                    ids=list(mapping)
                    target=st.selectbox('新的任课教师',ids,index=ids.index(owner) if owner in ids else 0,format_func=mapping.get) if ids else None
                    st.caption('更换任课教师后，班级、实验和历史提交继续保留，由新教师管理。')
                    if st.form_submit_button('保存任课教师',disabled=target is None):_action(accounts.assign_class_teacher,a['id'],cid,target)
        roster=accounts.roster(a['id'],cid)
        student_management(a,cid,class_name,roster)
        with st.expander('开通学生账号'):
            st.caption('将加入班级：'+class_name)
            with st.form('add_student'):
                sid=st.text_input('学生学号');name=st.text_input('学生姓名');password=st.text_input('学生初始密码',type='password',help='至少10个字符；由教师私下交给对应学生。首次登录必须修改。')
                if st.form_submit_button('开通学生'):_action(accounts.add_students,a['id'],cid,[{'学号':sid,'姓名':name,'初始密码':password}])
            st.caption('批量 CSV 表头：学号,姓名,初始密码。每批最多200名；若有重复或冲突，全批不写入。请妥善保管含初始密码的名册。')
            upload=_roster_uploader()
            oversized=upload is not None and upload.size>200000
            if oversized:
                st.error('名册文件过大，请选择不超过200KB的CSV文件。')
            if st.button('核实名册后导入',disabled=upload is None or oversized):
                try:
                    rows=accounts.parse_roster(upload.getvalue().decode('utf-8-sig'))
                    _action(accounts.add_students,a['id'],cid,rows)
                except (ValueError,UnicodeError) as e:st.error(str(e))
            if is_admin:
                with st.form('enroll_existing'):
                    st.caption('将系统中已有的学生账号加入当前班级，沿用原账号、密码和历史记录。操作前，请管理员确认学号对应的学生身份。')
                    sid=st.text_input('已有学生的学号')
                    if st.form_submit_button('将已有学生加入当前班级'):_action(accounts.enroll_existing,a['id'],cid,sid)
    if is_admin:
        with st.expander('教师账号管理'):
            teachers=accounts.list_teachers(a['id'])
            st.caption('下表仅列教师账号。管理员使用独立账号，不作为任课教师；账号设置通过下方表单保存。')
            st.markdown(teacher_accounts_table_html(teachers), unsafe_allow_html=True)
            with st.form('create_teacher'):
                user=st.text_input('新教师账号');name=st.text_input('新教师姓名');password=st.text_input('教师初始密码',type='password')
                if st.form_submit_button('开通教师'):_action(accounts.create_teacher,a['id'],user,name,password)
            others=[t for t in teachers if t['id']!=a['id']]
            if others:
                with st.form('manage_teacher'):
                    mapping=teacher_selection_labels(others)
                    target=st.selectbox('管理教师',list(mapping),format_func=mapping.get)
                    selected_teacher=next(t for t in others if t['id']==target)
                    st.caption(f"教师姓名：{selected_teacher['display_name']} · 登录用户名：{selected_teacher['username']} · 角色：{teacher_identity_label(selected_teacher)}")
                    active=st.checkbox('启用该教师',value=bool(selected_teacher['active']))
                    password=st.text_input('重置教师密码（留空时仅设置启用状态）',type='password')
                    if st.form_submit_button('保存教师状态'):
                        _action(accounts.manage_teacher,a['id'],target,active,password)
    with st.expander('我的账号', expanded=False):
        st.write('姓名：'+a['display_name'])
        st.write('登录用户名：'+a['username'])
        st.write('角色：'+teacher_identity_label(a))
        st.caption('修改本人密码后，需要重新登录。')
        password_form(a)
    if not is_admin:
        with st.expander('LearnBuddy－个人只读连接'):
            st.caption('连接仅能读取本教师账号负责的班级。生成新配置会撤销旧连接令牌；请勿向他人分享配置文件。')
            if st.button('生成个人连接配置'):
                import launch
                token=accounts.connector_token(a['id'])
                config=launch.connector_config(db.DB_PATH)
                server=next(iter(config['mcpServers'].values()))
                server['env']['WUMA_TEACHER_TOKEN']=token
                st.download_button('下载个人连接配置',json.dumps(config,ensure_ascii=False,indent=2),file_name='wuma_personal_mcp.json',mime='application/json')
    if is_admin:
        st.subheader('高级诊断')
        st.caption('用于系统维护和模型更新后的核验，不属于日常批改步骤。真实模型对照会使用模型额度。')
        if st.checkbox('显示高级诊断',value=False,key='show_advanced_diagnostics'):
            benchmark()
