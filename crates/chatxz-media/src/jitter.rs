use std::collections::BTreeMap;

use chatxz_protocol::MediaPacket;

const DEFAULT_TARGET_MS: u32 = 40;
const MIN_TARGET_MS: u32 = 20;
const MAX_TARGET_MS: u32 = 180;
const FRAME_MS: u32 = 10;

pub struct JitterBuffer {
    packets: BTreeMap<u32, MediaPacket>,
    next_seq: u32,
    target_delay_ms: u32,
    max_packets: usize,
}

impl JitterBuffer {
    pub fn new() -> Self {
        Self {
            packets: BTreeMap::new(),
            next_seq: 0,
            target_delay_ms: DEFAULT_TARGET_MS,
            max_packets: 96,
        }
    }

    pub fn reset(&mut self) {
        self.packets.clear();
        self.next_seq = 0;
        self.target_delay_ms = DEFAULT_TARGET_MS;
    }

    pub fn push(&mut self, packet: MediaPacket) {
        self.packets.insert(packet.sequence, packet);
        while self.packets.len() > self.max_packets {
            if let Some(&first) = self.packets.keys().next() {
                self.packets.remove(&first);
                self.next_seq = first.saturating_add(1);
            }
        }
        self.adapt_delay();
    }

    fn adapt_delay(&mut self) {
        let span = self.packets.len() as u32 * FRAME_MS;
        if span > self.target_delay_ms + FRAME_MS * 2 {
            self.target_delay_ms = (self.target_delay_ms + 5).min(MAX_TARGET_MS);
        } else if span < self.target_delay_ms.saturating_sub(FRAME_MS * 3)
            && self.target_delay_ms > MIN_TARGET_MS
        {
            self.target_delay_ms = self.target_delay_ms.saturating_sub(2).max(MIN_TARGET_MS);
        }
    }

    pub fn pop_ready(&mut self, now_ms: u32) -> Option<MediaPacket> {
        if self.packets.is_empty() {
            return None;
        }
        let oldest_ts = self.packets.values().next()?.timestamp_ms;
        if now_ms.saturating_sub(oldest_ts) < self.target_delay_ms && self.packets.len() < 2 {
            return None;
        }
        let seq = if self.packets.contains_key(&self.next_seq) {
            self.next_seq
        } else {
            *self.packets.keys().next()?
        };
        let pkt = self.packets.remove(&seq)?;
        self.next_seq = seq.saturating_add(1);
        Some(pkt)
    }

    pub fn depth(&self) -> usize {
        self.packets.len()
    }

    pub fn target_delay_ms(&self) -> u32 {
        self.target_delay_ms
    }
}

impl Default for JitterBuffer {
    fn default() -> Self {
        Self::new()
    }
}