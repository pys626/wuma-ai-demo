import ast
import copy
import unittest
from pathlib import Path


class TeacherStatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        names = {"teacher_assignment_key", "latest_completed_teacher_records", "compact_code_result"}
        helpers = [node for node in ast.parse(source).body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
        cls.helpers = {}
        exec(compile(ast.Module(body=helpers, type_ignores=[]), "app.py", "exec"), cls.helpers)

    def record(self, id, student="001", assignment=1, score=80, status="已完成"):
        return {"id": id, "student_id": student, "assignment_id": assignment,
                "problem": "同名实验", "assignment_title": "同名实验", "name": "同名学生",
                "status": status, "overall": score}

    def test_repeated_attempts_count_once_and_unfinished_attempt_does_not_hide_report(self):
        records = [self.record(5, status="答辩中", score=None), self.record(2),
                   self.record(4, score=0), self.record(1, student="002")]
        original = copy.deepcopy(records)
        select = self.helpers["latest_completed_teacher_records"]
        self.assertEqual([row["id"] for row in select(records)], [1, 4])
        self.assertEqual(select(records), select(list(reversed(records))))
        self.assertEqual(records, original)

    def test_identical_names_and_titles_do_not_merge_different_students_or_assignments(self):
        records = [self.record(1), self.record(2, student="002"),
                   self.record(3, assignment=2), self.record(4, score=90)]
        result = self.helpers["latest_completed_teacher_records"](records)
        self.assertEqual([row["id"] for row in result], [2, 3, 4])

    def test_legacy_records_use_original_problem_and_missing_student_ids_stay_separate(self):
        records = [self.record(1, assignment=None), self.record(2, assignment=None),
                   self.record(3, assignment=None), self.record(4, student=""),
                   self.record(5, student="")]
        records[2]["problem"] = "另一道历史题"
        self.assertEqual([row["id"] for row in self.helpers["latest_completed_teacher_records"](records)],
                         [2, 3, 4, 5])

    def test_no_completed_report_produces_no_statistics_sample(self):
        records = [self.record(1, score=None), self.record(2, status="答辩中")]
        self.assertEqual(self.helpers["latest_completed_teacher_records"](records), [])

    def test_environment_label_refers_to_saved_submission(self):
        for status in ["环境不可用", "编译环境不兼容"]:
            with self.subTest(status=status):
                label, score = self.helpers["compact_code_result"]({"code_verification": {"overall_status": status}})
                self.assertEqual(label, "提交时评测环境异常")
                self.assertEqual(score, "—")


if __name__ == "__main__":
    unittest.main()
