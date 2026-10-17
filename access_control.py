"""Request-bound authorization for every teaching database entry point.
None is reserved for trusted local migration/test scripts, never a logged-in web route.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import database as db

_principal=ContextVar('wuma_principal',default=None)


def current(): return _principal.get()


@contextmanager
def scope(account,class_id=None):
    from accounts import get_account, needs_role_split
    canonical=get_account(account['id']) if account else None
    if not canonical or not canonical['active'] or canonical['must_change']:
        raise PermissionError('请用有效的个人账号登录并修改初始密码。')
    if needs_role_split(canonical):raise PermissionError('请先完成教师与管理员账号分离。')
    canonical['class_id']=class_id
    token=_principal.set(canonical)
    try:yield canonical
    finally:_principal.reset(token)


def _teacher(p):
    if p['role']!='teacher':raise PermissionError('该操作需要教师身份。')


def _student(p):
    if p['role']!='student':raise PermissionError('该操作需要学生身份。')


def _fresh(p):
    from accounts import get_account
    a=get_account(p['id'])
    if not a or not a['active'] or a['must_change'] or a['role']!=p['role'] or a['is_admin']:
        raise PermissionError('账号已失效，请重新登录。')


def _class_allowed(p,cid):
    if cid is None:return False
    if p['role']=='teacher' and p.get('class_id') is not None and int(cid)!=int(p['class_id']):
        return False
    with db.get_connection() as c:
        if p['role']=='teacher':
            return bool(c.execute('SELECT 1 FROM classes WHERE id=? AND teacher_id=?',(cid,p['id'])).fetchone())
        if p['role']!='student':return False
        return bool(c.execute('SELECT 1 FROM enrollments WHERE class_id=? AND account_id=?',(cid,p['id'])).fetchone())


def assignment_allowed(p,aid):
    with db.get_connection() as c:
        row=c.execute('SELECT ac.class_id,a.status FROM assignments a LEFT JOIN assignment_classes ac ON ac.assignment_id=a.id WHERE a.id=?',(aid,)).fetchone()
    return bool(row and _class_allowed(p,row['class_id']) and (p['role']=='teacher' or row['status']=='已发布'))


def submission_allowed(p,sid):
    with db.get_connection() as c:
        row=c.execute('SELECT s.student_id,COALESCE(ac.class_id,lc.class_id) class_id FROM submissions s LEFT JOIN assignment_classes ac ON ac.assignment_id=s.assignment_id LEFT JOIN legacy_submission_classes lc ON lc.submission_id=s.id WHERE s.id=?',(sid,)).fetchone()
    if not row:return False
    if p['role']=='student':return row['student_id']==p['student_id']
    return _class_allowed(p,row['class_id'])


def _assert_assignment(p,aid):
    if not assignment_allowed(p,aid):raise PermissionError('无权访问该实验。')


def _assert_submission(p,sid):
    if not submission_allowed(p,sid):raise PermissionError('无权访问该提交。')


def attach_assignment(connection,aid):
    p=current()
    if p is None:return
    _teacher(p)
    if not _class_allowed(p,p.get('class_id')):raise PermissionError('请先选择有权限的发布班级。')
    connection.execute('INSERT INTO assignment_classes VALUES(?,?)',(aid,p['class_id']))
    from accounts import _audit
    _audit(connection,p['id'],'发布实验',aid)


def _related(table,column,value):
    with db.get_connection() as c:
        row=c.execute(f'SELECT {column} FROM {table} WHERE id=?',(value,)).fetchone()
    if not row:raise PermissionError('记录不存在或无权访问。')
    return row[0]


TEACHER_WRITES={'create_assignment','update_assignment','set_assignment_status','save_teacher_review','process_student_feedback'}
STUDENT_WRITES={'save_ai_hint','create_submission_with_questions','save_preliminary_review','save_initial_result',
                'save_follow_up_result','save_report','start_redefense','save_student_feedback','mark_student_feedback_viewed'}
LISTS={'list_assignments':'assignment','list_submissions':'submission','list_learning_records':'submission',
       'list_student_feedbacks':'feedback','list_ai_hints':'hint','list_teacher_tasks':'task','list_student_tasks':'task'}
SINGLES={'get_assignment','get_submission','get_qa_records','get_report','get_preliminary_review','get_teacher_review',
         'list_teacher_review_history','get_latest_redefense_request','get_redefense_request_by_new_submission','count_ai_hints'}


def _filter(p,kind,result):
    if kind=='assignment':return [r for r in result if assignment_allowed(p,r['id'])]
    if kind=='submission':return [r for r in result if submission_allowed(p,r['id'])]
    if kind in {'feedback','task'}:return [r for r in result if submission_allowed(p,r['submission_id'])]
    if kind=='hint':return [r for r in result if assignment_allowed(p,r['assignment_id']) and (p['role']=='teacher' or r['student_id']==p['student_id'])]
    raise AssertionError(kind)


def _wrap(name,fn):
    sig=inspect.signature(fn)
    @wraps(fn)
    def guarded(*args,**kwargs):
        p=current()
        if p is None:return fn(*args,**kwargs)
        _fresh(p)
        if p['role']=='admin':raise PermissionError('管理员仅维护账号与系统，教学材料请使用教师账号访问。')
        bound=sig.bind(*args,**kwargs);bound.apply_defaults();v=bound.arguments
        if name in TEACHER_WRITES or name=='list_teacher_tasks':_teacher(p)
        if name in STUDENT_WRITES:_student(p)
        if v.get('assignment_id') is not None:_assert_assignment(p,v['assignment_id'])
        if v.get('submission_id') is not None:_assert_submission(p,v['submission_id'])
        if v.get('request_id') is not None:_assert_submission(p,_related('redefense_requests','original_submission_id',v['request_id']))
        if v.get('feedback_id') is not None:_assert_submission(p,_related('student_feedbacks','submission_id',v['feedback_id']))
        if p['role']=='student' and v.get('student_id') is not None and str(v['student_id'])!=p['student_id']:
            raise PermissionError('学号与登录身份不一致。')
        if name=='create_assignment' and not _class_allowed(p,p.get('class_id')):raise PermissionError('请先选择发布班级。')
        if name=='create_submission_with_questions':
            a=v.get('assignment')
            if not a or a.get('id') is None:raise PermissionError('请选择班级内的已发布实验。')
            _assert_assignment(p,a['id'])
            # Always snapshot canonical requirements; a caller cannot forge the teacher's policy.
            v['assignment']=db.get_assignment(a['id']);v['problem']=v['assignment']['title'];v['name']=p['display_name']
        if name=='save_ai_hint':v['name']=p['display_name']
        if name=='save_teacher_review':v['reviewer_name']=p['display_name']
        if name=='process_student_feedback':v['teacher_name']=p['display_name']
        result=fn(*bound.args,**bound.kwargs)
        if name in LISTS:result=_filter(p,LISTS[name],result)
        if name in {'save_teacher_review','process_student_feedback','update_assignment','set_assignment_status'}:
            from accounts import _audit
            with db.get_connection() as c:_audit(c,p['id'],name,v.get('submission_id') or v.get('feedback_id') or v.get('assignment_id'))
        return result
    guarded._authorized=True
    return guarded


def install():
    for name in TEACHER_WRITES|STUDENT_WRITES|set(LISTS)|SINGLES:
        fn=getattr(db,name)
        if not getattr(fn,'_authorized',False):setattr(db,name,_wrap(name,fn))


install()
