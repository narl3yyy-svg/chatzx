package com.chatzx.android;

import android.Manifest;
import android.content.Context;
import android.content.pm.PackageManager;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebSettings;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.widget.Toast;
import android.app.AlertDialog;

import androidx.annotation.NonNull;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

import com.chaquo.python.Python;
import com.chaquo.python.PyObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.util.ArrayList;
import java.util.List;

public class MainActivity extends AppCompatActivity {
    private static final int PERM_REQUEST = 1001;

    private WebView webView;
    private WifiManager.MulticastLock multicastLock;
    private String serverUrl = "http://127.0.0.1:8742";
    private boolean serverStarted = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        Thread.setDefaultUncaughtExceptionHandler((thread, throwable) -> {
            try {
                StringWriter sw = new StringWriter();
                PrintWriter pw = new PrintWriter(sw);
                throwable.printStackTrace(pw);
                String stack = sw.toString();
                FileOutputStream fos = openFileOutput("crash_log.txt", MODE_PRIVATE);
                fos.write(stack.getBytes());
                fos.close();
                runOnUiThread(() -> showError("App Error", stack));
            } catch (Exception ignored) {}
        });

        acquireMulticastLock();

        webView = new WebView(this);
        setContentView(webView);
        setupWebView();

        showLoading("Starting chatxz...");
        requestNeededPermissions();
    }

    private void acquireMulticastLock() {
        try {
            WifiManager wifi = (WifiManager) getApplicationContext().getSystemService(Context.WIFI_SERVICE);
            if (wifi != null) {
                multicastLock = wifi.createMulticastLock("chatxz");
                multicastLock.setReferenceCounted(true);
                multicastLock.acquire();
            }
        } catch (Exception ignored) {}
    }

    private void setupWebView() {
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);

        webView.setWebViewClient(new WebViewClient() {
            private int retryCount = 0;

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                if (retryCount < 15) {
                    retryCount++;
                    view.postDelayed(() -> view.loadUrl(serverUrl), 1500);
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                request.grant(request.getResources());
            }
        });
    }

    private void showLoading(String message) {
        String html = "<html><body style='background:#080a0f;color:#eef1f6;display:flex;"
            + "align-items:center;justify-content:center;height:100vh;margin:0;"
            + "font-family:sans-serif;font-size:18px'>" + escapeHtml(message) + "</body></html>";
        webView.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null);
    }

    private void showError(String title, String message) {
        String shortMsg = message.length() > 3500 ? message.substring(message.length() - 3500) : message;
        String html = "<html><body style='background:#080a0f;color:#eef1f6;padding:20px;"
            + "font-family:monospace;font-size:12px;white-space:pre-wrap'>"
            + "<h2 style='color:#ff6b7a;font-family:sans-serif'>" + escapeHtml(title) + "</h2>"
            + escapeHtml(shortMsg) + "</body></html>";
        webView.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null);
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(shortMsg.length() > 2000 ? shortMsg.substring(shortMsg.length() - 2000) : shortMsg)
            .setPositiveButton("OK", null)
            .show();
    }

    private static String escapeHtml(String s) {
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
    }

    private void requestNeededPermissions() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            startPythonServer();
            return;
        }

        List<String> needed = new ArrayList<>();
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.RECORD_AUDIO);
        }
        // POST_NOTIFICATIONS only exists on Android 13+ — requesting it earlier crashes the app.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS);
        }

        if (needed.isEmpty()) {
            startPythonServer();
        } else {
            ActivityCompat.requestPermissions(this, needed.toArray(new String[0]), PERM_REQUEST);
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions,
                                           @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERM_REQUEST) {
            startPythonServer();
        }
    }

    private synchronized void startPythonServer() {
        if (serverStarted) return;
        serverStarted = true;

        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    throw new IllegalStateException("Python was not started in ChatxzApplication");
                }
                Python python = Python.getInstance();
                PyObject module = python.getModule("main");
                PyObject result = module.callAttr("start_server");
                String host = result.asList().get(0).toString();
                String port = result.asList().get(1).toString();
                if (host != null && !host.equals("None")) {
                    serverUrl = "http://" + host + ":" + port;
                    runOnUiThread(() -> {
                        webView.loadUrl(serverUrl);
                        Toast.makeText(this, "chatxz ready", Toast.LENGTH_SHORT).show();
                    });
                } else {
                    final String error = port;
                    File crashFile = new File(getFilesDir(), "python_crash_log.txt");
                    try {
                        FileOutputStream fos = new FileOutputStream(crashFile);
                        fos.write(error.getBytes());
                        fos.close();
                    } catch (Exception ignored) {}
                    runOnUiThread(() -> showError("Server Error", error));
                }
            } catch (Exception e) {
                String stack = android.util.Log.getStackTraceString(e);
                final String fullError = (e.getMessage() != null ? e.getMessage() : "Python error") + "\n\n" + stack;
                File crashFile = new File(getFilesDir(), "python_crash_log.txt");
                try {
                    FileOutputStream fos = new FileOutputStream(crashFile);
                    fos.write(fullError.getBytes());
                    fos.close();
                } catch (Exception ignored) {}
                runOnUiThread(() -> showError("Python Error", fullError));
            }
        }, "chatxz-python").start();
    }

    @Override
    protected void onDestroy() {
        if (multicastLock != null && multicastLock.isHeld()) {
            multicastLock.release();
        }
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        webView.evaluateJavascript(
            "(function(){if(typeof closeSidebar==='function'&&document.body.classList.contains('sidebar-open')){closeSidebar();return 'true';}return 'false';})()",
            value -> {
                if (!"true".equals(value)) {
                    if (webView.canGoBack()) {
                        webView.goBack();
                    } else {
                        super.onBackPressed();
                    }
                }
            }
        );
    }
}