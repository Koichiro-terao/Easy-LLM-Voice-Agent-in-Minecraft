import io
import wave
import requests
import numpy as np
import logging

class VOICEVOX_TTS:
    def __init__(
        self,
        logger:logging.Logger,
        host: str = "localhost",
        port: int = 50021,
        enable_interrogative_upspeak: bool = True,
        output_sampling_rate: int = 48000,
        output_stereo: bool = False,
    ):
        self.enable_interrogative_upspeak = enable_interrogative_upspeak
        self.output_sampling_rate = output_sampling_rate
        self.output_stereo = output_stereo
        self.path = f"http://{host}:{port}"
        self.logger = logger

    def txt_to_speech(self, text: str, speaker_id: int = 23):
        self.logger.info("text:%s", text)
        self.logger.info("access tts server")

        query_resp = requests.post(
            f"{self.path}/audio_query",
            params={
                "text": text,
                "speaker": speaker_id,
            },
            timeout=30,
        )
        query_resp.raise_for_status()
        query_data = query_resp.json()
        query_data["outputSamplingRate"] = self.output_sampling_rate
        query_data["outputStereo"] = self.output_stereo
        synthesis_resp = requests.post(
            f"{self.path}/synthesis",
            params={
                "speaker": speaker_id,
                "enable_interrogative_upspeak": self.enable_interrogative_upspeak,
            },
            json=query_data,
            timeout=60,
        )
        synthesis_resp.raise_for_status()
        wav_bytes = synthesis_resp.content
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            sample_rate = wf.getframerate()
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            n_frames = wf.getnframes()
            pcm_bytes = wf.readframes(n_frames)
        if sample_width != 2:
            raise ValueError(f"unsupported sample width: {sample_width}")
        if channels != 1:
            raise ValueError(f"expected mono audio, got {channels} channels")
        wav_array = np.frombuffer(pcm_bytes, dtype=np.int16).copy()
        duration = n_frames / float(sample_rate)
        return wav_array, duration, sample_rate, wav_bytes
    
if __name__ == "__main__":
    from pathlib import Path
    import sounddevice as sd
    def make_file_logger(
        name: str,
        log_path: str | Path,
        *,
        level: int = logging.INFO,
        ) -> logging.Logger:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        if logger.handlers:
            return logger
        logger.propagate = False
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(level)
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        sh = logging.StreamHandler()
        sh.setLevel(level)
        sh.setFormatter(formatter)
        logger.addHandler(sh)
        return logger
    
    logger = make_file_logger("tts", "logs/debug_tts.log")
    tts = VOICEVOX_TTS(logger, "localhost", 50021, True, 48000, False)
    wav_array, duration, sample_rate, wav_bytes = tts.txt_to_speech("おはようございます。", 23)
    sd.play(wav_array, 48000, blocking=False)
    print(f"finish debug tts")