import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import code_verifier as v
import evaluation_benchmark as b

DATA=json.loads((Path(__file__).resolve().parents[1]/'test_materials/complex_cpp/benchmark_cases.json').read_text(encoding='utf-8'))
class ComplexCppTests(unittest.TestCase):
    def test_benchmark_contains_independent_complex_cases_and_preserves_old_scopes(self):
        plan=b.build_plan(2,'complex')
        self.assertEqual(len(plan),36);self.assertEqual(len({p['id'] for p in plan}),36)
        self.assertEqual(sum(p['kind']=='answer' for p in plan),24)
        self.assertEqual(sum(p['kind']=='preliminary' for p in plan),12)
        self.assertEqual(len(b.build_plan(2,'all')),26);self.assertEqual(len(b.build_plan(2,'focused')),6)
        for p in plan:
            refs=p['question']['reference_points'] if p['kind']=='answer' else p['rubric']
            self.assertEqual(len(refs),len(p['expected']))
    def test_complex_routing_passes_actual_problem_and_rubric(self):
        class Service:
            def evaluate_initial_answer(self,problem,code,question,answer):return {'problem':problem}
            def preliminary_review(self,problem,requirements,code,*args,**kwargs):return {'problem':problem,'requirements':requirements}
        for item in b.build_plan(1,'complex'):
            result=b.evaluate(item,Service());self.assertEqual(result['problem'],item['problem'])
    def test_compile_run_tree_graph_dp_and_boundary_inputs(self):
        with patch.dict(os.environ,{'CODE_EXECUTION_ENABLED':'true','CODE_EXECUTION_BACKEND':'local','CXX_COMPILER':r'D:\dev-c++\Dev-Cpp\MinGW64\bin\g++.exe'}):
            v.reset_execution_backend()
            for c in DATA['cases']:
                samples=[{**s,'count_only':True} for s in c['samples']]
                if c['id']=='tree':
                    chain='1000\n'+'\n'.join(f'1 {i+1 if i<1000 else 0} 0' for i in range(1,1001))+'\n'
                    samples.append({'name':'1000节点链式递归','input':chain,'expected_output':'1000 1000\n','count_only':True})
                if c['id']=='graph':
                    n=1000;chain=f'{n} {n-1} 1 {n}\n'+'\n'.join(f'{i} {i+1}' for i in range(1,n))+'\n'
                    samples.append({'name':'1000节点图','input':chain,'expected_output':'999\n','count_only':True})
                if c['id']=='dp':samples.append({'name':'长整型价值','input':'2 2\n1 2000000000\n1 2000000000\n','expected_output':'4000000000\n','count_only':True})
                with self.subTest(case=c['id']):
                    result=v.verify_cpp_code(c['code'],samples,cpp_standard='c++14')
                    self.assertEqual(result['overall_status'],'全部通过',json.dumps(result,ensure_ascii=False))
                    self.assertEqual(result['passed'],5)
            v.reset_execution_backend()
    def test_knapsack_forward_capacity_mutation_is_rejected(self):
        case=next(c for c in DATA['cases'] if c['id']=='dp')
        with patch.dict(os.environ,{'CODE_EXECUTION_ENABLED':'true','CODE_EXECUTION_BACKEND':'local','CXX_COMPILER':r'D:\dev-c++\Dev-Cpp\MinGW64\bin\g++.exe'}):
            v.reset_execution_backend()
            result=v.verify_cpp_code(case['incorrect_code'],[{'input':'1 4\n2 3\n','expected_output':'3\n','count_only':True}],cpp_standard='c++14')
            self.assertNotEqual(result['overall_status'],'全部通过',json.dumps(result,ensure_ascii=False))
            self.assertEqual(result['passed'],0)
            self.assertEqual(result['cases'][0]['verdict_code'],'WA')
            v.reset_execution_backend()

    def test_oversized_input_is_rejected_before_compilation_and_is_not_truncated(self):
        import database as db
        text='0'*200001
        with self.assertRaisesRegex(ValueError,'不会截断保存'):
            db.normalize_test_cases([{'name':'过大输入','input':text,'expected_output':'0','count_only':True}])
        with patch.object(v,'get_execution_backend') as backend:
            result=v.verify_cpp_code('int main(){}',[{'input':text,'expected_output':'0'}])
            custom=v.run_cpp_code('int main(){}',text)
        backend.assert_not_called();self.assertEqual(result['overall_status'],'未执行');self.assertEqual(custom['overall_status'],'未执行')
        self.assertIn('未截断',result['message'])
