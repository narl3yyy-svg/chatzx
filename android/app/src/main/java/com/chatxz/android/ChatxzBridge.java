package com.chatxz.android;

import android.Manifest;
import android.content.pm.PackageManager;
import android.webkit.JavascriptInterface;

import androidx.core.content.ContextCompat;

public class ChatxzBridge {
    private final MainActivity activity;

    public ChatxzBridge(MainActivity activity) {
        this.activity = activity;
    }

    @JavascriptInterface
    public void pickFolder() {
        activity.runOnUiThread(activity::openFolderPicker);
    }

    @JavascriptInterface
    public void pickSendFolder() {
        activity.runOnUiThread(activity::openFolderSendPicker);
    }

    @JavascriptInterface
    public boolean isAndroid() {
        return true;
    }

    @JavascriptInterface
    public boolean hasAudioPermission() {
        return ContextCompat.checkSelfPermission(activity, Manifest.permission.RECORD_AUDIO)
                == PackageManager.PERMISSION_GRANTED;
    }

    @JavascriptInterface
    public void requestAudioPermission() {
        activity.runOnUiThread(activity::requestAudioPermission);
    }

    @JavascriptInterface
    public void openAppSettings() {
        activity.runOnUiThread(activity::openAppSettings);
    }

    @JavascriptInterface
    public void requestUsbPermission(String deviceName) {
        activity.runOnUiThread(() -> UsbSerialHelper.requestPermission(deviceName));
    }

    @JavascriptInterface
    public void restartApp() {
        activity.runOnUiThread(activity::restartApp);
    }

    @JavascriptInterface
    public void showNotification(String title, String body) {
        activity.runOnUiThread(() -> activity.showMessageNotification(title, body));
    }
}