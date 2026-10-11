"""Request-bound playback of exact saved materials; never a substitute grader."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import lru_cache, wraps
import inspect
import json
from pathlib import Path

_active = ContextVar('wuma_case_playback', default=False)

def active():
    return _active.get()

@contextmanager
def scope():
    token = _active.set(True)
    try:
        yield
    finally:
        _active.reset(token)

@lru_cache(maxsize=1)
def _fixture():
    data = json.loads((Path(__file__).parent / 'demo_assets/finals_cases.json').read_text(encoding='utf-8'))
    if data.get('synthetic') is not True or data.get('case_set') != 'finals-20261010':
        raise ValueError('固定案例来源标记不匹配。')
    return data

def fixture():
    return deepcopy(_fixture())

def _same(a, b):
    return str(a or '').strip().replace('\r\n', '\n') == str(b or '').strip().replace('\r\n', '\n')

def _materials(values):
    data = _fixture()
    if not _same(values.get('code'), data['code']) or values.get('problem') != data['assignment']['title']:
        raise ValueError('在线回放仅覆盖选择排序的固定案例。请填入演示材料；修改代码后须在完整本地版实时评价。')
    for key in ('requirements', 'rubric', 'constraints_text'):
        if key in values and values[key] != data['assignment'][key]:
            raise ValueError('实验要求或评分点已改变，已有评价不能用于新材料。请恢复演示数据，或使用完整本地版。')
    if 'explanation' in values and not _same(values['explanation'], data['explanation']):
        raise ValueError('解题思路已改变，不能套用预存评价。请填入演示材料，或使用完整本地版实时评价。')
    if 'lab_report' in values and not _same(values['lab_report'], data.get('lab_report', '')):
        raise ValueError('实验报告与固定材料不一致，不能套用预存评价。请使用完整本地版实时评价。')
    return data

def questions():
    return [{key: qa[key] for key in ('dimension', 'question', 'reference_points', 'reference_dimensions')}
            | {'reason': qa.get('question_reason', '')} for qa in fixture()['cases'][0]['qa']]

def example_answer(question, stage, case_index):
    for qa in _fixture()['cases'][case_index]['qa']:
        if qa['question'] == question.get('question') and qa['reference_points'] == question.get('reference_points'):
            return qa['follow_up_answer'] if stage == 'follow_up' else qa['answer']
    return ''

def invoke(name, values):
    simple = {'is_configured': True, 'provider_name': '已保存的真实模型结果回放',
              'model_name': 'DeepSeek / deepseek-flash（2026-10-10）', 'is_tencent_provider': False}
    if name in simple:
        return simple[name]
    if name == 'generate_report':
        submission, records = values['submission'], values['qa_records']
        _materials({'problem': submission['problem'], 'code': submission['code']})
        for case in _fixture()['cases']:
            if len(records) == len(case['qa']) and all(
                item['question'] == saved['question'] and _same(item['answer'], saved['answer'])
                and _same(item['follow_up_answer'], saved['follow_up_answer'])
                and (item.get('final_evaluation') or item.get('initial_evaluation')) == saved['final_evaluation']
                for item, saved in zip(records, case['qa'])):
                return deepcopy(case['report'])
        raise ValueError('当前作答组合没有对应的已保存报告，不能编造新的AI评分。请使用同一案例的示例回答。')
    if name == 'generate_learning_hint':
        raise ValueError('固定案例没有可回放的渐进提示；在线演示不生成新AI内容。')
    data = _materials(values)
    if name == 'preliminary_review':
        return deepcopy(data['preliminary'])
    if name == 'generate_questions':
        requests = [str(item).removeprefix('教师要求学生重新答辩：') for item in values.get('teaching_focus') or []
                    if str(item).startswith('教师要求学生重新答辩：')]
        if requests:
            case = data['cases'][1]
            if requests != [case['teacher_reason']]:
                raise ValueError('该教师意见没有对应的预存重答题。意见已保存；新出题请在完整本地版调用AI。')
            return deepcopy(case['redefense_questions'])
        if values.get('teaching_focus') != data['assignment']['teaching_focus']:
            raise ValueError('教学重点已改变，不能套用预存问题。请恢复演示数据，或使用完整本地版。')
        return questions()
    question = values['question']
    for case in data['cases']:
        for qa in case['qa']:
            if qa['question'] != question.get('question') or qa['reference_points'] != question.get('reference_points'):
                continue
            if name == 'evaluate_initial_answer' and _same(values['answer'], qa['answer']):
                return deepcopy(qa['initial_evaluation'])
            if name == 'evaluate_follow_up' and qa['follow_up_answer'] and all(
                _same(values[key], qa[saved]) for key, saved in (
                    ('initial_answer', 'answer'), ('follow_up_question', 'follow_up_question'),
                    ('follow_up_answer', 'follow_up_answer'))
            ) and values.get('initial_evaluation') == qa['initial_evaluation']:
                return deepcopy(qa['final_evaluation'])
    raise ValueError('这段回答没有对应的预存评价，在线演示不会为新回答编造评分。请填入示例回答，或在完整本地版实时答辩。')

def dispatch(function):
    signature = inspect.signature(function)
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not active():
            return function(*args, **kwargs)
        from llm_service import LLMServiceError
        values = signature.bind(*args, **kwargs)
        values.apply_defaults()
        try:
            return invoke(function.__name__, values.arguments)
        except ValueError as error:
            raise LLMServiceError(str(error)) from error
    return wrapped

def verification(code, test_cases, cpp_standard):
    import code_verifier as verifier
    data = _fixture()
    if (_same(code, data['code']) and test_cases == verifier.formal_test_cases(data['assignment'])
            and cpp_standard == data['assignment']['cpp_standard']):
        result = deepcopy(data['verification'])
        result['execution_backend'] = 'saved-case-playback'
        result['message'] = '回放2026-10-10本例已保存的11/11判题证据；本次没有执行代码。'
        return result
    return verifier._base_result('未执行', '材料或测试设置与固定案例不一致，不能套用11/11结果。请填入演示材料，或使用完整本地版真实评测。', '未执行', cpp_standard)
