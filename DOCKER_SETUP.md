# Docker隔离执行配置

悟码AI v1.3.15可以把C++编译和运行放进临时Docker容器。正式判题与AI答辩已经分离；Docker环境不可用时不会误判学生代码，也不会直接进入正式答辩。

## 一、准备环境

1. 安装Docker Desktop并完成首次启动。
2. 打开PowerShell，确认客户端和服务都可用：

~~~powershell
docker version
docker info
~~~

3. 提前下载固定的C++镜像：

~~~powershell
docker pull gcc:13-bookworm
docker image inspect gcc:13-bookworm
~~~

应用只检查本机已有镜像，不会在学生点击按钮后自动下载。

## 二、修改.env

把项目`.env`中的代码执行配置改为：

~~~text
CODE_EXECUTION_ENABLED=true
CODE_EXECUTION_BACKEND=docker
DOCKER_COMMAND=docker
DOCKER_CPP_IMAGE=gcc:13-bookworm
CODE_EXECUTION_TIMEOUT_SECONDS=2
CODE_COMPILE_TIMEOUT_SECONDS=15
CODE_EXECUTION_MAX_OUTPUT_CHARS=4000
~~~

保存后完全停止并重新启动Streamlit。使用教师密码登录后，侧栏应显示“Docker隔离代码验证可用”，并列出镜像和限制状态。

## 三、安全限制

每次编译和每组测试都会创建独立临时容器，并应用以下限制：

- 网络模式为`none`；
- CPU上限1核，内存及交换空间合计上限256MB；
- 进程数上限64，文件描述符上限64；
- 移除Linux capabilities，启用`no-new-privileges`；
- 容器根文件系统只读，仅提供64MB临时目录；
- 运行阶段使用无特权用户，并以只读方式挂载编译产物；
- 运行容器使用`docker run -i`保持标准输入，使教师样例能够送入学生程序；
- 使用`-O2`编译学生程序，避免算法题因关闭优化而产生不公平超时；
- 宿主程序监控运行时间和输出量，触发限制后强制删除容器。

Docker模式显著降低学生程序直接影响Web进程的风险，但不是完整的生产级在线判题平台。公开部署还应把Docker守护进程与Web服务分开、限制并发和请求频率、启用登录权限及审计，并定期更新镜像。

## 四、常见状态

| 页面提示 | 含义 | 处理方法 |
| --- | --- | --- |
| Docker代码验证未启用 | 总开关仍为false | 修改`.env`并重启 |
| 未找到Docker命令 | Docker未安装或未加入PATH | 安装Docker Desktop，重新打开终端 |
| Docker服务未启动 | 命令存在但后台服务未运行 | 启动Docker Desktop并等待就绪 |
| 尚未下载Docker C++镜像 | 配置的镜像不存在 | 执行`docker pull gcc:13-bookworm` |
| 环境不可用 | 运行期间Docker服务或镜像异常 | 教师检查环境；系统不会把它记为学生代码错误 |

## 五、退回本机兼容模式

如果演示电脑暂时不能使用Docker，可改为：

~~~text
CODE_EXECUTION_BACKEND=local
CXX_COMPILER=g++
~~~

local模式保留v1.3行为，只应用宿主机能够提供的超时和资源限制，仅用于可信本地演示。
