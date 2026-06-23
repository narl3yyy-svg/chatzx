package com.chatxz.android;

import android.Manifest;
import android.annotation.SuppressLint;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.net.Uri;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.DocumentsContract;
import android.provider.Settings;
import android.webkit.ValueCallback;
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
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

import android.app.NotificationChannel;
import android.app.NotificationManager;

import com.chaquo.python.Python;
import com.chaquo.python.PyObject;

import android.database.Cursor;
import android.content.ContentResolver;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

public class MainActivity extends AppCompatActivity {
    private static final int PERM_REQUEST = 1001;
    private static final int REQ_AUDIO = 1002;
    private static final int REQ_FOLDER = 1003;
    private static final int REQ_FILE = 1004;
    private static final int REQ_SEND_FOLDER = 1005;
    private static final String MSG_CHANNEL_ID = "chatxz_messages";
    private static int notificationId = 2000;

    private WebView webView;
    private ValueCallback<Uri[]> filePathCallback;
    private PermissionRequest pendingWebPermissionRequest;
    private WifiManager.MulticastLock multicastLock;
    private UsbPermissionReceiver usbPermissionReceiver;
    private String serverUrl = "http://127.0.0.1:8742";
    private static boolean serverStarted = false;
    private static boolean webViewLoaded = false;

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
        registerUsbPermissionReceiver();
        handleAttachedUsbDevice(getIntent());

        webView = new WebView(this);
        setContentView(webView);
        setupWebView();

        showLoading("Starting chatxz...");
        requestNeededPermissions();
    }

    private void registerUsbPermissionReceiver() {
        if (usbPermissionReceiver != null) {
            return;
        }
        usbPermissionReceiver = new UsbPermissionReceiver();
        IntentFilter filter = new IntentFilter(UsbSerialHelper.ACTION_USB_PERMISSION);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(usbPermissionReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(usbPermissionReceiver, filter);
        }
    }

    private void handleAttachedUsbDevice(Intent intent) {
        if (intent == null) {
            return;
        }
        UsbDevice device = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
        if (device == null) {
            return;
        }
        UsbSerialHelper.requestPermission(device.getDeviceName());
    }

    public void restartApp() {
        recreate();
    }

    public void showMessageNotification(String title, String body) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        ensureMessageNotificationChannel();
        String safeTitle = title != null && !title.isEmpty() ? title : "chatxz";
        String safeBody = body != null ? body : "New message";
        NotificationCompat.Builder builder = new NotificationCompat.Builder(this, MSG_CHANNEL_ID)
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentTitle(safeTitle)
                .setContentText(safeBody)
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .setAutoCancel(true);
        try {
            NotificationManagerCompat.from(this).notify(notificationId++, builder.build());
        } catch (SecurityException ignored) {}
    }

    private void ensureMessageNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                MSG_CHANNEL_ID,
                "Messages",
                NotificationManager.IMPORTANCE_DEFAULT
        );
        channel.setDescription("Incoming chat messages");
        manager.createNotificationChannel(channel);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleAttachedUsbDevice(intent);
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

    @SuppressLint("SetJavaScriptEnabled")
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

        webView.addJavascriptInterface(new ChatxzBridge(this), "chatxzAndroid");

        webView.setWebViewClient(new WebViewClient() {
            private int retryCount = 0;

            @Override
            public void onPageFinished(WebView view, String url) {
                if (url != null && url.startsWith(serverUrl)) {
                    webViewLoaded = true;
                }
            }

            @Override
            public void onReceivedError(WebView view, int errorCode, String description, String failingUrl) {
                if (retryCount < 15 && !webViewLoaded) {
                    retryCount++;
                    view.postDelayed(() -> view.loadUrl(serverUrl), 1500);
                }
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> handleWebPermissionRequest(request));
            }

            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (filePathCallback != null) {
                    filePathCallback.onReceiveValue(null);
                }
                filePathCallback = callback;
                Intent intent = params.createIntent();
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                try {
                    startActivityForResult(Intent.createChooser(intent, "Attach file"), REQ_FILE);
                } catch (Exception e) {
                    filePathCallback = null;
                    Toast.makeText(MainActivity.this, "Could not open file picker", Toast.LENGTH_SHORT).show();
                    return false;
                }
                return true;
            }
        });
    }

    private void handleWebPermissionRequest(PermissionRequest request) {
        if (request == null) {
            return;
        }
        boolean needsAudio = false;
        for (String resource : request.getResources()) {
            if (PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(resource)) {
                needsAudio = true;
                break;
            }
        }
        if (needsAudio && ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            pendingWebPermissionRequest = request;
            ActivityCompat.requestPermissions(
                    this,
                    new String[]{Manifest.permission.RECORD_AUDIO},
                    REQ_AUDIO
            );
            return;
        }
        request.grant(request.getResources());
    }

    public void notifyAudioPermissionGranted() {
        webView.post(() -> webView.evaluateJavascript(
                "window.onChatxzAudioPermissionGranted && window.onChatxzAudioPermissionGranted();",
                null));
    }

    public void requestAudioPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) {
            notifyAudioPermissionGranted();
            return;
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED) {
            notifyAudioPermissionGranted();
            return;
        }
        if (ActivityCompat.shouldShowRequestPermissionRationale(this, Manifest.permission.RECORD_AUDIO)) {
            new AlertDialog.Builder(this)
                    .setTitle("Microphone")
                    .setMessage("chatxz needs microphone access to record voice notes.")
                    .setPositiveButton("Allow", (d, w) -> ActivityCompat.requestPermissions(
                            this, new String[]{Manifest.permission.RECORD_AUDIO}, REQ_AUDIO))
                    .setNegativeButton("Cancel", null)
                    .show();
            return;
        }
        ActivityCompat.requestPermissions(this, new String[]{Manifest.permission.RECORD_AUDIO}, REQ_AUDIO);
    }

    public void openAppSettings() {
        try {
            Intent intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS);
            intent.setData(Uri.parse("package:" + getPackageName()));
            startActivity(intent);
        } catch (Exception e) {
            Toast.makeText(this, "Open Settings → Apps → chatxz → Permissions", Toast.LENGTH_LONG).show();
        }
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
            showStartModeDialog();
            return;
        }

        List<String> needed = new ArrayList<>();
        // Microphone is requested when the user taps 🎤 (not at startup).
        // POST_NOTIFICATIONS only exists on Android 13+ — requesting it earlier crashes the app.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS);
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && ContextCompat.checkSelfPermission(this, Manifest.permission.NEARBY_WIFI_DEVICES)
                != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.NEARBY_WIFI_DEVICES);
        }

        if (needed.isEmpty()) {
            showStartModeDialog();
        } else {
            ActivityCompat.requestPermissions(this, needed.toArray(new String[0]), PERM_REQUEST);
        }
    }

    private void showStartModeDialog() {
        new AlertDialog.Builder(this)
            .setTitle("Start chatxz")
            .setMessage("Choose run mode")
            .setPositiveButton("Normal", (d, w) -> startPythonServer(false))
            .setNegativeButton("Debug", (d, w) -> startPythonServer(true))
            .setCancelable(false)
            .show();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, @NonNull String[] permissions,
                                           @NonNull int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == PERM_REQUEST) {
            showStartModeDialog();
            return;
        }
        if (requestCode == REQ_AUDIO) {
            boolean granted = grantResults.length > 0
                    && grantResults[0] == PackageManager.PERMISSION_GRANTED;
            PermissionRequest pending = pendingWebPermissionRequest;
            pendingWebPermissionRequest = null;
            if (pending != null) {
                if (granted) {
                    pending.grant(pending.getResources());
                } else {
                    pending.deny();
                    Toast.makeText(this, "Microphone permission denied", Toast.LENGTH_SHORT).show();
                }
            } else if (!granted) {
                Toast.makeText(this, "Microphone permission denied", Toast.LENGTH_SHORT).show();
            }
            if (granted) {
                notifyAudioPermissionGranted();
            } else if (!ActivityCompat.shouldShowRequestPermissionRationale(
                    this, Manifest.permission.RECORD_AUDIO)) {
                new AlertDialog.Builder(this)
                        .setTitle("Microphone blocked")
                        .setMessage("Enable microphone for chatxz in Android Settings to record voice notes.")
                        .setPositiveButton("Open Settings", (d, w) -> openAppSettings())
                        .setNegativeButton("Cancel", null)
                        .show();
            }
        }
    }

    private void startForegroundService() {
        try {
            Intent intent = new Intent(this, ChatxzForegroundService.class);
            intent.setAction(ChatxzForegroundService.ACTION_START);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                startForegroundService(intent);
            } else {
                startService(intent);
            }
        } catch (Exception ignored) {}
    }

    private synchronized void startPythonServer(boolean debug) {
        if (serverStarted) {
            if (!webViewLoaded) {
                webView.loadUrl(serverUrl);
            }
            startForegroundService();
            return;
        }
        serverStarted = true;
        runOnUiThread(() -> showLoading(debug ? "Starting chatxz (debug)..." : "Starting chatxz..."));

        new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    throw new IllegalStateException("Python was not started in ChatxzApplication");
                }
                Python python = Python.getInstance();
                PyObject module = python.getModule("main");
                module.callAttr("set_debug_mode", debug ? "1" : "0");
                PyObject result = module.callAttr("start_server");
                String host = result.asList().get(0).toString();
                String port = result.asList().get(1).toString();
                if (host != null && !host.equals("None")) {
                    serverUrl = "http://" + host + ":" + port;
                    runOnUiThread(() -> {
                        if (!webViewLoaded) {
                            webView.loadUrl(serverUrl);
                        }
                        startForegroundService();
                        Toast.makeText(this, "chatxz ready", Toast.LENGTH_SHORT).show();
                    });
                } else {
                    final String error = port;
                    serverStarted = false;
                    File crashFile = new File(getFilesDir(), "python_crash_log.txt");
                    try {
                        FileOutputStream fos = new FileOutputStream(crashFile);
                        fos.write(error.getBytes());
                        fos.close();
                    } catch (Exception ignored) {}
                    runOnUiThread(() -> showError("Server Error", error));
                }
            } catch (Exception e) {
                serverStarted = false;
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

    public void openFolderSendPicker() {
        try {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
            intent.addFlags(
                Intent.FLAG_GRANT_READ_URI_PERMISSION
                    | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
            );
            startActivityForResult(intent, REQ_SEND_FOLDER);
        } catch (Exception e) {
            notifyFolderSendError("Could not open folder picker: " + e.getMessage());
        }
    }

    public void openFolderPicker() {
        try {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
            intent.addFlags(
                Intent.FLAG_GRANT_READ_URI_PERMISSION
                    | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                    | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION
            );
            startActivityForResult(intent, REQ_FOLDER);
        } catch (Exception e) {
            notifyFolderPickError("Could not open folder picker: " + e.getMessage());
        }
    }

    private void notifyFolderPickError(String message) {
        String js = "window.onChatxzFolderPickError && window.onChatxzFolderPickError("
            + org.json.JSONObject.quote(message) + ")";
        webView.post(() -> webView.evaluateJavascript(js, null));
    }

    private void notifyFolderPicked(String path) {
        String js = "window.onChatxzFolderPicked && window.onChatxzFolderPicked("
            + org.json.JSONObject.quote(path) + ")";
        webView.post(() -> webView.evaluateJavascript(js, null));
    }

    private void notifyFolderSendError(String message) {
        String js = "window.onChatxzFolderSendError && window.onChatxzFolderSendError("
            + org.json.JSONObject.quote(message) + ")";
        webView.post(() -> webView.evaluateJavascript(js, null));
    }

    private void notifyFolderSendOk(String name, long size) {
        String js = "window.onChatxzFolderSendOk && window.onChatxzFolderSendOk("
            + org.json.JSONObject.quote(name) + "," + size + ")";
        webView.post(() -> webView.evaluateJavascript(js, null));
    }

    private void zipAndUploadFolder(Uri treeUri) {
        Toast.makeText(this, "Zipping folder...", Toast.LENGTH_SHORT).show();
        new Thread(() -> {
            try {
                String treeId = DocumentsContract.getTreeDocumentId(treeUri);
                String folderName = "folder";
                if (treeId != null) {
                    int slash = treeId.lastIndexOf('/');
                    folderName = slash >= 0 ? treeId.substring(slash + 1) : treeId;
                    int colon = folderName.indexOf(':');
                    if (colon >= 0 && colon < folderName.length() - 1) {
                        folderName = folderName.substring(colon + 1);
                    }
                }
                if (folderName.isEmpty()) {
                    folderName = "folder";
                }
                File zipFile = new File(getCacheDir(), folderName + ".zip");
                try (ZipOutputStream zos = new ZipOutputStream(new FileOutputStream(zipFile))) {
                    zipDocumentChildren(treeUri, treeId, "", zos);
                }
                long size = zipFile.length();
                if (size == 0) {
                    throw new IllegalStateException("Folder is empty");
                }
                uploadZipToServer(zipFile, folderName + ".zip");
                notifyFolderSendOk(folderName + ".zip", size);
            } catch (Exception e) {
                notifyFolderSendError(e.getMessage() != null ? e.getMessage() : "Folder send failed");
            }
        }, "folder-send").start();
    }

    private void zipDocumentChildren(Uri treeUri, String parentDocId, String pathPrefix, ZipOutputStream zos)
            throws Exception {
        Uri childrenUri = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentDocId);
        ContentResolver resolver = getContentResolver();
        try (Cursor cursor = resolver.query(childrenUri,
                new String[]{
                    DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                    DocumentsContract.Document.COLUMN_MIME_TYPE
                }, null, null, null)) {
            if (cursor == null) {
                return;
            }
            while (cursor.moveToNext()) {
                String docId = cursor.getString(0);
                String name = cursor.getString(1);
                String mime = cursor.getString(2);
                if (name == null || docId == null) {
                    continue;
                }
                String entryPath = pathPrefix.isEmpty() ? name : pathPrefix + "/" + name;
                if (DocumentsContract.Document.MIME_TYPE_DIR.equals(mime)) {
                    zipDocumentChildren(treeUri, docId, entryPath, zos);
                } else {
                    Uri docUri = DocumentsContract.buildDocumentUriUsingTree(treeUri, docId);
                    zos.putNextEntry(new ZipEntry(entryPath));
                    try (InputStream in = new BufferedInputStream(resolver.openInputStream(docUri))) {
                        byte[] buf = new byte[8192];
                        int n;
                        while ((n = in.read(buf)) > 0) {
                            zos.write(buf, 0, n);
                        }
                    }
                    zos.closeEntry();
                }
            }
        }
    }

    private void uploadZipToServer(File zipFile, String filename) throws Exception {
        String boundary = "----ChatxzBoundary" + System.currentTimeMillis();
        URL url = new URL(serverUrl + "/api/file");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setDoOutput(true);
        conn.setRequestMethod("POST");
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        try (OutputStream out = conn.getOutputStream()) {
            String header = "--" + boundary + "\r\n"
                + "Content-Disposition: form-data; name=\"file\"; filename=\"" + filename + "\"\r\n"
                + "Content-Type: application/zip\r\n\r\n";
            out.write(header.getBytes());
            try (InputStream in = new FileInputStream(zipFile)) {
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                }
            }
            out.write(("\r\n--" + boundary + "--\r\n").getBytes());
        }
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            throw new IllegalStateException("Upload failed (HTTP " + code + ")");
        }
        conn.disconnect();
    }

    private String treeUriToPath(Uri uri) {
        if (uri == null) {
            return null;
        }
        try {
            String treeId = DocumentsContract.getTreeDocumentId(uri);
            if (treeId == null) {
                return null;
            }
            if (treeId.startsWith("primary:")) {
                String rel = treeId.substring("primary:".length());
                File base = Environment.getExternalStorageDirectory();
                if (base == null) {
                    return null;
                }
                return new File(base, rel).getAbsolutePath();
            }
            int idx = treeId.indexOf(':');
            if (idx > 0) {
                String volume = treeId.substring(0, idx);
                String rel = treeId.substring(idx + 1);
                return new File("/storage/" + volume, rel).getAbsolutePath();
            }
        } catch (Exception ignored) {}
        return null;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_FILE) {
            if (filePathCallback == null) {
                return;
            }
            Uri[] results = null;
            if (resultCode == RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    int count = data.getClipData().getItemCount();
                    results = new Uri[count];
                    for (int i = 0; i < count; i++) {
                        results[i] = data.getClipData().getItemAt(i).getUri();
                    }
                } else if (data.getData() != null) {
                    results = new Uri[]{data.getData()};
                }
            }
            filePathCallback.onReceiveValue(results);
            filePathCallback = null;
            return;
        }
        if (requestCode == REQ_SEND_FOLDER) {
            if (resultCode != RESULT_OK || data == null || data.getData() == null) {
                return;
            }
            Uri uri = data.getData();
            try {
                final int flags = data.getFlags() & Intent.FLAG_GRANT_READ_URI_PERMISSION;
                getContentResolver().takePersistableUriPermission(uri, flags);
            } catch (Exception ignored) {}
            zipAndUploadFolder(uri);
            return;
        }
        if (requestCode != REQ_FOLDER) {
            return;
        }
        if (resultCode != RESULT_OK || data == null || data.getData() == null) {
            return;
        }
        Uri uri = data.getData();
        try {
            final int flags = data.getFlags()
                & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            getContentResolver().takePersistableUriPermission(uri, flags);
        } catch (Exception ignored) {}

        String path = treeUriToPath(uri);
        if (path == null || path.isEmpty()) {
            notifyFolderPickError("Could not resolve folder path. Try another folder.");
            return;
        }
        notifyFolderPicked(path);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (webView != null) {
            webView.onResume();
            webView.evaluateJavascript(
                    "if(typeof syncUiState==='function')syncUiState();",
                    null);
        }
    }

    @Override
    protected void onPause() {
        if (webView != null) {
            webView.evaluateJavascript(
                    "if(typeof syncUiState==='function')syncUiState();",
                    null);
            webView.onPause();
        }
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        if (usbPermissionReceiver != null) {
            try {
                unregisterReceiver(usbPermissionReceiver);
            } catch (Exception ignored) {}
            usbPermissionReceiver = null;
        }
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        webView.evaluateJavascript(
            "(function(){if(typeof androidHandleBack==='function'&&androidHandleBack())return 'true';"
            + "if(typeof closeSidebar==='function'&&document.body.classList.contains('sidebar-open')){closeSidebar();return 'true';}"
            + "return 'false';})()",
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