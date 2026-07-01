import argparse
import traceback
import json
import time
import queue
import threading
import numpy as np
import audioop
import base64
import sys
from datetime import datetime
from dataclasses import dataclass

from modules.js_client import MineflayerJsClient
from modules.websocketconnector import WebsocketConnector
from modules.belief import StandaloneWorldObservationRuntime, build_world_config_from_block_snapshot_buffer, get_event_result
from modules.llm import OpenAILLM, OllamaLLM, OpenAIRealtimeLLM
from modules.mc_operator import ensure_operator_bot_connected, grant_operator_via_mineflayer
from modules.tts import VOICEVOX_TTS
from modules.asr import RealtimeSTT
from modules.audio_packet_utils import encode_opus_packets, decode_opus_packets, split_packets_by_speaker, mix_timed_chunks
from modules.utils import make_file_logger, load_config, load_primitives, read_files

__VERSION__ = "20260427_0658"

DISALLOWED_EXPRESSIONS = []

###############################################################
@dataclass(frozen=True)
class WebSocketConfig:
    host: str
    port: int

@dataclass(frozen=True)
class MinecraftServerConfig:
    host: str
    port: int
    server_id: str

@dataclass(frozen=True)
class MineflayerServerConfig:
    host: str
    port: int

@dataclass(frozen=True)
class MinecraftConfig:
    offset: list[int]
    env_box: list[list[int]]
    can_dig_when_move: bool
    move_timeout_sec: int
    stuck_check_interval_sec: int
    stuck_offset_range: int

@dataclass(frozen=True)
class OpusConfig:
    sample_rate: int
    frame_millis: int
    application: str

@dataclass(frozen=True)
class TTSConfig:
    host: str
    port: int
    enable_interrogative_upspeak: bool
    output_sampling_rate: int
    output_stereo: bool

@dataclass(frozen=True)
class OpenAILLMConfig:
    llm_type: str
    api_key: str
    model_name: str
    temperature: float
    request_timeout: int
    max_trial: int

@dataclass(frozen=True)
class OpenAIRealtimeLLMConfig:
    llm_type: str
    api_key: str
    model_name: str
    max_trial: int
###############################################################

###############################################################
def build_mineflayer_variables(agent_name, offset, env_box):
    return {
        "offsetVec3": json.dumps({"__Vec3__": offset}),
        "envBox": json.dumps([
                {"__Vec3__": env_box[0]},
                {"__Vec3__": env_box[1]}])
        }

def build_easy_llm_voice_variables(agent_id, player_name, sample_rate, frame_millis):
    return {
            "type": "setup",
            "agent_id": agent_id,
            "player_name": player_name,
            "audio_codec": "opus",
            "sample_rate": sample_rate,
            "channels": 1,
            "frame_millis": frame_millis,
        }
def build_easy_llm_variables(env_box):
    return {
        "type": "first_access",
        "min": {"x": env_box[0][0], "y": env_box[0][1], "z": env_box[0][2]},
        "max": {"x": env_box[1][0], "y": env_box[1][1], "z": env_box[1][2]},
    }

def build_chat_observation_variables(speaker, text):
    return {
        "eventName": "chat",
        "visible": {
            "agentName": speaker,
            "msg": text,
        },
        "hidden": None,
    }

def build_agent_dataclasses(config):
    easy_llm = WebSocketConfig(
        host=config["easy_llm"]["host"],
        port=config["easy_llm"]["port"],
    )
    minecraft_server = MinecraftServerConfig(
        host=config["minecraft_server"]["host"],
        port=config["minecraft_server"]["port"],
        server_id=config["minecraft_server"]["server_id"],
    )
    mineflayer_server = MineflayerServerConfig(
        host=config["mineflayer_server"]["host"],
        port=config["mineflayer_server"]["port"],
    )
    minecraft = MinecraftConfig(
        offset=config["minecraft"]["offset"],
        env_box=[
            config["minecraft"]["env_box"]["min"],
            config["minecraft"]["env_box"]["max"],
        ],
        can_dig_when_move=config["minecraft"]["can_dig_when_move"],
        move_timeout_sec=config["minecraft"]["move_timeout_sec"],
        stuck_check_interval_sec=config["minecraft"]["stuck_check_interval_sec"],
        stuck_offset_range=config["minecraft"]["stuck_offset_range"],
    )
    if config["llm_type"] == "openai":
        llm = OpenAILLMConfig(
            llm_type=config["llm_type"],
            api_key=config["openai"]["api_key"],
            model_name=config["openai"]["model_name"],
            temperature=config["openai"]["temperature"],
            request_timeout=config["openai"]["request_timeout"],
            max_trial=config["openai"]["max_trial"],
        )
    elif config["llm_type"] == "openairealtime":
        llm = OpenAIRealtimeLLMConfig(
            llm_type=config["llm_type"],
            api_key=config["openairealtime"]["api_key"],
            model_name=config["openairealtime"]["model_name"],
            max_trial=config["openairealtime"]["max_trial"],
        )
    else:
        assert f"not match llm_type:[openai, openairealtime], your key:{config['llm_type']}"

    easy_llm_voice = WebSocketConfig(
        host=config["easy_llm_voice"]["host"],
        port=config["easy_llm_voice"]["port"],
    )
    opus = OpusConfig(
        sample_rate=config["opus"]["sample_rate"],
        frame_millis=config["opus"]["frame_millis"],
        application=config["opus"]["application"],
    )
    tts = TTSConfig(
        host=config["tts"]["host"],
        port=config["tts"]["port"],
        enable_interrogative_upspeak=config["tts"]["enable_interrogative_upspeak"],
        output_sampling_rate=config["tts"]["output_sampling_rate"],
        output_stereo=config["tts"]["output_stereo"],
    )
    return easy_llm, minecraft_server, mineflayer_server, minecraft, llm, easy_llm_voice, opus, tts
###############################################################

###############################################################
class Agent:
    def __init__(
        self,
        *,
        log_dir: str,
        agent_name: str,
        agent_id: str,
        speaker_id: int,
        prompt_path: str,
        minecraft_server_cfg: MinecraftServerConfig,
        mineflayer_server_cfg: MineflayerServerConfig,
        easy_llm_cfg: WebSocketConfig,
        minecraft_cfg: MinecraftConfig,
        llm_cfg: OpenAILLMConfig | OpenAIRealtimeLLMConfig,
        easy_llm_voice_cfg: WebSocketConfig,
        opus_cfg: OpusConfig,
        tts_cfg: TTSConfig,
    ):
        self.log_dir = log_dir
        self.agent_name = agent_name
        self.agent_id = agent_id
        self.speaker_id = speaker_id
        self.minecraft_server_cfg = minecraft_server_cfg
        self.mineflayer_server_cfg = mineflayer_server_cfg
        self.easy_llm_cfg = easy_llm_cfg
        self.minecraft_cfg = minecraft_cfg
        self.llm_cfg = llm_cfg
        self.easy_llm_voice_cfg = easy_llm_voice_cfg
        self.opus_cfg = opus_cfg
        self.tts_cfg = tts_cfg
        self.agent_logger = make_file_logger(self.agent_name, f"{self.log_dir}/{self.agent_name}.log")
        self.primitives = load_primitives()
        self.prompts = read_files(prompt_path)
        self.obs_audio_q = queue.Queue()
        self.for_belief_update_q = queue.Queue()
        self.asr_input_q = queue.Queue()
        self.asr_output_q = queue.Queue()
        self.asr_chunk_q = queue.Queue()
        self.result_asr_for_belief_q = queue.Queue()
        self.asr_last_end_ms = None
        self.asr_ratecv_state = None
        self.asr_chunk_samples = 16000 * self.opus_cfg.frame_millis // 1000
        self.asr_chunk_bytes = self.asr_chunk_samples * 2
        self._speaker_lock = threading.Lock()
        self._currentspeakername: str | None = None

        self.belief = None
        self.world_config = None
        self.belief_ready_event = threading.Event()

        self.llm = self.setup_llm(self.prompts["system"]["primitive"])

        self.MineflayerJsClient_logger = make_file_logger("MineflayerJsClient", f"{self.log_dir}/MineflayerJsClient.log")
        self.js_client = MineflayerJsClient(host=self.mineflayer_server_cfg.host, port=self.mineflayer_server_cfg.port, logger=self.MineflayerJsClient_logger)
        self.mineflayer_variables = build_mineflayer_variables(self.agent_name, self.minecraft_cfg.offset, self.minecraft_cfg.env_box)

        self.easy_llm_ws = WebsocketConnector("easy_llm", self.easy_llm_cfg.host, self.easy_llm_cfg.port, True)
        self.easy_llm_variables = build_easy_llm_variables(self.minecraft_cfg.env_box)
        
        self.easy_llm_voice_ws = WebsocketConnector("easy_llm_voice", self.easy_llm_voice_cfg.host, self.easy_llm_voice_cfg.port, False)
        self.easy_llm_voice_variables=build_easy_llm_voice_variables(self.agent_id, self.agent_name, self.opus_cfg.sample_rate, self.opus_cfg.frame_millis)
        self.easy_llm_voice_send_speech_stop_event = threading.Event()
        self.easy_llm_voice_send_speech_thread = None

        self.tts = VOICEVOX_TTS(make_file_logger("tts", f"{self.log_dir}/tts.log"), self.tts_cfg.host, self.tts_cfg.port, self.tts_cfg.enable_interrogative_upspeak, self.tts_cfg.output_sampling_rate, self.tts_cfg.output_stereo)
        
        self.asr = RealtimeSTT(logdir=self.log_dir, input_buffer=self.asr_input_q, output_buffer=self.asr_output_q)

    @property
    def currentspeakername(self) -> str | None:
        with self._speaker_lock:
            return self._currentspeakername
 
    @currentspeakername.setter
    def currentspeakername(self, value: str | None) -> None:
        with self._speaker_lock:
            self._currentspeakername = value
            
    #################################### setup #####################################
    def setup_llm(self, system_prompt=""):
        if self.llm_cfg.llm_type == "openai":
            return OpenAILLM(self.log_dir, self.llm_cfg.api_key, self.llm_cfg.model_name, self.llm_cfg.temperature, self.llm_cfg.request_timeout, self.llm_cfg.max_trial)
        elif self.llm_cfg.llm_type == "openairealtime":
            return OpenAIRealtimeLLM(self.log_dir, self.llm_cfg.api_key, self.llm_cfg.model_name, system_prompt, self.llm_cfg.max_trial)

    ################################################################################

    ########################### action methods from mod ############################
    def add_avatar(self):
        self.js_client.connect()
        self.js_client.setup(
            can_dig_when_move=self.minecraft_cfg.can_dig_when_move,
            move_timeout_sec=self.minecraft_cfg.move_timeout_sec,
            stuck_check_interval_sec=self.minecraft_cfg.stuck_check_interval_sec,
            stuck_offset_range=self.minecraft_cfg.stuck_offset_range,
            sync=True,
        )
        self.js_client.join(server_id=self.minecraft_server_cfg.server_id, mc_name=self.agent_name, mc_port=self.minecraft_server_cfg.port, mc_host=self.minecraft_server_cfg.host)
        self.js_client.update_agent_variables(server_id=self.minecraft_server_cfg.server_id, mc_name=self.agent_name, variables=self.mineflayer_variables)
    
    def add_admin_avator(self):
        operator_mc_name = ensure_operator_bot_connected(
            js_client=self.js_client,
            server_id=self.minecraft_server_cfg.server_id,
            mc_host=self.minecraft_server_cfg.host,
            mc_port=self.minecraft_server_cfg.port,
            mc_name=self.agent_name
        )
        grant_operator_via_mineflayer(
            js_client=self.js_client,
            server_id=self.minecraft_server_cfg.server_id,
            mc_name=self.agent_name,
            operator_mc_name=operator_mc_name,
        )
        self.agent_logger.info(
            'Granted operator to "%s" via operator bot %s.',
            self.agent_name,
            operator_mc_name,
        )

    def exec_js(self, js):
        self.js_client.exec_js(server_id=self.minecraft_server_cfg.server_id, mc_name=self.agent_name, code=js, primitives=self.primitives, sync=False, timeout=180)
    ##################################################################################

    #################################### LLM ######################################
    def create_prompt(self, human_prompt_type, system_prompt_type, extra_variables:dict={}):
        human_base_prompt, system_prompt = self.prompts["human"][human_prompt_type], self.prompts["system"][system_prompt_type]
        #--- BeliefNest依存　情報の取得 + プロンプトへの入力 ---# # BeliefNest
        extra_variables.update({"self_name":self.agent_name})
        try:
            loader = self.belief.create_current_observation_loader()
            human_prompt = self.belief.load_from_template(loader, human_base_prompt, variables=extra_variables, extra_filters=[], allow_filter_override=True)
        except Exception as e:
            self.agent_logger.critical("create_prompt: テンプレート展開エラー: %s", e)
            self.agent_logger.critical(traceback.format_exc())
            sys.exit(1) 
        #----------------------------------------------------#
        self.agent_logger.info(f"----------------------------------------------")
        self.agent_logger.info(f"system_prompt:{system_prompt}")
        self.agent_logger.info(f"----------------------------------------------")
        self.agent_logger.info(f"human_prompt:{human_prompt}")
        self.agent_logger.info(f"----------------------------------------------")
        return human_prompt, system_prompt

    def execute_llm(self, format_prompts, validate_js=True):
        code, time_str = self.llm.request_llm(prompts=format_prompts, disallowed_expressions=DISALLOWED_EXPRESSIONS, javascript_check=validate_js)
        self.agent_logger.info(f"-----------------------------------")
        self.agent_logger.info(f"code:{code}")
        self.agent_logger.info(f"time_str:{time_str}")
        self.agent_logger.info(f"-----------------------------------")
        return code
    
    def generate_action_js(self):
        human_prompt, system_prompt = self.create_prompt(human_prompt_type="generate_action", system_prompt_type="primitive", extra_variables={})
        format_prompts = self.llm.format_prompts_for_llm([("system", system_prompt), ("user", human_prompt)])
        javascript = self.execute_llm(format_prompts, validate_js=True)
        return javascript
    ##################################################################################
    
    #################################### ASR ######################################
    def send_asr_loop(self, input_q: queue.Queue, output_q: queue.Queue):
        frame_sec = self.opus_cfg.frame_millis / 1000.0
        silence_chunk = b"\x00" * self.asr_chunk_bytes
        while True:
            start_time = time.monotonic()
            time.sleep(0.001)
            try:
                chunk = input_q.get_nowait()
            except queue.Empty:
                chunk = silence_chunk
            output_q.put(chunk)
            elapsed = time.monotonic() - start_time
            remain = frame_sec - elapsed
            if remain > 0:
                time.sleep(remain)

    def get_asr_result(self, input_q: queue.Queue, output_q: queue.Queue):
        while True:
            try:
                text, is_final = input_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if is_final:
                self.agent_logger.info("[ASR final] %s", text)
                output_q.put((self.currentspeakername, text))
            else:
                self.agent_logger.info("[ASR partial] %s", text)
    ###########################################################################################

    #################### send audio data methods from mod ############################
    def send_speech_for_easy_llm_voicemod(self, text):
        print(f"start send_speech_for_easy_llm_voicemod: {text}")
        self.easy_llm_voice_send_speech_stop_event.clear()
        wav_array, duration, sample_rate, wav_bytes = self.tts.txt_to_speech(text, self.speaker_id)
        self.agent_logger.info("speech duration=%.2fs samples=%d", duration, len(wav_array))
        encoded_packets = encode_opus_packets(wav_array, sample_rate=self.opus_cfg.sample_rate, frame_ms=self.opus_cfg.frame_millis, application=self.opus_cfg.application)
        sequence = 0
        for packet in encoded_packets:
            if self.easy_llm_voice_send_speech_stop_event.is_set():
                break
            self.easy_llm_voice_ws.send({
                "type": "voice_frame",
                "sequence": sequence,
                "opus_data_base64": base64.b64encode(packet).decode("ascii"),
                "whispering": False,
            })
            sequence += 1
            time.sleep(self.opus_cfg.frame_millis / 1000.0)
        self.easy_llm_voice_ws.send({
            "type": "voice_stop",
            "last_sequence": max(0, sequence - 1),
        })

    def start_speech(self, text):
        if self.easy_llm_voice_send_speech_thread is not None and self.easy_llm_voice_send_speech_thread.is_alive():
            self.stop_speech()
        self.easy_llm_voice_send_speech_thread = threading.Thread(target=self.send_speech_for_easy_llm_voicemod, args=(text,), daemon=True)
        self.easy_llm_voice_send_speech_thread.start()

    def stop_speech(self):
        self.easy_llm_voice_send_speech_stop_event.set()
        try:
            self.easy_llm_voice_ws.send({"type": "interrupt"})
        except Exception:
            pass
        if self.easy_llm_voice_send_speech_thread and self.easy_llm_voice_send_speech_thread.is_alive():
            self.easy_llm_voice_send_speech_thread.join(timeout=1.0)
    ###########################################################################################

    ################################ get audio data methods from mod ##########################
    def feed_asr_chunked(self, pcm_bytes: bytes, output_q: queue.Queue):
        for i in range(0, len(pcm_bytes), self.asr_chunk_bytes):
            chunk = pcm_bytes[i:i + self.asr_chunk_bytes]
            if len(chunk) < self.asr_chunk_bytes:
                chunk += b"\x00" * (self.asr_chunk_bytes - len(chunk))
            output_q.put(chunk)

    def feed_mixed_pcm_to_asr(self, mixed_pcm: np.ndarray, end_ms: int, output_q: queue.Queue): # 音声形式をrealtimeSTT用に変換
        if self.opus_cfg.sample_rate == 16000:
            pcm_bytes = mixed_pcm.tobytes()
        else:
            pcm_bytes, self.asr_ratecv_state = audioop.ratecv(
                mixed_pcm.tobytes(),
                2,
                1,
                self.opus_cfg.sample_rate,
                16000,
                self.asr_ratecv_state,
            )

        self.feed_asr_chunked(pcm_bytes, output_q)
        self.asr_last_end_ms = end_ms

    def get_audio_obs_loop(self, input_q: queue.Queue, output_q: queue.Queue):
        while True:
            try:
                obs = input_q.get(timeout=self.opus_cfg.frame_millis / 1000.0)
            except queue.Empty:
                continue

            split_packets = split_packets_by_speaker(obs, self.agent_name)
            if not split_packets:
                continue
            timed_chunks = []
            for speaker_name, packets in split_packets.items():
                opus_packets = [packet["opus_bytes"] for packet in packets]
                pcm = decode_opus_packets(opus_packets, self.opus_cfg.sample_rate, self.agent_logger)
                if len(pcm) == 0:
                    continue
                timed_chunks.append({
                    "speaker_name": speaker_name,
                    "speaker_uuid": packets[0]["speaker_uuid"],
                    "captured_at_ms": packets[0]["captured_at_ms"],
                    "pcm": pcm,
                })
                self.currentspeakername = speaker_name
            if not timed_chunks:
                continue
            mixed_pcm, start_ms, end_ms = mix_timed_chunks(
                timed_chunks,
                sample_rate=self.opus_cfg.sample_rate,
            )
            self.feed_mixed_pcm_to_asr(mixed_pcm, end_ms, output_q)
    ###########################################################################################

    ################################# belief ####################################
    def update_belief_loop(self, input_from_minecraft_q: queue.Queue, input_from_asr_q: queue.Queue):
        block_snapshot_buffer = {}
        while True:
            try:
                obs = input_from_minecraft_q.get(timeout=0.1)
            except queue.Empty:
                continue

            snapshot_result = build_world_config_from_block_snapshot_buffer(obs, block_snapshot_buffer)
            if snapshot_result is not None:
                self.world_config, snapshot_info = snapshot_result
                self.belief = StandaloneWorldObservationRuntime.from_world_config(self.world_config, offset=[0, 0, 0])
                self.belief_ready_event.set()
                self.agent_logger.info("block_snapshot complete: requestId=%s sequences=%s blocks=%s/%s chests=%s", snapshot_info["request_id"], snapshot_info["sequences"], snapshot_info["actual_count"], snapshot_info["expected_count"], snapshot_info["chest_count"])
                continue
            if obs.get("type") == "block_snapshot" or any(item.get("type") == "block_snapshot"for item in obs.get("items", [])):
                continue
            if self.belief is not None:
                while True:
                    try:
                        speaker_name, result_asr = input_from_asr_q.get_nowait()
                        self.belief.add_raw_observation(build_chat_observation_variables(speaker_name, result_asr))
                    except queue.Empty:
                        break
                result = self.belief.add_raw_observation(obs)
                if result is not None:
                    chat_info = get_event_result(result, "chat")
                    if chat_info["occurred"]:
                        self.agent_logger.info("chat agents: %s", chat_info["agents"])
                        self.agent_logger.info("chat messages: %s", chat_info["messages"])
                        if chat_info["agents"][0] == self.agent_name:
                            self.start_speech(chat_info["messages"][0]["msg"])

                    move_info = get_event_result(result, "moveStart")
                    if move_info["occurred"]:
                        self.agent_logger.info("moveStart agents: %s", move_info["agents"])
                
    ###########################################################################################

    ################################# OBS from mod ####################################
    def get_mc_obs(self, input_q: queue.Queue, output_for_belief_q: queue.Queue, output_for_asr_q: queue.Queue):
        self.agent_logger.info("start get_mc_obs")
        while True:
            try:
                obs = input_q.get(timeout=0.1)
            except queue.Empty:
                continue

            if isinstance(obs, str):
                obs = json.loads(obs)
            output_for_belief_q.put(obs)
            output_for_asr_q.put(obs)
    ###########################################################################################

    def main(self):
        self.agent_logger.info("Action generation will start when you press Enter.")
        while True:
            try:
                line = input("[enter]:")
            except EOFError:
                try:
                    self.easy_llm_ws.websocket.close()
                except Exception:
                    pass
                return

            action_js = self.generate_action_js()
            self.exec_js(action_js)

    def run(self):
        self.add_avatar()
        self.add_admin_avator()

        easy_llm_voice_ws_thread = threading.Thread(target=self.easy_llm_voice_ws.run, daemon=True)
        easy_llm_voice_ws_thread.start()
        easy_llm_ws_thread = threading.Thread(target=self.easy_llm_ws.run, daemon=True)
        easy_llm_ws_thread.start()

        time.sleep(0.5)
        self.easy_llm_voice_ws.send(self.easy_llm_voice_variables)
        self.easy_llm_ws.send(self.easy_llm_variables)

        get_mc_obs_thread = threading.Thread(target=self.get_mc_obs, args=(self.easy_llm_ws.queue, self.obs_audio_q, self.for_belief_update_q), daemon=True)
        get_mc_obs_thread.start()
        get_audio_obs_loop_thread = threading.Thread(target=self.get_audio_obs_loop, args=(self.obs_audio_q, self.asr_chunk_q,), daemon=True)
        get_audio_obs_loop_thread.start()
        send_asr_loop_thread = threading.Thread(target=self.send_asr_loop, args=(self.asr_chunk_q, self.asr_input_q,), daemon=True)
        send_asr_loop_thread.start()
        get_asr_result_thread = threading.Thread(target=self.get_asr_result, args=(self.asr_output_q, self.result_asr_for_belief_q,), daemon=True)
        get_asr_result_thread.start()
        update_belief_loop_thread = threading.Thread(target=self.update_belief_loop, args=(self.for_belief_update_q, self.result_asr_for_belief_q,), daemon=True)
        update_belief_loop_thread.start()

        if not self.belief_ready_event.is_set():
            self.belief_ready_event.wait()

        self.asr.run()

        self.main_thread = threading.Thread(target=self.main)
        self.main_thread.start()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()

    config = load_config(args.config)
    easy_llm_cfg, minecraft_server_cfg, mineflayer_server_cfg, minecraft_cfg, llm_cfg, easy_llm_voice_cfg, opus_cfg, tts_cfg = build_agent_dataclasses(config)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    agent = Agent(
        log_dir=f'{config["agent"]["logs_dir"]}/{timestamp}',
        agent_name=config["agent"]["agent_name"],
        agent_id=config["agent"]["agent_id"],
        speaker_id=config["agent"]["speaker_id"],
        prompt_path=config["agent"]["prompts"],
        minecraft_server_cfg=minecraft_server_cfg,
        mineflayer_server_cfg=mineflayer_server_cfg,
        easy_llm_cfg=easy_llm_cfg,
        minecraft_cfg=minecraft_cfg,
        llm_cfg=llm_cfg,
        easy_llm_voice_cfg=easy_llm_voice_cfg,
        opus_cfg=opus_cfg,
        tts_cfg=tts_cfg,
    )
    agent.run()
    agent.main_thread.join()

if __name__ == "__main__":
    main()