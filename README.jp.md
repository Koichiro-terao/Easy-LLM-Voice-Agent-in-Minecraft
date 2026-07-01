<!-- markdownlint-disable MD033 -->
<p style="font-size:18px">
  | <a href="./README.md">EN</a> | JP |
</p>

# Easy LLM Voice

Easy LLM Voice は、Minecraft 内で一緒に遊べる AI エージェントを開発するためのプラットフォームです。
<br>[Easy LLM Agent in Minecraft](https://github.com/Koichiro-terao/Easy-LLM-Agent-in-Minecraft) を拡張した環境であり、Easy LLM Voice を用いた AI エージェントは、ゲーム内の情報を参照し、LLM を用いて行動や発話を生成し、音声対話を通じて会話できます。

## 最新情報

### 2026-07-02
- AIエージェント実行用 Docker Image を作成 

### 2026-05-31
- AIエージェントの物理演算処理を修正し、ダメージを受けた際にサーバーから kick される問題を修正
- LLM による行動生成を安定させるため、プロンプトの形式を変更
- Minecraft サーバー起動時に、使用するワールドデータを選択できるように変更

### 2026-05-22
- エージェントが利用できる関数に関する制限を撤廃しました。
- これにより、エージェントが正常に動作しない可能性を低減しました。

## このリポジトリでできること

Minecraft の世界で、話しながら一緒に遊べる AI エージェントを実装できます。

- ゲーム内の行動・状態・観測情報を WebSocket で外部へ送信する
- LLM を用いて、ゲーム内の情報を考慮しながら行動・発話を行う AI エージェントを実装する
- Minecraft 内の音声を外部 AI エージェントへ渡す
- AI エージェントが生成した音声を Minecraft 内で再生する
- AI エージェントが Simple Voice Chat mod の設定に基づき、プレイヤーと同じ条件で対話できるようにする

## リポジトリ構成

- [minecraft_server_on_docker](./minecraft_server_on_docker/)
  : Docker 上に Minecraft サーバーを構築するためのソースコード
- [mineflayer_server_on_docker](./mineflayer_server_on_docker/)
  : Docker 上に Mineflayer サーバーを構築するためのソースコード
- [src](./src/)
  : AI エージェントを実装するための Python サンプルコード
- [bat](./bat/)
  : 各 Docker コンテナ実行用batファイル

---

本リポジトリは、[Easy LLM Agent in Minecraft](https://github.com/Koichiro-terao/Easy-LLM-Agent-in-Minecraft) を拡張したものです。まずは Easy LLM Agent in Minecraft をお試しいただき、基本的な使い方や挙動を確認したうえで、Easy LLM Voice Agent in Minecraft をご利用ください。

## Easy LLM Agent in Minecraft: <br>[https://github.com/Koichiro-terao/Easy-LLM-Agent-in-Minecraft](https://github.com/Koichiro-terao/Easy-LLM-Agent-in-Minecraft)

---

# 本リポジトリの起動方法

Minecraft mod と Python プログラムを用いて、Minecraft 内に AI エージェントを実装する手順を示します。

## 1. 事前準備

以下の手順に従って事前準備を行ってください。動作確認は Windows 11 上で行っています。

### 1.1 Docker のインストール

Windows を使用している場合は、[Docker Docs](https://docs.docker.com/desktop/setup/install/windows-install/) からインストーラをダウンロードして実行してください。Docker はバージョン 20.10 以降である必要があります。

### 1.2 Minecraft のインストール

[Minecraft Launcher](https://www.minecraft.net/) をインストールし、Minecraft: Java Edition バージョン 1.21 をプレイできるようにしてください。これはクライアントとして使用します。Java Edition のライセンスが必要です。
<br>Easy LLM Voice で使用する mod は Fabric 環境での使用を前提としています。[Fabric Loader](https://fabricmc.net/) をインストールし、Minecraft バージョン 1.21 の環境を作成してください。その後、Minecraft Launcher で `Fabric` を選択して起動してください。
<br>または、[Prism Launcher](https://prismlauncher.org) などを使用して環境を構築してください。Prism Launcher を使用する場合は、以下の URL を参考にして環境を構築してください。
<br>Fabric with Prism Launcher: [https://wiki.fabricmc.net/player:tutorials:third-party:prism](https://wiki.fabricmc.net/player:tutorials:third-party:prism)

初回起動時は、mod ファイルを入れるフォルダが生成されていないため、クライアントを一度起動してから終了してください。

以下は、Fabric 環境を構築する際に参考となる Web サイトです。

- Fabric Loader: [https://fabricmc.net/](https://fabricmc.net/)
- Installing Fabric: [https://docs.fabricmc.net/players/installing-fabric/](https://docs.fabricmc.net/players/installing-fabric/)
- Fabric with Prism Launcher: [https://wiki.fabricmc.net/player:tutorials:third-party:prism](https://wiki.fabricmc.net/player:tutorials:third-party:prism)

### 1.3 OpenAI API キーの取得

- [こちら](https://platform.openai.com/api-keys)から OpenAI API キーを発行してください。アカウントの作成が必要です。また、[こちら](https://platform.openai.com/settings/organization/billing/overview)から残高が $0.10 以上あることを確認してください。

AI エージェントの行動生成に LLM を使用するため、この API キーを利用します。

### 1.4 mod ファイルのダウンロード

以下の mod ファイルをダウンロードしてください。

- Fabric API: [fabric-api-0.102.0+1.21.jar](https://modrinth.com/mod/fabric-api)
- Simple Voice Chat mod: [`voicechat-fabric-1.21.1-2.6.17.jar`](https://modrinth.com/plugin/simple-voice-chat)
- Easy LLM mod: [easy-llm-fabric-1.0.0+mc1.21.jar][easyllm]
- Easy LLM Voice mod: [easy-llm-voice-fabric-1.0.0+mc1.21.jar][easyllmvoice]

Fabric API は、以下のようにバージョンを選択してダウンロードしてください。

<p align="center">
  <img src="./image/fabric_api_download.png" alt="Voice Bridge GUI sample" width="450">
</p>

## 2. mod ファイルの配置

### 2.1 Minecraft サーバーへの mod 導入

使用する Minecraft サーバーの `data/mods` フォルダに、ダウンロードした 4 つのファイルを配置してください。

本プロジェクトに含まれる Minecraft サーバーを使用する場合は、作業は不要です。

### 2.2 Minecraft クライアントへの mod 導入

使用する Minecraft クライアントの `mods` フォルダに以下のファイルを配置してください。
easy-llm-fabric-1.0.0+mc1.21.jar は、サーバーでのみ動作するため、Minecraft クライアントには必要ありません。


- Fabric API: [`fabric-api-0.102.0+1.21.jar`](https://modrinth.com/mod/fabric-api)
- Simple Voice Chat: [`voicechat-fabric-1.21.1-2.6.17.jar`](https://modrinth.com/plugin/simple-voice-chat)
- Easy LLM Voice mod: [easy-llm-voice-fabric-1.0.0+mc1.21.jar][easyllmvoice]

mod導入手順参考資料 : [Fabric Documentation/installing-mods](https://docs.fabricmc.net/players/installing-mods)

## 3. Agent起動手順

本リポジトリでは、以下の4つの Docker コンテナを用いて実行されます。
スタートメニューなどから Docker Desktop を起動してください。

- Minecraft サーバー コンテナ
- Mineflayer サーバー コンテナ
- VOICEVOX サーバー コンテナ
- Easy LLM Agent コンテナ

### 3.1 Minecraft サーバー コンテナ の起動

- サーバーの起動

  [bat/start_Minecraft_server.bat](./bat/start_Minecraft_server.bat)を実行して下さい。
  初回のみ起動に時間がかかります。
  minecraft ターミナルが起動されます。

  以下のようにターミナルに `Done` が出力されると、サーバーの起動が完了しています。

  ```
  [00:39:28] [Server thread/INFO]: Done (0.465s)! For help, type "help"
  ```

- ワールドへの参加

  Minecraft クライアントを起動し、`マルチプレイ` からワールドに参加してください。ワールドが表示されない場合は、`サーバーを追加` で `localhost:25565` のようなサーバーアドレスを指定してください。

- 権限の付与

 ワールドに参加したら、起動した Minecraft サーバーのターミナルで以下を実行してください。
  ```
  op xxx
  ```
  ここで、`xxx` はあなたの Minecraft ユーザー名です。これにより、あなたのユーザーに op 権限が付与され、さまざまなコマンドを使用可能になります。同一サーバーを再度使用する際には、上記のコマンド実行は不要です。

- simple voice chat mod の確認
  
  simple voice chat mod が利用可能な状態にあるかは以下のファイルの内容を参考に確認をしてください。

  [simplevoicechat 確認方法](./docs/simplevoicechat.jp.md)

**注意**
<br>過去に Docker 上で同じポート番号のサーバーを開いていた場合は、Docker コンテナが停止しているかを確認したうえで、[3.1 minecraft サーバー コンテナ の起動](#31-minecraft-サーバー-コンテナ-の起動)を行ってください。


### 3.2 Mineflayer サーバー コンテナ の起動

  [bat/start_Mineflayer_server.bat](./bat/start_Mineflayer_server.bat)を実行して下さい。初回のみ起動に時間がかかります。
  mineflayer ターミナルが起動されます。

以下のような出力がターミナルに出ると、サーバーの起動が完了しています。

```
Starting container: beliefnestjs
Server started on port 3000
```

### 3.3 VOICEVOX サーバー コンテナ の起動

[bat/start_VOICEVOX.bat](./bat/start_VOICEVOX.bat) を実行してください。
voicevox ターミナルが起動されます。

以下のような出力がターミナルに出ると、サーバーの起動が完了しています。

```
done!
INFO:     Started server process [1]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:50021 (Press CTRL+C to quit)
```

### 3.4 config の設定

`src/sally_cfg.yml` L35 `api_key` に、取得した OpenAI API キーを入力してください。

日本語で会話したい場合は、以下の変更を行ってください。

- L8 `generate_action: ./prompts/coding_llm_human_prompt_template.txt` を `generate_action: ./prompts/coding_llm_human_prompt_template_jp.txt` に変更する
- L9 `primitive: ./prompts/coding_llm_system_prompt_template.txt` を `primitive: ./prompts/coding_llm_system_prompt_template_jp.txt` に変更する

### 3.5 Minecraft の設定

Easy LLM Voice mod の接続設定

  Minecraft 上で Simple Voice Chat mod を介して行われる音声対話に AI エージェントが参加するための WebSocket 接続先アドレスを設定します。
  <br>Minecraft 上の Easy LLM Voice mod GUI から設定を行ってください。

  Easy LLM Voice mod GUI の開き方: `Bキー`を押す

  以下は、[3.6 Easy LLM Agent コンテナ の起動](#36-easy-llm-agent-コンテナ-の起動)を行う際に必要となる接続先アドレスを設定する場合の入力例です。
  GUI のテキストボックスに以下のように入力し、`追加` を押してください。

  ```
  プレイヤー名: sally
  ホスト: host.docker.internal
  ポート: 8765
  ```

  `追加` を押した後、以下の図のように `接続先一覧` に `sally @ host.docker.internal:8765 [ON]` が追加されれば完了です。

  <img src="./image/mod_gui_jp.png" alt="Voice Bridge GUI sample" width="900">

### 3.6 Easy LLM Agent コンテナ の起動

[bat/start_sally.bat](./bat/start_sally.bat) を実行してください。
初回起動時には、Docker Image 作成のため、10 ~ 15 分かかることがあります。

Agent-sally ターミナルが起動されます。

`sally` が Minecraft ワールドに参加したら、Minecraft サーバー ターミナルで以下を実行してください。
<br>AI エージェントがチェストなどを使用するために、op 権限を付与する必要があります。

```
op sally
```

以降は任意のタイミングで、Agent-sally ターミナル に対して Enter キーを一度入力すると、エージェントが行動・発話を生成します。

## 推奨環境
CPU: 12コア
RAM: 16GB
GPU: VRAM 6GB
NVIDIA ドライバー: 525.60.13 以上 (CUDA Version >= 12.1 が利用可能)
NVIDIA Container Toolkit: 1.13.0 以上

## 開発環境

Windows 11
<br>Intel(R) Core(TM) i7-13620H (2.40 GHz)
<br>NVIDIA GeForce RTX 4060

AI エージェント 1 体（RealtimeSTT と VOICEVOX ENGINE を 1 つずつ）を起動した状態で、約 4.7 GB の VRAM を使用します。

## Credits

- RealtimeSTT by Kolja Beigel
  Github: https://github.com/KoljaB/RealtimeSTT

- Simple Voice Chat by Max Henkel / henkelmax
  <br>Official site: https://modrepo.de/minecraft/voicechat
  <br>Modrinth: https://modrinth.com/plugin/simple-voice-chat

Simple Voice Chat itself is not distributed as part of this project.

## License

本プロジェクトは MIT ライセンスの下で提供されています。詳細については、[LICENSE](./LICENSE) をご参照ください。

コードの一部は、同じく MIT ライセンスの下で提供されている [MineDojo/Voyager](https://github.com/MineDojo/Voyager) を改変して使用しています。

[easyllm]: https://www.curseforge.com/minecraft/mc-mods/easy-llm/files/all?page=1&pageSize=20&showAlphaFiles=hide
[easyllmvoice]: https://www.curseforge.com/minecraft/mc-mods/easy-llm-voice/files/all?page=1&pageSize=20&showAlphaFiles=hide

<!-- markdownlint-enable MD033 -->
