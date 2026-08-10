package com.und.rcs.tablet

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.view.View
import android.view.WindowManager
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView

    // 빌드 flavor 로 결정: "console"(관리자 콘솔) / "robot"(로봇 부착 태블릿)
    private val isConsole: Boolean get() = BuildConfig.APP_MODE == "console"

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 화면 항상 켜두기 (WakeLock 대신 FLAG 사용)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        webView = WebView(this)
        setContentView(webView)

        // 전체화면
        hideSystemUI()

        webView.addJavascriptInterface(AndroidBridge(), "Android")

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            cacheMode = WebSettings.LOAD_NO_CACHE
            mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            mediaPlaybackRequiresUserGesture = false
        }

        val prefs = getSharedPreferences("config", Context.MODE_PRIVATE)
        val serverUrl = prefs.getString("server_url", "") ?: ""

        if (isConsole) {
            // 관리자 콘솔 — 서버 주소만 필요
            if (serverUrl.isEmpty()) {
                showConfigDialog(prefs) { url, id -> loadPage(url, id) }
            } else {
                loadPage(serverUrl, "")
            }
        } else {
            // 로봇 부착 태블릿 — 서버 주소 + 로봇 ID
            val robotId = prefs.getString("robot_id", "") ?: ""
            if (serverUrl.isEmpty() || robotId.isEmpty()) {
                showConfigDialog(prefs) { url, id -> loadPage(url, id) }
            } else {
                loadPage(serverUrl, robotId)
            }
        }
    }

    private fun loadPage(serverUrl: String, robotId: String) {
        webView.webViewClient = WebViewClient()
        val base = serverUrl.trimEnd('/')
        val url = if (isConsole) {
            "$base/api/dispatch/console"
        } else {
            "$base/api/dispatch/robot-tablet/$robotId"
        }
        webView.loadUrl(url)
    }

    private fun showConfigDialog(
        prefs: SharedPreferences,
        onConfirm: (String, String) -> Unit
    ) {
        val dp = resources.displayMetrics.density
        val pad = (24 * dp).toInt()

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad / 2, pad, 0)
        }

        fun label(text: String) = TextView(this).apply {
            this.text = text
            textSize = 16f
            setPadding(0, (8 * dp).toInt(), 0, (4 * dp).toInt())
        }

        val etUrl = EditText(this).apply {
            setText(prefs.getString("server_url", "http://192.168.0.21:8002"))
            hint = "http://서버IP:8002"
            textSize = 18f
        }
        layout.addView(label("서버 주소"))
        layout.addView(etUrl)

        // 로봇 태블릿 모드일 때만 로봇 ID 입력란 표시
        val etId: EditText? = if (!isConsole) {
            EditText(this).apply {
                setText(prefs.getString("robot_id", ""))
                hint = "예: 1, 2, 3 … (로봇 ID)"
                textSize = 22f
                inputType = android.text.InputType.TYPE_CLASS_NUMBER
            }.also {
                layout.addView(label("로봇 ID"))
                layout.addView(it)
            }
        } else null

        val title = if (isConsole) "관리자 콘솔 설정" else "로봇 태블릿 설정"

        AlertDialog.Builder(this)
            .setTitle(title)
            .setView(layout)
            .setCancelable(false)
            .setPositiveButton("시작") { _, _ ->
                val url = etUrl.text.toString().trim().trimEnd('/')
                val id = etId?.text?.toString()?.trim() ?: ""
                val ok = url.isNotEmpty() && (isConsole || id.isNotEmpty())
                if (ok) {
                    val edit = prefs.edit().putString("server_url", url)
                    if (!isConsole) edit.putString("robot_id", id)
                    edit.apply()
                    onConfirm(url, id)
                } else {
                    showConfigDialog(prefs, onConfirm)
                }
            }
            .setNeutralButton("설정 초기화") { _, _ ->
                prefs.edit().clear().apply()
                showConfigDialog(prefs, onConfirm)
            }
            .show()
    }

    private fun hideSystemUI() {
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility = (
            View.SYSTEM_UI_FLAG_FULLSCREEN or
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        )
    }

    inner class AndroidBridge {
        @JavascriptInterface
        fun openSettings() {
            runOnUiThread {
                val prefs = getSharedPreferences("config", Context.MODE_PRIVATE)
                showConfigDialog(prefs) { url, id -> loadPage(url, id) }
            }
        }

        @JavascriptInterface
        fun goHome() {
            runOnUiThread {
                val intent = Intent(Intent.ACTION_MAIN).apply {
                    addCategory(Intent.CATEGORY_HOME)
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK
                }
                startActivity(intent)
            }
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemUI()
    }
}
