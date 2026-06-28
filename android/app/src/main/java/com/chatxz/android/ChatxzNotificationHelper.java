package com.chatxz.android;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

public final class ChatxzNotificationHelper {
    public static final String EXTRA_OPEN_PEER = "open_peer";
    public static final String EXTRA_INCOMING_CALL = "incoming_call";
    private static final String MSG_CHANNEL_ID = "chatxz_messages";
    private static final String CALL_CHANNEL_ID = "chatxz_calls";
    private static final int INCOMING_CALL_NOTIFICATION_ID = 1999;
    private static int notificationId = 2000;

    private ChatxzNotificationHelper() {}

    public static void showIncomingCall(String callerName, String subtitle, String peerHash) {
        Context ctx = ChatxzApplication.getInstance();
        if (ctx == null) {
            return;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            return;
        }
        ensureCallChannel(ctx);
        String title = callerName != null && !callerName.isEmpty() ? callerName : "Incoming call";
        String body = subtitle != null && !subtitle.isEmpty() ? subtitle : "Tap to answer in chatxz";
        String safePeer = peerHash != null ? peerHash.replace(":", "") : "";

        Intent intent = new Intent(ctx, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        intent.putExtra(EXTRA_INCOMING_CALL, true);
        if (!safePeer.isEmpty()) {
            intent.putExtra(EXTRA_OPEN_PEER, safePeer);
        }

        int pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            pendingFlags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent contentIntent = PendingIntent.getActivity(ctx, INCOMING_CALL_NOTIFICATION_ID, intent, pendingFlags);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(ctx, CALL_CHANNEL_ID)
                .setSmallIcon(android.R.drawable.stat_sys_phone_call)
                .setContentTitle(title)
                .setContentText(body)
                .setSubText("Voice call")
                .setCategory(NotificationCompat.CATEGORY_CALL)
                .setPriority(NotificationCompat.PRIORITY_HIGH)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setAutoCancel(true)
                .setOngoing(true)
                .setContentIntent(contentIntent);
        try {
            NotificationManagerCompat.from(ctx).notify(INCOMING_CALL_NOTIFICATION_ID, builder.build());
        } catch (SecurityException ignored) {}
    }

    public static void cancelIncomingCall() {
        Context ctx = ChatxzApplication.getInstance();
        if (ctx == null) {
            return;
        }
        try {
            NotificationManagerCompat.from(ctx).cancel(INCOMING_CALL_NOTIFICATION_ID);
        } catch (SecurityException ignored) {}
    }

    public static void show(String title, String body, String peerHash) {
        Context ctx = ChatxzApplication.getInstance();
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
        String safePeer = peerHash != null ? peerHash.replace(":", "") : "";

        Intent intent = new Intent(ctx, MainActivity.class);
        intent.setFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        if (!safePeer.isEmpty()) {
            intent.putExtra(EXTRA_OPEN_PEER, safePeer);
        }

        int reqCode = notificationId;
        int pendingFlags = PendingIntent.FLAG_UPDATE_CURRENT;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            pendingFlags |= PendingIntent.FLAG_IMMUTABLE;
        }
        PendingIntent contentIntent = PendingIntent.getActivity(ctx, reqCode, intent, pendingFlags);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(ctx, MSG_CHANNEL_ID)
                .setSmallIcon(android.R.drawable.stat_notify_chat)
                .setContentTitle(safeTitle)
                .setContentText(safeBody)
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .setAutoCancel(true)
                .setContentIntent(contentIntent);
        try {
            NotificationManagerCompat.from(ctx).notify(notificationId++, builder.build());
        } catch (SecurityException ignored) {}
    }

    private static void ensureCallChannel(Context ctx) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = ctx.getSystemService(NotificationManager.class);
        if (manager == null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CALL_CHANNEL_ID,
                "Calls",
                NotificationManager.IMPORTANCE_HIGH
        );
        channel.setDescription("Incoming voice calls");
        channel.enableVibration(true);
        manager.createNotificationChannel(channel);
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