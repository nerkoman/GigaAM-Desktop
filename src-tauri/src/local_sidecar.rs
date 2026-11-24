use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::{Arc, Mutex},
};

use serde::Deserialize;
use tauri::{AppHandle, Emitter, Manager};

use crate::domain::{
    SegmentState, SourceTrack, TranscriptSegment, Transcription, TranscriptionKind,
    TranscriptionStatus,
};
use crate::storage::Store;

#[derive(Deserialize)]
struct SidecarSegment {
    speaker_key: String,
    speaker_name: String,
    text: String,
    ts_start_ms: i64,
    ts_end_ms: i64,
}

fn run_local(command: &mut Command) -> Result<String, String> {
    let output = command
        .env("PYTHONIOENCODING", "utf-8")
        .output()
        .map_err(|error| format!("Не удалось запустить локальный обработчик: {error}"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let start = stderr.char_indices().rev().nth(3999).map_or(0, |(i, _)| i);
        let message = stderr[start..].trim();
        return Err(if message.is_empty() {
            format!("Обработчик завершился со статусом {}", output.status)
        } else {
            message.to_owned()
        });
    }
    String::from_utf8(output.stdout).map_err(|_| "Некорректный ответ локального обработчика".into())
}

pub(crate) fn bundled_runtime(app: &AppHandle) -> Option<PathBuf> {
    let root = app.path().resource_dir().ok()?.join("meeting-runtime");
    root.join("sidecar/process_meeting.py").is_file().then_some(root)
}

fn development_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf()
}

fn configured_path(name: &str, default: PathBuf) -> PathBuf {
    std::env::var_os(name).map(PathBuf::from).unwrap_or(default)
}

pub fn process_recording(
    app: &AppHandle,
    store: Arc<Mutex<Store>>,
    id: String,
    speaker_count: Option<usize>,
    ollama_model: Option<String>,
) -> Result<Option<String>, String> {
    static PROCESSING: Mutex<()> = Mutex::new(());
    let _processing = PROCESSING
        .try_lock()
        .map_err(|_| "Диаризация уже выполняется")?;
    let bundled = bundled_runtime(app);
    let packaged = bundled.is_some();
    let root = bundled.unwrap_or_else(development_root);
    let python = configured_path(
        "LOCAL_MEETINGS_PYTHON",
        if packaged {
            root.join("python/bin/python3.12")
        } else {
            root.join("project-input/dia-venv/bin/python")
        },
    );
    let asr_model = configured_path("LOCAL_MEETINGS_ASR_MODEL", root.join("models/runtime"));
    let diarization_model = configured_path(
        "LOCAL_MEETINGS_DIARIZATION_MODEL",
        root.join("models/pyannote/community-1"),
    );
    for (path, label) in [
        (&python, "Python sidecar"),
        (&asr_model, "модель GigaAM"),
        (&diarization_model, "модель диаризации"),
    ] {
        if !path.exists() {
            return Err(format!("Не найден {label}: {}", path.display()));
        }
    }

    let (record, original_segments, original_summary) = {
        let db = store.lock().map_err(|_| "База данных недоступна")?;
        let record = db
            .get(&id)
            .map_err(|error| error.to_string())?
            .ok_or("Запись не найдена")?;
        if matches!(
            record.status,
            TranscriptionStatus::Preparing
                | TranscriptionStatus::Transcribing
                | TranscriptionStatus::Queued
        ) {
            return Err("Дождитесь завершения записи или распознавания".into());
        }
        (
            record,
            db.segments(&id).map_err(|e| e.to_string())?,
            db.load_summary(&id).map_err(|e| e.to_string())?,
        )
    };
    let live_dir = if record.kind == TranscriptionKind::Live {
        Some(crate::prepare_stopped_live_archive(app, &store, &record)?)
    } else {
        None
    };
    let audio_path = Path::new(record.audio_path.as_deref().unwrap_or(&record.source_path));
    let session = live_dir.as_deref().into_iter().chain(audio_path.parent())
        .find(|path| path.join("tracks.json").is_file())
        .or_else(|| live_dir.as_deref().filter(|path| path.is_dir()))
        .ok_or("Не найдены сохранённые дорожки встречи. Для отдельного файла выберите «Распознать с диаризацией»")?;
    let output_directory = app
        .path()
        .app_data_dir()
        .map_err(|_| "Каталог данных недоступен")?
        .join("processed")
        .join(&id);

    let local_command = || {
        let mut command = Command::new(&python);
        command.current_dir(&root).arg("-B");
        if packaged {
            command.env("FFMPEG_BINARY", root.join("bin/ffmpeg"))
                .env("DYLD_FALLBACK_LIBRARY_PATH", root.join("lib"))
                .env("PYTHONNOUSERSITE", "1").env_remove("PYTHONPATH").env_remove("PYTHONHOME");
        }
        command
    };
    let mut command = local_command();
    command.args([
        "-m",
        "sidecar.process_meeting",
        "--skip-protocol",
        session.to_str().ok_or("Некорректный путь записи")?,
        "--asr-model",
        asr_model.to_str().ok_or("Некорректный путь GigaAM")?,
        "--diarization-model",
        diarization_model
            .to_str()
            .ok_or("Некорректный путь диаризации")?,
        "--output",
        output_directory
            .to_str()
            .ok_or("Некорректный путь результата")?,
    ]);
    if let Some(count) = speaker_count {
        command.args(["--speakers", &count.to_string()]);
    }
    run_local(&mut command)
        .map_err(|e| format!("Локальная обработка завершилась с ошибкой: {e}"))?;

    let segments_path = output_directory.join("segments.json");
    let raw = fs::read_to_string(&segments_path)
        .map_err(|error| format!("Не найден результат диаризации: {error}"))?;
    let parsed: Vec<SidecarSegment> = serde_json::from_str(&raw)
        .map_err(|error| format!("Некорректный результат sidecar: {error}"))?;
    let mut segments = Vec::with_capacity(parsed.len());
    for (index, item) in parsed.into_iter().enumerate() {
        let track = if item.speaker_key == "microphone" {
            SourceTrack::Microphone
        } else {
            SourceTrack::System
        };
        let mut segment = TranscriptSegment::new(
            &id,
            index,
            item.ts_start_ms as f64 / 1000.0,
            item.ts_end_ms as f64 / 1000.0,
            item.text,
            track,
            SegmentState::Final,
        )
        .map_err(|error| error.to_string())?;
        segment.speaker_id = Some(item.speaker_name);
        segments.push(segment);
    }

    save_meeting_results(
        &store,
        &record,
        &original_segments,
        &original_summary,
        &segments,
        &output_directory,
        || {
            let _ = app.emit("local-meeting-protocol", &id);
            let model = ollama_model
                .as_deref()
                .filter(|s| !s.trim().is_empty())
                .unwrap_or("qwen3.5:4b");
            run_local(
                local_command().args(["-m", "sidecar.protocol"])
                    .arg(output_directory.join("transcript.md"))
                    .args(["--ollama-model", model]),
            )
        },
    )
}

fn save_meeting_results(
    store: &Mutex<Store>,
    record: &Transcription,
    original_segments: &[TranscriptSegment],
    original_summary: &str,
    segments: &[TranscriptSegment],
    output_directory: &Path,
    generate_protocol: impl FnOnce() -> Result<String, String>,
) -> Result<Option<String>, String> {
    let id = &record.id;
    {
        let mut db = store.lock().map_err(|_| "База данных недоступна")?;
        if db
            .get(id)
            .map_err(|e| e.to_string())?
            .is_none_or(|current| current.status != record.status || current.kind != record.kind)
            || db.segments(id).map_err(|e| e.to_string())? != original_segments
        {
            return Err(
                "Запись изменилась во время обработки. Обновите запись и повторите диаризацию"
                    .into(),
            );
        }
        db.replace_segments(id, segments)
            .map_err(|error| format!("Не удалось сохранить транскрипт: {error}"))?;
    }
    let result = (|| -> Result<(), String> {
        let protocol = generate_protocol()?;
        if protocol.trim().is_empty() {
            return Err("Ollama вернула пустой протокол".into());
        }
        let db = store.lock().map_err(|_| "База данных недоступна")?;
        if db.segments(id).map_err(|e| e.to_string())? != segments
            || db.load_summary(id).map_err(|e| e.to_string())? != original_summary
        {
            return Err("Запись или протокол были изменены во время составления протокола".into());
        }
        db.save_summary(id, &protocol).map_err(|e| e.to_string())?;
        let staged = output_directory.join("protocol.md.partial");
        fs::write(&staged, &protocol)
            .and_then(|_| fs::rename(staged, output_directory.join("protocol.md")))
            .map_err(|e| format!("Протокол есть в истории. Не удалось сохранить файл: {e}"))
    })();
    Ok(result
        .err()
        .map(|message| format!("Диаризация сохранена. {message}")))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn speaker_labels_are_visible_before_protocol_generation_and_survive_failure() {
        let record = Transcription {
            id: "meeting".into(),
            kind: TranscriptionKind::Live,
            source_filename: "Meeting".into(),
            source_path: String::new(),
            source_size_bytes: 0,
            source_modified_ms: 1,
            created_ms: 1,
            started_ms: Some(1),
            completed_ms: Some(2),
            status: TranscriptionStatus::Cancelled,
            duration_seconds: Some(1.0),
            model_version: String::new(),
            error: None,
            audio_path: None,
            completed_chunks: 0,
            total_chunks: None,
            queue_visible: true,
        };
        let store = Mutex::new(Store::memory().unwrap());
        store.lock().unwrap().enqueue(&record).unwrap();
        store
            .lock()
            .unwrap()
            .save_summary(&record.id, "предыдущий протокол")
            .unwrap();
        let mut segment = TranscriptSegment::new(
            &record.id,
            0,
            0.0,
            1.0,
            "речь".into(),
            SourceTrack::System,
            SegmentState::Final,
        )
        .unwrap();
        segment.speaker_id = Some("Участник 1".into());
        let segments = [segment];
        let warning = save_meeting_results(
            &store,
            &record,
            &[],
            "предыдущий протокол",
            &segments,
            Path::new("."),
            || {
                let db = store.try_lock().unwrap();
                assert_eq!(
                    db.get(&record.id).unwrap().unwrap().status,
                    TranscriptionStatus::Completed
                );
                assert_eq!(db.segments(&record.id).unwrap(), segments);
                Err("Ollama недоступна".into())
            },
        )
        .unwrap()
        .unwrap();
        assert!(warning.contains("Диаризация сохранена"));
        assert_eq!(
            store.lock().unwrap().load_summary(&record.id).unwrap(),
            "предыдущий протокол"
        );
    }
}
