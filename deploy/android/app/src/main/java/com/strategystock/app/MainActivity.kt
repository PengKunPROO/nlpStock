package com.strategystock.app

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebViewClient
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import kotlin.concurrent.thread

/**
 * 自闭环 App 壳：Activity 内嵌 Python 后端（Chaquopy）+ WebView 前端，无 Termux 依赖。
 *
 * 启动流程（全部在 py-server 线程）：
 *  1. Python.start —— 必须与 uvicorn 服务同线程（该线程成为 Python 主线程，否则 set_wakeup_fd 报错）
 *  2. copyAssetsFrontend —— 把 assets 里的前端复制到私有目录（StaticFiles 需要真实文件路径）
 *  3. main.start_server —— 启动 FastAPI（端口由 OS 分配，返回实际端口）
 *  4. loadWhenReady —— 轮询 /api/health 就绪后加载 WebView（前端 fetch 相对路径，天然同源）
 */
class MainActivity : Activity() {
    private lateinit var webView: WebView

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        webView = WebView(this)
        setContentView(webView)
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
        }
        webView.webViewClient = object : WebViewClient() {
            private var retries = 0
            override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
                // 后端未就绪（连接拒绝）时自动重试，直到 uvicorn 起来（最多 60 次 × 1s）
                if (request?.isForMainFrame == true && retries < 60) {
                    retries++
                    view?.postDelayed({ view.loadUrl(BASE_URL) }, 1000)
                }
            }
        }

        thread(name = "py-server") {
            if (!Python.isStarted()) Python.start(AndroidPlatform(this@MainActivity))
            val py = Python.getInstance()
            val filesDir = filesDir.absolutePath
            val frontendDir = "$filesDir/frontend"
            copyAssetsFrontend(frontendDir)
            // 阻塞：uvicorn 的 asyncio 事件循环必须在 Python 主线程（本线程）运行，
            // 否则 Android 的 SelectorEventLoop 会报 set_wakeup_fd 错误（见 main.py 注释）
            py.getModule("main").callAttr("start_server", filesDir, frontendDir)
        }

        // 主线程立即加载，服务未就绪时的失败由 onReceivedError 自动重试兜底
        webView.loadUrl(BASE_URL)
    }

    companion object {
        private const val BASE_URL = "http://127.0.0.1:8123/"  // 与 main.py 的 _PORT 一致
    }

    /** 首次启动/版本升级时把 assets 里的前端复制到私有目录。 */
    private fun copyAssetsFrontend(targetDirPath: String) {
        val targetDir = File(targetDirPath)
        val version = packageManager.getPackageInfo(packageName, 0).versionName ?: "?"
        val marker = File(targetDir, ".version")
        if (marker.exists() && marker.readText() == version) return
        if (targetDir.exists()) targetDir.deleteRecursively()
        targetDir.mkdirs()
        copyAssetDir("", targetDir)
        marker.writeText(version)
    }

    private fun copyAssetDir(path: String, target: File) {
        val names = assets.list(path) ?: return
        for (name in names) {
            val assetPath = if (path.isEmpty()) name else "$path/$name"
            val outFile = File(target, assetPath)
            if (!assets.list(assetPath).isNullOrEmpty()) {  // 子目录
                outFile.mkdirs()
                copyAssetDir(assetPath, target)
            } else {  // 文件
                outFile.parentFile?.mkdirs()
                assets.open(assetPath).use { input ->
                    outFile.outputStream().use { input.copyTo(it) }
                }
            }
        }
    }

    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        if (webView.canGoBack()) webView.goBack() else super.onBackPressed()
    }
}
