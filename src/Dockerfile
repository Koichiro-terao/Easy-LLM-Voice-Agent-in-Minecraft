FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y python3.11 python3.11-dev python3-pip portaudio19-dev && \
    rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python3 && \
    ln -sf /usr/bin/python3.11 /usr/local/bin/python

RUN python3 -m pip install --upgrade pip

WORKDIR /app/src

RUN pip3 install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

COPY src/requirements.txt /tmp/requirements.txt

RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

RUN python3 -c "from faster_whisper import WhisperModel; WhisperModel('turbo', device='cpu', compute_type='int8')"

CMD ["python3", "-u", "main.py", "--config", "sally_cfg.yml"]
