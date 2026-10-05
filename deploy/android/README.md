# 自闭环 Android App（Chaquopy 内嵌 Python 后端）

> 这是 **Termux-free** 的独立 App：Python 后端（FastAPI）内嵌在 APK 里，点开即用。
> 与 `deploy/apk/`（Capacitor 套壳，依赖 Termux 跑后端）是两个不同方案，本方案是最终形态。

## 架构

```
APK（Chaquopy：Python 3.10 解释器 + backend/ 源码 + pip 依赖）
 └─ MainActivity 后台线程
     ├─ Python.start → main.start_server(filesDir, filesDir/frontend)
     │    └─ FastAPI(uvicorn) 监听 127.0.0.1:<OS动态分配端口>
     │         ├─ /api/*  ← 业务逻辑（指标/选股/回测/LLM对齐）
     │         └─ /       ← StaticFiles 托管 filesDir/frontend（首次从 assets 复制）
     └─ WebView 加载 http://127.0.0.1:<port>/（fetch 相对路径，天然同源）
```

关键决策：
- **动态端口**（port=0 由 OS 分配）：手机上 Termux 版/其他 App 占 8000 也不冲突
- **前端不打 API_BASE 补丁**：WebView 直接加载后端同源地址，`fetch('/api/...')` 原样工作
- **数据库/日志在 App 私有目录**（`filesDir/app.db`、`filesDir/logs/`），卸载即清
- **pydantic 锁 v1.10**：Chaquopy 不支持 pydantic v2 的 Rust 核心（`pydantic-core`）
- **pip 依赖与根 `requirements.txt` 保持一致**（版本改动需同步 `app/build.gradle`）

## 环境要求

| 工具 | 版本 | 说明 |
|---|---|---|
| JDK | 17 | `java -version` 确认 |
| Android SDK | Platform 35 + Build-Tools | Android Studio → SDK Manager（构建时 AGP 可自动补装缺失 platform） |
| Gradle | 8.11.1（wrapper 已随仓库提交） | 无需单独安装 gradle |
| Node.js | 不需要 | 前端无构建步骤 |

## 构建步骤

```bash
cd deploy/android
gradlew.bat assembleDebug     # wrapper 已随仓库提交，clone 即可构建（首次约 2~10 分钟）

# 安装到手机
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

或直接用 **Android Studio** 打开 `deploy/android/` → Build → Build APK(s)。

首次打开 App 会看到 2~5 秒白屏（Python 解释器初始化 + 服务启动），就绪后自动进入前端页面。

## 常见问题

| 症状 | 解法 |
|---|---|
| pip 拉包慢/超时 | `app/build.gradle` 的 `pip { }` 里加 `options "-i", "https://mirrors.aliyun.com/pypi/simple/"`（构建机网络差时） |
| 模拟器白屏/闪退 | `abiFilters` 追加 `"x86_64"`（模拟器架构） |
| `SDK location not found` | Android Studio 打开一次工程生成 `local.properties`，或手建该文件写 `sdk.dir=...` |
| 长时间白屏不进页面 | logcat 过滤 `PythonStdout`/`AndroidRuntime` 看启动异常（首次启动含 SQLite 初始化，最长 15s） |
| 更换 App 图标 | 当前用系统默认图标；在 `app/src/main/res/mipmap-*` 放图标并 Manifest `application` 加 `android:icon="@mipmap/ic_launcher"` |
| 升级版本 | 改 `versionCode`/`versionName`；前端 assets 会按版本号自动重拷（`.version` marker） |
| 与 Termux 版共存 | 无冲突（动态端口）；两者数据库各自独立 |

## Chaquopy 授权

Chaquopy 对个人/开源项目免费（当前许可证），商业用途需商业授权，详见 <https://chaquo.com/chaquopy/license/>。

## 后续打包流程

前端或后端代码改动后重新出包：

```bash
gradlew.bat assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

前端改动只需重跑构建（`prepareFrontendAssets` 任务自动重新拷贝 assets）。
