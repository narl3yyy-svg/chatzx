package com.chatzx.android;

import android.os.Bundle;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebSettings;
import android.webkit.PermissionRequest;
import android.webkit.WebChromeClient;
import android.widget.Toast;
import android.app.AlertDialog;

import androidx.appcompat.app.AppCompatActivity;

import com.chaquo.python.Python;
import com.chaquo.python.PyObject;

public class MainActivity extends AppCompatActivity {
    private WebView webView;
    private String serverUrl = "http://127.0.0.1:8742";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        webView = new WebView(this);
        setContentView(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        settings.setAllowContentAccess(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                view.loadUrl(serverUrl);
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                request.grant(request.getResources());
            }
        });

        startPythonServer();
    }

    private void startPythonServer() {
        new Thread(() -> {
            try {
                Python python = Python.getInstance();
                PyObject module = python.getModule("main");
                PyObject result = module.callAttr("start_server");
                String host = result.asList().get(0).toString();
                String port = result.asList().get(1).toString();
                if (host != null && !host.equals("None")) {
                    serverUrl = "http://" + host + ":" + port;
                    runOnUiThread(() -> {
                        webView.loadUrl(serverUrl);
                        Toast.makeText(this, "chatxz starting...", Toast.LENGTH_SHORT).show();
                    });
                } else {
                    String error = port;
                    runOnUiThread(() -> {
                        new AlertDialog.Builder(this)
                            .setTitle("Server Error")
                            .setMessage(error)
                            .setPositiveButton("OK", null)
                            .show();
                    });
                }
            } catch (Exception e) {
                String msg = e.getMessage();
                if (msg == null) msg = "Unknown error (null message)";
                String stack = android.util.Log.getStackTraceString(e);
                final String fullError = msg + "\n\n" + stack;
                runOnUiThread(() -> {
                    new AlertDialog.Builder(this)
                        .setTitle("Python Error")
                        .setMessage(fullError)
                        .setPositiveButton("OK", null)
                        .show();
                });
            }
        }).start();
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
