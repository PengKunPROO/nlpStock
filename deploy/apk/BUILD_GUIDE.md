# APK 编译指南（Capacitor 7 → Android APK，手把手）

> 前置结论（详见本目录 README.md）：APK = Capacitor WebView 外壳（打包 `frontend/` 静态文件），后端仍跑在手机 Termux 的 `localhost:8000`。本指南从零环境到手机安装，全程可照抄。
>
> **不想编译 APK？** 手机浏览器打开 `http://localhost:8000` → 菜单 →「添加到主屏幕」（PWA），效果接近且零成本。APK 仅在你需要独立图标/全屏外壳时才有必要。

## 0. 你需要准备什么

| 工具 | 版本要求 | 用途 | 获取方式 |
|---|---|---|---|
| JDK | **17**（Temurin 推荐） | Gradle/Android 编译 | <https://adoptium.net/> 选 Temurin 17 (LTS) |
| Android Studio | 最新稳定版 | SDK 管理 + 首次构建（也可纯命令行） | <https://developer.android.com/studio> |
| Android SDK | Platform **35**、Build-Tools、Platform-Tools | 编译目标 | Android Studio → SDK Manager 安装 |
| Node.js | ≥ 18 | npm / Capacitor CLI | <https://nodejs.org/> |
| 本项目 | 已 clone | — | `git clone https://github.com/PengKunPROO/nlpStock.git` |

磁盘需求：Android Studio + SDK 约 8~10 GB。

## 1. 环境变量（Windows）

安装 Android Studio 后确认/新增（PowerShell，或「系统属性→环境变量」图形界面）：

```powershell
# JAVA_HOME → JDK 17 安装目录（示例，以实际路径为准）
setx JAVA_HOME "C:\Program Files\Eclipse Adoptium\jdk-17.x.x-hotspot"
# ANDROID_HOME → SDK 目录（Android Studio 默认装在这里）
setx ANDROID_HOME "%LOCALAPPDATA%\Android\Sdk"
# PATH 追加（图形界面操作则把下面两段追加到 Path）
setx PATH "$env:PATH;%ANDROID_HOME%\platform-tools;%ANDROID_HOME%\build-tools"
```

改完**重开终端**验证：

```powershell
java -version          # 应显示 17.x
adb --version          # 应显示 Android Debug Bridge 版本
```

> 注意：Android Studio 首次启动会引导装 SDK；若命令行 `sdkmanager` 报找不到，先在 Android Studio 里完成一次 SDK 初始化（装 Platform 35 + Build-Tools + Platform-Tools）。

## 2. 构建（一次性流程）

```bash
cd deploy/apk
npm install                      # 安装 @capacitor/core/cli/android（首次，约1分钟）
```

**第 1 步：注入后端地址**（仅 APK 需要，构建后还原）。编辑 `frontend/index.html`，在 `<head>` 内第一行加：

```html
<script>window.API_BASE='http://localhost:8000';</script>
```

**第 2 步：生成原生工程**（首次执行；已存在 `android/` 目录则跳过）：

```bash
npx cap add android
```

**第 3 步：允许明文 HTTP**（Android 9+ 默认禁止 http://）。编辑 `deploy/apk/android/app/src/main/AndroidManifest.xml`，在 `<application` 标签上加属性：

```xml
<application
    android:usesCleartextTraffic="true"
    ... 其余属性保持不变 ...>
```

（个人本地工具可接受；若走公网请改 HTTPS。）

**第 4 步：同步前端 + 构建**：

```bash
npx cap sync                     # 拷贝 frontend/ → android/app/src/main/assets/public
```

然后二选一：

- **GUI**：`npx cap open android` → Android Studio 打开 → 菜单 `Build → Build App Bundle(s)/APK(s) → Build APK(s)`
- **命令行**：

```bash
cd android
gradlew.bat assembleDebug        # Windows；首次编译约 5~15 分钟
```

**产物**：`deploy/apk/android/app/build/outputs/apk/debug/app-debug.apk`

**第 5 步：还原注入**。把 `frontend/index.html` 里的 `API_BASE` 那行删掉（避免影响 Termux/PWA 同源模式），提交代码前确认已还原。

## 3. 安装到手机

```bash
# USB 连接手机，开启「开发者选项 → USB 调试」
adb devices                      # 应看到设备号
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

或直接把 `app-debug.apk` 传到手机（微信/QQ/网盘均可）→ 文件管理器点开安装 → 允许「未知来源」。

## 4. 运行（APK 只是外壳，后端要在手机上跑）

```bash
# 手机打开 Termux：
cd nlpStock                      # 项目在手机上的路径
bash deploy/termux_bootstrap.sh  # 启动后端 localhost:8000 并常驻
```

保持 Termux 在后台（Acquire wakelock 免杀后台），再打开桌面上的「策略选股」App。

## 5. 签名（可选：仅正式分发需要）

`app-debug.apk` 自带 debug 签名，自装自用完全够。要发布给别人装且可升级覆盖，做正式签名：

```bash
# 1) 生成密钥（一次，妥善保管，丢失则无法覆盖升级）
keytool -genkey -v -keystore strategystock.keystore -alias strategystock \
  -keyalg RSA -keysize 2048 -validity 10000

# 2) 在 android/app/build.gradle 的 android {} 里加：
#    signingConfigs { release { storeFile file('../../strategystock.keystore')
#        storePassword '你的密码' keyAlias 'strategystock' keyPassword '你的密码' } }
#    buildTypes { release { signingConfig signingConfigs.release ... } }

# 3) 构建 release 包
cd android && gradlew.bat assembleRelease
```

## 6. 前端更新后重新打包

```bash
# 1) 重新注入 API_BASE（见第2步）→ 2) 同步 → 3) 构建 → 4) 还原注入
npx cap sync
cd android && gradlew.bat assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## 7. 常见问题排查

| 症状 | 原因与解法 |
|---|---|
| `SDK location not found` | 未设 `ANDROID_HOME`，或 `android/local.properties` 缺失。用 Android Studio 打开一次工程会自动生成；或手建 `local.properties` 写 `sdk.dir=C:\\Users\\你\\AppData\\Local\\Android\\Sdk` |
| `Unsupported class file major version` | JDK 版本不对：Capacitor 7 需 **JDK 17**，`java -version` 确认 |
| `Failed to install the following Android SDK packages` | SDK Platform 35 / Build-Tools 未装：Android Studio → SDK Manager 勾选安装 |
| App 打开白屏 | 后端没在跑（Termux 启动它）；或 `API_BASE` 未注入；或 cleartext 未放行（第3步） |
| 请求全部失败 | Termux 后端没启动 / 端口不是 8000 / Termux 被系统杀后台（`termux-wake-lock`） |
| `gradlew.bat` 卡在下载 Gradle | 网络问题：配置镜像（`gradle/wrapper/gradle-wrapper.properties` 的 distributionUrl 换腾讯镜像 `https://mirrors.cloud.tencent.com/gradle/`）或挂代理 |
| 装不上提示解析失败 | 传输过程文件损坏，重新传输；或 Android 版本低于最低要求 |

## 8. 验证清单

- [ ] `java -version` = 17，`adb --version` 正常
- [ ] `npx cap sync` 无报错，`assets/public/index.html` 含 `API_BASE` 注入
- [ ] `gradlew.bat assembleDebug` BUILD SUCCESSFUL
- [ ] 手机 Termux 后端在跑：手机浏览器访问 `http://localhost:8000` 能打开
- [ ] App 打开 → 策略列表正常加载（说明 WebView → Termux 后端链路通）
