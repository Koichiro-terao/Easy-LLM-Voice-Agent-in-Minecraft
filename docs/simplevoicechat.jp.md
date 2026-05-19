## Minecraft Simple Voice Chat の使用方法

本プロジェクトでは、Minecraft 内の音声入力に Simple Voice Chat mod を使用します。

### 導入

Simple Voice Chat は、Minecraft クライアントとサーバーの両方に導入してください。

公式ダウンロード:
https://modrepo.de/minecraft/voicechat/downloads

インストール手順:
https://modrepo.de/minecraft/voicechat/wiki/installation

### 初回設定

Minecraft にログイン後、`V` キーを押して Voice Chat GUI を開きます。

初回設定画面で以下を設定してください。

1. 使用するマイクを選択する
2. 使用するスピーカーを選択する
3. 音声入力方式を選択する
   - Push to Talk
   - Voice activation
4. Push to Talk を使う場合は、使用するキーを設定する
5. マイク入力テストを行う

クライアント設定の詳細:
https://modrepo.de/minecraft/voicechat/wiki/client_setup

### よく使うキー

| キー | 内容 |
|---|---|
| `V` | Voice Chat GUI を開く |
| `M` | マイクをミュート |
| `N` | ボイスチャットを無効化 |
| `H` | ボイスチャット関連アイコンを非表示 |

キー割り当ての詳細:
https://modrepo.de/minecraft/voicechat/wiki/key_bindings

### サーバー使用時の注意

マルチプレイで使用する場合、サーバー側で Simple Voice Chat 用の UDP ポートを開ける必要があります。  
デフォルトでは `24454/UDP` が使用されます。

接続できない場合は、以下を確認してください。

- Simple Voice Chat がクライアントとサーバーの両方に入っているか
- サーバーの `24454/UDP` ポートが開いているか
- マイク・スピーカーの設定が正しいか
- Minecraft や OS 側でマイク使用が許可されているか

トラブルシューティング:
https://modrepo.de/minecraft/voicechat/wiki/troubleshooting