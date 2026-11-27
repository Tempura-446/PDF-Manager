// Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
package com.tempura446.zpdf

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.OpenableColumns
import android.util.Base64
import android.webkit.JavascriptInterface
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject

/**
 * ZPDF Android Companion App (Phase 2).
 *
 * Wraps ZPDF's touch-first PWA / Reader shell in a native Android WebView with:
 * - Native file-picker integration (PDF & CBZ)
 * - Incoming ACTION_VIEW application/pdf intent handoff to the Reader
 * - Clean offline reconnect screen with ZpdfAndroid.openSettings() JS bridge
 * - Saved local / LAN companion server URL preference
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null
    private var pendingPdfUri: Uri? = null

    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val uris = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
            filePathCallback?.onReceiveValue(uris)
            filePathCallback = null
        }

    inner class ZpdfAndroidBridge {
        @JavascriptInterface
        fun openSettings() {
            runOnUiThread { promptServerUrl() }
        }

        @JavascriptInterface
        fun retryConnection() {
            runOnUiThread {
                val url = getPreferences(Context.MODE_PRIVATE).getString(KEY_SERVER_URL, DEFAULT_URL) ?: DEFAULT_URL
                webView.loadUrl(url)
            }
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        webView = WebView(this)
        setContentView(webView)

        with(webView.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = true
            builtInZoomControls = true
            displayZoomControls = false
            useWideViewPort = true
            loadWithOverviewMode = true
        }

        webView.addJavascriptInterface(ZpdfAndroidBridge(), "ZpdfAndroid")
        webView.setDownloadListener { dlUrl, _, _, _, _ ->
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(dlUrl)))
            } catch (_: Exception) {
                // Ignore if no handler available
            }
        }

        webView.webViewClient = object : WebViewClient() {
            private fun isPrivateLanHost(host: String): Boolean {
                if (host == "127.0.0.1" || host == "localhost" || host == "10.0.2.2" || host.endsWith(".local")) {
                    return true
                }
                val parts = host.split(".")
                if (parts.size != 4) return false
                val octets = parts.map { it.toIntOrNull() ?: return false }
                if (octets.any { it !in 0..255 }) return false
                return octets[0] == 10 ||
                    (octets[0] == 192 && octets[1] == 168) ||
                    (octets[0] == 172 && octets[1] in 16..31)
            }

            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url ?: return false
                val host = (url.host ?: "").lowercase()
                if (isPrivateLanHost(host)) return false
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, url))
                } catch (_: Exception) {
                    // Ignore unhandleable external URLs
                }
                return true
            }

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                if (url == null || url == "about:blank" || url.startsWith("data:")) return
                pendingPdfUri?.let { uri ->
                    pendingPdfUri = null
                    deliverPdfIntentToWebView(uri)
                }
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                if (request?.isForMainFrame == true) {
                    val savedUrl = getPreferences(Context.MODE_PRIVATE).getString(KEY_SERVER_URL, DEFAULT_URL) ?: DEFAULT_URL
                    val escapedUrl = android.text.TextUtils.htmlEncode(savedUrl)
                    val html = """
                        <!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
                        <style>body{font-family:system-ui,sans-serif;background:#111318;color:#f3f4f6;display:grid;place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center}
                        .card{max-width:360px;background:#1b1f27;border:1px solid #2e3440;border-radius:14px;padding:24px}
                        button{margin:8px 4px;padding:12px 18px;border-radius:10px;border:0;font-size:15px;font-weight:600;cursor:pointer}
                        .pri{background:#d93829;color:#fff}.sec{background:#2e3440;color:#f3f4f6}code{color:#93c5fd}</style></head>
                        <body><div class="card"><h2>ZPDF Desktop Not Reachable</h2>
                        <p>Start ZPDF on your computer with LAN companion mode enabled, or update the server address.</p>
                        <p><code>$escapedUrl</code></p>
                        <button class="pri" onclick="ZpdfAndroid.retryConnection()">Retry</button>
                        <button class="sec" onclick="ZpdfAndroid.openSettings()">Change URL</button>
                        </div></body></html>
                    """.trimIndent()
                    view?.loadDataWithBaseURL(null, html, "text/html", "utf-8", null)
                }
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                webView: WebView?,
                filePathCallback: ValueCallback<Array<Uri>>?,
                fileChooserParams: FileChooserParams?
            ): Boolean {
                this@MainActivity.filePathCallback?.onReceiveValue(null)
                this@MainActivity.filePathCallback = filePathCallback
                val intent = fileChooserParams?.createIntent() ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                    type = "application/pdf"
                    addCategory(Intent.CATEGORY_OPENABLE)
                }
                return try {
                    fileChooserLauncher.launch(intent)
                    true
                } catch (_: Exception) {
                    this@MainActivity.filePathCallback?.onReceiveValue(null)
                    this@MainActivity.filePathCallback = null
                    false
                }
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })

        if (intent?.action == Intent.ACTION_VIEW && intent.data != null) {
            pendingPdfUri = intent.data
        }

        val savedUrl = getPreferences(Context.MODE_PRIVATE).getString(KEY_SERVER_URL, DEFAULT_URL) ?: DEFAULT_URL
        webView.loadUrl(savedUrl)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (intent.action == Intent.ACTION_VIEW && intent.data != null) {
            deliverPdfIntentToWebView(intent.data!!)
        }
    }

    private fun deliverPdfIntentToWebView(uri: Uri) {
        Thread {
            try {
                val out = java.io.ByteArrayOutputStream()
                val buf = ByteArray(65536)
                var total = 0
                val opened = contentResolver.openInputStream(uri)?.use { stream ->
                    while (true) {
                        val n = stream.read(buf)
                        if (n <= 0) break
                        total += n
                        if (total > MAX_INTENT_PDF_BYTES) return@Thread
                        out.write(buf, 0, n)
                    }
                    true
                } ?: false
                if (!opened) return@Thread
                val b64 = Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP)
                var name = "document.pdf"
                contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
                    if (c.moveToFirst()) name = c.getString(0) ?: name
                }
                val js = "window.zpdfOpenBase64Pdf && window.zpdfOpenBase64Pdf(${JSONObject.quote(b64)}, ${JSONObject.quote(name)});"
                runOnUiThread { webView.evaluateJavascript(js, null) }
            } catch (_: Exception) {
                // Ignore unreadable external URIs cleanly
            }
        }.start()
    }

    fun promptServerUrl() {
        val prefs = getPreferences(Context.MODE_PRIVATE)
        val input = EditText(this).apply {
            setText(prefs.getString(KEY_SERVER_URL, DEFAULT_URL))
        }
        AlertDialog.Builder(this)
            .setTitle("ZPDF Companion Server URL")
            .setMessage("Enter your local or LAN ZPDF address (e.g. http://192.168.1.20:8765/#token=...):")
            .setView(input)
            .setPositiveButton("Connect") { _, _ ->
                val url = input.text.toString().trim()
                if (url.startsWith("http://") || url.startsWith("https://")) {
                    prefs.edit().putString(KEY_SERVER_URL, url).apply()
                    webView.loadUrl(url)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    companion object {
        private const val KEY_SERVER_URL = "zpdf_server_url"
        private const val DEFAULT_URL = "http://10.0.2.2:8765/"
        private const val MAX_INTENT_PDF_BYTES = 32 * 1024 * 1024
    }
}
