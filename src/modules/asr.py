import os
import time
import queue
import datetime
import threading
from google.cloud import speech
from google.api_core.exceptions import OutOfRange
from RealtimeSTT import AudioToTextRecorder
from .utils import make_file_logger

class Google_ASR():
    def __init__(self,
                 logdir:str,
                 input_buffer:queue.Queue,      # 音声データ入力用キュー
                 output_buffer:queue.Queue,     # 音声認識結果出力用キュー
                 json_key_path,                 # GoogleCloudAPI用jsonファイルのパス
                 language:str="ja-jp",          # 言語設定
                 frame_length:int=0.02,         # フレーム長　      マイクストリームと揃える
                 sample_rate:int=16000,         # サンプリングレート マイクストリームと揃える
                 auto_shutdown=True,            # ASRの自動シャットダウン
                 auto_shutdown_time=180         # ASRの自動シャットダウン秒数
                 ):
        
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = json_key_path

        self.language = language
        self.chunk_size  = round(frame_length*sample_rate)
        self.rate     = sample_rate

        self.input_buffer = input_buffer    # 受信用キュー
        self.output_buffer = output_buffer  # 送信用キュー

        self.responses = None
        self.current_text = ""
        self.before_text = ""
        self.final_recognized_result = ""   
        self.current_time = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')       # 現在の時刻

        self.auto_shutdown = auto_shutdown
        self.auto_shutdown_time = auto_shutdown_time
        self.stop_event = threading.Event()

        # terao 追加項目
        self.logger = make_file_logger(f"asr", f"{logdir}/asr.log")

        self.logger.info(f"ASR __init__ start")
        self.client, self.config, self.streaming_config = self.asr_init()
        self.logger.info(f"ASR __init__ finish")

    # 認識結果を出力するループ
    def transcription_loop(self):
        requests = self.audio_request_generator()
        self.responses = self.client.streaming_recognize(self.streaming_config, requests)

        while not self.stop_event.is_set():
            try: 
                responses = self.responses
                for response in responses:

                    if self.stop_event.is_set():
                        return

                    self.current_text = ""
                    self.final_recognized_result = ""
                    self.current_time = f"[{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}]"

                    #print(f"\033[33m{response}\033[0m")    #応答データ形式確認
                    if not response.results:
                        continue

                    for result in response.results:
                        if not result.alternatives:
                            continue
                        
                        # 認識結果を取得
                        if result.is_final != True:
                            self.current_text += result.alternatives[0].transcript
                        else:
                            self.final_recognized_result += result.alternatives[0].transcript

                    #  発話継続なら逐次表示
                    if response.results[0].is_final != True:

                        #前の認識結果と全く一緒の場合は表示しない
                        if self.before_text != self.current_text:
                            # print(self.current_time, "Transcript: {}".format(self.current_text))
                            self.logger.info(f"partial utterance:{self.current_text}")
                            self.output_buffer.put((self.current_text, False))

                    # 発話終了検知したら最終錦結果を表示
                    elif response.results[0].is_final == True:
                        # print("\033[33m", self.current_time, "Transcript Final: {}".format(self.final_recognized_result,), "\033[0m")
                        self.logger.info(f"final utterane:{self.final_recognized_result}")
                        self.output_buffer.put((self.final_recognized_result, True))
                    self.before_text = self.current_text

            except OutOfRange:
                self.logger.critical("\033[35mGoogle-Speech Timeout:Connection has lost\033[0m")
                break

    # 入力用キュー内の音声データを受け取ってGoogleSpeechにリクエスト
    def audio_request_generator(self):
        start = time.perf_counter()
        while True:
            try:
                data = self.input_buffer.get(timeout=0.1)

                # 自動シャットダウン
                if self.auto_shutdown == True:
                    if (time.perf_counter() - start) > self.auto_shutdown_time:
                        self.logger.error("Automatically timeout: ASR shutdown")
                        self.stop_event.set()
                        return

            # キューにデータがない場合のエラー処理
            except queue.Empty:
                print("ASR input empty: no data in queue")
                continue

            #データをイテレータで送信
            yield speech.StreamingRecognizeRequest(audio_content=data)

    def asr_init(self):
        client = speech.SpeechClient()
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=self.rate,
            language_code=self.language,
            enable_automatic_punctuation=True       # 句読点を自動でつけるフラグ
        )

        streaming_config = speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True,  # 中間結果を随時受け取る
        )

        return client, config, streaming_config
    
    def run(self):
        self.logger.info("ASR start")
        threading.Thread(target=self.transcription_loop, daemon=True).start()

class RealtimeSTT:
    def __init__(self, 
                 logdir,
                 input_buffer:queue.Queue,
                 output_buffer:queue.Queue,
                 mic_device:int = 1,
                 unknown_sentence_detection_pause:float = 0.7
                 ):
        self.logdir = logdir
        self.input_buffer = input_buffer
        self.output_buffer = output_buffer
        self.mic_device = mic_device
        self.unknown_sentence_detection_pause = unknown_sentence_detection_pause
        self.logger = make_file_logger(f"asr", f"{logdir}/asr.log")

        recorder_config = {
        'use_microphone':False,
        'spinner': True,
        'device': 'cuda',
        #'model': 'large-v2', # or large-v2 or deepdml/faster-whisper-large-v3-turbo-ct2 or ...
        'model': 'turbo', # or large-v2 or deepdml/faster-whisper-large-v3-turbo-ct2 or ...
        # 'download_root': None, # default download root location. Ex. ~/.cache/huggingface/hub/ in Linux
        'input_device_index': mic_device,
        'realtime_model_type': 'turbo', # or small.en or distil-small.en or ...
        'language': 'ja',
         'silero_sensitivity': 0.05,
         'webrtc_sensitivity': 3,
         'post_speech_silence_duration': unknown_sentence_detection_pause,
        'min_length_of_recording': 1.1,        
        'min_gap_between_recordings': 0,                
        'enable_realtime_transcription': True,
        # 'realtime_processing_pause': 0.02,
        # 'realtime_processing_pause': 0.1,
        'realtime_processing_pause': 0.2,
        'on_realtime_transcription_update': self.realtime_transcription_update,
        # 'on_realtime_transcription_stabilized': text_detected,
        'silero_deactivity_detection': True,
        'early_transcription_on_silence': 0,
        'beam_size': 10,
        # 'beam_size': 10,
        'beam_size_realtime': 10,
        # 'beam_size_realtime': 10,
        #  'batch_size': 0,
        #  'realtime_batch_size': 0,        
         'no_log_file': False,
         'initial_prompt_realtime': (
             "End incomplete sentences with ellipses.\n"
             "Examples:\n"
             "Complete: The sky is blue.\n"
             "Incomplete: When the sky...\n"
             "Complete: She walked home.\n"
             "Incomplete: Because he...\n"
         ),
         'silero_use_onnx': True,
         'faster_whisper_vad_filter': False,
        }
        # end_of_sentence_detection_pause = 0.45
        # mid_sentence_detection_pause = 2.0

        self.recorder = AudioToTextRecorder(**recorder_config)
        print(f"finish create realtimeSTT instance")

    def realtime_transcription_update(self, realtime_recognized):
        self.logger.info('------ in realtime transcriptin update ---------')
        self.logger.info(f"partial utterance: {realtime_recognized}")
        self.output_buffer.put((realtime_recognized, False))
        self.logger.info('------  out realtime transcriptin update ---------\n')

    def transcription_after_pause(self, recognized):
        self.logger.info('------ in transcription after pausee ---------')
        self.logger.info(f"final utterane: {recognized}")
        self.output_buffer.put((recognized, True))
        self.logger.info('------  out transcription after pause  ---------\n')

    def audio_feed_loop(self):
        while True:
            try:
                pcm_bytes = self.input_buffer.get(timeout=0.1)
            except queue.Empty:
                continue
        
            self.recorder.feed_audio(pcm_bytes)

    def transcription_loop(self):
        while True:
            self.recorder.text(self.transcription_after_pause)

    def run(self):
        self.logger.info("ASR start")
        get_audio_thread = threading.Thread(target=self.audio_feed_loop, daemon=True)
        get_audio_thread.start()
        transcription_thread = threading.Thread(target=self.transcription_loop, daemon=True)
        transcription_thread.start()

if __name__ == "__main__":
    import pyaudio
    from pynput import keyboard
    class AudioInput():
        def __init__(self, 
                    audio_input_q:queue.Queue,
                    frame_length:float=0.02,
                    rate:int=16000,
                    sample_width:int=2,
                    audio_channel:int=1,
                    device_index:int=1,
                    use_push_to_talk:bool=True
                    ):

            self.audio_input_q = audio_input_q
            self.frame_length      = frame_length
            self.rate              = rate
            self.sample_width      = sample_width
            self.num_audio_channel = audio_channel
            self.device_index      = device_index
            self.use_push_to_talk  = use_push_to_talk
            self.chunk_size = round(self.frame_length * self.rate)
            self._p = pyaudio.PyAudio() # 音声入力ストリームの宣言
            self.stream = self._p.open(
                format=self._p.get_format_from_width(self.sample_width),
                channels=self.num_audio_channel,
                rate=self.rate,
                input=True,
                output=False,
                frames_per_buffer=self.chunk_size,
                start=False,
                input_device_index=self.device_index,
            )
            self.ptt = PushtoTalk() 
            self.ptt.start()        

        def listen_wav_loop(self, q:queue.Queue):
            stream, chunk_size = self.stream, self.chunk_size
            stream.start_stream()

            while stream.is_active():
                input_data = stream.read(chunk_size, exception_on_overflow=False)
                if not self.ptt.event.is_set() and self.use_push_to_talk:                                 
                    input_data = b'\x00\x00' * chunk_size                    
                q.put(input_data)

        def run(self):
            audio_input_q = self.audio_input_q
            t1 = threading.Thread(target=self.listen_wav_loop, args=(audio_input_q,), name="audio_input")
            t1.start()
    class PushtoTalk:
        def __init__(self):
            self.event = threading.Event()       # PTT フラグ
            self.stop_event = threading.Event()  # 終了制御
            self.pushing_key = set()
            self.ptt_shortcut_key = {keyboard.KeyCode.from_char('m')}  #ショートカットキー　複数登録も可能 キーコード一覧はkeyboardモジュールを参照してください
            self.listener = None
            self.thread = None

        # ---- キー入力 ----
        def on_press(self, key):
            self.pushing_key.add(key)
            if not self.event.is_set() and self.ptt_shortcut_key.issubset(self.pushing_key):
                print("recording now🎤")
                self.event.set()

        def on_release(self, key):
            self.pushing_key.discard(key)
            if self.event.is_set() and not self.ptt_shortcut_key.issubset(self.pushing_key):
                print("No recode🔇")
                self.event.clear()

        # ---- 内部スレッド ----
        def _run(self):
            self.listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release, daemon=True)
            self.listener.start()

        # ---- 開始 ----
        def start(self):
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

        # ---- 終了 ----
        def stop(self):
            self.stop_event.set()
            self.thread.join(timeout=2)

    from enum import Enum
    class TalkState(str, Enum):
        IDLE = "idle"          # 無音（誰も話していない）
        TALKING = "talking"    # 発話中
    
    JSON_PATH = "C:/Users/koichiro_terao/Desktop/asr_forvoyager/tabitoc-ac497f8bb9c4.json"

    output_text_buffer = queue.Queue()
    output_audiodata_buffer = queue.Queue()

    print(f"asr __init__ AudioInput start")
    ainput = AudioInput(output_audiodata_buffer)
    print(f"asr __init__ AudioInput finish")
    print(f"asr __init__ ASR start")
    # asr = Google_ASR(JSON_PATH, output_audiodata_buffer, output_text_buffer, check_input_user_buffer)
    asr = RealtimeSTT("logs", output_audiodata_buffer, output_text_buffer)
    print(f"asr __init__ ASR finish")


    ainput.run()
    print(f"started AudioInput")
    
    asr.run()
    print(f"started ASR")

    state = TalkState.IDLE

    while True:
        try:
            input_iu = output_text_buffer.get(timeout=0.1)
            state=TalkState.IDLE
            print(f"取得結果:{input_iu}")

        except queue.Empty:
            continue  # user入力終了