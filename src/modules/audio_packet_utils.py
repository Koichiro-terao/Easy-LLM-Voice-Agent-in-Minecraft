import base64
import numpy as np
import av

###############################################################
def encode_opus_packets(pcm, sample_rate, frame_ms, application="voip"):
    frame_samples = sample_rate * frame_ms // 1000
    if frame_samples <= 0:
        raise ValueError("invalid frame_ms")
    codec = av.CodecContext.create("libopus", "w")
    codec.sample_rate = sample_rate
    codec.layout = "mono"
    codec.format = "s16"
    codec.options = {"application": application, "frame_duration": str(frame_ms)}
    pcm = np.ascontiguousarray(pcm, dtype=np.int16)
    packets = []
    for start in range(0, len(pcm), frame_samples):
        chunk = pcm[start:start + frame_samples]
        if len(chunk) < frame_samples:
            chunk = np.pad(chunk, (0, frame_samples - len(chunk)))
        frame = av.AudioFrame(format="s16", layout="mono", samples=frame_samples)
        frame.sample_rate = sample_rate
        frame.planes[0].update(np.ascontiguousarray(chunk, dtype=np.int16).tobytes())
        packets.extend(bytes(packet) for packet in codec.encode(frame))
    packets.extend(bytes(packet) for packet in codec.encode(None))
    return packets

def decode_opus_packets(opus_packets, sample_rate):
    def normalize_decoded_frame(frame):
        decoded = frame.to_ndarray()

        if decoded.ndim == 1:
            mono = decoded
        elif decoded.ndim == 2:
            if decoded.shape[0] == 1:
                mono = decoded[0]
            elif decoded.shape[0] == 2:
                left = np.asarray(decoded[0], dtype=np.int32)
                right = np.asarray(decoded[1], dtype=np.int32)
                if np.array_equal(left, right):
                    mono = left.astype(np.int16)
                else:
                    mono = ((left + right) // 2).astype(np.int16)
            else:
                mono = decoded.reshape(-1)
        else:
            mono = decoded.reshape(-1)

        mono = np.asarray(mono, dtype=np.int16).copy()

        if len(mono) >= 2 and len(mono) % 2 == 0 and np.array_equal(mono[0::2], mono[1::2]):
            mono = mono[::2]

        return mono

    codec = av.CodecContext.create("libopus", "r")
    codec.sample_rate = sample_rate
    decoded_chunks = []
    try:
        for opus_packet in opus_packets:
            packet = av.Packet(opus_packet)
            for frame in codec.decode(packet):
                decoded_chunks.append(normalize_decoded_frame(frame))

        for frame in codec.decode(None):
            decoded_chunks.append(normalize_decoded_frame(frame))
    finally:
        del codec

    if not decoded_chunks:
        return np.zeros(0, dtype=np.int16)

    return np.concatenate(decoded_chunks)

def split_packets_by_speaker(audio_obs: dict, agent_name: str):
    packets_by_speaker = {}
    for data in audio_obs:
        listeners = data.get("data", {}).get("listeners", {})
        for listener_name, listener_data in listeners.items():
            for packet in listener_data.get("heard_packets", []):
                if packet["speaker_name"] == agent_name:
                    continue

                speaker_name = packet["speaker_name"]
                packets_by_speaker.setdefault(speaker_name, []).append({
                    "listener_name": listener_name,
                    "speaker_uuid": packet["speaker_uuid"],
                    "captured_at_ms": packet["captured_at_ms"],
                    "opus_bytes": base64.b64decode(packet["opus_data_base64"]),
                })
    for speaker_name in packets_by_speaker:
        packets_by_speaker[speaker_name].sort(key=lambda p: p["captured_at_ms"])
    return packets_by_speaker

def mix_timed_pcm_chunks_to_single_pcm(chunks, sample_rate=48000):
    if not chunks:
        return np.zeros(0, dtype=np.int16), None, None
    t0 = min(chunk["captured_at_ms"] for chunk in chunks)
    total_samples = 0
    for chunk in chunks:
        start_sample = int((chunk["captured_at_ms"] - t0) * sample_rate / 1000)
        total_samples = max(total_samples, start_sample + len(chunk["pcm"]))
    mixed = np.zeros(total_samples, dtype=np.int32)
    for chunk in chunks:
        start_sample = int((chunk["captured_at_ms"] - t0) * sample_rate / 1000)
        end_sample = start_sample + len(chunk["pcm"])
        mixed[start_sample:end_sample] += chunk["pcm"].astype(np.int32)
    mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
    return mixed
