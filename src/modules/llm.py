import re
import time
import json
from datetime import datetime
from pathlib import Path
from javascript import require
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

def _process_ai_message(message, disallowed_expressions=[]):
    assert isinstance(message, str)

    retry = 3
    error = None
    code = None
    while retry > 0:
        try:
            babel = require("@babel/core")
            babel_generator = require("@babel/generator").default

            code_pattern = re.compile(r"```(?:javascript|js)(.*?)```", re.DOTALL)
            code = "\n".join(code_pattern.findall(message))

            # check whether disallowed expressions are included
            for dic in disallowed_expressions:
                assert dic["expression"] not in code, dic["message"]

            #for line in code.split("\n"):
            #    if "/tell" in line and "/tell @s" not in line:
            #        raise Exception('Do not whisper to others using `bot.chat("/tell otherPlayerName ...")`. You can only whisper to yourself.')

            parsed = babel.parse(code)
            functions = []
            assert len(list(parsed.program.body)) > 0, "No functions found"
            for i, node in enumerate(parsed.program.body):
                if node.type != "FunctionDeclaration":
                    continue
                node_type = (
                    "AsyncFunctionDeclaration"
                    if node["async"]
                    else "FunctionDeclaration"
                )
                functions.append(
                    {
                        "name": node.id.name,
                        "type": node_type,
                        "body": babel_generator(node).code,
                        "params": list(node["params"]),
                    }
                )
            # find the last async function
            main_function = None
            for function in reversed(functions):
                if function["type"] == "AsyncFunctionDeclaration":
                    assert main_function is None, "Do not define multiple async functions. Only the main function can be defined as an async function. Also, just use the provided useful programs instead of redefining them."
                    main_function = function
            assert (
                main_function is not None
            ), "No async function found. Your main function must be async."
            assert (
                len(main_function["params"]) == 1
                and main_function["params"][0].name == "bot"
            ), f"Main function {main_function['name']} must take a single argument named 'bot'"
            program_code = "\n\n".join(function["body"] for function in functions)
            exec_code = f"await {main_function['name']}(bot);"
            return {
                "program_code": program_code,
                "program_name": main_function["name"],
                "exec_code": exec_code,
                "whole_code": program_code + "\n" + exec_code
            }, None
        except Exception as e:
            retry -= 1
            error = e
            time.sleep(1)

    return {"failed_code": code}, f"Error parsing action response (before program execution): {error}"
def _process_ai_message_json(message, disallowed_expressions=[]):
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
                parsed_result, error = _process_ai_message(message.content, disallowed_expressions=disallowed_expressions)
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
                parsed_result, error = _process_ai_message(message, disallowed_expressions=disallowed_expressions)
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
    # llm = OpenaiLLM("logs", api_key=f"sk-xxx")
    # llm = ollamaLLM("logs", api_key=f"sk-xxx")
    llm = LangchainLLM("logs")

    prompt = llm.format_prompts_for_llm([("system", "こんにちは"), ("user", "こんにちは")])

    output = llm.request_llm(prompts=prompt, disallowed_expressions=[])

    print(f"{output}")


    

