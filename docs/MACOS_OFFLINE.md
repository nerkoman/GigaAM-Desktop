# GigaAM Desktop для Mac с Apple Silicon

Образ DMG содержит приложение для macOS 15 и новее. Откройте образ, перетащите **GigaAM Desktop** в **Программы** и запустите оттуда. При первой записи разрешите доступ к микрофону и системному аудио по запросу macOS.

Для записи из приложения откройте завершённую встречу, нажмите **Распознать** и выберите **Диаризация + протокол**. Модель разделяет голоса на системной дорожке. Дорожка микрофона получает метку «Я». Для импортированного аудиофайла используйте **Распознать с диаризацией**. Распознавание и разделение голосов выполняются локально. Для составления протокола потребуется запущенная Ollama с моделью `qwen3.5:4b` или выбранной в настройках моделью. Расшифровка с именами говорящих сохраняется до обращения к Ollama.

Сборка подписана локальным сертификатом GigaAM Local Code Signing. При предупреждении macOS об издателе воспользуйтесь разрешённым в вашей организации способом открытия. [Справка Apple](https://support.apple.com/ru-ru/guide/mac-help/mh40616/mac)

## Models and attribution

The offline package includes the following unmodified files. Checksums for each model are in `scripts/stage-macos-runtime.py`.

| Files | Authors and upstream version | License |
| --- | --- | --- |
| GigaAM v3 E2E CTC int8 and vocabulary | Sber / GigaAM authors, ONNX conversion by Ilya Stupakov. [Pinned conversion](https://huggingface.co/istupakov/gigaam-v3-onnx/tree/322c3b29492673eb7d0b434bfa9dfb8653e34d02) | MIT |
| Silero VAD | Silero Team. [Source revision](https://github.com/snakers4/silero-vad/tree/1e261b036686cd0017d500ee96acd1c4ba572a9d) | MIT |
| Speaker Diarization Community-1, including segmentation, embedding and PLDA | pyannote, Hervé Bredin and contributors. [Model card and credits](https://huggingface.co/pyannote/speaker-diarization-community-1/tree/3533c8cf8e369892e6b79ff1bf80f7b0286a54ee) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) |
| WeSpeaker VoxCeleb ResNet34 ONNX | WeSpeaker authors. [Model card](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34/tree/ff1ac5bca8ef11e90662b879aa923979e0bd277b) | Apache-2.0 |

## Rebuilding the offline package

Use Apple Silicon, the repository's locked Cargo and npm dependencies, [CPython 3.12.13 standalone](https://github.com/astral-sh/python-build-standalone/releases/tag/20260414), and the indexed wheels pinned in `sidecar/requirements-macos-arm64.lock`. Install the wheels into a dedicated virtual environment. Build the app with `BUILD_NUMBER=3 bash scripts/build-macos-local.sh`, using your own signing configuration. The frontend build ID also includes the local calendar date.

The release uses standalone shared [FFmpeg 8.0.1](https://ffmpeg.org/releases/ffmpeg-8.0.1.tar.xz), LGPL-2.1-or-later. The unmodified source tarball is included with the release. Build it with the following configuration, then run `make` and `make DESTDIR="$PWD/installed" install`.

```sh
export MACOSX_DEPLOYMENT_TARGET=15.0
./configure --prefix=/ --disable-autodetect --disable-everything --disable-network \
  --disable-doc --disable-debug --disable-programs --enable-ffmpeg --enable-shared \
  --disable-static --install-name-dir=@rpath --enable-protocol=file,pipe \
  --enable-demuxer=ogg,flac,wav,mp3,mov,aiff,matroska \
  --enable-decoder=opus,vorbis,flac,mp3,mp3float,aac,alac,pcm_s16le,pcm_s16be,pcm_s24le,pcm_s24be,pcm_s32le,pcm_f32le,pcm_f64le,pcm_u8 \
  --enable-encoder=pcm_f32le --enable-muxer=pcm_f32le --enable-filter=aresample,aformat,anull \
  --extra-cflags=-mmacosx-version-min=15.0 --extra-ldflags=-Wl,-rpath,@loader_path/../lib --cc=clang
```

Run `scripts/stage-macos-runtime.py` with the virtual environment's Python. Set the output to `Contents/Resources/meeting-runtime` inside a copy of the built .app. Supply the model directories, WeSpeaker ONNX file and installed FFmpeg prefix using the arguments listed by `--help`. Add the licenses and notices, sign the completed bundle and package it in a disk image. The app resolves these resources from its installed location.
