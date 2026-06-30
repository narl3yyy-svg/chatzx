/* chatxz v1.0.0 — voice/video/screen calls over RNS (no WebRTC) */

let callState = null;
let mediaWs = null;
let localStream = null;
let screenStream = null;
let audioCtx = null;
let micSource = null;
let audioProcessor = null;
let callTimer = null;
let callSeconds = 0;

const FRAME_MS = 20;
const SAMPLE_RATE = 48000;

function callEl(id) { return document.getElementById(id); }

/** Resolve the peer hash for the open chat (uses viewingPeer from index.html). */
function getCallPeer() {
  if (typeof viewingPeer !== 'undefined' && viewingPeer) return viewingPeer;
  return null;
}

function isCallPeerLinked() {
  const peer = getCallPeer();
  if (!peer) return false;
  if (typeof isPeerLinked === 'function') {
    return isPeerLinked(peer, typeof viewingVia !== 'undefined' ? viewingVia : null);
  }
  if (typeof linkedPeers !== 'undefined' && linkedPeers && linkedPeers.size > 0) return true;
  return false;
}

function updateCallUI() {
  const bar = callEl('call-bar');
  const overlay = callEl('call-overlay');
  const active = callState && ['outgoing','incoming','connecting','active'].includes(callState.state);
  if (bar) bar.style.display = active ? 'flex' : 'none';
  if (overlay) overlay.style.display = (callState && callState.state === 'active') ? 'flex' : 'none';
  const peer = getCallPeer();
  const linked = isCallPeerLinked();
  const btns = document.querySelectorAll('.call-action-btn');
  btns.forEach(b => {
    b.disabled = !peer || !linked || !!(callState && callState.state === 'active');
  });
  if (callState) {
    const label = callEl('call-status-label');
    if (label) {
      const modes = {audio:'Voice', video:'Video', screen:'Screen'};
      label.textContent = `${modes[callState.mode] || 'Call'} — ${callState.state}`;
    }
    const timer = callEl('call-timer');
    if (timer && callState.state === 'active') timer.textContent = formatCallTime(callSeconds);
  }
}

function formatCallTime(s) {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2,'0')}`;
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
  const peer = getCallPeer();
  if (!peer) { toast('Open a chat first'); return; }
  if (!isCallPeerLinked()) { toast('Wait for link to become Active'); return; }
  const d = await apiCall('start', {peer, mode});
  if (d.error) { toast(d.error === 'busy' ? 'Already in a call' : d.error); return; }
  callState = d.call;
  updateCallUI();
  await setupLocalMedia(mode);
  connectMediaWs();
}

async function acceptCall() {
  if (!callState) return;
  await apiCall('accept', {call_id: callState.call_id});
  await setupLocalMedia(callState.mode);
  connectMediaWs();
}

async function rejectCall() {
  if (!callState) return;
  await apiCall('reject', {call_id: callState.call_id});
  cleanupCall();
}

async function hangupCall() {
  if (callState) await apiCall('hangup', {call_id: callState.call_id});
  cleanupCall();
}

function cleanupCall() {
  stopCallTimer();
  stopLocalMedia();
  if (mediaWs) { try { mediaWs.close(); } catch(e){} mediaWs = null; }
  callState = null;
  if (window.chatxzAndroid && typeof window.chatxzAndroid.setCallActive === 'function') {
    try { window.chatxzAndroid.setCallActive(false); } catch (_) {}
  }
  updateCallUI();
}

function connectMediaWs() {
  if (mediaWs) return;
  const peer = getCallPeer() || '';
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  mediaWs = new WebSocket(`${proto}//${location.host}/ws/media?peer=${encodeURIComponent(peer)}`);
  mediaWs.binaryType = 'arraybuffer';
  mediaWs.onmessage = (ev) => {
    if (typeof ev.data === 'string') handleMediaJson(JSON.parse(ev.data));
  };
  mediaWs.onclose = () => { mediaWs = null; };
}

function handleMediaJson(msg) {
  if (msg.type !== 'media') return;
  if (msg.kind === 1) playAudioFrame(msg.data);
  else if (msg.kind === 2 || msg.kind === 3) renderVideoFrame(msg);
}

function playAudioFrame(hexData) {
  if (!hexData) return;
  audioCtx = audioCtx || new AudioContext({sampleRate: SAMPLE_RATE});
  try {
    const bytes = hexToBytes(hexData);
    const samples = bytes.length / 2;
    const buf = audioCtx.createBuffer(1, samples, SAMPLE_RATE);
    const ch = buf.getChannelData(0);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let i = 0; i < samples; i++) ch[i] = view.getInt16(i * 2, true) / 32768;
    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(audioCtx.destination);
    src.start();
  } catch (e) { console.warn('audio play', e); }
}

function renderVideoFrame(msg) {
  const el = callEl(msg.kind === 3 ? 'call-screen-remote' : 'call-video-remote');
  if (!el || !msg.data) return;
  const bytes = hexToBytes(msg.data);
  if (bytes.length < 4) return;
  const blob = new Blob([bytes], {type: 'image/jpeg'});
  const url = URL.createObjectURL(blob);
  if (el.tagName === 'IMG') el.src = url;
  else { el.src = url; el.play?.().catch(() => {}); }
}

function hexToBytes(hex) {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.substr(i * 2, 2), 16);
  return out;
}

async function setupLocalMedia(mode) {
  stopLocalMedia();
  const constraints = {audio: {echoCancellation:true, noiseSuppression:true, autoGainControl:true}};
  if (mode === 'video' || mode === 'screen') {
    constraints.video = {width:{ideal:1280}, height:{ideal:720}, frameRate:{ideal:24}};
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
    screenStream = await navigator.mediaDevices.getDisplayMedia({video:true, audio:false});
    const el = callEl('call-screen-local');
    if (el) {
      if (el.tagName === 'VIDEO') {
        el.srcObject = screenStream;
        el.muted = true;
        el.style.display = '';
        el.play().catch(() => {});
      }
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
  const frameSamples = 960;
  audioProcessor.onaudioprocess = (ev) => {
    if (!callState || callState.state !== 'active' || !mediaWs || mediaWs.readyState !== 1) return;
    if (callState.muted) return;
    const input = ev.inputBuffer.getChannelData(0);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      acc.push(s < 0 ? s * 0x8000 : s * 0x7FFF);
    }
    while (acc.length >= frameSamples) {
      const chunk = acc.splice(0, frameSamples);
      const pcm = new Int16Array(chunk);
      const buf = new ArrayBuffer(5 + pcm.byteLength);
      const view = new DataView(buf);
      view.setUint8(0, 1);
      view.setUint32(1, (Date.now() & 0xFFFFFFFF) >>> 0);
      new Uint8Array(buf, 5).set(new Uint8Array(pcm.buffer));
      mediaWs.send(buf);
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
      while (callState && callState.state === 'active') {
        const {value: frame, done} = await reader.read();
        if (done) break;
        if (!mediaWs || mediaWs.readyState !== 1) { frame.close(); continue; }
        try {
          sendVideoFrame(frame, isScreen);
        } catch (e) { console.warn('video frame', e); }
        frame.close();
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
    if (!callState || callState.state !== 'active') return;
    if (video.videoWidth && mediaWs && mediaWs.readyState === 1) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      ctx.drawImage(video, 0, 0);
      canvas.toBlob(blob => {
        if (!blob || !mediaWs || mediaWs.readyState !== 1) return;
        blob.arrayBuffer().then(ab => {
          const jpg = new Uint8Array(ab);
          const buf = new ArrayBuffer(6 + jpg.length);
          const view = new DataView(buf);
          view.setUint8(0, isScreen ? 3 : 2);
          view.setUint32(1, (Date.now() & 0xFFFFFFFF) >>> 0);
          view.setUint8(5, 1);
          new Uint8Array(buf, 6).set(jpg);
          mediaWs.send(buf);
        });
      }, 'image/jpeg', 0.65);
    }
    setTimeout(tick, 100);
  };
  tick();
}

function sendVideoFrame(frame, isScreen) {
  const canvas = document.createElement('canvas');
  canvas.width = frame.displayWidth;
  canvas.height = frame.displayHeight;
  canvas.getContext('2d').drawImage(frame, 0, 0);
  return new Promise(resolve => {
    canvas.toBlob(blob => {
      if (!blob || !mediaWs || mediaWs.readyState !== 1) { resolve(); return; }
      blob.arrayBuffer().then(ab => {
        const jpg = new Uint8Array(ab);
        const buf = new ArrayBuffer(6 + jpg.length);
        const view = new DataView(buf);
        view.setUint8(0, isScreen ? 3 : 2);
        view.setUint32(1, (Date.now() & 0xFFFFFFFF) >>> 0);
        view.setUint8(5, 1);
        new Uint8Array(buf, 6).set(jpg);
        mediaWs.send(buf);
        resolve();
      });
    }, 'image/jpeg', 0.65);
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
    if (window.chatxzAndroid && typeof window.chatxzAndroid.vibrateIncomingCall === 'function') {
      try { window.chatxzAndroid.vibrateIncomingCall(); } catch (_) {}
    }
    showIncomingCallDialog(data);
  } else if (ev === 'accepted') {
    callState = data;
    updateCallUI();
    startCallTimer();
    connectMediaWs();
  } else if (ev === 'ended' || ev === 'rejected' || ev === 'busy') {
    toast(ev === 'busy' ? 'Peer is busy' : 'Call ended');
    cleanupCall();
  } else if (ev === 'state' || ev === 'update' || ev === 'outgoing') {
    callState = data;
    updateCallUI();
    if (data.state === 'active') {
      startCallTimer();
      connectMediaWs();
    }
  }
}

function showIncomingCallDialog(data) {
  const modes = {audio:'Voice', video:'Video', screen:'Screen'};
  const mode = modes[data.mode] || 'Call';
  if (confirm(`Incoming ${mode} call. Accept?`)) acceptCall();
  else rejectCall();
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