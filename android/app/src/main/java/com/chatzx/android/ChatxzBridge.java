package com.chatzx.android;

import android.webkit.JavascriptInterface;

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
    public boolean isAndroid() {
        return true;
    }
}