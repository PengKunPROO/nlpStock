# 原生 APK 打包方案（调研结论与脚手架）

## 背景

本机无 Android SDK，无法在此构建/验证 APK。以下为调研结论 + 可直接在你侧构建的脚手架。

## 方案对比（网页应用 → APK）

| 方案 | 原理 | 适合场景 | 结论 |
|---|---|---|---|
| **Capacitor**（本目录采用） | 把 `frontend/` 打包进原生 WebView，后端仍跑 Termux `localhost:8000` | 需要真·APK 图标/全屏/离线外壳，且可复用现有前端 | ✅ 推荐 |
| TWA / Bubblewrap（PWABuilder） | 把 PWA 清单封装成 Android Trusted Web Activity，实际仍是 Chrome 渲染 | 纯 PWA、不想维护原生工程 | 备选；同样需要 Android SDK 构建 |
| Chaquopy / Kivy | 把 Python 后端内嵌进 APK，彻底脱离 Termux | 想要完全独立的 App | 复杂度高，暂不采用 |

**关键约束（Capacitor 官方文档明确）**：`server.url`（WebView 加载外部 URL）**不支持生产环境**，仅限开发热重载。因此生产 APK 采用「**打包前端 + 本地后端 + CORS**」架构，而非 `server.url` 指向 `localhost:8000`。

## 架构

```
APK（Capacitor WebView，内含 frontend/ 静态文件）
   └─ fetch(window.API_BASE + '/api/...')   ← api.js 已支持 window.API_BASE 覆盖
        └─ 后端 FastAPI（Termux 跑在 localhost:8000，已开启 CORS）
```

前端在打包时把 `window.API_BASE = 'http://localhost:8000'`（见下「构建步骤」第 5 步）。

## 构建步骤（需 Android Studio / SDK + JDK17）

```bash
cd deploy/apk
npm install                      # 安装 @capacitor/core @capacitor/cli @capacitor/android

# 注入 API 基址：在 frontend/index.html 的 <head> 加一行（仅 APK 用）
#   <script>window.API_BASE='http://localhost:8000';</script>

npx cap add android              # 生成 android/ 原生工程
npx cap sync                     # 把 frontend/ 拷进 android/app/src/main/assets/public
npx cap open android             # 用 Android Studio 打开
# 在 Android Studio: Build → Build App Bundle / APK
```

### Android 明文 HTTP 白名单（仅允许回环地址）

Android API 28+ 默认禁止明文 HTTP。需在 `android/app/src/main/AndroidManifest.xml` 的 `<application>` 上加：

```xml
android:usesCleartextTraffic="true"
```

（个人本地工具可接受；若走公网请改为 HTTPS 并把后端反代到 443。）

### 前端打包前注入

编辑 `frontend/index.html`，在 `<head>` 内加一行：

```html
<script>window.API_BASE='http://localhost:8000';</script>
```

（Termux/浏览器 PWA 场景无需此注入，同源 `fetch('/api/...')` 即可。）

## 运行前提

APK 是「外壳」，后端仍需在手机上运行：打开 Termux 执行 `bash deploy/termux_bootstrap.sh`，保持 `localhost:8000` 在跑，再打开 App。

## 若不想用 APK

直接用 PWA：手机浏览器打开 `http://localhost:8000` → 菜单 →「添加到主屏幕」，即可全屏、独立图标、无构建成本。这是当前默认推荐路径。
