use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tracing::{info, warn};

static RNSD_CHILD: Mutex<Option<Child>> = Mutex::new(None);

pub struct RnsdHandle;

impl Drop for RnsdHandle {
    fn drop(&mut self) {
        stop_rnsd();
    }
}

pub fn spawn_rnsd(root: &PathBuf, ipc_port: u16, public_port: u16, extra_args: &[String]) -> RnsdHandle {
    let python = resolve_python(root);
    let mut cmd = Command::new(&python);
    cmd.current_dir(root)
        .env("PYTHONPATH", root)
        .env("CHATXZ_ROOT", root)
        .env("CHATXZ_IPC_PORT", ipc_port.to_string())
        .env("CHATXZ_APP_URL", format!("http://127.0.0.1:{public_port}"))
        .arg("-m")
        .arg("chatxz.rnsd")
        .arg("--port")
        .arg(ipc_port.to_string())
        .arg("--public-port")
        .arg(public_port.to_string());
    for arg in extra_args {
        cmd.arg(arg);
    }
    cmd.stdout(Stdio::inherit()).stderr(Stdio::inherit());

    match cmd.spawn() {
        Ok(child) => {
            info!(pid = child.id(), ipc_port, "started RNS transport daemon (IPC)");
            *RNSD_CHILD.lock().expect("rnsd") = Some(child);
        }
        Err(e) => warn!(%e, "failed to spawn RNS daemon — is Python installed?"),
    }
    RnsdHandle
}

pub fn stop_rnsd() {
    if let Some(mut child) = RNSD_CHILD.lock().expect("rnsd").take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn resolve_python(root: &PathBuf) -> String {
    if let Ok(v) = std::env::var("CHATXZ_PYTHON") {
        if !v.is_empty() {
            return v;
        }
    }
    let venv = root.join(".venv");
    #[cfg(windows)]
    let candidate = venv.join("Scripts").join("python.exe");
    #[cfg(not(windows))]
    let candidate = venv.join("bin").join("python");
    if candidate.is_file() {
        return candidate.to_string_lossy().into_owned();
    }
    "python3".into()
}