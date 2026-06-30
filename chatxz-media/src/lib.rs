mod jitter_buffer;
mod opus_codec;
mod protocol;

use jitter_buffer::JitterBuffer;
use opus_codec::{OpusEngine, FRAME_BYTES};
use protocol::{MediaKind, MediaPacket, HEADER_SIZE, MAGIC, MAX_PAYLOAD};
use pyo3::prelude::*;
#[pyclass(unsendable)]
struct MediaSession {
    opus: OpusEngine,
    jitter: JitterBuffer,
    tx_seq: u32,
    start_ts: u64,
}

#[pymethods]
impl MediaSession {
    #[new]
    fn new() -> PyResult<Self> {
        Ok(Self {
            opus: OpusEngine::new().map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)?,
            jitter: JitterBuffer::new(),
            tx_seq: 0,
            start_ts: 0,
        })
    }

    fn reset(&mut self) {
        self.jitter.reset();
        self.tx_seq = 0;
        self.start_ts = 0;
    }

    fn encode_audio_frame(&mut self, pcm: &[u8]) -> PyResult<Vec<u8>> {
        self.opus
            .encode_frame(pcm)
            .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)
    }

    fn decode_audio_frame(&mut self, opus: &[u8]) -> PyResult<Vec<u8>> {
        self.opus
            .decode_frame(opus)
            .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)
    }

    fn packetize_audio(&mut self, opus_payload: &[u8], timestamp_ms: u32) -> PyResult<Vec<u8>> {
        let pkt = MediaPacket {
            kind: MediaKind::Audio,
            flags: 0,
            sequence: self.tx_seq,
            timestamp_ms,
            payload: opus_payload.to_vec(),
        };
        self.tx_seq = self.tx_seq.wrapping_add(1);
        Ok(pkt.encode())
    }

    fn packetize_video(&mut self, payload: &[u8], timestamp_ms: u32, keyframe: bool) -> PyResult<Vec<u8>> {
        let pkt = MediaPacket {
            kind: MediaKind::Video,
            flags: if keyframe { 1 } else { 0 },
            sequence: self.tx_seq,
            timestamp_ms,
            payload: payload.to_vec(),
        };
        self.tx_seq = self.tx_seq.wrapping_add(1);
        Ok(pkt.encode())
    }

    fn packetize_screen(&mut self, payload: &[u8], timestamp_ms: u32, keyframe: bool) -> PyResult<Vec<u8>> {
        let pkt = MediaPacket {
            kind: MediaKind::Screen,
            flags: if keyframe { 1 } else { 0 },
            sequence: self.tx_seq,
            timestamp_ms,
            payload: payload.to_vec(),
        };
        self.tx_seq = self.tx_seq.wrapping_add(1);
        Ok(pkt.encode())
    }

    fn ingest_packet(&mut self, data: &[u8]) -> PyResult<Option<(u8, u8, u32, u32, Vec<u8>)>> {
        let pkt = MediaPacket::decode(data)
            .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>("invalid media packet"))?;
        self.jitter.push(pkt.clone());
        Ok(Some((
            pkt.kind as u8,
            pkt.flags,
            pkt.sequence,
            pkt.timestamp_ms,
            pkt.payload,
        )))
    }

    fn pop_audio(&mut self, now_ms: u32) -> PyResult<Option<(Vec<u8>, Vec<u8>)>> {
        if let Some(pkt) = self.jitter.pop_ready(now_ms) {
            if pkt.kind != MediaKind::Audio {
                return Ok(None);
            }
            let pcm = self
                .opus
                .decode_frame(&pkt.payload)
                .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)?;
            return Ok(Some((pkt.payload, pcm)));
        }
        Ok(None)
    }

    fn jitter_depth(&self) -> usize {
        self.jitter.depth()
    }

    fn jitter_delay_ms(&self) -> u32 {
        self.jitter.target_delay_ms()
    }
}

#[pyfunction]
fn is_media_packet(data: &[u8]) -> bool {
    data.len() >= HEADER_SIZE && &data[0..4] == MAGIC
}

#[pyfunction]
fn parse_packet(data: &[u8]) -> PyResult<Option<(u8, u8, u32, u32, Vec<u8>)>> {
    match MediaPacket::decode(data) {
        Some(pkt) => Ok(Some((
            pkt.kind as u8,
            pkt.flags,
            pkt.sequence,
            pkt.timestamp_ms,
            pkt.payload,
        ))),
        None => Ok(None),
    }
}

#[pyfunction]
fn frame_bytes() -> usize {
    FRAME_BYTES
}

#[pyfunction]
fn max_payload() -> usize {
    MAX_PAYLOAD
}

#[pymodule]
fn chatxz_media(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MediaSession>()?;
    m.add_function(wrap_pyfunction!(is_media_packet, m)?)?;
    m.add_function(wrap_pyfunction!(parse_packet, m)?)?;
    m.add_function(wrap_pyfunction!(frame_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(max_payload, m)?)?;
    Ok(())
}