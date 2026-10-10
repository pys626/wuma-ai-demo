# 悟码AI · 决赛版v1.4

面向编程实验的教学辅助Demo：代码测试后，结合学生代码、材料与教师评分点进行文字答辩，保存逐点作答依据，并由教师复核。

## 在线只读入口

决赛 Demo：[https://wuma-ai-finals-pys626.streamlit.app/](https://wuma-ai-finals-pys626.streamlit.app/)。当前部署分支为 `finals-20261010`；提交材料中的源码链接请使用该分支。

Streamlit入口文件：`competition_demo.py`。本入口强制只读、不调用模型、不执行学生代码，使用独立合成数据库。包含2026-10-10保存的真实模型评价：案例甲遗漏边界后补答；案例乙持续误解后由教师要求重新答辩。作答为人工设计，不对应真实学生，不能当作实际教学效果或总体准确率。

```powershell
python -m pip install -r requirements.txt
python -m streamlit run competition_demo.py
```

无需配置模型密钥。入口默认使用系统临时目录内的`wuma_finals_20261010.db`。若设置`WUMA_AI_DB_PATH`，必须是新的独立演示文件；已有业务库、账号库或其他案例记录不会被覆盖。

## 完整本地功能

```powershell
python -m streamlit run app.py
```

完整本地版提供独立管理员、教师、学生账号，按班级管理权限；支持实验管理、代码评测、AI答辩、报告与教师复核。模型通过OpenAI兼容接口配置，账号首次设置与模型连接、执行环境须在本地完成。录屏使用独立合成数据库，避免真实教学数据进入作品材料。

## LearnBuddy

技能目录：`learnbuddy/wuma-ai-teacher`。四个工具查询教师概况、待办、单次诊断和学生进展，均为只读。完整本地版在教师“班级与学生管理”生成个人连接配置，按教师班级授权；模型密钥及个人连接令牌不放入源码仓库。公开网站的数据库与本地MCP数据库分别管理。

## 检查

`python run_tests.py`使用临时数据库，禁止读取密钥配置和模型网络请求。它验证程序行为，不证明模型总体准确率。与本次新增公开案例直接相关的检查：`python run_tests.py --pattern test_finals_demo_data.py`。

本发布候选包含最新源码与合成案例，无`.env`、真实数据库、个人令牌或生成连接配置。发布前核对仓库差异；发布后另行检查HTTPS地址可访问、学生/教师入口、报告证据、刷新和只读行为。
