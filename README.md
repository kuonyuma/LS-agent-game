# Agent Service

基于纯 Python 内核的 Agent，提供本地 CLI 和 Web 工作台。
项目按 [架构划分](docs/framewokr.md) 拆成五个内核模块与外部适配器。

## 启动

需要 Python 3.14、uv 和交互式终端。以下命令使用 PowerShell：

```powershell
uv sync --frozen --all-groups
$env:GEMINI_API_KEY = "替换为有效密钥"
& .venv\Scripts\python.exe -m agent_service.main
```

Key 优先来自进程环境变量 `GEMINI_API_KEY`，回退到
`src/agent_service/config/config.yaml` 中的 `model.key`。
不自动读取 `.env`；不要把真实密钥提交到仓库。

模型名称、输出 token 上限、审核参数来自 `config.yaml`；
系统提示词与审核提示词也在同一配置目录。配置导入时加载，修改后需重启进程。

CLI 以启动时的当前目录作为文件工具工作区。文件参数使用相对路径，例如
`src/agent_service/main.py`，不是绝对路径。需要改用其他工作区时，可通过
Python 组装接口 `create_runner(workspace=Path(...))` 明确指定。

## 启动共享 Web 工作台

Web 工作台默认要求登录。从项目根目录执行以下 PowerShell 命令。

```powershell
uv sync --frozen --all-groups
npm --prefix frontend install
npm --prefix frontend run build -- --no-emptyOutDir
& .venv\Scripts\python.exe -m agent_service.web_main
```

首次在运行后端的电脑上直接打开 `http://127.0.0.1:8000`，按页面提示创建管理员账号，密码至少 15 个字符。创建后会自动登录，之后打开工作台会显示普通登录页。无浏览器时也可用 `python -m agent_service.web_main --create-admin admin` 创建首个管理员。

管理员可在右上角账号菜单开通成员账号。成员登录后可访问自己的聊天、知识库和 PPT 成品。首个管理员可继续访问升级前的本机会话、知识库和成品。账号与浏览器会话存放在 `.agent_data/auth/accounts.sqlite3`，成员资源存放在 `.agent_data/users/`。在界面删除聊天会让它退出会话列表，并保留原始历史文件。

共享模式中的宿主机目录、文件工具、终端命令和 ChatGPT 套餐授权关闭。用户可以使用管理员配置的模型 API Key，也可以在模型设置里为当前页面输入自己的 Key。知识库使用后端 `config.yaml` 配置的向量模型和地址。

向同伴开放时，由 HTTPS 反向代理在同一域名提供页面和 `/api`，并让后端仅信任该代理传来的协议标头。共享工作台使用单个 Web 进程运行，SSE 流和审批状态保存在进程内。仅在自己电脑上使用原有的目录与工具功能时，可用 `python -m agent_service.web_main --local` 在回环地址启动。

### 天气、参考汇率与地图

在启动后端或 CLI 的 PowerShell 进程中配置以下变量。密钥保留在服务端。

| 功能 | 配置 |
| --- | --- |
| 天气和空气质量 | `QWEATHER_API_HOST` 填写和风天气控制台分配的 `*.qweatherapi.com` 域名，配合 `QWEATHER_API_KEY`。本机模式默认启用 Open-Meteo 公共 API。共享模式的非商业部署可以设置 `$env:OPEN_METEO_ENABLED = '1'`，商业部署设为 `'0'`。 [和风天气 API Host](https://dev.qweather.com/en/docs/configuration/api-host/) · [Open-Meteo 条款](https://open-meteo.com/en/terms) |
| 参考汇率 | Frankfurter 免 Key，提供实际观测日期和最近 2～30 个自然日的观测序列。 [Frankfurter](https://frankfurter.dev/) |
| 高德地点和路线 | `AMAP_API_KEY` 使用高德 Web 服务 Key。搜到地点后可用其 GCJ-02 坐标规划驾车、步行、骑行或公交，公交另需地点的 `city_code`。 [高德路线规划](https://lbs.amap.com/api/webservice/guide/api/newroute) |
| OSM 地点和路线 | `NOMINATIM_BASE_URL` 配置自托管或已授权的地理编码端点。按需配置 `OSRM_DRIVING_BASE_URL`、`OSRM_WALKING_BASE_URL`、`OSRM_CYCLING_BASE_URL`，分别指向用相应出行模式构建的服务。URL 填服务根地址，工具使用 WGS84 坐标。 [Nominatim 搜索](https://nominatim.org/release-docs/latest/api/Search/) · [OSRM 模式](https://project-osrm.org/docs/v26.4.0/profiles) |

OSRM 路径中的 profile 默认分别为 `driving`、`foot`、`bike`。若部署使用其他名称，设置对应的 `OSRM_DRIVING_PROFILE`、`OSRM_WALKING_PROFILE` 或 `OSRM_CYCLING_PROFILE`。

## Web 工作台生成 PPT

登录后，在首页点 **试做一份 PPT**，可以用内置的校园反诈示例体验完整流程。也可以直接输入主题，或上传资料后让工作台按资料制作。确认卡可以展开逐页内容，点 **生成 PPT** 后即可排版。成品支持下载，刷新会话后也能找回。排版服务支持 PDF 导出时，成品卡会提供整套预览。

### 部署者首次配置

提前为工作台配置一个可用的聊天模型和一个支持逐页 Markdown 输入的 Presenton 实例。在 Presenton 配置排版模型与图片提供商，在管理界面创建 API key。评委可使用预先开通的成员账号和服务端的模型配置。安装前端依赖后，从项目根目录运行以下 PowerShell 命令。 [Presenton 部署与 API](https://github.com/presenton/presenton)

```powershell
$env:PRESENTON_BASE_URL = 'http://127.0.0.1:5001'
$env:PRESENTON_API_KEY = Read-Host '输入 Presenton API key' -MaskInput
npm --prefix frontend run build -- --no-emptyOutDir
& .venv\Scripts\python.exe -m agent_service.web_main
```

打开 `http://127.0.0.1:8000`，用成员账号跑一遍示例并下载 PPT。后端启动时会报告 PPT 工具是否挂载，也可以登录后在 `/api/health` 查看 `presentation_generation`。首页会根据后端是否挂载工具显示示例入口。在启动后端的同一个 PowerShell 窗口补齐缺少的变量后重启服务。

如果 Presenton 支持将已有演示导出为 PDF，成品卡同时显示封面和整套 PDF 预览。只生成一次演示，预览复用同一份演示。 [Presenton PDF 导出接口](https://github.com/presenton/presenton/pull/831)

## Web 工作台生成图片

在启动后端的 PowerShell 窗口中配置百炼业务空间域名和同地域 API Key。工具使用 `qwen-image-3.0`。示例中的业务空间 ID 需要替换为百炼控制台里的实际值。 [百炼生图接口](https://help.aliyun.com/zh/model-studio/qwen-image-generation-and-editing-api-reference)

```powershell
$env:DASHSCOPE_IMAGE_BASE_URL = 'https://你的业务空间ID.cn-beijing.maas.aliyuncs.com'
$env:DASHSCOPE_API_KEY = Read-Host '输入百炼 API Key' -MaskInput
npm --prefix frontend run build -- --no-emptyOutDir
& .venv\Scripts\python.exe -m agent_service.web_main
```

选择 **＋ → 生成图片**，输入图片描述并按需选择宽高比，发送后直接调用百炼。自动模式可从描述中识别比例，未提供比例时使用 1:1。生成的 PNG 可以在聊天中放大和下载，刷新会话后仍可查看。也可以直接在普通聊天中请求生成图片。如果菜单显示「当前工作台未启用百炼生图」，请在运行 Web 后端的环境里补齐配置并重启。

## 代码结构

```text
src/agent_service/
├── kernel/
│   ├── runtime/        # 单次运行生命周期、循环、事件、停止检查点
│   ├── context/        # 历史、实时消息、运行通知、压缩
│   ├── model/          # 模型端口、流式结果、主模型用量
│   ├── dispatch/       # 工具协议、声明过滤、审批、并发执行与结果
│   └── strategy/       # 规划/执行/反思、验证与完成提醒
├── adapters/
│   ├── gemini/         # SDK 客户端与流转换
│   ├── tools/          # 文件、命令、联网、计划与协作工具实现
│   ├── collaboration/ # 子代理角色、消息、会话、运行期工具绑定
│   ├── security/      # 工作区策略、输出审核与停止协调
│   └── cli/           # PromptSession / Rich 终端交互
├── config/            # 配置与提示词
├── bootstrap.py       # 显式组装与依赖注入
└── main.py            # CLI 入口
```

内核不能导入适配器、配置或组装入口。具体实现由 `bootstrap.create_runner()`
注入；`bootstrap.create_app()` 在 Runner 外组装审核协调器与 CLI。
完整职责、调用时序与扩展方法见 [架构说明](docs/architecture.md)。

## 已有能力与边界

- 流式文字、工具调用、规划/反思、基于消息数的上下文压缩。
- 单次运行最多 **10 轮**；子代理也受此上限限制，支持角色权限衰减和实时消息。
- 声明工具表、运行允许表与阶段限制共同约束执行；未声明工具不能靠模型臆造调用获得权限。
- 写入、编辑、命令和质量检查必须逐次审批；审批绑定调用 ID 和参数指纹，默认拒绝。
- 文件工具拒绝工作区外路径、敏感文件、符号链接与 Windows reparse point。
- 异步输出审核可以停止后续生成与工具启动，但文字可能在审核前短暂展示；
  不能撤销已发生的副作用。详见 [审核边界](docs/safety.md)。
- `run_command` 是本地高权限 shell，**不是沙箱**；批准后可产生工作区外副作用。
  同步线程中的进程也不保证随协程取消而终止。
- CLI 历史保存在内存中；没有跨进程恢复、持久化审计或任务恢复功能。
- “回读验证”和反思只是策略状态，不等同于相关测试全部通过。

## 验证

```powershell
& .venv\Scripts\python.exe -m ruff check .
& .venv\Scripts\python.exe -m pyright
& .venv\Scripts\python.exe -m pytest -q
```

默认只执行单元及内部集成测试，不调用真实模型、网络或 shell。
外部验证需显式运行，可能产生费用：

```powershell
& .venv\Scripts\python.exe -m pytest --run-external tests\external -q
```

CI 仅保留 Ruff、Pyright 和 Windows 内部测试，不启动服务或数据库。
更多说明见 [测试结构](tests/README.md)。
[CLI 产品需求池](docs/observability-requirements.md) 描述未来目标，不代表已实现。
