package com.chatxz.android;

import android.content.Context;
import android.content.pm.PackageManager;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioRecord;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaFormat;
import android.os.Build;
import android.Manifest;
import android.util.Base64;
import android.util.Log;
import android.util.SparseArray;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.ShortBuffer;
import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Native Android call audio: 48 kHz PCM capture/playback with MediaCodec Opus.
 * Receive path mirrors desktop VoiceJitterBuffer: seq-ordered playout + PLC.
 */
public final class CallAudioEngine {
    private static final String TAG = "CallAudioEngine";
    private static final int SAMPLE_RATE = 48000;
    private static final int CHANNELS = 1;
    private static final int FRAME_SAMPLES = 960; // 20 ms
    private static final int BIT_RATE = 32000;
    private static final int JITTER_TARGET = 4;
    private static final int JITTER_MAX = 24;

    private static CallAudioEngine instance;

    private Thread captureThread;
    private Thread playbackThread;
    private volatile boolean running;
    private AudioRecord recorder;
    private AudioTrack track;
    private MediaCodec encoder;
    private MediaCodec decoder;
    private AudioManager audioManager;
    private boolean speakerphone;
    private final Object decodeLock = new Object();
    private final Deque<Integer> pendingInputSeqs = new ArrayDeque<>();
    private final Deque<short[]> playQueue = new ArrayDeque<>();
    private final Object playLock = new Object();
    private final SparseArray<short[]> jitterFrames = new SparseArray<>();
    private int jitterNextSeq = -1;
    private boolean jitterPrimed;
    private short[] lastPcm = new short[FRAME_SAMPLES];
    private long encodePtsUs;

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
        Context ctx = MainActivity.appContext();
        if (ctx == null) {
            Log.w(TAG, "No app context");
            return false;
        }
        if (ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            Log.w(TAG, "RECORD_AUDIO permission not granted");
            return false;
        }
        audioManager = (AudioManager) ctx.getSystemService(Context.AUDIO_SERVICE);
        if (audioManager != null) {
            audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION);
            audioManager.setSpeakerphoneOn(speakerphone);
        }
        try {
            initEncoder();
            initDecoder();
            if (!initRecorder()) {
                throw new IllegalStateException("AudioRecord init failed");
            }
            initTrack();
            running = true;
            encodePtsUs = 0;
            resetJitter();
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
        synchronized (decodeLock) {
            pendingInputSeqs.clear();
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
        resetJitter();
        if (audioManager != null) {
            audioManager.setSpeakerphoneOn(false);
            audioManager.setMode(AudioManager.MODE_NORMAL);
        }
        Log.i(TAG, "Stopped call audio");
    }

    public synchronized void setSpeakerphone(boolean on) {
        speakerphone = on;
        if (audioManager != null) {
            audioManager.setMode(AudioManager.MODE_IN_COMMUNICATION);
            audioManager.setSpeakerphoneOn(on);
            Log.i(TAG, "Speakerphone " + (on ? "on" : "off"));
        }
    }

    public synchronized boolean isSpeakerphone() {
        return speakerphone;
    }

    public void playOpus(int seq, String b64) {
        if (!running || b64 == null || b64.isEmpty() || decoder == null) {
            return;
        }
        synchronized (decodeLock) {
            try {
                byte[] opus = Base64.decode(b64, Base64.DEFAULT);
                if (opus.length == 0) {
                    return;
                }
                int inIndex = decoder.dequeueInputBuffer(5_000);
                if (inIndex < 0) {
                    return;
                }
                ByteBuffer in = decoder.getInputBuffer(inIndex);
                if (in == null) {
                    return;
                }
                in.clear();
                in.put(opus);
                long pts = Math.max(0, seq) * 20_000L;
                pendingInputSeqs.addLast(seq);
                decoder.queueInputBuffer(inIndex, 0, opus.length, pts, 0);
                drainDecoderOutputs();
            } catch (Exception e) {
                Log.w(TAG, "playOpus failed", e);
                if (!pendingInputSeqs.isEmpty()) {
                    pendingInputSeqs.removeLast();
                }
            }
        }
    }

    private void drainDecoderOutputs() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        for (;;) {
            int outIndex = decoder.dequeueOutputBuffer(info, 0);
            if (outIndex == MediaCodec.INFO_TRY_AGAIN_LATER) {
                break;
            }
            if (outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                continue;
            }
            if (outIndex < 0) {
                continue;
            }
            if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                decoder.releaseOutputBuffer(outIndex, false);
                continue;
            }
            ByteBuffer out = decoder.getOutputBuffer(outIndex);
            if (out != null && info.size > 0) {
                ShortBuffer shorts = out.order(ByteOrder.LITTLE_ENDIAN).asShortBuffer();
                int n = Math.min(FRAME_SAMPLES, shorts.remaining());
                short[] pcm = new short[FRAME_SAMPLES];
                shorts.get(pcm, 0, n);
                int mappedSeq = seqFromPts(info.presentationTimeUs);
                Integer queuedSeq = pendingInputSeqs.pollFirst();
                int playSeq = queuedSeq != null ? queuedSeq : mappedSeq;
                enqueueJitter(playSeq, pcm);
            }
            decoder.releaseOutputBuffer(outIndex, false);
        }
    }

    private static int seqFromPts(long ptsUs) {
        if (ptsUs <= 0) {
            return 0;
        }
        return (int) (ptsUs / 20_000L);
    }

    private void resetJitter() {
        synchronized (playLock) {
            jitterFrames.clear();
            jitterNextSeq = -1;
            jitterPrimed = false;
            playQueue.clear();
        }
    }

    private void enqueueJitter(int seq, short[] pcm) {
        synchronized (playLock) {
            if (jitterNextSeq >= 0 && seq < jitterNextSeq) {
                return;
            }
            jitterFrames.put(seq, pcm);
            while (jitterFrames.size() > JITTER_MAX) {
                int oldest = jitterFrames.keyAt(0);
                jitterFrames.remove(oldest);
                if (jitterNextSeq >= 0 && jitterNextSeq <= oldest) {
                    jitterNextSeq = oldest + 1;
                }
            }
            if (!jitterPrimed) {
                if (jitterNextSeq < 0) {
                    jitterNextSeq = jitterFrames.keyAt(0);
                }
                int ahead = 0;
                for (int i = 0; i < jitterFrames.size(); i++) {
                    if (jitterFrames.keyAt(i) >= jitterNextSeq) {
                        ahead++;
                    }
                }
                if (ahead >= JITTER_TARGET) {
                    jitterPrimed = true;
                }
            }
            flushJitterToPlayQueue();
        }
    }

    private void flushJitterToPlayQueue() {
        while (jitterPrimed && jitterFrames.size() > 0 && jitterFrames.keyAt(0) == jitterNextSeq) {
            short[] frame = jitterFrames.valueAt(0);
            jitterFrames.removeAt(0);
            jitterNextSeq++;
            while (playQueue.size() > JITTER_MAX) {
                playQueue.pollFirst();
            }
            playQueue.addLast(frame);
        }
        while (jitterPrimed && jitterFrames.size() > 0 && jitterFrames.keyAt(0) > jitterNextSeq) {
            short[] plc = lastPcm.clone();
            while (playQueue.size() > JITTER_MAX) {
                playQueue.pollFirst();
            }
            playQueue.addLast(plc);
            jitterNextSeq++;
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
            encoder.queueInputBuffer(inIndex, 0, FRAME_SAMPLES * 2, encodePtsUs, 0);
            encodePtsUs += 20_000;
            drainEncoderOutputs();
        } catch (Exception e) {
            Log.w(TAG, "encode failed", e);
        }
    }

    private void drainEncoderOutputs() {
        MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
        for (;;) {
            int outIndex = encoder.dequeueOutputBuffer(info, 0);
            if (outIndex == MediaCodec.INFO_TRY_AGAIN_LATER) {
                break;
            }
            if (outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                continue;
            }
            if (outIndex < 0) {
                continue;
            }
            if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) != 0) {
                encoder.releaseOutputBuffer(outIndex, false);
                continue;
            }
            ByteBuffer out = encoder.getOutputBuffer(outIndex);
            if (out != null && info.size > 0) {
                byte[] packet = new byte[info.size];
                out.get(packet);
                String b64 = Base64.encodeToString(packet, Base64.NO_WRAP);
                deliverToPython(b64);
            }
            encoder.releaseOutputBuffer(outIndex, false);
        }
    }

    private void deliverToPython(String b64) {
        try {
            Python py = Python.getInstance();
            PyObject mod = py.getModule("chatxz.core.android_call_audio");
            mod.callAttr("on_encoded_opus", b64);
        } catch (Exception e) {
            Log.w(TAG, "python deliver failed", e);
        }
    }

    private void playbackLoop() {
        while (running && track != null) {
            short[] pcm;
            synchronized (playLock) {
                if (!jitterPrimed || playQueue.isEmpty()) {
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

    private static ByteBuffer buildOpusHead() {
        byte[] head = new byte[19];
        System.arraycopy(new byte[]{'O', 'p', 'u', 's', 'H', 'e', 'a', 'd'}, 0, head, 0, 8);
        head[8] = 1;
        head[9] = (byte) CHANNELS;
        head[10] = 0;
        head[11] = 0;
        head[12] = (byte) (SAMPLE_RATE & 0xff);
        head[13] = (byte) ((SAMPLE_RATE >> 8) & 0xff);
        head[14] = (byte) ((SAMPLE_RATE >> 16) & 0xff);
        head[15] = (byte) ((SAMPLE_RATE >> 24) & 0xff);
        head[16] = 0;
        head[17] = 0;
        head[18] = 0;
        return ByteBuffer.wrap(head);
    }

    private static ByteBuffer buildOpusCodecDelay() {
        ByteBuffer buf = ByteBuffer.allocate(8).order(ByteOrder.nativeOrder());
        buf.putLong(0);
        buf.flip();
        return buf;
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
        fmt.setByteBuffer("csd-0", buildOpusHead());
        fmt.setByteBuffer("csd-1", buildOpusCodecDelay());
        decoder = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_AUDIO_OPUS);
        decoder.configure(fmt, null, null, 0);
        decoder.start();
    }

    private boolean initRecorder() {
        int minBuf = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT);
        if (minBuf <= 0) {
            Log.e(TAG, "AudioRecord min buffer size invalid: " + minBuf);
            return false;
        }
        int buf = Math.max(minBuf, FRAME_SAMPLES * 4);
        int[] sources = {
                android.media.MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                android.media.MediaRecorder.AudioSource.MIC,
                android.media.MediaRecorder.AudioSource.DEFAULT,
        };
        for (int source : sources) {
            releaseRecorder();
            try {
                recorder = new AudioRecord(
                        source,
                        SAMPLE_RATE,
                        AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT,
                        buf);
                if (recorder.getState() == AudioRecord.STATE_INITIALIZED) {
                    recorder.startRecording();
                    Log.i(TAG, "AudioRecord started (source=" + source + ")");
                    return true;
                }
                Log.w(TAG, "AudioRecord not initialized for source=" + source);
            } catch (Exception e) {
                Log.w(TAG, "AudioRecord source=" + source + " failed", e);
            }
            releaseRecorder();
        }
        return false;
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