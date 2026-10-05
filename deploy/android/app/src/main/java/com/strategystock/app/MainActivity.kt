package com.strategystock.app

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.util.Log
import android.webkit.ConsoleMessage
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

/**
 * 自闭环 App 壳：Activity 内嵌 Python 后端（Chaquopy）+ WebView 前端，无 Termux 依赖。
 *
 * 启动流程（两个线程）：
 *  - py-server：Python.start（必须与 uvicorn 服务同线程，该线程成为 Python 主线程）
 *               → copyAssetsFrontend → main.start_server（阻塞运行 server.run）
 *  - wait-ready：socket 探测 8123 端口，服务就绪后切主线程 loadUrl（避免时序竞态）
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
            cacheMode = WebSettings.LOAD_NO_CACHE  // 不缓存：确保加载最新前端，避免旧 index.html（含 API_BASE 注入）被缓存
        }
        webView.clearCache(true)  // 清除历史缓存（首次可能缓存过含注入的旧页面）
        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedError(view: WebView?, request: WebResourceRequest?, error: WebResourceError?) {
                Log.e("WebNav", "加载失败: code=${error?.errorCode} desc=${error?.description} url=${request?.url}")
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(msg: ConsoleMessage): Boolean {
                Log.i("WebConsole", "[${msg.messageLevel()}] ${msg.message()}")
                return true
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
        thread(name = "wait-ready") {
            waitAndLoad()
        }
    }

    /** HTTP 探测 health 200（服务完全就绪），成功才加载 WebView（最多 30s，超时也尝试）。 */
    private fun waitAndLoad() {
        for (i in 0..149) {
            if (isHealthOk()) {
                Log.i("WebBoot", "health 200 就绪（第 ${i + 1} 次探测），加载 WebView")
                runOnUiThread { webView.loadUrl(BASE_URL) }
                return
            }
            Thread.sleep(200)
        }
        Log.w("WebBoot", "30 秒超时，服务未就绪，强制加载")
        runOnUiThread { webView.loadUrl(BASE_URL) }  // 超时兜底（显示连接错误页）
    }

    /** 用 HttpURLConnection 请求 /api/health，返回 200 才算服务就绪（比 socket TCP 探测更严格）。 */
    private fun isHealthOk(): Boolean {
        return try {
            val conn = URL("http://localhost:8123/api/health").openConnection() as HttpURLConnection
            conn.connectTimeout = 500
            conn.readTimeout = 500
            val ok = conn.responseCode == 200
            conn.disconnect()
            ok
        } catch (e: Exception) {
            false
        }
    }

    companion object {
        // 用 localhost（而非 127.0.0.1）：Android 优先 IPv6 把 loopback 解析为 ::1，
        // localhost 让系统动态解析到与 uvicorn（绑定 "::" 双栈）一致的地址
        private const val BASE_URL = "http://localhost:8123/"
    }

    /** 每次启动重新复制 assets 里的前端到私有目录（确保前端最新，避免版本 marker 残留旧文件）。 */
    private fun copyAssetsFrontend(targetDirPath: String) {
        val targetDir = File(targetDirPath)
        if (targetDir.exists()) targetDir.deleteRecursively()
        targetDir.mkdirs()
        copyAssetDir("", targetDir)
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
