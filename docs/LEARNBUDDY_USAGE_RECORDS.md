# 悟码AI LearnBuddy开发与使用记录

团队：forbetter；作品：悟码AI；成员：庞颖森；学校：深圳大学。

本页是评委阅读原始证据的索引。截图保持原样，历史版本、测试数量和当时状态不改写为当前结果。LearnBuddy负责辅助查询、比较与审计，最终工程和教学决策由参赛者作出。

## 初赛保留的原始记录

初赛公开目录：[main分支原目录](https://github.com/pys626/wuma-ai-demo/tree/main/docs/learnbuddy_history)。当前决赛分支保留了这些原截图：

| 阶段 | 原始证据 |
| --- | --- |
| 教师待办、提交诊断、学生进展查询 | [待办](learnbuddy_history/01_teacher_pending_empty.png)、[提交诊断](learnbuddy_history/02_submission_5_review.png)、[学生进展](learnbuddy_history/03_student_222222_progress.png) |
| 工程复盘、审查范围与人工决策 | [审查范围](learnbuddy_history/04_project_review_scope.png)、[人工决策](learnbuddy_history/05_project_review_decisions.png) |
| “先判题、后答辩”方案比较与选择 | [方案比较](learnbuddy_history/06_flow_comparison.png)、[最终人工选择](learnbuddy_history/07_flow_human_decision.png) |
| 专项测试及证据审计 | [第一轮测试](learnbuddy_history/08_flow_tests_6_passed.png)、[第一轮审计](learnbuddy_history/09_flow_first_audit.png)、[WA/TLE补测](learnbuddy_history/10_wa_tle_tests_3_passed.png)、[最终审计](learnbuddy_history/11_flow_final_audit.png) |

决赛合并附件中的38页《悟码AI_LearnBuddy历史对话记录.pdf》来自参赛者保留的2026年9月原文件。它记录当时开发阶段的对话；本次整理未独立重新认证全部原始会话的完整性。

## 决赛补充的实际使用记录

2026年10月10日三次教师查询与修正：

1. [查询空待办队列](learnbuddy_history/01_待办空队列_20261010.png)：空队列没有用已处理历史记录代替。
2. [查询提交诊断与学生进展](learnbuddy_history/02_提交诊断与学生进展_20261010.png)：按当时本机合成数据核对。
3. [参赛者指出问题后的简报修正](learnbuddy_history/03_复核简报修正_20261010.png)：修正缺乏证据的跨轮比较，不宣称模型自行发现了问题。

数据源、模型区别、旧字段及证据边界见[原记录说明](learnbuddy_history/README.md)。相邻JSON是核对记录，不是完整平台调用日志；本机查询不能称为实时查询公开Demo。

## 提交附件与技能源码

最终附件名称：`forbetter_悟码AI_LearnBuddy使用记录_决赛合并版_20261011.zip`。包含11张初赛原截图与README、38页历史对话PDF、三次决赛补充记录及4页使用说明，共23个成员。说明文档不替代原始对话，历史材料不证明真实学生教学效果。

技能源码：[教师Skill](../learnbuddy/wuma-ai-teacher/SKILL.md)、[MCP连接器](../learnbuddy/mcp_server.py)、[接入说明](../learnbuddy/LEARNBUDDY_SETUP.md)。工具只读；模型密钥和个人连接令牌不随源码提交。
