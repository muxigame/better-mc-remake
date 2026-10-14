"""Decode actual local bytes with bounded workers; never fetch URLs or extract DRM."""
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore
import hashlib
import shutil
import subprocess
import tempfile
import wave


@dataclass(frozen=True)
class VerifiedAudio:
    data: bytes
    extension: str
    duration: int


def audio_format(data):
    if len(data)>=12 and data[4:8]==b'ftyp': return 'm4a'
    if data[:4] == b'RIFF' and data[8:12] == b'WAVE': return 'wav'
    if data[:4] == b'fLaC': return 'flac'
    if data[:4] == b'OggS' and b'\x01vorbis' in data[:256]: return 'ogg'
    if len(data) > 2 and data[0] == 255 and data[1] & 0xf6 == 0xf0: return 'aac'
    if data[:3] == b'ID3' or len(data) > 2 and data[0] == 255 and data[1] & 0xe0 == 0xe0: return 'mp3'
    return None


class AudioNormalizer:
    """Existing deployment ffmpeg only. QA may inject its private executable."""
    def __init__(self, executable=None):
        self.executable = executable or shutil.which('ffmpeg')
        self.slots = BoundedSemaphore(2)

    @property
    def formats(self):
        return ['wav', 'mp3', 'ogg', 'flac', 'aac','m4a'] if self.executable else ['wav']

    def __call__(self, data):
        from .music_upload import MusicError, MAX_BYTES, wav_duration
        fmt = audio_format(data)
        if fmt not in self.formats: raise MusicError(422, 'unsupported_or_truncated_audio')
        if not self.executable:
            return VerifiedAudio(data, 'wav', wav_duration(data))
        if not self.slots.acquire(timeout=1): raise MusicError(429, 'audio_validation_busy')
        try:
            with tempfile.TemporaryDirectory(prefix='muxi-music-check-') as directory:
                root = Path(directory)
                source, pcm, target = root/'source.audio', root/'checked.wav', root/'published.mp3'
                source.write_bytes(data)
                common = [self.executable, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                          '-protocol_whitelist', 'file,pipe', '-err_detect', 'explode', '-xerror']
                # Forced demuxer rejects playlists/URLs. Decode at most 601 seconds;
                # the extra second makes overlong sources fail instead of clipping.
                self.run(common+['-f', 'mov' if fmt=='m4a' else fmt, '-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn',
                    '-t', '601', '-ac', '2', '-ar', '44100', '-c:a', 'pcm_s16le',
                    '-fs', str(601*44100*4+4096), '-f', 'wav', str(pcm)])
                with wave.open(str(pcm)) as audio:
                    frames, rate = audio.getnframes(), audio.getframerate()
                    if not 1 <= frames <= rate*600: raise MusicError(422, 'unsupported_or_truncated_audio')
                    # Read every decoded frame without holding the full PCM in RAM.
                    remaining = frames
                    while remaining:
                        count = min(remaining, 16384)
                        if len(audio.readframes(count)) != count*4:
                            raise MusicError(422, 'unsupported_or_truncated_audio')
                        remaining -= count
                self.run(common+['-f', 'wav', '-i', str(pcm), '-map', '0:a:0', '-map_metadata', '-1',
                    '-c:a', 'libmp3lame', '-b:a', '128k', '-fs', str(MAX_BYTES+1), '-f', 'mp3', str(target)])
                encoded = target.read_bytes()
                if not 1 <= len(encoded) <= MAX_BYTES: raise MusicError(422, 'invalid_audio_size')
                return VerifiedAudio(encoded, 'mp3', max(1, (frames+rate-1)//rate))
        except (OSError, wave.Error):
            raise MusicError(422, 'unsupported_or_truncated_audio') from None
        finally:
            self.slots.release()

    @staticmethod
    def run(command):
        from .music_upload import MusicError
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=45, check=False)
        except subprocess.TimeoutExpired:
            raise MusicError(422, 'audio_validation_timeout') from None
        if result.returncode: raise MusicError(422, 'unsupported_or_truncated_audio')
