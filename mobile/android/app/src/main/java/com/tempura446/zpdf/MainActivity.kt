// Copyright (c) 2026 Tempura-446. Licensed under the PolyForm Internal Use License 1.0.0 with a personal-use permission; see LICENSE.
package com.tempura446.zpdf

import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity

/**
 * ZPDF Android Companion App (Phase 1 Foundation).
 *
 * Wraps ZPDF's touch-first PWA / Reader shell in a native Android WebView with
 * system file-picker integration, hardware back-button navigation, and a saved
 * local/LAN companion URL preference.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null

    private val fileChooserLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val uris = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
            filePathCallback?.onReceiveValue(uris)
            filePathCallback = null
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

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                val url = request?.url ?: return false
                val host = url.host ?: ""
                val isLocal = host == "127.0.0.1" || host == "localhost" || host == "10.0.2.2" ||
                    host.startsWith("192.168.") || host.startsWith("10.")
                if (isLocal) return false
                startActivity(Intent(Intent.ACTION_VIEW, url))
                return true
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
                fileChooserLauncher.launch(intent)
                return true
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })

        val savedUrl = getPreferences(Context.MODE_PRIVATE).getString(KEY_SERVER_URL, DEFAULT_URL) ?: DEFAULT_URL
        webView.loadUrl(savedUrl)
    }

    fun promptServerUrl() {
        val prefs = getPreferences(Context.MODE_PRIVATE)
        val input = EditText(this).apply {
            setText(prefs.getString(KEY_SERVER_URL, DEFAULT_URL))
        }
        AlertDialog.Builder(this)
            .setTitle("ZPDF Companion Server URL")
            .setMessage("Enter your local or LAN ZPDF address (e.g. http://10.0.2.2:8765):")
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
    }
}
