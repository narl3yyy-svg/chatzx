use audiopus::coder::{Decoder, Encoder};
use audiopus::{Application, Bitrate, Channels, SampleRate};

pub const SAMPLE_RATE: u32 = 48_000;
pub const FRAME_SAMPLES: usize = 480; // 10 ms @ 48 kHz — MTU-safe on RNS
pub const FRAME_BYTES: usize = FRAME_SAMPLES * 2;

pub struct OpusCodec {
    encoder: Encoder,
    decoder: Decoder,
}

impl OpusCodec {
    pub fn new() -> Result<Self, String> {
        let mut encoder = Encoder::new(SampleRate::Hz48000, Channels::Mono, Application::Voip)
            .map_err(|e| format!("opus encoder: {e}"))?;
        encoder
            .set_bitrate(Bitrate::BitsPerSecond(32_000))
            .map_err(|e| format!("opus bitrate: {e}"))?;
        let decoder = Decoder::new(SampleRate::Hz48000, Channels::Mono)
            .map_err(|e| format!("opus decoder: {e}"))?;
        Ok(Self { encoder, decoder })
    }

    pub fn encode_frame(&mut self, pcm: &[u8]) -> Result<Vec<u8>, String> {
        if pcm.len() < FRAME_BYTES {
            return Err(format!("need {FRAME_BYTES} bytes, got {}", pcm.len()));
        }
        let samples: Vec<i16> = pcm[..FRAME_BYTES]
            .chunks_exact(2)
            .map(|c| i16::from_le_bytes([c[0], c[1]]))
            .collect();
        let mut out = vec![0u8; 400];
        let len = self
            .encoder
            .encode(&samples, &mut out)
            .map_err(|e| format!("encode: {e}"))?;
        out.truncate(len);
        Ok(out)
    }

    pub fn decode_frame(&mut self, opus: &[u8]) -> Result<Vec<u8>, String> {
        let mut pcm = vec![0i16; FRAME_SAMPLES];
        let n = self
            .decoder
            .decode(Some(opus), &mut pcm, false)
            .map_err(|e| format!("decode: {e}"))?;
        pcm.truncate(n);
        pcm_to_bytes(&pcm)
    }

    /// Packet-loss concealment — Opus built-in PLC.
    pub fn decode_loss(&mut self) -> Result<Vec<u8>, String> {
        let mut pcm = vec![0i16; FRAME_SAMPLES];
        let n = self
            .decoder
            .decode(None::<&[u8]>, &mut pcm, false)
            .map_err(|e| format!("plc: {e}"))?;
        pcm.truncate(n);
        pcm_to_bytes(&pcm)
    }
}

fn pcm_to_bytes(pcm: &[i16]) -> Result<Vec<u8>, String> {
    let mut out = Vec::with_capacity(pcm.len() * 2);
    for s in pcm {
        out.extend_from_slice(&s.to_le_bytes());
    }
    Ok(out)
}