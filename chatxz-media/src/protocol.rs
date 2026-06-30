//! Media packet framing for RNS transport.
//!
//! Wire format:
//!   magic[4]   = b"CXMZ"
//!   version[1] = 1
//!   kind[1]    = payload type (audio/video/screen/control)
//!   flags[1]   = keyframe, fec, etc.
//!   seq[4]     = sequence number (big-endian u32)
//!   ts[4]      = timestamp ms (big-endian u32)
//!   len[2]     = payload length (big-endian u16)
//!   payload[len]

pub const MAGIC: &[u8; 4] = b"CXMZ";
pub const HEADER_SIZE: usize = 17;
pub const MAX_PAYLOAD: usize = 1200;

#[repr(u8)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
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
        out.push(1);
        out.push(self.kind as u8);
        out.push(self.flags);
        out.extend_from_slice(&self.sequence.to_be_bytes());
        out.extend_from_slice(&self.timestamp_ms.to_be_bytes());
        out.extend_from_slice(&(plen as u16).to_be_bytes());
        out.extend_from_slice(&self.payload[..plen]);
        out
    }

    pub fn decode(data: &[u8]) -> Option<Self> {
        if data.len() < HEADER_SIZE {
            return None;
        }
        if &data[0..4] != MAGIC {
            return None;
        }
        if data[4] != 1 {
            return None;
        }
        let kind = MediaKind::from_u8(data[5])?;
        let flags = data[6];
        let sequence = u32::from_be_bytes([data[7], data[8], data[9], data[10]]);
        let timestamp_ms = u32::from_be_bytes([data[11], data[12], data[13], data[14]]);
        let plen = u16::from_be_bytes([data[15], data[16]]) as usize;
        if data.len() < HEADER_SIZE + plen {
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
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip() {
        let pkt = MediaPacket {
            kind: MediaKind::Audio,
            flags: 0,
            sequence: 42,
            timestamp_ms: 1000,
            payload: vec![1, 2, 3, 4],
        };
        let enc = pkt.encode();
        let dec = MediaPacket::decode(&enc).unwrap();
        assert_eq!(dec.sequence, 42);
        assert_eq!(dec.payload, vec![1, 2, 3, 4]);
    }
}