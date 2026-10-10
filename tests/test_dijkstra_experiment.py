import copy
import os
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import code_verifier as verifier
import database as db
import experiment_packages as packages
import ui_uploads
from auth_helpers import authenticate, load_teacher_draft

ROOT=Path(__file__).resolve().parents[1]
PACKAGE=ROOT/'test_materials/dijkstra/experiment_config.json'


def oracle(text):
    values=list(map(int,text.split()))
    n,m,s,t=values[:4]
    assert len(values)==4+3*m and 1<=n<=2000 and 0<=m<=10000
    assert 1<=s<=n and 1<=t<=n
    edges=[tuple(values[i:i+3]) for i in range(4,len(values),3)]
    assert all(1<=u<=n and 1<=v<=n and 0<=w<=1000000000 for u,v,w in edges)
    # Bellman-Ford is independent of the C++ reference heap implementation.
    dist=[None]*(n+1);dist[s]=0
    for _ in range(n-1):
        changed=False
        for u,v,w in edges:
            if dist[u] is not None and (dist[v] is None or dist[u]+w<dist[v]):
                dist[v]=dist[u]+w;changed=True
        if not changed:break
    return str(dist[t] if dist[t] is not None else -1)+'\n'


class DijkstraExperimentTests(unittest.TestCase):
    def setUp(self):
        self.values=packages.parse(PACKAGE.read_bytes())
        self.tmp=tempfile.TemporaryDirectory()
        self.old=db.DB_PATH;db.DB_PATH=Path(self.tmp.name)/'isolated.db'
        db.init_db()
    def tearDown(self):
        db.DB_PATH=self.old;self.tmp.cleanup();verifier.reset_execution_backend()
    def execute(self,code,cases):
        with patch.dict(os.environ,{'CODE_EXECUTION_ENABLED':'true','CODE_EXECUTION_BACKEND':'local','CXX_COMPILER':r'D:\dev-c++\Dev-Cpp\MinGW64\bin\g++.exe'}):
            verifier.reset_execution_backend()
            return verifier.verify_cpp_code(code,cases,'c++14')
    def test_complete_package_preserves_materials_and_pass_only_rule(self):
        self.assertEqual(len(self.values['public_samples']),3)
        self.assertEqual(len(self.values['test_cases']),8)
        self.assertEqual(sum(x['weight'] for x in self.values['rubric']),100)
        self.assertTrue(self.values['explanation_required'])
        self.assertFalse(self.values['rubric_reviewed'])
        self.assertTrue(all(x['count_only'] and x['weight']==0 for x in self.values['test_cases']))
        self.assertEqual(self.values['reference_code'],(ROOT/'test_materials/dijkstra/reference.cpp').read_text(encoding='utf-8'))
        restored=packages.parse(packages.export(self.values))
        self.assertEqual(restored,self.values)
    def test_all_sample_answers_match_independent_bellman_ford(self):
        for item in self.values['public_samples']:
            self.assertEqual(item['output'],oracle(item['input']),item['name'])
        for item in self.values['test_cases']:
            self.assertEqual(item['expected_output'],oracle(item['input']),item['name'])
    def test_reference_compiles_and_passes_official_and_random_graphs(self):
        cases=[{'name':s['name'],'input':s['input'],'expected_output':s['output'],'count_only':True} for s in self.values['public_samples']]
        cases.extend(self.values['test_cases'])
        rng=random.Random(20261008)
        for i in range(12):
            n=2+i;m=n*3;s=rng.randint(1,n);t=rng.randint(1,n)
            edges=[(rng.randint(1,n),rng.randint(1,n),rng.choice([0,1,7,1000000000])) for _ in range(m)]
            text=f'{n} {m} {s} {t}\n'+''.join(f'{u} {v} {w}\n' for u,v,w in edges)
            cases.append({'name':f'独立随机图{i+1}','input':text,'expected_output':oracle(text),'count_only':True})
        result=self.execute(self.values['reference_code'],cases)
        self.assertEqual(result['overall_status'],'全部通过',result)
        self.assertEqual(result['passed'],23)
    def test_wrong_direction_integer_type_and_early_return_are_detected(self):
        code=self.values['reference_code'];cases=self.values['test_cases']
        variants=[
            (code.replace('graph[u].push_back({v, weight});','graph[u].push_back({v, weight}); graph[v].push_back({u, weight});'),cases[3]),
            (code.replace('using ll = long long;','using ll = int;'),cases[5]),
            (code.replace('pending.push({candidate, edge.to});','pending.push({candidate, edge.to}); if (edge.to == target) return candidate;'),cases[0]),
        ]
        for mutant,case in variants:
            with self.subTest(case=case['name']):
                result=self.execute(mutant,[case])
                self.assertEqual(result['passed'],0,result)
                self.assertEqual(result['cases'][0]['verdict_code'],'WA',result)
    def test_zero_cycle_mutant_is_stopped_instead_of_hanging(self):
        code=self.values['reference_code'].replace('candidate < dist[edge.to]','candidate <= dist[edge.to]')
        result=self.execute(code,[self.values['test_cases'][1]])
        self.assertEqual(result['passed'],0,result)
        case=result['cases'][0]
        self.assertIn(case['verdict_code'],('TLE','RE'),result)
        if case['verdict_code']=='RE':
            # The growing heap can exhaust local memory before the timeout.
            self.assertIn('bad_alloc',case['stderr'],result)
            self.assertNotEqual(case['exit_code'],0,result)
        self.assertLess(case['duration_ms'],10000,result)
    def test_upload_check_and_draft_import_do_not_publish_or_replace_old_assignments(self):
        app=AppTest.from_file(str(ROOT/'app.py'));authenticate(app,'教师')
        app.session_state['current_page']='实验管理'
        before=db.list_assignments();native=ui_uploads.chinese_file_uploader
        data=PACKAGE.read_bytes();upload=SimpleNamespace(size=len(data),getvalue=lambda:data)
        def uploaded(*args,**kwargs):
            native(*args,**kwargs);return upload
        with patch('ui_uploads.chinese_file_uploader',side_effect=uploaded):
            app.run(timeout=20)
            next(b for b in app.button if b.label=='检查实验包').click().run(timeout=20)
            self.assertFalse(app.exception)
            self.assertTrue(any(self.values['title'] in x.value for x in app.markdown))
            next(b for b in app.button if b.label=='载入未发布草稿').click().run(timeout=20)
            self.assertFalse(app.exception)
            self.assertEqual(load_teacher_draft()['data']['title'],self.values['title'])
            self.assertEqual(next(i for i in app.text_input if i.label=='实验名称').value,self.values['title'])
        self.assertEqual(db.list_assignments(),before)
    def test_oversized_json_is_rejected_before_reading_contents(self):
        app=AppTest.from_file(str(ROOT/'app.py'));authenticate(app,'教师')
        app.session_state['current_page']='实验管理';native=ui_uploads.chinese_file_uploader
        def fail_read():raise AssertionError('不应读取超限文件正文')
        def uploaded(*args,**kwargs):
            native(*args,**kwargs);return SimpleNamespace(size=1000001,getvalue=fail_read)
        with patch('ui_uploads.chinese_file_uploader',side_effect=uploaded):app.run(timeout=20)
        self.assertFalse(app.exception)
        self.assertTrue(next(b for b in app.button if b.label=='检查实验包').disabled)
        self.assertTrue(any('不超过1MB' in e.value for e in app.error))
