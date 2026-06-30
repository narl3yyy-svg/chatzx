/* chatxz v1.0.0 — voice/video/screen over RNS (Rust media engine) */

let callState = null;
let mediaWs = null;
let localStream = null;
let screenStream = null;
let audioCtx = null;
let micSource = null;
let audioProcessor = null;
let callTimer = null;
let callSeconds = 0;
let nextAudioTime = 0;
let videoJpegQuality = 0.65;
let videoFrameIntervalMs = 100;
let mediaStatsTimer = null;
let lastMediaStatsSent = 0;

const FRAME_MS = 20;
const SAMPLE_RATE = 48000;
const FRAME_SAMPLES = 480;  // 10ms @ 48kHz — matches Rust Opus frame size

function callEl(id) { return document.getElementById(id); }

function resolveCallPeerHash() {
  const peer = (typeof viewingPeer !== 'undefined' && viewingPeer) ? viewingPeer : null;
  if (!peer) return null;
  if (typeof linkedPeers !== 'undefined' && linkedPeers && typeof peersMatch === 'function') {
    for (const lp of linkedPeers) {
      const base = String(lp).split(':')[0];
      if (peersMatch(base, peer)) return base;
    }
  }
  if (typeof linkPeer !== 'undefined' && linkPeer) {
    const base = String(linkPeer).split(':')[0];
    if (!base || typeof peersMatch !== 'function' || peersMatch(base, peer)) return base || peer;
  }
  return peer;
}

function getCallPeer() {
  return resolveCallPeerHash();
}

function isCallPeerLinked() {
  const peer = resolveCallPeerHash();
  if (!peer) return false;

  const statusEl = document.getElementById('peer-status');
  if (statusEl && /\bConnected\b/i.test(statusEl.textContent || '')) return true;

  if (typeof linkPeer !== 'undefined' && linkPeer && typeof peersMatch === 'function') {
    if (peersMatch(String(linkPeer).split(':')[0], peer)) return true;
  }

  if (typeof isPeerLinked === 'function') {
    if (isPeerLinked(peer, null)) return true;
    const via = (typeof viewingVia !== 'undefined') ? viewingVia : null;
    if (via && isPeerLinked(peer, via)) return true;
    if (!via && isPeerLinked(peer, 'lan')) return true;
  }

  if (typeof linkedPeers !== 'undefined' && linkedPeers && typeof peersMatch === 'function') {
    for (const lp of linkedPeers) {
      if (peersMatch(String(lp).split(':')[0], peer)) return true;
    }
  }
  return false;
}

function callIsActive() {
  return callState && ['outgoing', 'incoming', 'connecting', 'active'].includes(callState.state);
}

function callMediaReady() {
  return callState && (callState.state === 'active' || callState.state === 'outgoing');
}

function updateCallUI() {
  const bar = callEl('call-bar');
  const overlay = callEl('call-overlay');
  const incoming = callEl('incoming-call-prompt');
  const active = callIsActive();
  const ringing = callState && callState.state === 'incoming';
  if (incoming) {
    incoming.classList.toggle('active', !!ringing);
    const label = callEl('incoming-call-label');
    if (label && ringing) {
      const modes = {audio: 'Voice', video: 'Video', screen: 'Screen'};
      label.textContent = `Incoming ${modes[callState.mode] || 'call'}`;
    }
  }
  if (bar) bar.style.display = (active && !ringing) ? 'flex' : 'none';
  if (overlay) overlay.style.display = (callState && callState.state === 'active') ? 'flex' : 'none';
  const peer = resolveCallPeerHash();
  const linked = isCallPeerLinked();
  const btns = document.querySelectorAll('.call-action-btn');
  btns.forEach(b => {
    b.disabled = !peer || !linked || !!(callState && callState.state === 'active');
  });
  if (callState) {
    const label = callEl('call-status-label');
    if (label) {
      const modes = {audio: 'Voice', video: 'Video', screen: 'Screen'};
      label.textContent = `${modes[callState.mode] || 'Call'} — ${callState.state}`;
    }
    const timer = callEl('call-timer');
    if (timer && callState.state === 'active') timer.textContent = formatCallTime(callSeconds);
  }
}

function formatCallTime(s) {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, '0')}`;
}

function startCallTimer() {
  stopCallTimer();
  callSeconds = 0;
  callTimer = setInterval(() => { callSeconds++; updateCallUI(); }, 1000);
}

function stopCallTimer() {
  if (callTimer) { clearInterval(callTimer); callTimer = null; }
  callSeconds = 0;
}

async function apiCall(action, body) {
  const r = await fetch(`/api/call/${action}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

async function startCall(mode) {
  const peer = resolveCallPeerHash();
  if (!peer) { toast('Open a chat first'); return; }
  if (!isCallPeerLinked()) {
    const probe = await apiCall('status', {peer});
    if (probe.error === 'not_linked') {
      toast('Wait for link to become Active');
      return;
    }
  }
  const d = await apiCall('start', {peer, mode});
  if (d.error) {
    if (d.error === 'busy') toast('Already in a call');
    else if (d.error === 'not_linked') toast('Wait for link to become Active');
    else toast(d.error);
    return;
  }
  callState = d.call;
  updateCallUI();
  await setupLocalMedia(mode);
  connectMediaWs();
}

async function acceptCall() {
  if (!callState) return;
  const d = await apiCall('accept', {call_id: callState.call_id});
  if (d.error || d.status === 'error') {
    toast('Could not accept call');
    return;
  }
  callState = {...callState, state: 'active'};
  updateCallUI();
  startCallTimer();
  await setupLocalMedia(callState.mode);
  connectMediaWs();
}

async function rejectCall() {
  if (!callState) return;
  await apiCall('reject', {call_id: callState.call_id});
  cleanupCall();
}

async function hangupCall() {
  const cid = callState && callState.call_id;
  if (cid) await apiCall('hangup', {call_id: cid});
  cleanupCall();
}

function cleanupCall() {
  stopCallTimer();
  stopMediaStats();
  stopLocalMedia();
  nextAudioTime = 0;
  if (mediaWs) { try { mediaWs.close(); } catch (e) {} mediaWs = null; }
  callState = null;
  if (window.chatxzAndroid && typeof window.chatxzAndroid.setCallActive === 'function') {
    try { window.chatxzAndroid.setCallActive(false); } catch (_) {}
  }
  if (window.chatxzAndroid && typeof window.chatxzAndroid.stopCallVibrate === 'function') {
    try { window.chatxzAndroid.stopCallVibrate(); } catch (_) {}
  }
  updateCallUI();
}

function connectMediaWs() {
  if (mediaWs && mediaWs.readyState === WebSocket.OPEN) return;
  if (mediaWs) { try { mediaWs.close(); } catch (e) {} mediaWs = null; }
  const peer = getCallPeer() || '';
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  mediaWs = new WebSocket(`${proto}//${location.host}/ws/media?peer=${encodeURIComponent(peer)}`);
  mediaWs.binaryType = 'arraybuffer';
  mediaWs.onopen = () => startMediaStats();
  mediaWs.onmessage = (ev) => {
    if (typeof ev.data === 'string') handleMediaJson(JSON.parse(ev.data));
  };
  mediaWs.onclose = () => { mediaWs = null; stopMediaStats(); };
}

function handleMediaJson(msg) {
  if (msg.type === 'stats') {
    applyRemoteMediaStats(msg);
    return;
  }
  if (msg.type !== 'media') return;
  if (msg.peer && getCallPeer() && typeof peersMatch === 'function' && !peersMatch(msg.peer, getCallPeer())) return;
  if (msg.kind === 1) playAudioFrame(msg.data);
  else if (msg.kind === 2 || msg.kind === 3) renderVideoFrame(msg);
}

function playAudioFrame(hexData) {
  if (!hexData || !callMediaReady()) return;
  audioCtx = audioCtx || new AudioContext({sampleRate: SAMPLE_RATE});
  if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
  try {
    const bytes = hexToBytes(hexData);
    if (bytes.length < 4) return;
    const samples = Math.floor(bytes.length / 2);
    const buf = audioCtx.createBuffer(1, samples, SAMPLE_RATE);
    const ch = buf.getChannelData(0);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let i = 0; i < samples; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(audioCtx.destination);
    const now = audioCtx.currentTime;
    if (nextAudioTime < now) nextAudioTime = now + 0.02;
    src.start(nextAudioTime);
    nextAudioTime += buf.duration;
  } catch (e) { console.warn('audio play', e); }
}

function renderVideoFrame(msg) {
  const el = callEl(msg.kind === 3 ? 'call-screen-remote' : 'call-video-remote');
  if (!el || !msg.data) return;
  const bytes = hexToBytes(msg.data);
  if (bytes.length < 4) return;
  const blob = new Blob([bytes], {type: 'image/jpeg'});
  const url = URL.createObjectURL(blob);
  if (el._lastBlobUrl) URL.revokeObjectURL(el._lastBlobUrl);
  el._lastBlobUrl = url;
  if (el.tagName === 'IMG') el.src = url;
  else { el.src = url; el.play?.().catch(() => {}); }
}

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

function applyRemoteMediaStats(stats) {
  if (!stats) return;
  const loss = Number(stats.loss_pct) || 0;
  const jitter = Number(stats.jitter_ms) || 0;
  if (loss > 8 || jitter > 120) {
    videoJpegQuality = Math.max(0.35, videoJpegQuality - 0.08);
    videoFrameIntervalMs = Math.min(250, videoFrameIntervalMs + 25);
  } else if (loss < 2 && jitter < 60) {
    videoJpegQuality = Math.min(0.75, videoJpegQuality + 0.03);
    videoFrameIntervalMs = Math.max(66, videoFrameIntervalMs - 10);
  }
}

function startMediaStats() {
  stopMediaStats();
  mediaStatsTimer = setInterval(() => {
    if (!mediaWs || mediaWs.readyState !== 1 || !callState) return;
    const now = Date.now();
    if (now - lastMediaStatsSent < 1800) return;
    lastMediaStatsSent = now;
    mediaWs.send(JSON.stringify({
      type: 'stats',
      peer: getCallPeer(),
      jitter_ms: callState._localJitter || 0,
      loss_pct: callState._localLoss || 0,
      video_quality: videoJpegQuality,
    }));
  }, 2000);
}

function stopMediaStats() {
  if (mediaStatsTimer) { clearInterval(mediaStatsTimer); mediaStatsTimer = null; }
}

async function setupLocalMedia(mode) {
  stopLocalMedia();
  const constraints = {audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}};
  if (mode === 'video' || mode === 'screen') {
    constraints.video = {width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 24}};
  }
  if (window.chatxzAndroid && !window.chatxzAndroid.hasAudioPermission()) {
    window.chatxzAndroid.requestAudioPermission();
    toast('Allow microphone for calls');
    return;
  }
  try {
    localStream = await navigator.mediaDevices.getUserMedia(constraints);
  } catch (e) {
    toast(micErrorMessage(e));
    return;
  }
  if (window.chatxzAndroid && typeof window.chatxzAndroid.setCallActive === 'function') {
    try { window.chatxzAndroid.setCallActive(true); } catch (_) {}
  }
  const localVid = callEl('call-video-local');
  if (localVid && localStream.getVideoTracks().length) {
    if (localVid.tagName === 'VIDEO') {
      localVid.srcObject = localStream;
      localVid.muted = true;
      localVid.play().catch(() => {});
    }
    startVideoCapture(localStream, false);
  }
  startAudioCapture(localStream);
  if (mode === 'screen') await startScreenShare();
}

async function startScreenShare() {
  try {
    screenStream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: false});
    const el = callEl('call-screen-local');
    if (el && el.tagName === 'VIDEO') {
      el.srcObject = screenStream;
      el.muted = true;
      el.style.display = '';
      el.play().catch(() => {});
    }
    startVideoCapture(screenStream, true);
    await apiCall('update', {screen: true, peer: getCallPeer()});
  } catch (e) { toast('Screen share denied'); }
}

function startAudioCapture(stream) {
  audioCtx = audioCtx || new AudioContext({sampleRate: SAMPLE_RATE});
  if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
  micSource = audioCtx.createMediaStreamSource(stream);
  audioProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
  let acc = [];
  audioProcessor.onaudioprocess = (ev) => {
    if (!callMediaReady() || !mediaWs || mediaWs.readyState !== 1) return;
    if (callState && callState.muted) return;
    const input = ev.inputBuffer.getChannelData(0);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      acc.push(s < 0 ? s * 0x8000 : s * 0x7FFF);
    }
    while (acc.length >= FRAME_SAMPLES) {
      const chunk = acc.splice(0, FRAME_SAMPLES);
      const pcm = new Int16Array(chunk);
      const buf = new ArrayBuffer(5 + pcm.byteLength);
      const view = new DataView(buf);
      view.setUint8(0, 1);
      view.setUint32(1, (Date.now() & 0xFFFFFFFF) >>> 0);
      new Uint8Array(buf, 5).set(new Uint8Array(pcm.buffer));
      try { mediaWs.send(buf); } catch (_) {}
    }
  };
  micSource.connect(audioProcessor);
  audioProcessor.connect(audioCtx.destination);
}

function startVideoCapture(stream, isScreen) {
  const track = stream.getVideoTracks()[0];
  if (!track) return;
  if (window.MediaStreamTrackProcessor && window.VideoFrame) {
    const processor = new MediaStreamTrackProcessor({track});
    const reader = processor.readable.getReader();
    (async function pump() {
      while (callState && callMediaReady()) {
        const {value: frame, done} = await reader.read();
        if (done) break;
        if (!mediaWs || mediaWs.readyState !== 1) { frame.close(); continue; }
        try { await sendVideoFrame(frame, isScreen); } catch (e) { console.warn('video frame', e); }
        frame.close();
        await new Promise(r => setTimeout(r, videoFrameIntervalMs));
      }
    })();
    return;
  }
  const video = document.createElement('video');
  video.srcObject = stream;
  video.muted = true;
  video.play().catch(() => {});
  const canvas = document.createElement('canvas');
  const ctx = canvas.getContext('2d');
  const tick = () => {
    if (!callState || !callMediaReady()) return;
    if (video.videoWidth && mediaWs && mediaWs.readyState === 1) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0);
      canvas.toBlob(blob => {
        if (!blob || !mediaWs || mediaWs.readyState !== 1) return;
        blob.arrayBuffer().then(ab => sendJpegBytes(new Uint8Array(ab), isScreen));
      }, 'image/jpeg', videoJpegQuality);
    }
    setTimeout(tick, videoFrameIntervalMs);
  };
  tick();
}

function sendJpegBytes(jpg, isScreen) {
  const buf = new ArrayBuffer(6 + jpg.length);
  const view = new DataView(buf);
  view.setUint8(0, isScreen ? 3 : 2);
  view.setUint32(1, (Date.now() & 0xFFFFFFFF) >>> 0);
  view.setUint8(5, 1);
  new Uint8Array(buf, 6).set(jpg);
  mediaWs.send(buf);
}

function sendVideoFrame(frame, isScreen) {
  const canvas = document.createElement('canvas');
  canvas.width = frame.displayWidth;
  canvas.height = frame.displayHeight;
  canvas.getContext('2d').drawImage(frame, 0, 0);
  return new Promise(resolve => {
    canvas.toBlob(blob => {
      if (!blob || !mediaWs || mediaWs.readyState !== 1) { resolve(); return; }
      blob.arrayBuffer().then(ab => { sendJpegBytes(new Uint8Array(ab), isScreen); resolve(); });
    }, 'image/jpeg', videoJpegQuality);
  });
}

function stopLocalMedia() {
  if (audioProcessor) { audioProcessor.disconnect(); audioProcessor = null; }
  if (micSource) { micSource.disconnect(); micSource = null; }
  if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
  if (screenStream) { screenStream.getTracks().forEach(t => t.stop()); screenStream = null; }
}

function onCallWsEvent(ev, data) {
  if (ev === 'incoming') {
    callState = data;
    updateCallUI();
    toast(`Incoming ${data.mode || 'voice'} call — tap Accept`);
    if (window.chatxzAndroid && typeof window.chatxzAndroid.vibrateIncomingCall === 'function') {
      try { window.chatxzAndroid.vibrateIncomingCall(); } catch (_) {}
    }
    if (typeof showMessageNotification === 'function') {
      try { showMessageNotification(data.peer || 'peer', `Incoming ${data.mode || 'voice'} call`); } catch (_) {}
    }
  } else if (ev === 'accepted') {
    callState = data;
    updateCallUI();
    startCallTimer();
    if (!localStream) setupLocalMedia(data.mode || 'audio');
    connectMediaWs();
  } else if (ev === 'ended' || ev === 'rejected' || ev === 'busy') {
    toast(ev === 'busy' ? 'Peer is busy' : 'Call ended');
    cleanupCall();
  } else if (ev === 'state' || ev === 'update' || ev === 'outgoing') {
    callState = data;
    updateCallUI();
    if (data.state === 'active') {
      startCallTimer();
      if (!localStream) setupLocalMedia(data.mode || 'audio');
      connectMediaWs();
    }
    if (ev === 'update' && data.stats) applyRemoteMediaStats(data.stats);
  }
}

function micErrorMessage(err) {
  if (!err) return 'Microphone unavailable';
  if (err.name === 'NotAllowedError') return 'Microphone permission denied';
  if (err.name === 'NotFoundError') return 'No microphone found';
  return err.message || 'Microphone error';
}

async function toggleMute() {
  if (!callState) return;
  callState.muted = !callState.muted;
  if (localStream) localStream.getAudioTracks().forEach(t => { t.enabled = !callState.muted; });
  await apiCall('update', {muted: callState.muted, peer: getCallPeer()});
  updateCallUI();
}

window.startVoiceCall = () => startCall('audio');
window.startVideoCall = () => startCall('video');
window.startScreenCall = () => startCall('screen');
window.acceptCall = acceptCall;
window.rejectCall = rejectCall;
window.hangupCall = hangupCall;
window.toggleMute = toggleMute;
window.startScreenShare = startScreenShare;
window.onCallWsEvent = onCallWsEvent;
window.updateCallUI = updateCallUI;
window.isCallPeerLinked = isCallPeerLinked;