import os
import subprocess
import tempfile
import threading
import queue
import struct
import time

AUDIO_DIR = "voice_notes"

class VoiceRecorder:
    def __init__(self, config_dir):
        self.config_dir = config_dir
        self.audio_dir = os.path.join(config_dir, AUDIO_DIR)
        os.makedirs(self.audio_dir, exist_ok=True)
        self.recording = False
        self.audio_file = None
        self._thread = None
        self._audio_queue = queue.Queue()

    def _get_pyaudio(self):
        try:
            import pyaudio
            return pyaudio
        except ImportError:
            return None

    def start_recording(self):
        pa = self._get_pyaudio()
        if pa is None:
            return None

        timestamp = int(time.time())
        self.audio_file = os.path.join(self.audio_dir, f"voice_{timestamp}.wav")
        self.recording = True

        FORMAT = pa.paInt16
        CHANNELS = 1
        RATE = 24000
        CHUNK = 1024

        def record_thread():
            audio = pa.PyAudio()
            stream = audio.open(format=FORMAT, channels=CHANNELS,
                                rate=RATE, input=True,
                                frames_per_buffer=CHUNK)
            frames = []
            while self.recording:
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
            stream.stop_stream()
            stream.close()
            audio.terminate()

            import wave
            with wave.open(self.audio_file, 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(audio.get_sample_size(FORMAT))
                wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))

        self._thread = threading.Thread(target=record_thread, daemon=True)
        self._thread.start()
        return self.audio_file

    def stop_recording(self):
        self.recording = False
        if self._thread:
            self._thread.join(timeout=5)
        path = self.audio_file
        self.audio_file = None
        return path

class VoicePlayer:
    @staticmethod
    def play(file_path):
        try:
            import pyaudio
            import wave
            wf = wave.open(file_path, 'rb')
            audio = pyaudio.PyAudio()
            stream = audio.open(format=audio.get_format_from_width(wf.getsampwidth()),
                                channels=wf.getnchannels(),
                                rate=wf.getframerate(),
                                output=True)
            data = wf.readframes(1024)
            while data:
                stream.write(data)
                data = wf.readframes(1024)
            stream.stop_stream()
            stream.close()
            audio.terminate()
            wf.close()
            return True
        except:
            import subprocess
            try:
                subprocess.run(["paplay", file_path], check=False)
                return True
            except:
                try:
                    subprocess.run(["aplay", file_path], check=False)
                    return True
                except:
                    subprocess.Popen(["xdg-open", file_path])
                    return False
