# 悟码AI在线Demo部署说明

本说明用于初赛浏览器Demo。默认模式会自动生成合成教学数据，不读取真实学生信息，不调用模型API，也不运行学生代码。评委可直接体验学生端和教师端。

## 1. 本机先验收

### Docker方式（推荐）

在PowerShell中进入项目目录：

```powershell
Set-Location "D:\AI_Coding\wuma_ai_v1_3_15"
docker compose -f docker-compose.demo.yml up --build
```

浏览器打开 `http://localhost:8501`。停止服务使用：

```powershell
docker compose -f docker-compose.demo.yml down
```

如需同时清除合成演示数据库并重新生成：

```powershell
docker compose -f docker-compose.demo.yml down -v
```

### Python方式

```powershell
Set-Location "D:\AI_Coding\wuma_ai_v1_3_15"
$env:WUMA_DEMO_MODE="true"
$env:WUMA_DEMO_READ_ONLY="true"
$env:CODE_EXECUTION_ENABLED="false"
$env:WUMA_AI_DB_PATH="$PWD\data\wuma_ai_demo.db"
python -m streamlit run app.py
```

## 2. 上传源码仓库

仓库必须包含源代码、`Dockerfile`、本说明和项目README，但不得提交 `.env`、真实数据库、密钥或学生数据。

```powershell
git init
git add .
git commit -m "release: wuma ai v1.3.15 online demo"
git branch -M main
git remote add origin <你的GitHub或Gitee仓库地址>
git push -u origin main
```

上传后检查仓库网页中不存在 `.env`、`data/*.db` 和任何API密钥。

## 3. 部署到浏览器环境

选择支持从Git仓库构建Dockerfile的托管平台，新建Web Service并连接上一步仓库：

| 设置 | 值 |
|---|---|
| 构建方式 | 仓库根目录的 `Dockerfile` |
| 对外端口 | 使用平台提供的 `PORT`；未提供时为8501 |
| 健康检查 | `/_stcore/health` |
| `WUMA_DEMO_MODE` | `true` |
| `WUMA_DEMO_READ_ONLY` | `true` |
| `CODE_EXECUTION_ENABLED` | `false` |
| `WUMA_AI_DB_PATH` | `/tmp/wuma_ai_demo.db`（无持久盘时推荐） |

此模式不需要配置 `LLM_API_KEY` 或 `TEACHER_PASSWORD`。容器启动后会自动生成两名虚拟学生、三次实验提交及教师待办。使用 `/tmp` 时，服务重启会恢复干净的演示数据。

部署完成后复制HTTPS地址，并用无痕窗口检查：

1. 链接无需登录托管平台即可访问；
2. 首页显示“赛事在线Demo”；
3. “体验学生端”可查看任务、报告和学习趋势；
4. “体验教师端”可查看教师待办、证据摘要和班级分析；
5. 页面不出现提交代码、运行程序、保存复核等写入入口；
6. 刷新和重新打开链接不会报错。

## 4. 完整本地版与公开Demo的边界

公开Demo关闭代码执行、模型调用与业务写入，用于稳定展示产品闭环。完整本地版保持原有提交、判题、AI答辩、教师复核和实验管理功能。

如以后需要把“学生在线提交并运行任意C++代码”开放到公网，必须使用独立沙箱或远程判题节点、身份认证、速率限制和日志审计；不要在公网服务器上启用本机 `g++` 执行模式。
