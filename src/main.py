import argparse
import traceback
import json
import time
import queue
import threading
import logging
import numpy as np
import audioop
import base64
import sys
from datetime import datetime
from dataclasses import dataclass

from modules.js_client import MineflayerJsClient
from modules.websocketconnector import WebsocketConnector
from modules.agent_view import build_filtered_initial_state, obs_agent_perspective
from modules.belief import BeliefStateHistory, StandaloneWorldObservationRuntime, build_world_config_from_block_snapshot_buffer, get_event_result
from modules.llm import OpenAILLM, OllamaLLM, OpenAIRealtimeLLM
from modules.mc_operator import ensure_operator_bot_connected, grant_operator_via_mineflayer
from modules.tts import VOICEVOX_TTS
from modules.asr import RealtimeSTT
from modules.audio_packet_utils import encode_opus_packets, decode_opus_packets, split_packets_by_speaker, mix_timed_pcm_chunks_to_single_pcm
from modules.utils import make_file_logger, load_config, load_primitives, read_files

__VERSION__ = "07092350"

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

@dataclass(frozen=True)
class Belief_cfg:
    enable_player_visibility: bool = True
    enable_block_visibility: bool = True
    block_visibility_interval: int = 20

BELIEF_CFG = Belief_cfg(
    enable_player_visibility=True,
    enable_block_visibility=True,
    block_visibility_interval=20,
)

@dataclass (frozen=True)
class Modules:
    asr: RealtimeSTT
    tts: VOICEVOX_TTS
    llm: OpenAILLM|OpenAIRealtimeLLM
    mineflayer: MineflayerJsClient
    minecraft_observation_ws: WebsocketConnector
    send_self_speech_ws: WebsocketConnector
    logger: logging.Logger
    obs_runtime: StandaloneWorldObservationRuntime

@dataclass (frozen=True)
class Queues:
    obs_from_minecraft_to_get_obs: queue.Queue
    vision_obs_from_get_obs_to_accumulate_vision: queue.Queue
    audio_obs_from_get_obs_to_asr: queue.Queue
    asr_input: queue.Queue
    asr_output: queue.Queue
    self_audio_final_result_from_asr_to_accumulate_audio: queue.Queue
    pcm_bytes_from_main_to_asr: queue.Queue

@dataclass (frozen=True)
class Agentconfig:
    name: str
    tts_speaker_id: int
    asr_chunk_bytes: int
    frame_sec: float
    primitives: list[str]
    minecraft_server_cfg: MinecraftServerConfig
    mineflayer_server_cfg: MineflayerServerConfig
    easy_llm_cfg: WebSocketConfig
    minecraft_cfg: MinecraftConfig
    llm_cfg: OpenAILLMConfig | OpenAIRealtimeLLMConfig
    easy_llm_voice_cfg: WebSocketConfig
    opus_cfg: OpusConfig
    belief_cfg: Belief_cfg

@dataclass
class AgentState:
    beliefs: dict[BeliefStateHistory]
    base_prompts:dict[str]
    latest_send_speech_for_minecraft_thread: threading.Thread|None
    start_generation_action_event: threading.Event
    stop_tts_audio_stream_event: threading.Event

###############################################################
def build_mineflayer_variables(offset, env_box):
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
        name: str,
        agent_type: str,
        tts_speaker_id: int,
        prompt_path: str,
        minecraft_server_cfg: MinecraftServerConfig,
        mineflayer_server_cfg: MineflayerServerConfig,
        easy_llm_cfg: WebSocketConfig,
        minecraft_cfg: MinecraftConfig,
        llm_cfg: OpenAILLMConfig | OpenAIRealtimeLLMConfig,
        easy_llm_voice_cfg: WebSocketConfig,
        opus_cfg: OpusConfig,
        tts: VOICEVOX_TTS,
        asr_input_q: queue.Queue,
        asr_output_q: queue.Queue,
        asr: RealtimeSTT,
        belief_cfg: Belief_cfg,
    ):
        queues = Queues(
            obs_from_minecraft_to_get_obs = queue.Queue(),
            vision_obs_from_get_obs_to_accumulate_vision = queue.Queue(),
            audio_obs_from_get_obs_to_asr = queue.Queue(),
            pcm_bytes_from_main_to_asr = queue.Queue(),
            asr_input = asr_input_q,
            asr_output = asr_output_q,
            self_audio_final_result_from_asr_to_accumulate_audio = queue.Queue(),
        )
        
        primitives = load_primitives()
        base_prompts = read_files(prompt_path)
        asr_chunk_bytes = 16000 * opus_cfg.frame_millis // 1000 * 2
        frame_sec = opus_cfg.frame_millis / 1000.0

        llm = self.setup_llm(log_dir, llm_cfg, base_prompts["system"]["primitive"])
        mineflayer = MineflayerJsClient(host=mineflayer_server_cfg.host, port=mineflayer_server_cfg.port, logger=make_file_logger("MineflayerJsClient", f"{log_dir}/MineflayerJsClient.log"))
        minecraft_observation_ws = WebsocketConnector("easy_llm", easy_llm_cfg.host, easy_llm_cfg.port, True, queues.obs_from_minecraft_to_get_obs)
        send_self_speech_ws = WebsocketConnector("easy_llm_voice", easy_llm_voice_cfg.host, easy_llm_voice_cfg.port, False)
        logger = make_file_logger(name, f"{log_dir}/{name}.log")

        self.add_avatar(name, mineflayer, minecraft_cfg, minecraft_server_cfg, build_mineflayer_variables(minecraft_cfg.offset, minecraft_cfg.env_box))
        self.add_admin_avator(name, mineflayer, minecraft_server_cfg)

        pcm_bytes_from_main_to_asr_q = queues.pcm_bytes_from_main_to_asr
        asr_input_q = queues.asr_input
        asr_output_q = queues.asr_output
        self_audio_final_result_from_asr_to_accumulate_audio_q = queues.self_audio_final_result_from_asr_to_accumulate_audio
        start_generation_action_event = threading.Event()
        stop_tts_audio_stream_event = threading.Event()

        thread_dict = {
            "send_self_speech_ws" : threading.Thread(target=send_self_speech_ws.run, daemon=True),
            "inecraft_observation_ws" : threading.Thread(target=minecraft_observation_ws.run, daemon=True),
            "send_asr_loop" : threading.Thread(target=self.send_asr_loop, args=(asr_chunk_bytes, frame_sec, pcm_bytes_from_main_to_asr_q, asr_input_q,), daemon=True),
            "get_asr_result_loop" : threading.Thread(target=self.get_asr_result_loop, args=(asr_output_q, self_audio_final_result_from_asr_to_accumulate_audio_q, logger,), daemon=True),
            "input_user_enter_loop" : threading.Thread(target=self.input_user_enter_loop, args=(start_generation_action_event, logger,))
        }
        for thread in thread_dict.values():
            thread.start()

        asr.run()

        send_self_speech_ws.send(build_easy_llm_voice_variables(agent_type, name, opus_cfg.sample_rate, opus_cfg.frame_millis))
        minecraft_observation_ws.send(build_easy_llm_variables(minecraft_cfg.env_box))

        beliefs, obs_runtime = self.initialize_belief_from_snapshot(queues.obs_from_minecraft_to_get_obs, name, belief_cfg, logger)

        config = Agentconfig(
            name=name,
            tts_speaker_id=tts_speaker_id,
            asr_chunk_bytes = asr_chunk_bytes,
            frame_sec = frame_sec,
            primitives = primitives,
            minecraft_server_cfg = minecraft_server_cfg,
            mineflayer_server_cfg = mineflayer_server_cfg,
            easy_llm_cfg = easy_llm_cfg,
            minecraft_cfg = minecraft_cfg,
            llm_cfg = llm_cfg,
            easy_llm_voice_cfg = easy_llm_voice_cfg,
            opus_cfg = opus_cfg,
            belief_cfg= belief_cfg
        )

        state = AgentState(
            beliefs=beliefs,
            base_prompts=base_prompts,
            latest_send_speech_for_minecraft_thread = None,
            start_generation_action_event=start_generation_action_event,
            stop_tts_audio_stream_event=stop_tts_audio_stream_event
        )
        
        modules = Modules(
            asr=asr,
            tts=tts,
            llm=llm,
            mineflayer=mineflayer,
            send_self_speech_ws=send_self_speech_ws,
            minecraft_observation_ws=minecraft_observation_ws,
            logger=logger,
            obs_runtime=obs_runtime
        )
        self.queues = queues
        self.config = config
        self.state = state
        self.modules = modules

    #################################### setup #####################################
    def setup_llm(self, log_dir, llm_cfg:OpenAILLMConfig|OpenAIRealtimeLLMConfig, system_prompt=""):
        if llm_cfg.llm_type == "openai":
            return OpenAILLM(log_dir, llm_cfg.api_key, llm_cfg.model_name, llm_cfg.temperature, llm_cfg.request_timeout, llm_cfg.max_trial)
        elif llm_cfg.llm_type == "openairealtime":
            return OpenAIRealtimeLLM(log_dir, llm_cfg.api_key, llm_cfg.model_name, system_prompt, llm_cfg.max_trial)

    def add_avatar(self, name, mineflayer:MineflayerJsClient, minecraft_cfg:MinecraftConfig, minecraft_server_cfg:MinecraftServerConfig, mineflayer_variables):
        mineflayer.connect()
        mineflayer.setup(
            can_dig_when_move=minecraft_cfg.can_dig_when_move,
            move_timeout_sec=minecraft_cfg.move_timeout_sec,
            stuck_check_interval_sec=minecraft_cfg.stuck_check_interval_sec,
            stuck_offset_range=minecraft_cfg.stuck_offset_range,
            sync=True,
        )
        mineflayer.join(server_id=minecraft_server_cfg.server_id, mc_name=name, mc_port=minecraft_server_cfg.port, mc_host=minecraft_server_cfg.host)
        mineflayer.update_agent_variables(server_id=minecraft_server_cfg.server_id, mc_name=name, variables=mineflayer_variables)
    
    def add_admin_avator(self, name, mineflayer:MineflayerJsClient, minecraft_server_cfg:MinecraftServerConfig):
        operator_mc_name = ensure_operator_bot_connected(
            js_client=mineflayer,
            server_id=minecraft_server_cfg.server_id,
            mc_host=minecraft_server_cfg.host,
            mc_port=minecraft_server_cfg.port,
            mc_name=name
        )
        grant_operator_via_mineflayer(
            js_client=mineflayer,
            server_id=minecraft_server_cfg.server_id,
            mc_name=name,
            operator_mc_name=operator_mc_name,
        )

    def build_initial_belief_from_snapshot(self, name, snapshot_result, beleif_cfg:Belief_cfg, logger:logging.Logger):
        world_config, snapshot_info = snapshot_result
        obs_runtime = StandaloneWorldObservationRuntime.from_world_config(
            world_config,
            offset=[0, 0, 0],
            enable_player_visibility=beleif_cfg.enable_player_visibility,
            enable_block_visibility=beleif_cfg.enable_block_visibility,
            block_visibility_interval=beleif_cfg.block_visibility_interval,
        )
        beliefs = {
            "world": obs_runtime.world_belief,
        }
        logger.info("block_snapshot complete: requestId=%s sequences=%s blocks=%s/%s chests=%s", snapshot_info["request_id"], snapshot_info["sequences"], snapshot_info["actual_count"], snapshot_info["expected_count"], snapshot_info["chest_count"])
        beliefs["self"] = BeliefStateHistory(
            initial_state=build_filtered_initial_state(obs_runtime.initial_state),
            main_agent_name=name,
        )
        logger.info("completed fps Belief")
        return beliefs, obs_runtime

    def initialize_belief_from_snapshot(self, input_from_minecraft_q: queue.Queue, self_name, beleif_cfg, logger):
        block_snapshot_buffer = {}
        while True:
            obs = self.get_obs_from_minecraft(input_from_minecraft_q)
            if obs is None:
                continue

            snapshot_result = build_world_config_from_block_snapshot_buffer(obs, block_snapshot_buffer)
            if snapshot_result is None:
                continue
            else:
                beliefs, obs_runtime = self.build_initial_belief_from_snapshot(self_name, snapshot_result, beleif_cfg, logger)
                break
        return beliefs, obs_runtime

    ################################################################################

    #################################### LLM ######################################
    def create_prompt(self, belief:BeliefStateHistory, base_human_prompt:str, base_system_prompt:str, name:str, obs_runtime:StandaloneWorldObservationRuntime, logger:logging.Logger, extra_variables:dict={}):
        extra_variables.update({"self_name":name})
        try:
            loader = belief.to_loader()
            human_prompt = obs_runtime.load_from_template(loader, base_human_prompt, variables=extra_variables, extra_filters=[], allow_filter_override=True)
        except Exception as e:
            logger.critical("create_prompt: テンプレート展開エラー: %s", e)
            logger.critical(traceback.format_exc())
            sys.exit(1) 
        logger.info(f"----------------------------------------------")
        logger.info(f"system_prompt:{base_system_prompt}")
        logger.info(f"----------------------------------------------")
        logger.info(f"human_prompt:{human_prompt}")
        logger.info(f"----------------------------------------------")
        return human_prompt, base_system_prompt
    
    def generate_action_js(self, belief, base_human_prompt, base_system_prompt, disallowed_expressions, self_name, llm_module:OpenAILLM|OpenAIRealtimeLLM, obs_runtime:StandaloneWorldObservationRuntime, logger:logging.Logger):
        human_prompt, system_prompt = self.create_prompt(belief, base_human_prompt, base_system_prompt, self_name, obs_runtime, logger, extra_variables={})
        format_prompts = llm_module.format_prompts_for_llm([("system", system_prompt), ("user", human_prompt)])
        code, time_str = llm_module.request_llm(prompts=format_prompts, disallowed_expressions=disallowed_expressions, javascript_check=True)
        logger.info(f"-----------------------------------")
        logger.info(f"code:{code}")
        logger.info(f"time_str:{time_str}")
        logger.info(f"-----------------------------------")
        return code
    
    def exec_js(self, server_id, name, js, primitives, mineflayer:MineflayerJsClient):
        mineflayer.exec_js(server_id=server_id, mc_name=name, code=js, primitives=primitives, sync=False, timeout=180)
    
    def generate_and_execution_action_js(self, self_belief, base_prompts, disallowed_expressions, self_name, mc_server_id, primitives, mineflayer, llm_module, obs_runtime, logger):
        action_js = self.generate_action_js(self_belief, base_prompts["human"]["generate_action"], base_prompts["system"]["primitive"], disallowed_expressions, self_name, llm_module, obs_runtime, logger)
        self.exec_js(mc_server_id, self_name, action_js, primitives, mineflayer)
    ##################################################################################

    ################################# belief ####################################
    def get_obs_from_minecraft(self, input_q: queue.Queue):
        try:
            obs = input_q.get(timeout=0.1)
            if isinstance(obs, str):
                obs_json = json.loads(obs)
            return obs_json
        except queue.Empty:
            return None

    def get_asr_results_from_queue(self, input_from_asr_q: queue.Queue):
        try:
            result_asr = input_from_asr_q.get_nowait()
            return result_asr
        except queue.Empty:
            return None

    def accumulate_self_audio(self, speaker_name, result_asr, obs_runtime:StandaloneWorldObservationRuntime):
        if result_asr is not None:
            obs_runtime.build_raw_observation(build_chat_observation_variables(speaker_name, result_asr))

    def accumulate_world_self_vision(self, obs:dict, world_belief:BeliefStateHistory, self_belief:BeliefStateHistory, agent_name, env_box, offset, obs_runtime:StandaloneWorldObservationRuntime):
        self_observation = None
        world_observation = obs_runtime.build_raw_observation(obs)
        if world_observation is not None:
            self.accmulate_vision(world_belief, world_observation)
            self_observation = obs_agent_perspective(world_observation, agent_name, world_belief.state, self_belief.state, env_box, offset)
            self.accmulate_vision(self_belief, self_observation)
        return world_observation, world_belief, self_belief
        # return self_observation

    def accmulate_vision(self, belief:BeliefStateHistory, observation):
        belief.update_state(observation)
        belief.append_history(observation)
    ###########################################################################################

    #################### send audio data methods from minecraft ############################
    def send_stop_to_stream_opus_voice_command(self, sequence, send_self_speech_ws:WebsocketConnector):
        send_self_speech_ws.send({
            "type": "voice_stop",
            "last_sequence": max(0, sequence - 1),
        })
        
    def send_play_tts_audio_command(self, packet, sequence, send_self_speech_ws:WebsocketConnector):
        send_self_speech_ws.send({
            "type": "voice_frame",
            "sequence": sequence,
            "opus_data_base64": base64.b64encode(packet).decode("ascii"),
            "whispering": False,
        })

    def stream_opus_voice_frames_for_minecraft(self, encoded_packets, frame_sec, stop_tts_audio_stream:threading.Event, send_self_speech_ws:WebsocketConnector):
        sequence = 0
        next_send_time = time.monotonic()
        for packet in encoded_packets:
            if stop_tts_audio_stream.is_set():
                break
            sleep_time = next_send_time - time.monotonic()
            if sleep_time > 0:
                time.sleep(sleep_time)
            self.send_play_tts_audio_command(packet, sequence, send_self_speech_ws)
            sequence += 1
            next_send_time += frame_sec
        return sequence

    def send_speech_for_minecraft(self, wav_array, frame_sec, sample_rate, frame_millis, application, stop_tts_audio_stream_event:threading.Event, send_self_speech_ws:WebsocketConnector):
        encoded_packets = encode_opus_packets(wav_array, sample_rate=sample_rate, frame_ms=frame_millis, application=application)
        sequence = self.stream_opus_voice_frames_for_minecraft(encoded_packets, frame_sec, stop_tts_audio_stream_event, send_self_speech_ws)
        self.send_stop_to_stream_opus_voice_command(sequence, send_self_speech_ws)

    def stop_speech(self, send_self_speech_ws:WebsocketConnector):
        try:
            send_self_speech_ws.send({"type": "interrupt"})
        except Exception:
            pass

    def start_speech(self, text, tts_speaker_id, frame_sec, sample_rate, frame_millis, application, stop_tts_audio_stream_event:threading.Event, latest_send_speech_for_minecraft_thread:threading.Thread, tts_module:VOICEVOX_TTS, send_self_speech_ws:WebsocketConnector):
        wav_array, _, _, _ = tts_module.txt_to_speech(text, tts_speaker_id)
        if latest_send_speech_for_minecraft_thread is not None and latest_send_speech_for_minecraft_thread.is_alive():
            stop_tts_audio_stream_event.set()
            self.stop_speech(send_self_speech_ws)

        stop_tts_audio_stream_event.clear()
        self.send_speech_for_minecraft(wav_array, frame_sec, sample_rate, frame_millis, application, stop_tts_audio_stream_event, send_self_speech_ws)

    def speak_own_chat_message(self, observation, agent_name, tts_speaker_id, frame_sec, sample_rate, frame_millis, application, stop_tts_audio_stream_event, latest_send_speech_for_minecraft_thread, tts_module, send_self_speech_ws:WebsocketConnector):
        chat_info = get_event_result(observation, "chat")
        if chat_info["occurred"] and chat_info["agents"][0] == agent_name:
            latest_send_speech_for_minecraft_thread = threading.Thread(target=self.start_speech, args=(chat_info["messages"][0]["msg"], tts_speaker_id, frame_sec, sample_rate, frame_millis, application, stop_tts_audio_stream_event, latest_send_speech_for_minecraft_thread, tts_module, send_self_speech_ws,), daemon=True)
            latest_send_speech_for_minecraft_thread.start()
        return latest_send_speech_for_minecraft_thread
    
    ###########################################################################################

    ################################ get audio data methods from mod ##########################
    def put_pcm_bytes_to_asr_queue_in_chunks(self, pcm_bytes: bytes, asr_chunk_bytes, output_q: queue.Queue):
        for i in range(0, len(pcm_bytes), asr_chunk_bytes):
            chunk = pcm_bytes[i:i + asr_chunk_bytes]
            if len(chunk) < asr_chunk_bytes:
                chunk += b"\x00" * (asr_chunk_bytes - len(chunk))
            output_q.put(chunk)

    def convert_mixed_pcm_to_asr_input_and_queue(self, mixed_pcm: np.ndarray, audio_obs_sample_rate:int, resample_state):
        if audio_obs_sample_rate == 16000:
            pcm_bytes = mixed_pcm.tobytes()
        else:
            pcm_bytes, resample_state = audioop.ratecv(mixed_pcm.tobytes(), 2, 1, audio_obs_sample_rate, 16000, resample_state,)
        return pcm_bytes, resample_state

    def build_pcm_chunks_from_speaker_packets(self, split_packets, sample_rate):
        timed_chunks = []
        for speaker_name, packets in split_packets.items():
            opus_packets = [packet["opus_bytes"] for packet in packets]
            pcm = decode_opus_packets(opus_packets, sample_rate)
            if len(pcm) == 0:
                continue
            timed_chunks.append({
                "speaker_name": speaker_name,
                "speaker_uuid": packets[0]["speaker_uuid"],
                "captured_at_ms": packets[0]["captured_at_ms"],
                "pcm": pcm,
            })
            self.currentspeakername = speaker_name
        return timed_chunks, speaker_name

    def get_audio_obs(self, obs:dict):
        return [ item for item in obs.get("items", []) if item.get("type") == "heard_audio_batch" ]

    def build_pcm_bytes_data_from_audio_obs(self, audio_obs:dict, self_name:str, opus_sample_rate:int, last_speaker_name, resample_state):
        split_packets = split_packets_by_speaker(audio_obs, self_name)
        if not split_packets:
            return None, last_speaker_name, resample_state
        
        timed_chunks, latest_speaker_name = self.build_pcm_chunks_from_speaker_packets(split_packets, opus_sample_rate)
        if not timed_chunks:
            return None, None, resample_state

        last_speaker_name = latest_speaker_name
        mixed_pcm = mix_timed_pcm_chunks_to_single_pcm(timed_chunks, sample_rate=opus_sample_rate,)
        pcm_bytes, resample_state = self.convert_mixed_pcm_to_asr_input_and_queue(mixed_pcm, opus_sample_rate, resample_state)
        return pcm_bytes, last_speaker_name, resample_state

    ###########################################################################################

    ################################# thread loop subroutine ####################################

    def send_asr_loop(self, asr_chunk_bytes, frame_sec, input_q: queue.Queue, output_q: queue.Queue):
        silence_chunk = b"\x00" * asr_chunk_bytes
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

    def get_asr_result_loop(self, input_q: queue.Queue, output_q: queue.Queue, logger:logging.Logger):
        while True:
            try:
                text, is_final = input_q.get(timeout=0.1)
            except queue.Empty:
                continue
            if is_final:
                logger.info("[ASR final] %s", text)
                output_q.put(text)
            else:
                logger.info("[ASR partial] %s", text)

    def input_user_enter_loop(self, start_generate_action_flag: threading.Event, logger: logging.Logger):
        while True:
            try:
                input("[enter]:")
                start_generate_action_flag.set()
            except EOFError:
                logger.info(f"finish this program.")
    ###########################################################################################

    def main(self):
        obs_from_minecraft_to_get_obs_q = self.queues.obs_from_minecraft_to_get_obs
        self_audio_final_result_from_asr_to_accumulate_audio_q = self.queues.self_audio_final_result_from_asr_to_accumulate_audio
        pcm_bytes_from_main_to_asr_q = self.queues.pcm_bytes_from_main_to_asr

        self_name = self.config.name
        tts_speaker_id = self.config.tts_speaker_id
        primitives = self.config.primitives
        asr_chunk_bytes = self.config.asr_chunk_bytes
        frame_sec = self.config.frame_sec        
        sample_rate = self.config.opus_cfg.sample_rate
        frame_millis = self.config.opus_cfg. frame_millis
        application = self.config.opus_cfg.application
        opus_sample_rate = self.config.opus_cfg.sample_rate
        mc_server_id = self.config.minecraft_server_cfg.server_id
        env_box, offset = self.config.minecraft_cfg.env_box, self.config.minecraft_cfg.offset
        
        logger = self.modules.logger
        obs_runtime = self.modules.obs_runtime
        mineflayer = self.modules.mineflayer
        send_self_speech_ws = self.modules.send_self_speech_ws
        llm_module = self.modules.llm
        tts_module = self.modules.tts

        world_belief, self_belief = self.state.beliefs["world"], self.state.beliefs["self"]
        base_prompts = self.state.base_prompts
        latest_send_speech_for_minecraft_thread = self.state.latest_send_speech_for_minecraft_thread
        start_generation_action_event = self.state.start_generation_action_event
        stop_tts_audio_stream_event = self.state.stop_tts_audio_stream_event

        disallowed_expressions = DISALLOWED_EXPRESSIONS        
        resample_state = None
        last_speaker_name = None
        logger.info("Action generation will start when you press Enter.")
        while True:
            # obs data取得
            obs = self.get_obs_from_minecraft(obs_from_minecraft_to_get_obs_q)
            if obs is None:
                continue

            # ASR処理
            if obs is not None:
                audio_obs = self.get_audio_obs(obs)
                if audio_obs is not None and audio_obs is not []:
                    pcm_bytes, last_speaker_name, resample_state = self.build_pcm_bytes_data_from_audio_obs(audio_obs, self_name, opus_sample_rate, last_speaker_name, resample_state)
                    if pcm_bytes is not None:
                        self.put_pcm_bytes_to_asr_queue_in_chunks(pcm_bytes, asr_chunk_bytes, pcm_bytes_from_main_to_asr_q)
                      
            # 音声情報追加
            if not self_audio_final_result_from_asr_to_accumulate_audio_q.empty():
                result_asr = self.get_asr_results_from_queue(self_audio_final_result_from_asr_to_accumulate_audio_q)
                self.accumulate_self_audio(last_speaker_name, result_asr, obs_runtime)
    
            # 観測情報追加
            if obs is not None:
                vision_self, world_belief, self_belief = self.accumulate_world_self_vision(obs, world_belief, self_belief, self_name, env_box, offset, obs_runtime)
            
            # 観測に基づく自己発話の音声合成化
            if vision_self is not None:
                latest_send_speech_for_minecraft_thread = self.speak_own_chat_message(vision_self, self_name, tts_speaker_id, frame_sec, sample_rate, frame_millis, application, stop_tts_audio_stream_event, latest_send_speech_for_minecraft_thread, tts_module, send_self_speech_ws)

            # 行動生成・実行
            if start_generation_action_event.is_set():
                start_generation_action_event.clear()
                generate_and_execution_action_js_thread = threading.Thread(target=self.generate_and_execution_action_js, args=(self_belief, base_prompts, disallowed_expressions, self_name, mc_server_id, primitives, mineflayer, llm_module, obs_runtime, logger,))
                generate_and_execution_action_js_thread.start()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yml")
    args = parser.parse_args()

    config = load_config(args.config)
    easy_llm_cfg, minecraft_server_cfg, mineflayer_server_cfg, minecraft_cfg, llm_cfg, easy_llm_voice_cfg, opus_cfg, tts_cfg = build_agent_dataclasses(config)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_dir = f'{config["agent"]["logs_dir"]}/{timestamp}'

    tts = VOICEVOX_TTS(make_file_logger("tts", f"{log_dir}/tts.log"), tts_cfg.host, tts_cfg.port, tts_cfg.enable_interrogative_upspeak, tts_cfg.output_sampling_rate, tts_cfg.output_stereo)
    asr_input_q, asr_output_q = queue.Queue(), queue.Queue()
    asr = RealtimeSTT(logdir=log_dir, input_buffer=asr_input_q, output_buffer=asr_output_q)

    agent = Agent(
        log_dir=log_dir,
        name=config["agent"]["name"],
        agent_type=config["agent"]["type"],
        tts_speaker_id=config["agent"]["tts_model_id"],
        prompt_path=config["agent"]["prompts"],
        minecraft_server_cfg=minecraft_server_cfg,
        mineflayer_server_cfg=mineflayer_server_cfg,
        easy_llm_cfg=easy_llm_cfg,
        minecraft_cfg=minecraft_cfg,
        llm_cfg=llm_cfg,
        easy_llm_voice_cfg=easy_llm_voice_cfg,
        opus_cfg=opus_cfg,
        tts=tts,
        asr_input_q = asr_input_q,
        asr_output_q = asr_output_q,
        asr = asr,
        belief_cfg=BELIEF_CFG,
    )    
    agent.main()

if __name__ == "__main__":
    main()