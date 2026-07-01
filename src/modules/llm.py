import json
from datetime import datetime
from pathlib import Path
import websocket
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, AIMessage, HumanMessage
from openai import OpenAI

import requests
try:
    from .utils import make_file_logger
# スクリプト単体実行時用（フォールバック）
except ImportError:
    import sys, os
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from modules.utils import make_file_logger

TIMESTR_FORMAT = '%Y%m%d_%H%M%S_%f'

def _process_ai_message_json(message:str, disallowed_expressions=[]):
    message = message.replace('"""', '').replace('"""', '').replace("'''", '').replace("'''", '').replace('json', '').replace("```", '').replace("```", '')
    parsed = json.loads(message)
    lines = parsed["code"]
    code = ""
    for line in lines:
        code += f"await {line};\n"

    result = {
        "whole_code": code
    }

    return result, None

class OpenAILLM:
    def __init__(self, 
                 log_dir:str, 
                 api_key:str, 
                 model_name:str="gpt-4o-mini", 
                 temperature:float=1.0, 
                 request_timeout:int=120,
                 max_trial:int=3,
                 ):
        
        self.api_key = api_key
        self.model_name= model_name
        self.temperature=temperature
        self.request_timeout = request_timeout
        self.max_trial = max_trial
        self.log_dir = log_dir
        self.client = OpenAI(api_key=self.api_key)
        self.logger = make_file_logger(f"llm", f"{log_dir}/llm.log")
        print(f"openai LLM __init__ finish")

        pass

    def format_prompts_for_llm(self, prompts: list[tuple[str, str]]) -> list[dict]:
        formatted = []
        for role, content in prompts:
            if role in ("user", "system", "assistant"):
                formatted.append({"role": role, "content": content})
            else:
                raise ValueError(f"対応したメッセージ形式がありません: {role}")
        return formatted

    def request_llm(self, prompts:list, disallowed_expressions=[], javascript_check=True):
        client, model_name, temperature, request_timeout = self.client, self.model_name, self.temperature, self.request_timeout
        max_trial, log_dir, logger = self.max_trial, self.log_dir, self.logger

        for _ in range(max_trial):
            logger.info(f"start llm chat create")
            response = client.chat.completions.create(
                        model=model_name,
                        messages=prompts,
                        temperature=temperature,
                        timeout=request_timeout,
                        # max_tokens=100
                        response_format={"type": "json_object"},
                    )
            logger.info(f"finish llm chat create")
            if javascript_check:
                logger.info(f"check javascript")
                #parsed_result, error = _process_ai_message(response.choices[0].message.content, disallowed_expressions=disallowed_expressions)
                parsed_result, error = _process_ai_message_json(response.choices[0].message.content, disallowed_expressions=disallowed_expressions)
            else:
                parsed_result, error = {"whole_code":response.choices[0].message.content}, None
            
            timestr = datetime.now().strftime(TIMESTR_FORMAT)
            log_prompt = f""
            for prompt in prompts:
                log_prompt += f"{prompt['content']}"

            if error is None:
                with (Path(log_dir) / f"coding_llm_{timestr}_human_prompt.txt").open("w", encoding='utf-8') as f:
                    f.write(log_prompt)

                code = parsed_result["whole_code"]
                with (Path(log_dir) / f"coding_llm_{timestr}_code.txt").open("w", encoding='utf-8') as f:
                    f.write(code)

                print(f"\033[32m\n###### LLM ###### \033[0m")
                print(f"\033[32m{log_prompt}\033[0m")
                print(f"\033[31m----------------------------\033[0m")
                print(f"\033[31m{code}\033[0m")
                return code, timestr

            else:
                with (Path(log_dir) / f"coding_llm_{timestr}_failed_human_prompt.txt").open("w", encoding='utf-8') as f:
                    f.write(log_prompt)
                with (Path(log_dir) / f"coding_llm_{timestr}_failed_code.txt").open("w", encoding='utf-8') as f:
                    f.write(parsed_result["failed_code"])
                logger.info(f"Failed coding. Message: {error}")
                continue

        print(f"Failed coding in {max_trial} trials.")
        return "await doNothing(bot);", None

class OpenAIRealtimeLLM:
    def __init__(
        self,
        log_dir:str,
        api_key:str,
        model_name="gpt-realtime-2",
        instructions="",
        max_trial:int=3,
    ):
        self.log_dir = log_dir
        self.api_key = api_key or os.environ["OPENAI_API_KEY"]
        self.model_name = model_name
        self.url = f"wss://api.openai.com/v1/realtime?model={model_name}"
        self.max_trial = max_trial
        self.logger = make_file_logger(f"llm", f"{log_dir}/llm.log")

        self.ws = websocket.create_connection(
            self.url,
            header=[f"Authorization: Bearer {self.api_key}"],
        )

        self.wait("session.created")

        self.send({
            "type": "session.update",
            "session": {
                "type": "realtime",
                "output_modalities": ["text"],
                "instructions": instructions,
            },
        })

        self.wait("session.updated")

    def format_prompts_for_llm(self, prompts: list[tuple[str, str]]) -> list[dict]:
        return [{"role": role, "content": content} for role, content in prompts]

    def request_llm(self, prompts: list[dict], disallowed_expressions=[], javascript_check=True):
        human_prompt = "\n\n".join(p["content"] for p in prompts if p["role"] != "system")

        self.send({
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": human_prompt}],
            },
        })

        for _ in range(self.max_trial):
            self.send({
                "type": "response.create",
                "response": {"output_modalities": ["text"]},
            })

            result = ""

            while True:
                event = json.loads(self.ws.recv())
                event_type = event.get("type")

                if event_type == "response.output_text.delta":
                    result += event.get("delta", "")

                elif event_type == "response.done":
                    break

                elif event_type == "error":
                    raise RuntimeError(json.dumps(event, ensure_ascii=False))

            if javascript_check:
                parsed_result, error = _process_ai_message_json(result, disallowed_expressions=disallowed_expressions)
            else:
                parsed_result, error = {"whole_code":result}, None

            timestr = datetime.now().strftime(TIMESTR_FORMAT)

            if error is None:
                with (Path(self.log_dir) / f"coding_llm_{timestr}_human_prompt.txt").open("w", encoding='utf-8') as f:
                    f.write(human_prompt)

                code = parsed_result["whole_code"]
                with (Path(self.log_dir) / f"coding_llm_{timestr}_code.txt").open("w", encoding='utf-8') as f:
                    f.write(code)

                print(f"\033[32m\n###### LLM ###### \033[0m")
                print(f"\033[32m{human_prompt}\033[0m")
                print(f"\033[31m----------------------------\033[0m")
                print(f"\033[31m{code}\033[0m")
                return code, timestr
            
            else:
                with (Path(self.log_dir) / f"coding_llm_{timestr}_failed_human_prompt.txt").open("w", encoding='utf-8') as f:
                    f.write(human_prompt)
                with (Path(self.log_dir) / f"coding_llm_{timestr}_failed_code.txt").open("w", encoding='utf-8') as f:
                    f.write(parsed_result["failed_code"])
                self.logger.info(f"Failed coding. Message: {error}")
                continue

        print(f"Failed coding in {self.max_trial} trials.")
        return "await doNothing(bot);", None

    def close(self):
        self.ws.close()

    def send(self, event: dict):
        self.ws.send(json.dumps(event, ensure_ascii=False))

    def wait(self, event_type: str):
        while True:
            event = json.loads(self.ws.recv())
            if event.get("type") == event_type:
                return event
            if event.get("type") == "error":
                raise RuntimeError(json.dumps(event, ensure_ascii=False))

class LangchainLLM:
    def __init__(self, 
                 log_dir:str, 
                 api_key:str, 
                 model_name:str="gpt-4o-mini", 
                 temperature:float=1.0, 
                 request_timeout:int=120,
                 max_trial:int=3,
                 ):
        print(f"langchain(openai) LLM __init__ start")
        
        self.api_key = api_key
        self.model_name= model_name
        self.temperature=temperature
        self.request_timeout = request_timeout
        self.max_trial = max_trial
        self.model = ChatOpenAI(
                model_name=model_name,
                temperature=temperature,
                request_timeout=request_timeout,
                api_key=api_key,
                
            )
        self.log_dir = log_dir
        self.logger = make_file_logger(f"llm", f"{log_dir}/llm.log")
        print(f"langchain(openai) LLM __init__ finish")

        pass

    def format_prompts_for_llm(self, prompts: list[tuple[str, str]]) -> list:
        formatted = []
        for role, content in prompts:
            if role == "user":
                formatted.append(HumanMessage(content=content))
            elif role == "system":
                formatted.append(SystemMessage(content=content))
            elif role == "assistant":
                formatted.append(AIMessage(content=content))
            else:
                raise ValueError(f"対応したメッセージ形式がありません: {role}")
        return formatted
   
    def request_llm(self, prompts:list, disallowed_expressions=[], javascript_check=True):
        model = self.model
        max_trial, log_dir, logger = self.max_trial, self.log_dir, self.logger

        for _ in range(max_trial):
            logger.info(f"start llm chat create")
            message = model.invoke(prompts)
            logger.info(f"finish llm chat create")

            if javascript_check:
                parsed_result, error = _process_ai_message_json(message.content, disallowed_expressions=disallowed_expressions)
            else:
                parsed_result, error = {"whole_code":message.content}, None
            # parsed_result, error = message.content, None # デバッグ用
            
            timestr = datetime.now().strftime(TIMESTR_FORMAT)
            log_prompt = f""
            for prompt in prompts:
                log_prompt += f"{prompt.content}"
            if error is None:

                with (Path(log_dir) / f"coding_llm_{timestr}_human_prompt.txt").open("w", encoding='utf-8') as f:
                    f.write(log_prompt)

                code = parsed_result["whole_code"]
                # code = parsed_result # デバッグ用
                with (Path(log_dir) / f"coding_llm_{timestr}_code.txt").open("w", encoding='utf-8') as f:
                    f.write(code)

                print(f"\033[32m\n###### LLM ###### \033[0m")
                print(f"\033[32m{log_prompt}\033[0m")
                print(f"\033[31m----------------------------\033[0m")
                print(f"\033[31m{code}\033[0m")
                return code, timestr
            
            else:
                with (Path(log_dir) / f"coding_llm_{timestr}_failed_human_prompt.txt").open("w", encoding='utf-8') as f:
                    f.write(log_prompt)
                with (Path(log_dir) / f"coding_llm_{timestr}_failed_code.txt").open("w", encoding='utf-8') as f:
                    f.write(parsed_result["failed_code"])
                logger.info(f"Failed coding. Message: {error}")
                continue
            
        print(f"Failed coding in {max_trial} trials.")
        return "await doNothing(bot);", None

class OllamaLLM:
    def __init__(self,
                 log_dir:str, 
                 model_name:str="gpt-oss:20b", 
                 address="localhost",
                 port=11434, 
                 temprature:float=1.0, 
                 request_timeout:int=120,
                 max_trial:int=3,
                 ):
        print(f"ollama LLM __init__ start")
        self.model_name = model_name
        self.temperature =temprature
        self.request_timeout = request_timeout
        self.max_trial = max_trial
        self.address=address
        self.port=port
        self.url = f"http://{address}:{port}/api/chat" 
        # model起動
        payload = {
            "model":self.model_name,
            "prompt":[],
            }
        requests.post(self.url, json=payload)
        self.log_dir = log_dir
        self.logger = make_file_logger(f"llm", f"{log_dir}/llm.log")
        print(f"ollama LLM __init__ finish")
        pass

    def format_prompts_for_llm(self, prompts: list[tuple[str, str]]) -> list[dict]:
        formatted = []
        for role, content in prompts:
            if role in ("user", "system", "assistant"):
                formatted.append({"role": role, "content": content})
            else:
                raise ValueError(f"対応したメッセージ形式がありません: {role}")
        return formatted

    def request_llm(self, prompts:list, disallowed_expressions=[], javascript_check=True):
        model_name, templature, url,  =  self.model_name, self.temperature, self.url
        max_trial, log_dir, logger = self.max_trial, self.log_dir, self.logger

        for _ in range(max_trial):
            
            payload = {
                "model":model_name,
                "messages":prompts,
                "stream":False,
                "options":{
                    "temperature":templature
                    }
                }
            logger.info(f"start llm chat create")
            response = requests.post(url, json=payload)
            logger.info(f"finish llm chat create")
    
            #応答の整形
            message = response.json()["message"]["content"]

            print(f"ollama 応答")
            print(f"{message}")

            if javascript_check:
                parsed_result, error = _process_ai_message_json(message, disallowed_expressions=disallowed_expressions)
            else:
                parsed_result, error = {"whole_code":message}, None
            # parsed_result, error = message, None

            timestr = datetime.now().strftime(TIMESTR_FORMAT)
            log_prompt = f""
            for prompt in prompts:
                log_prompt += f"{prompt['content']}"
            if error is None:

                with (Path(log_dir) / f"coding_llm_{timestr}_human_prompt.txt").open("w", encoding='utf-8') as f:
                    f.write(log_prompt)

                code = parsed_result["whole_code"]
                with (Path(log_dir) / f"coding_llm_{timestr}_code.txt").open("w", encoding='utf-8') as f:
                    f.write(code)

                print(f"\033[32m\n###### LLM ###### \033[0m")
                print(f"\033[32m{log_prompt}\033[0m")
                print(f"\033[31m----------------------------\033[0m")
                print(f"\033[31m{code}\033[0m")
                return code, timestr
            
            else:
                with (Path(log_dir) / f"coding_llm_{timestr}_failed_human_prompt.txt").open("w", encoding='utf-8') as f:
                    f.write(log_prompt)
                with (Path(log_dir) / f"coding_llm_{timestr}_failed_code.txt").open("w", encoding='utf-8') as f:
                    f.write(parsed_result["failed_code"])
                logger.info(f"Failed coding. Message: {error}")
                continue

        print(f"Failed coding in {max_trial} trials.")
        return "await doNothing(bot);", None

if __name__ == "__main__":
    llm = OpenAILLM("logs", api_key=f"sk-xxx")
    # llm = OllamaLLM("logs", api_key=f"sk-xxx")
    # llm = LangchainLLM("logs")
    # llm = OpenAIRealtimeLLM("logs", api_key=f"sk-xxx", instructions="output format is Json")

    prompt = llm.format_prompts_for_llm([("system", "こんにちは"), ("user", "こんにちは")])

    output = llm.request_llm(prompts=prompt, disallowed_expressions=[])

    print(f"{output}")