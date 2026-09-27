# 背景音乐连续播放配置

- `biomemusic.json`: `smartMusic=false`，不因群系、洞穴、水下或昼夜条件变化提前结束曲目。
- `biomemusic.json`: `stopMusicForRecords=false`，唱片、网络音乐机和使用唱片分类的 Boss 音乐不再直接调用 `MusicManager.stopPlaying()`。
- `PasterDream-Client.toml`: `bgm use song complete mode=true`，当前曲目完整播放后再切换。

Audio Improvements 的唱片/音符盒处理仍保留 40 tick 淡出与 100 tick 淡入，因为它是平滑压低而非硬切。Aether 音乐管理器也保留，否则会同时失去 Aether 维度音乐和 Boss 音乐。

运行 `python apply_music_continuity.py --apply` 应用，省略 `--apply` 只校验。
