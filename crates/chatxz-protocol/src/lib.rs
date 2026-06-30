//! Clean media framing for RNS links (v2 rewrite).
//!
//! Wire format:
//!   magic[4]   = b"CHXZ"
//!   version[1] = 2
//!   kind[1]    = audio | video | screen | control
//!   flags[1]   = bit0 keyframe, bit1 fragment, bit2 last-frag
//!   seq[4]     = sequence (BE u32)
//!   ts[4]       = timestamp ms (BE u32)
//!   len[2]      = payload length (BE u16)
//!   payload[len]

pub const MAGIC: &[u8; 4] = b"CHXZ";
pub const VERSION: u8 = 2;
pub const HEADER_SIZE: usize = 17;
/// RNS link MTU ~1064; leave headroom for encryption overhead.
pub const MAX_PAYLOAD: usize = 480;
pub const MAX_PACKET: usize = HEADER_SIZE + MAX_PAYLOAD;

pub const FLAG_KEYFRAME: u8 = 0x01;
pub const FLAG_FRAG: u8 = 0x02;
pub const FLAG_FRAG_LAST: u8 = 0x04;

#[repr(u8)]
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MediaKind {
    Audio = 1,
    Video = 2,
    Screen = 3,
    Control = 4,
}

impl MediaKind {
    pub fn from_u8(v: u8) -> Option<Self> {
        match v {
            1 => Some(Self::Audio),
            2 => Some(Self::Video),
            3 => Some(Self::Screen),
            4 => Some(Self::Control),
            _ => None,
        }
    }
}

#[derive(Clone, Debug)]
pub struct MediaPacket {
    pub kind: MediaKind,
    pub flags: u8,
    pub sequence: u32,
    pub timestamp_ms: u32,
    pub payload: Vec<u8>,
}

impl MediaPacket {
    pub fn encode(&self) -> Vec<u8> {
        let plen = self.payload.len().min(MAX_PAYLOAD);
        let mut out = Vec::with_capacity(HEADER_SIZE + plen);
        out.extend_from_slice(MAGIC);
        out.push(VERSION);
        out.push(self.kind as u8);
        out.push(self.flags);
        out.extend_from_slice(&self.sequence.to_be_bytes());
        out.extend_from_slice(&self.timestamp_ms.to_be_bytes());
        out.extend_from_slice(&(plen as u16).to_be_bytes());
        out.extend_from_slice(&self.payload[..plen]);
        out
    }

    pub fn decode(data: &[u8]) -> Option<Self> {
        if data.len() < HEADER_SIZE || &data[0..4] != MAGIC || data[4] != VERSION {
            return None;
        }
        let kind = MediaKind::from_u8(data[5])?;
        let flags = data[6];
        let sequence = u32::from_be_bytes([data[7], data[8], data[9], data[10]]);
        let timestamp_ms = u32::from_be_bytes([data[11], data[12], data[13], data[14]]);
        let plen = u16::from_be_bytes([data[15], data[16]]) as usize;
        if data.len() < HEADER_SIZE + plen || plen > MAX_PAYLOAD {
            return None;
        }
        Some(Self {
            kind,
            flags,
            sequence,
            timestamp_ms,
            payload: data[HEADER_SIZE..HEADER_SIZE + plen].to_vec(),
        })
    }

    pub fn fits_mtu(data: &[u8]) -> bool {
        data.len() <= MAX_PACKET
    }
}

pub fn is_media_packet(data: &[u8]) -> bool {
    data.len() >= HEADER_SIZE && &data[0..4] == MAGIC && data[4] == VERSION
}

/// Signaling envelope on RNS (`__call` message body).
pub const SIGNAL_MESSAGE_TYPE: &str = "__call";

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallSignal {
    pub action: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub call_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub muted: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub video: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub screen: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stats: Option<serde_json::Value>,
}

impl CallSignal {
    pub fn to_json(&self) -> String {
        serde_json::to_string(self).unwrap_or_else(|_| "{}".into())
    }

    pub fn from_json(s: &str) -> Option<Self> {
        serde_json::from_str(s).ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn media_roundtrip() {
        let pkt = MediaPacket {
            kind: MediaKind::Audio,
            flags: 0,
            sequence: 7,
            timestamp_ms: 120,
            payload: vec![9, 8, 7],
        };
        let enc = pkt.encode();
        assert!(MediaPacket::fits_mtu(&enc));
        let dec = MediaPacket::decode(&enc).unwrap();
        assert_eq!(dec.sequence, 7);
        assert_eq!(dec.payload, vec![9, 8, 7]);
    }
}