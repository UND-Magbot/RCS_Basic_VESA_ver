package com.und.rcs.tablet

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
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
        // VESA: 태블릿은 슬롯 번호(1, 2, 3 …)에 고정. 슬롯 ↔ POI 매핑은 서버 + 태블릿 페이지에서 관리
        val slotNumber = prefs.getString("slot_number", "") ?: ""

        if (serverUrl.isEmpty() || slotNumber.isEmpty()) {
            showConfigDialog(prefs) { url, id -> loadTablet(url, id) }
        } else {
            loadTablet(serverUrl, slotNumber)
        }
    }

    private fun loadTablet(serverUrl: String, slotNumber: String) {
        webView.webViewClient = WebViewClient()
        // 슬롯 URL — 백엔드가 슬롯 매핑 조회. 매핑 없으면 페이지 내에서 설정 UI 노출
        webView.loadUrl("${serverUrl.trimEnd('/')}/api/dispatch/tablet/$slotNumber")
    }

    private fun showConfigDialog(
        prefs: android.content.SharedPreferences,
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
        val etId = EditText(this).apply {
            setText(prefs.getString("slot_number", ""))
            hint = "예: 1, 2, 3 …"
            textSize = 22f
            inputType = android.text.InputType.TYPE_CLASS_NUMBER
        }

        layout.addView(label("서버 주소"))
        layout.addView(etUrl)
        layout.addView(label("슬롯 번호"))
        layout.addView(etId)

        AlertDialog.Builder(this)
            .setTitle("태블릿 설정 (슬롯 기반)")
            .setView(layout)
            .setCancelable(false)
            .setPositiveButton("시작") { _, _ ->
                val url = etUrl.text.toString().trim().trimEnd('/')
                val id = etId.text.toString().trim()
                if (url.isNotEmpty() && id.isNotEmpty()) {
                    prefs.edit()
                        .putString("server_url", url)
                        .putString("slot_number", id)
                        .apply()
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
                showConfigDialog(prefs) { url, id -> loadTablet(url, id) }
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
