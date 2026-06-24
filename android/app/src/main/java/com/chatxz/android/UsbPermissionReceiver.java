package com.chatxz.android;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbManager;
import android.widget.Toast;

public class UsbPermissionReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (intent == null || !UsbSerialHelper.ACTION_USB_PERMISSION.equals(intent.getAction())) {
            return;
        }
        UsbDevice device = intent.getParcelableExtra(UsbManager.EXTRA_DEVICE);
        boolean granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false);
        if (device == null) {
            return;
        }
        String name = device.getDeviceName();
        if (granted) {
            Toast.makeText(context, "USB access granted for " + name, Toast.LENGTH_SHORT).show();
            if (context instanceof MainActivity) {
                ((MainActivity) context).onUsbPermissionGranted(name);
            }
        } else {
            Toast.makeText(context, "USB access denied for " + name, Toast.LENGTH_SHORT).show();
        }
    }
}