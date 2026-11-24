use super::{LiveCapture, LiveSource};
use anyhow::{Context, Result, ensure};
use std::{
    io::Read,
    process::{Child, Command, Stdio},
    sync::{
        Arc, Mutex, OnceLock,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::Instant,
};

fn pactl(args: &[&str]) -> Result<String> {
    let output = Command::new("pactl")
        .args(args)
        .output()
        .context("Не удалось запустить pactl из пакета pulseaudio-utils")?;
    ensure!(
        output.status.success(),
        "Аудиосервер PulseAudio или PipeWire недоступен: {}",
        String::from_utf8_lossy(&output.stderr).trim()
    );
    Ok(String::from_utf8(output.stdout)?.trim().to_owned())
}

pub fn microphones() -> Result<Vec<String>> {
    Ok(pactl(&["list", "short", "sources"])?
        .lines()
        .filter_map(|line| line.split('\t').nth(1))
        .filter(|name| !name.ends_with(".monitor"))
        .map(str::to_owned)
        .collect())
}

pub struct CaptureHandle {
    stopped: Arc<AtomicBool>,
    children: Mutex<Vec<Child>>,
    readers: Mutex<Vec<JoinHandle<()>>>,
}

impl CaptureHandle {
    pub fn request_stop(&self) {
        self.stopped.store(true, Ordering::Release);
        for child in self.children.lock().unwrap().iter_mut() {
            let _ = child.kill();
        }
    }

    pub fn stop(&self) {
        self.request_stop();
        for mut child in self.children.lock().unwrap().drain(..) {
            let _ = child.wait();
        }
        for reader in self.readers.lock().unwrap().drain(..) {
            let _ = reader.join();
        }
    }

    fn add_source(
        &self,
        capture: Arc<Mutex<LiveCapture>>,
        mic: bool,
        device: String,
    ) -> Result<()> {
        let mut child = Command::new("parec")
            .args([
                "--raw",
                "--format=float32le",
                "--channels=1",
                "--rate=48000",
                "--latency-msec=20",
            ])
            .arg(format!("--device={device}"))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .context("Не удалось запустить parec из пакета pulseaudio-utils")?;
        let mut stdout = child
            .stdout
            .take()
            .context("parec: поток аудио недоступен")?;
        self.children.lock().unwrap().push(child);
        let stopped = self.stopped.clone();
        static CLOCK: OnceLock<Instant> = OnceLock::new();
        let clock = *CLOCK.get_or_init(Instant::now);
        let reader = thread::Builder::new()
            .name(format!("pulse-{device}"))
            .spawn(move || {
                let mut frame = [0_u8; 960 * 4];
                while !stopped.load(Ordering::Acquire) {
                    if let Err(error) = stdout.read_exact(&mut frame) {
                        if !stopped.load(Ordering::Acquire) {
                            let mut state = capture.lock().unwrap();
                            if mic {
                                state.microphone_capture_state = "failed".into();
                            } else {
                                state.system_capture_state = "failed".into();
                            }
                            state.set_error(format!("Аудиопоток {device} прерван: {error}"));
                        }
                        break;
                    }
                    let timestamp =
                        (clock.elapsed().as_micros() as u64).saturating_sub(20_000) * 10 + 1;
                    let samples = frame
                        .chunks_exact(4)
                        .map(|chunk| f32::from_le_bytes(chunk.try_into().unwrap()))
                        .collect();
                    let mut state = capture.lock().unwrap();
                    if stopped.load(Ordering::Acquire) {
                        break;
                    }
                    if mic && state.mic_packets == 0 {
                        state.mic_format = "48 kHz mono f32".into();
                        state.microphone_capture_state = "capturing".into();
                    } else if !mic && state.sys_packets == 0 {
                        state.sys_format = "48 kHz mono f32".into();
                        state.system_capture_state = "capturing".into();
                    }
                    state.append_timed(mic, timestamp, samples);
                }
            })?;
        self.readers.lock().unwrap().push(reader);
        Ok(())
    }
}

impl Drop for CaptureHandle {
    fn drop(&mut self) {
        self.stop();
    }
}

pub fn start(capture: Arc<Mutex<LiveCapture>>) -> Result<CaptureHandle> {
    let (source, microphone) = {
        let state = capture.lock().unwrap();
        (state.source, state.microphone_device.clone())
    };
    let handle = CaptureHandle {
        stopped: Arc::new(AtomicBool::new(false)),
        children: Mutex::new(Vec::new()),
        readers: Mutex::new(Vec::new()),
    };
    let available = pactl(&["list", "short", "sources"])?;
    let available: Vec<_> = available
        .lines()
        .filter_map(|line| line.split('\t').nth(1))
        .collect();
    if matches!(
        source,
        LiveSource::Microphone | LiveSource::SystemAndMicrophone
    ) {
        let device = match microphone.filter(|name| !name.is_empty()) {
            Some(name) => name,
            None => pactl(&["get-default-source"])?,
        };
        ensure!(
            available.contains(&device.as_str()),
            "Микрофон {device} недоступен"
        );
        handle.add_source(capture.clone(), true, device)?;
    }
    if matches!(source, LiveSource::System | LiveSource::SystemAndMicrophone) {
        let device = format!("{}.monitor", pactl(&["get-default-sink"])?);
        ensure!(
            available.contains(&device.as_str()),
            "Системная дорожка {device} недоступна"
        );
        handle.add_source(capture, false, device)?;
    }
    Ok(handle)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::live::ArchiveFormat;
    use std::{io::Write, time::Duration};

    #[test]
    #[ignore = "requires a dedicated PulseAudio server with a null sink as output and its monitor as input"]
    fn records_two_pulse_tracks_and_reaps_the_capture_processes() {
        let root = std::env::temp_dir().join(format!("gigaam-pulse-{}", std::process::id()));
        let mut state = LiveCapture::new(
            "pulse-test".into(),
            0,
            LiveSource::SystemAndMicrophone,
            None,
            true,
            ArchiveFormat::Wav,
            1.0,
            root.clone(),
            None,
        );
        state.initialize_paths().unwrap();
        let capture = Arc::new(Mutex::new(state));
        let handle = start(capture.clone()).unwrap();
        for _ in 0..50 {
            if pactl(&["list", "short", "source-outputs"])
                .unwrap()
                .lines()
                .count()
                >= 2
            {
                break;
            }
            thread::sleep(Duration::from_millis(50));
        }
        let mut player = Command::new("paplay")
            .args([
                "--raw",
                "--rate=48000",
                "--channels=1",
                "--format=float32le",
            ])
            .stdin(Stdio::piped())
            .spawn()
            .unwrap();
        let tone: Vec<u8> = (0..96000)
            .flat_map(|n| (0.2 * (n as f32 * 0.05).sin()).to_le_bytes())
            .collect();
        player.stdin.take().unwrap().write_all(&tone).unwrap();
        assert!(player.wait().unwrap().success());
        thread::sleep(Duration::from_millis(150));
        handle.stop();
        let mut state = capture.lock().unwrap();
        assert!(state.microphone.iter().any(|x| x.abs() > 0.01));
        assert!(state.system.iter().any(|x| x.abs() > 0.01));
        assert_eq!(state.finish().unwrap().len(), 2);
        assert!(root.join("tracks.json").is_file());
        drop(state);
        std::fs::remove_dir_all(root).unwrap();
    }
}
