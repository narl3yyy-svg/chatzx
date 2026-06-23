package com.chatxz.android;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

import com.chaquo.python.Python;

public final class ChatxzNotificationHelper {
    private static final String MSG_CHANNEL_ID = "chatxz_messages";
    private static int notificationId = 2000;

    private ChatxzNotificationHelper() {}

    public static void show(String title, String body) {
        Context ctx = Python.getPlatform().getApplication();
        if (ctx == null) {
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        ensureChannel(ctx);
        String safeTitle = title != null && !title.isEmpty() ? title : "chatxz";
        String safeBody = body != null ? body : "New message";
        NotificationCompat.Builder builder = new NotificationCompat.Builder(ctx, MSG_CHANNEL_ID)
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentTitle(safeTitle)
                .setContentText(safeBody)
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .setAutoCancel(true);
        try {
            NotificationManagerCompat.from(ctx).notify(notificationId++, builder.build());
        } catch (SecurityException ignored) {}
    }

    private static void ensureChannel(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = ctx.getSystemService(NotificationManager.class);
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
}