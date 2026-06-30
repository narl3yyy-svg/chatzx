use chatxz_protocol::{
    MediaKind, MediaPacket, FLAG_FRAG, FLAG_FRAG_LAST, FLAG_KEYFRAME, MAX_PAYLOAD,
};

use crate::jitter::JitterBuffer;
use crate::opus::OpusCodec;

pub struct MediaEngine {
    opus: OpusCodec,
    jitter: JitterBuffer,
    tx_seq: u32,
}

impl MediaEngine {
    pub fn new() -> Result<Self, String> {
        Ok(Self {
            opus: OpusCodec::new()?,
            jitter: JitterBuffer::new(),
            tx_seq: 0,
        })
    }

    pub fn reset(&mut self) {
        self.jitter.reset();
        self.tx_seq = 0;
    }

    pub fn encode_audio_pcm(&mut self, pcm: &[u8]) -> Result<Vec<u8>, String> {
        self.opus.encode_frame(pcm)
    }

    pub fn packetize_audio(&mut self, opus: &[u8], timestamp_ms: u32) -> Vec<Vec<u8>> {
        chunk_payload(MediaKind::Audio, 0, &mut self.tx_seq, timestamp_ms, opus)
    }

    pub fn packetize_video(&mut self, jpeg: &[u8], timestamp_ms: u32, keyframe: bool) -> Vec<Vec<u8>> {
        let flags = if keyframe { FLAG_KEYFRAME } else { 0 };
        chunk_payload(MediaKind::Video, flags, &mut self.tx_seq, timestamp_ms, jpeg)
    }

    pub fn packetize_screen(&mut self, jpeg: &[u8], timestamp_ms: u32, keyframe: bool) -> Vec<Vec<u8>> {
        let flags = if keyframe { FLAG_KEYFRAME } else { 0 };
        chunk_payload(MediaKind::Screen, flags, &mut self.tx_seq, timestamp_ms, jpeg)
    }

    pub fn ingest(&mut self, data: &[u8]) -> Option<MediaPacket> {
        let pkt = MediaPacket::decode(data)?;
        self.jitter.push(pkt.clone());
        Some(pkt)
    }

    pub fn pop_audio_opus(&mut self, now_ms: u32) -> Option<Vec<u8>> {
        let pkt = self.jitter.pop_ready(now_ms)?;
        if pkt.kind != MediaKind::Audio {
            return None;
        }
        Some(pkt.payload)
    }

    pub fn decode_audio_opus(&mut self, opus: &[u8]) -> Result<Vec<u8>, String> {
        self.opus.decode_frame(opus)
    }

    pub fn decode_audio_loss(&mut self) -> Result<Vec<u8>, String> {
        self.opus.decode_loss()
    }

    pub fn jitter_depth(&self) -> usize {
        self.jitter.depth()
    }

    pub fn jitter_delay_ms(&self) -> u32 {
        self.jitter.target_delay_ms()
    }
}

fn chunk_payload(
    kind: MediaKind,
    base_flags: u8,
    seq: &mut u32,
    timestamp_ms: u32,
    data: &[u8],
) -> Vec<Vec<u8>> {
    if data.len() <= MAX_PAYLOAD {
        let pkt = MediaPacket {
            kind,
            flags: base_flags,
            sequence: *seq,
            timestamp_ms,
            payload: data.to_vec(),
        };
        *seq = seq.wrapping_add(1);
        return vec![pkt.encode()];
    }
    let mut out = Vec::new();
    let chunks: Vec<_> = data.chunks(MAX_PAYLOAD).collect();
    let last = chunks.len().saturating_sub(1);
    for (i, chunk) in chunks.into_iter().enumerate() {
        let mut flags = base_flags | FLAG_FRAG;
        if i == last {
            flags |= FLAG_FRAG_LAST;
        }
        let pkt = MediaPacket {
            kind,
            flags,
            sequence: *seq,
            timestamp_ms,
            payload: chunk.to_vec(),
        };
        *seq = seq.wrapping_add(1);
        out.push(pkt.encode());
    }
    out
}