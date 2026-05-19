# Using Minecraft Simple Voice Chat

This project uses the Simple Voice Chat mod for voice input in Minecraft.

## Installation

Install Simple Voice Chat on both the Minecraft client and the Minecraft server.

Official downloads:  
https://modrepo.de/minecraft/voicechat/downloads

Installation guide:  
https://modrepo.de/minecraft/voicechat/wiki/installation

## Initial Setup

After joining Minecraft, press the `V` key to open the Voice Chat GUI.

In the initial setup screen, configure the following items:

1. Select the microphone to use.
2. Select the speaker or audio output device to use.
3. Choose the voice input mode.
   - Push to Talk
   - Voice Activation
4. If you use Push to Talk, assign a key for it.
5. Test the microphone input.

Client setup guide:  
https://modrepo.de/minecraft/voicechat/wiki/client_setup

## Common Key Bindings

| Key | Action |
|---|---|
| `V` | Open the Voice Chat GUI |
| `M` | Mute / unmute the microphone |
| `N` | Disable / enable voice chat |
| `H` | Hide / show voice chat icons |

Key bindings guide:  
https://modrepo.de/minecraft/voicechat/wiki/key_bindings

## Notes for Multiplayer Servers

When using Simple Voice Chat on a multiplayer server, the server must allow the UDP port used by the mod.

By default, Simple Voice Chat uses:
24454/UDP

If voice chat does not work, check the following:

- Simple Voice Chat is installed on both the client and the server.
- The server allows the 24454/UDP port.
- The correct microphone is selected.
- The correct speaker or output device is selected.
- Minecraft and the operating system allow microphone access.
- The voice input mode and sensitivity are configured correctly.

Troubleshooting guide:
https://modrepo.de/minecraft/voicechat/wiki/troubleshooting