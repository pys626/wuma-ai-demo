"""Synthetic-account server for browser regression; no real database or model secrets."""
import os
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
for stream in (sys.stdout,sys.stderr):
    if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
def audit(event, values):
    if event=='open' and isinstance(values[0],(str,bytes,os.PathLike)):
        name=Path(os.fsdecode(values[0])).name.lower()
        if name=='.env' or name.startswith('.env.') or name=='secrets.toml':
            raise RuntimeError('Browser regression cannot read secret configuration.')
sys.addaudithook(audit)
import dotenv
dotenv.load_dotenv=lambda *args,**kwargs:False
for key in tuple(os.environ):
    if key.startswith(('LLM_','HUNYUAN_','DEEPSEEK_','OPENAI_','WUMA_','CODE_','DOCKER_')) or key=='TEACHER_PASSWORD':
        os.environ.pop(key,None)
os.environ['WUMA_DEMO_MODE']='false'
os.environ['CODE_EXECUTION_ENABLED']='false'
with tempfile.TemporaryDirectory(prefix='wuma-browser-regression-') as folder:
    os.environ['WUMA_AI_DB_PATH']=str(Path(folder)/'isolated.db')
    import database as db
    import accounts
    import defense_drafts
    db.init_db()
    accounts.init_schema()
    password='Test-account-pass-2026'
    accounts.bootstrap('fixture-only','fixture-only','refresh_admin','刷新测试管理员',password,'刷新测试班级',
                       teacher_username='refresh_teacher',teacher_name='刷新测试教师',teacher_password=password)
    accounts.add_students(1,1,[{'学号':'20269851','姓名':'刷新测试学生','初始密码':password}])
    with db.get_connection() as c:c.execute('UPDATE accounts SET must_change=0')
    assignment=db.list_assignments()[0]
    questions=[{'dimension':'程序逻辑理解','question':f'请解释第{i}个步骤。','reference_points':['解释执行路径'],'reference_dimensions':['程序逻辑理解']} for i in range(1,4)]
    sid=db.create_submission_with_questions('20269851','刷新测试学生',assignment['title'],'解释执行路径','int main(){return 0;}','2026-10-08 23:00',questions,assignment=assignment)
    evaluation={'score':90,'confidence':'高','feedback':'解释明确','should_follow_up':False,'follow_up_question':''}
    db.save_initial_result(sid,0,'第一题已经完成','',evaluation,True)
    defense_drafts.save(db.DB_PATH,'20269851',sid,1,'initial',questions[1],'第二题刷新前已保存的草稿')
    # Metadata enables invalidation in the test process, without exposing session credentials.
    metadata=ROOT/'tmp'/'browser_fixture.json'
    metadata.parent.mkdir(exist_ok=True)
    import json
    metadata.write_text(json.dumps({'database':str(db.DB_PATH),'submission_id':sid}),encoding='utf-8')
    import streamlit.config as config
    config.set_option('secrets.files',[])
    from streamlit.runtime.secrets import secrets_singleton
    secrets_singleton.load_if_toml_exists=lambda:False
    from streamlit.web import cli
    sys.argv=['streamlit','run',str(ROOT/'app.py'),'--server.address','127.0.0.1','--server.port','18514','--server.headless','true','--browser.gatherUsageStats','false']
    try:cli.main()
    finally:metadata.unlink(missing_ok=True)
