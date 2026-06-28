package com.chatxz.android;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Build;
import android.util.Base64;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.ShortBuffer;
import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Native Android call audio: 48 kHz PCM capture/playback with MediaCodec Opus.
 */
public final class CallAudioEngine {
    private static final String TAG = "CallAudioEngine";
    private static final int SAMPLE_RATE = 48000;
    private static final int CHANNELS = 1;
    private static final int FRAME_SAMPLES = 960; // 20 ms
    private static final int BIT_RATE = 32000;

    private static CallAudioEngine instance;

    private Thread captureThread;
    private Thread playbackThread;
    private volatile boolean running;
    private AudioRecord recorder;
    private AudioTrack track;
    private MediaCodec encoder;
    private MediaCodec decoder;
    private final Deque<short[]> playQueue = new ArrayDeque<>();
    private final Object playLock = new Object();
    private int playSeq = 0;
    private short[] lastPcm = new short[FRAME_SAMPLES];
    private int prefetch = 4;

    public static synchronized CallAudioEngine getInstance() {
        if (instance == null) {
            instance = new CallAudioEngine();
        }
        return instance;
    }

    public synchronized boolean start() {
        if (running) {
            return true;
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            Log.w(TAG, "Opus MediaCodec requires API 29+");
            return false;
        }
        try {
            initEncoder();
            initDecoder();
            initRecorder();
            initTrack();
            running = true;
            captureThread = new Thread(this::captureLoop, "chatxz-call-cap");
            playbackThread = new Thread(this::playbackLoop, "chatxz-call-play");
            captureThread.start();
            playbackThread.start();
            Log.i(TAG, "Started Opus call audio (48 kHz, 20 ms)");
            return true;
        } catch (Exception e) {
            Log.e(TAG, "start failed", e);
            stop();
            return false;
        }
    }

    public synchronized void stop() {
        running = false;
        if (captureThread != null) {
            try {
                captureThread.join(800);
            } catch (InterruptedException ignored) {
            }
            captureThread = null;
        }
        if (playbackThread != null) {
            try {
                playbackThread.join(800);
            } catch (InterruptedException ignored) {
            }
            playbackThread = null;
        }
        releaseCodec(encoder);
        encoder = null;
        releaseCodec(decoder);
        decoder = null;
        releaseRecorder();
        releaseTrack();
        synchronized (playLock) {
            playQueue.clear();
        }
        Log.i(TAG, "Stopped call audio");
    }

    public void playOpus(int seq, String b64) {
        if (!running || b64 == null || b64.isEmpty() || decoder == null) {
            return;
        }
        try {
            byte[] opus = Base64.decode(b64, Base64.DEFAULT);
            ByteBuffer in = decoder.getInputBuffer(0);
            if (in == null) {
                return;
            }
            in.clear();
            in.put(opus);
            decoder.queueInputBuffer(0, 0, opus.length, seq * 20_000L, 0);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            int outIndex = decoder.dequeueOutputBuffer(info, 5_000);
            if (outIndex >= 0) {
                ByteBuffer out = decoder.getOutputBuffer(outIndex);
                if (out != null && info.size > 0) {
                    ShortBuffer shorts = out.order(ByteOrder.LITTLE_ENDIAN).asShortBuffer();
                    int n = Math.min(FRAME_SAMPLES, shorts.remaining());
                    short[] pcm = new short[FRAME_SAMPLES];
                    shorts.get(pcm, 0, n);
                    enqueuePlayback(pcm);
                }
                decoder.releaseOutputBuffer(outIndex, false);
            }
        } catch (Exception e) {
            Log.w(TAG, "playOpus failed", e);
        }
    }

    private void enqueuePlayback(short[] pcm) {
        synchronized (playLock) {
            while (playQueue.size() > 24) {
                playQueue.pollFirst();
            }
            playQueue.addLast(pcm);
        }
    }

    private void captureLoop() {
        short[] frame = new short[FRAME_SAMPLES];
        while (running && recorder != null) {
            int read = recorder.read(frame, 0, FRAME_SAMPLES);
            if (read < FRAME_SAMPLES) {
                continue;
            }
            encodeAndSend(frame);
        }
    }

    private void encodeAndSend(short[] pcm) {
        if (encoder == null) {
            return;
        }
        try {
            int inIndex = encoder.dequeueInputBuffer(10_000);
            if (inIndex < 0) {
                return;
            }
            ByteBuffer in = encoder.getInputBuffer(inIndex);
            if (in == null) {
                return;
            }
            in.clear();
            in.order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().put(pcm);
            encoder.queueInputBuffer(inIndex, 0, FRAME_SAMPLES * 2, System.nanoTime() / 1000, 0);
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            int outIndex = encoder.dequeueOutputBuffer(info, 10_000);
            if (outIndex >= 0) {
                ByteBuffer out = encoder.getOutputBuffer(outIndex);
                if (out != null && info.size > 0) {
                    byte[] packet = new byte[info.size];
                    out.get(packet);
                    String b64 = Base64.encodeToString(packet, Base64.NO_WRAP);
                    deliverToPython(b64);
                }
                encoder.releaseOutputBuffer(outIndex, false);
            }
        } catch (Exception e) {
            Log.w(TAG, "encode failed", e);
        }
    }

    private void deliverToPython(String b64) {
        try {
            Python py = Python.getInstance();
            PyObject mod = py.getModule("chatxz.android_call_audio");
            mod.callAttr("on_encoded_opus", b64);
        } catch (Exception e) {
            Log.w(TAG, "python deliver failed", e);
        }
    }

    private void playbackLoop() {
        while (running && track != null) {
            short[] pcm;
            synchronized (playLock) {
                if (playQueue.size() < prefetch) {
                    pcm = lastPcm.clone();
                } else {
                    pcm = playQueue.pollFirst();
                    if (pcm == null) {
                        pcm = lastPcm.clone();
                    } else {
                        lastPcm = pcm;
                    }
                }
            }
            track.write(pcm, 0, pcm.length);
        }
    }

    private void initEncoder() throws Exception {
        MediaFormat fmt = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
        fmt.setInteger(MediaFormat.KEY_BIT_RATE, BIT_RATE);
        fmt.setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, FRAME_SAMPLES * 2 * 2);
        encoder = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
        encoder.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE);
        encoder.start();
    }

    private void initDecoder() throws Exception {
        MediaFormat fmt = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_OPUS, SAMPLE_RATE, CHANNELS);
        decoder = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
        decoder.configure(fmt, null, null, 0);
        decoder.start();
    }

    private void initRecorder() throws Exception {
        int minBuf = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        int buf = Math.max(minBuf, FRAME_SAMPLES * 4);
        recorder = new AudioRecord(
                android.media.MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                buf);
        if (recorder.getState() != AudioRecord.STATE_INITIALIZED) {
            throw new IllegalStateException("AudioRecord init failed");
        }
        recorder.startRecording();
    }

    private void initTrack() throws Exception {
        int minBuf = AudioTrack.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        AudioAttributes attrs = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build();
        AudioFormat fmt = new AudioFormat.Builder()
                .setSampleRate(SAMPLE_RATE)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                .build();
        track = new AudioTrack(attrs, fmt, Math.max(minBuf, FRAME_SAMPLES * 8),
                AudioTrack.MODE_STREAM, android.media.AudioManager.AUDIO_SESSION_ID_GENERATE);
        if (track.getState() != AudioTrack.STATE_INITIALIZED) {
            throw new IllegalStateException("AudioTrack init failed");
        }
        track.play();
    }

    private static void releaseCodec(MediaCodec codec) {
        if (codec == null) {
            return;
        }
        try {
            codec.stop();
        } catch (Exception ignored) {
        }
        try {
            codec.release();
        } catch (Exception ignored) {
        }
    }

    private void releaseRecorder() {
        if (recorder == null) {
            return;
        }
        try {
            recorder.stop();
        } catch (Exception ignored) {
        }
        recorder.release();
        recorder = null;
    }

    private void releaseTrack() {
        if (track == null) {
            return;
        }
        try {
            track.stop();
        } catch (Exception ignored) {
        }
        track.release();
        track = null;
    }
}