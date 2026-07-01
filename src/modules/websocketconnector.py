import json
import time
import queue
import threading
from websockets.sync.server import serve

class WebsocketConnector:
    def __init__(self,
                 ws_name,
                 host:str="localhost",
                 port:int=8765,
                 output_data_for_q:bool=True):
        self.ws_name = ws_name
        self.host = host
        self.port = port
        self.max_size=10*1024*1024 # 10MB
        self.bool_output_data_for_q = output_data_for_q
        self.websocket = None
        self.queue = queue.Queue()

    def handler(self, websocket): # 接続すると駆動する recv と 同意義
        print(f"[{self.ws_name}] connected")
        self.websocket = websocket
        try:
            for message in websocket:   # 内部では recv() を繰り返している
                if self.bool_output_data_for_q:
                    self.queue.put(message)
        finally:
            print("disconnected")

    def send(self, data: dict) -> None:

        while self.websocket is None:
            print(f"[{self.ws_name}] not connected. Waiting for connection...")
            time.sleep(5.0)
 
        self.websocket.send(json.dumps(data))
        print(f"[{self.ws_name}] sent data")

    def run(self):
        with serve(self.handler, self.host, self.port, max_size=self.max_size) as server:
            print(f"listening on ws://{self.host}:{self.port}")
            server.serve_forever()

if __name__ == "__main__":
    wscon = WebsocketConnector("easy_llm", "0.0.0.0", 7891, True)
    threading.Thread(target=wscon.run).start()
    first_access_data = {"type": "first_access",
                         "min": {"x": 0, "y": 60, "z": 0},
                         "max": {"x": 15, "y": 80, "z": 15}}
    wscon.send(first_access_data)
    while True:
        data = wscon.output_data_for_q.get()
        print(json.dumps(json.loads(data), ensure_ascii=False, indent=2))