FROM node:18.18.0-bookworm-slim

WORKDIR /app

# 依存ファイルを先にコピー
COPY package*.json ./
COPY mineflayer-4.33.0 ./mineflayer-4.33.0

RUN npm install

# 残りのソースをコピー
COPY . .

CMD ["node", "server.js"]
