//! Boreas GPU helper. Root-only inherited stdio, no network or public socket.
//! Cardwire-derived eBPF/inode code is kept in ../vendor (GPL-3.0).
use anyhow::{Context, Result, bail, ensure};
use cardwire_core::{gpu::inode::get_inodes, pci::pci_device::read_pci_devices};
use cardwire_ebpf_userspace::{EbpfBlocker, InodeKey};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    fs,
    io::{self, BufRead, Write},
    os::unix::fs::MetadataExt,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

/// The UI polls status often; walking every /proc/*/fd each time is wasteful.
const USERS_CACHE: Duration = Duration::from_secs(5);

fn read(path: impl AsRef<Path>) -> Option<String> {
    fs::read_to_string(path).ok().map(|s| s.trim().to_owned())
}
#[derive(Debug, Serialize)]
struct Device {
    pci: String,
    card: String,
    render: String,
    minor: u32,
}

// Deliberately narrow initial support: exactly one non-primary NVIDIA GPU,
// with another GPU driving the internal panel. Do not guess on desktops/MUX dGPU.
fn discover(root: &Path) -> Result<Device> {
    let pci = root.join("sys/bus/pci/devices");
    let mut targets = Vec::new();
    let mut panel_on_other = false;
    let mut gpu_count = 0;
    for entry in fs::read_dir(pci)? {
        let entry = entry?;
        let p = entry.path();
        if !read(p.join("class")).is_some_and(|s| s.starts_with("0x03")) {
            continue;
        }
        gpu_count += 1;
        let nv = read(p.join("vendor")).as_deref() == Some("0x10de");
        let nodes = fs::read_dir(p.join("drm"))?.collect::<std::io::Result<Vec<_>>>()?;
        let names: Vec<String> = nodes
            .iter()
            .map(|n| n.file_name().to_string_lossy().into_owned())
            .collect();
        if !nv {
            for card in names.iter().filter(|s| numeric_node(s, "card")) {
                for connector in fs::read_dir(root.join("sys/class/drm"))? {
                    let connector = connector?;
                    if connector
                        .file_name()
                        .to_string_lossy()
                        .starts_with(&format!("{card}-eDP-"))
                        && read(connector.path().join("status")).as_deref() == Some("connected")
                    {
                        panel_on_other = true;
                    }
                }
            }
            continue;
        }
        ensure!(
            read(p.join("boot_vga")).as_deref() == Some("0"),
            "NVIDIA GPU is primary or its primary status is unknown"
        );
        let addr = entry.file_name().to_string_lossy().into_owned();
        let info = read(root.join(format!("proc/driver/nvidia/gpus/{addr}/information")))
            .context("NVIDIA driver is not ready")?;
        let minor = info
            .lines()
            .find_map(|s| s.strip_prefix("Device Minor:"))
            .context("NVIDIA minor is missing")?
            .trim()
            .parse()?;
        let node = |prefix| {
            names
                .iter()
                .find(|s| numeric_node(s, prefix))
                .cloned()
                .context("Missing DRM node")
        };
        targets.push(Device {
            pci: addr,
            card: node("card")?,
            render: node("renderD")?,
            minor,
        });
    }
    ensure!(
        gpu_count == 2 && panel_on_other && targets.len() == 1,
        "Requires one NVIDIA offload GPU and an internal panel on another GPU"
    );
    Ok(targets.remove(0))
}
fn numeric_node(s: &str, prefix: &str) -> bool {
    s.strip_prefix(prefix)
        .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
}
fn connected_outputs(root: &Path, card: &str) -> Result<Vec<String>> {
    let mut out = Vec::new();
    for e in fs::read_dir(root.join("sys/class/drm"))? {
        let e = e?;
        let name = e.file_name().to_string_lossy().into_owned();
        if name.starts_with(&format!("{card}-")) {
            let status = read(e.path().join("status")).context("Cannot read connector status")?;
            if status == "connected" {
                out.push(name);
            }
        }
    }
    Ok(out)
}
fn runtime_d3_status(text: &str) -> Option<String> {
    text.lines().find_map(|line| {
        line.strip_prefix("Runtime D3 status:")
            .map(|s| s.trim().to_owned())
    })
}
struct Engine {
    mode_map: aya::maps::Array<aya::maps::MapData, u8>,
    blocker: EbpfBlocker,
    device: Device,
    keys: Vec<InodeKey>,
    mode: u8,
    users_cache: Option<(Instant, Vec<String>)>,
}
impl Engine {
    fn new() -> Result<Self> {
        ensure!(
            read("/proc/self/status")
                .is_some_and(|s| s.lines().any(|l| l.starts_with("Uid:\t0\t0\t0\t0"))),
            "Root is required"
        );
        let device = discover(Path::new("/"))?;
        let mut blocker = EbpfBlocker::new()?;
        blocker.whitelist_cardwire_pid(std::process::id())?;
        // No block is active at startup, including when registering inodes later.
        let mut mode_map = blocker.get_mode_map()?;
        mode_map.set(0, 1, 0)?;
        for comm in [
            "(udev-worker)",
            "systemd-udevd",
            "pacman",
            "dnf",
            "apt",
            "nix",
            "nix-daemon",
        ] {
            blocker.allow_comm(comm)?;
        }
        Ok(Self {
            blocker,
            mode_map,
            device,
            keys: Vec::new(),
            mode: 1,
            users_cache: None,
        })
    }
    fn standard(&mut self) -> Result<()> {
        self.users_cache = None;
        self.mode_map.set(0, 1, 0)?;
        self.mode = 1;
        for key in self.keys.drain(..) {
            self.blocker.remove_inode(key)?;
        }
        Ok(())
    }
    fn eco(&mut self) -> Result<()> {
        self.users_cache = None;
        // The helper's PID bypasses its own eBPF block, so these checks remain readable.
        ensure!(
            connected_outputs(Path::new("/"), &self.device.card)?.is_empty(),
            "A display is connected to the NVIDIA GPU; refusing Eco"
        );
        if self.mode == 0 {
            return Ok(());
        }
        self.standard()?;
        self.device = discover(Path::new("/"))?;
        let pci_list = read_pci_devices()?;
        let keys = get_inodes(
            self.device.render.trim_start_matches("renderD").parse()?,
            self.device.card.trim_start_matches("card").parse()?,
            &self.device.pci,
            &None,
            &pci_list,
            Some(self.device.minor),
        )?;
        self.keys = keys;
        let result = (|| -> Result<()> {
            for key in &self.keys {
                self.blocker.block_inode(*key, 1)?;
            }
            // Enable shared NVIDIA control-file block only for this single-NVIDIA topology.
            // These nodes otherwise let Vulkan/NVML wake the GPU despite DRM blocking.
            for name in [
                "nvidiactl",
                "nvidia-modeset",
                "nvidia-uvm",
                "nvidia-uvm-tools",
            ] {
                let path = PathBuf::from(format!("/dev/{name}"));
                if path.exists() {
                    let key = InodeKey::new(name, fs::metadata(path)?.ino());
                    self.blocker.block_inode(key, 1)?;
                    self.keys.push(key);
                }
            }
            self.mode_map.set(0, 0, 0)?;
            self.mode = 0;
            Ok(())
        })();
        if result.is_err() {
            self.standard()?;
        }
        result
    }
    fn status(&mut self) -> Value {
        let p = PathBuf::from(format!("/sys/bus/pci/devices/{}", self.device.pci));
        let users = match &self.users_cache {
            Some((at, users)) if at.elapsed() < USERS_CACHE => users.clone(),
            _ => {
                let users = device_users(&self.device);
                self.users_cache = Some((Instant::now(), users.clone()));
                users
            }
        };
        let rtd3 = read(format!(
            "/proc/driver/nvidia/gpus/{}/power",
            self.device.pci
        ))
        .and_then(|s| runtime_d3_status(&s));
        json!({"rtd3_status":rtd3, "backend":"ebpf", "policy":if self.mode == 0 {"eco"} else {"standard"}, "device":self.device, "runtime_status":read(p.join("power/runtime_status")), "power_state":read(p.join("power_state")), "users":users})
    }
}
fn device_users(device: &Device) -> Vec<String> {
    let nodes = [
        format!("/dev/dri/{}", device.card),
        format!("/dev/dri/{}", device.render),
        format!("/dev/nvidia{}", device.minor),
    ];
    let mut users = Vec::new();
    if let Ok(entries) = fs::read_dir("/proc") {
        for e in entries.flatten() {
            if !e
                .file_name()
                .to_string_lossy()
                .bytes()
                .all(|c| c.is_ascii_digit())
            {
                continue;
            }
            if let Ok(fds) = fs::read_dir(e.path().join("fd"))
                && fds.flatten().any(|fd| {
                    fs::read_link(fd.path())
                        .ok()
                        .is_some_and(|p| nodes.iter().any(|n| p == Path::new(n)))
                })
            {
                users.push(format!(
                    "{}({})",
                    read(e.path().join("comm")).unwrap_or_default(),
                    e.file_name().to_string_lossy()
                ));
            }
        }
    }
    users
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    cmd: String,
}
fn protocol_info() -> Value {
    json!({"protocol_version": 1, "capabilities": ["status", "eco", "standard"]})
}
fn main() -> Result<()> {
    // No root, GPU discovery or BPF hooks needed for compatibility negotiation.
    if std::env::args().nth(1).as_deref() == Some("--protocol-info") {
        println!("{}", protocol_info());
        return Ok(());
    }
    // Inherited pipes are private to boreasd. EOF drops the BPF owner and its links.
    let mut engine = Engine::new()?;
    let mut out = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let result = (|| -> Result<Value> {
            let req: Request = serde_json::from_str(&line?)?;
            match req.cmd.as_str() {
                "status" => {}
                "eco" => engine.eco()?,
                "standard" => engine.standard()?,
                _ => bail!("Unknown GPU command"),
            }
            Ok(engine.status())
        })();
        let reply = match result {
            Ok(s) => json!({"ok":true,"status":s}),
            Err(e) => json!({"ok":false,"error":e.to_string()}),
        };
        writeln!(out, "{reply}")?;
        out.flush()?;
    }
    engine.standard()?;
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
            let p = std::env::temp_dir().join(format!(
                "boreas-gpu-test-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
            ));
            fs::create_dir_all(&p).unwrap();
            Self(p)
        }
        fn put(&self, name: &str, value: &str) {
            let p = self.0.join(name);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(p, value).unwrap();
        }
        fn laptop(&self) {
            for (addr, vendor, card, render) in [
                ("0000:01:00.0", "0x10de", "card0", "renderD128"),
                ("0000:06:00.0", "0x1002", "card1", "renderD129"),
            ] {
                self.put(&format!("sys/bus/pci/devices/{addr}/class"), "0x030000");
                self.put(&format!("sys/bus/pci/devices/{addr}/vendor"), vendor);
                self.put(
                    &format!("sys/bus/pci/devices/{addr}/boot_vga"),
                    if vendor == "0x10de" { "0" } else { "1" },
                );
                for node in [card, render] {
                    fs::create_dir_all(
                        self.0
                            .join(format!("sys/bus/pci/devices/{addr}/drm/{node}")),
                    )
                    .unwrap();
                }
            }
            self.put("sys/class/drm/card1-eDP-1/status", "connected");
            self.put("sys/class/drm/card0-HDMI-A-1/status", "disconnected");
            self.put(
                "proc/driver/nvidia/gpus/0000:01:00.0/information",
                "Model: RTX Test\nDevice Minor: 0\n",
            );
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    #[test]
    fn identifies_offload_gpu_without_nvml() {
        let f = Fixture::new();
        f.laptop();
        assert_eq!(discover(&f.0).unwrap().pci, "0000:01:00.0");
    }
    #[test]
    fn rejects_primary_nvidia() {
        let f = Fixture::new();
        f.laptop();
        f.put("sys/bus/pci/devices/0000:01:00.0/boot_vga", "1");
        assert!(discover(&f.0).is_err());
    }
    #[test]
    fn rejects_missing_internal_panel() {
        let f = Fixture::new();
        f.laptop();
        f.put("sys/class/drm/card1-eDP-1/status", "disconnected");
        assert!(discover(&f.0).is_err());
    }
    #[test]
    fn connected_monitor_detected() {
        let f = Fixture::new();
        f.laptop();
        f.put("sys/class/drm/card0-HDMI-A-1/status", "connected");
        assert_eq!(
            connected_outputs(&f.0, "card0").unwrap(),
            vec!["card0-HDMI-A-1"]
        );
    }
    #[test]
    fn missing_connector_status_is_error() {
        let f = Fixture::new();
        f.laptop();
        fs::remove_file(f.0.join("sys/class/drm/card0-HDMI-A-1/status")).unwrap();
        assert!(connected_outputs(&f.0, "card0").is_err());
    }
    #[test]
    fn rejects_missing_driver_metadata() {
        let f = Fixture::new();
        f.laptop();
        fs::remove_file(f.0.join("proc/driver/nvidia/gpus/0000:01:00.0/information")).unwrap();
        assert!(discover(&f.0).is_err());
    }
    #[test]
    fn rejects_missing_render_node() {
        let f = Fixture::new();
        f.laptop();
        fs::remove_dir(f.0.join("sys/bus/pci/devices/0000:01:00.0/drm/renderD128")).unwrap();
        assert!(discover(&f.0).is_err());
    }
    #[test]
    fn rejects_third_gpu() {
        let f = Fixture::new();
        f.laptop();
        f.put("sys/bus/pci/devices/0000:07:00.0/class", "0x030000");
        f.put("sys/bus/pci/devices/0000:07:00.0/vendor", "0x1002");
        fs::create_dir_all(f.0.join("sys/bus/pci/devices/0000:07:00.0/drm")).unwrap();
        assert!(discover(&f.0).is_err());
    }
    // Root integration regression: get_mode_map() takes ownership, not a borrow.
    // Repeated Standard and shutdown must reuse the original map descriptor.
    #[test]
    #[ignore = "requires root, BPF LSM and supported GPU topology; loads Standard-only hooks"]
    fn repeated_standard_retains_mode_map() {
        let mut engine = Engine::new().unwrap();
        for _ in 0..4 {
            engine.standard().unwrap();
            assert_eq!(engine.mode_map.get(&0, 0).unwrap(), 1);
            assert_eq!(engine.status()["policy"], "standard");
        }
    }
    #[test]
    fn parses_runtime_d3_status_without_claiming_sleep() {
        assert_eq!(
            runtime_d3_status(
                "Runtime D3 status:          Disabled by default\nVideo Memory: Active"
            ),
            Some("Disabled by default".into())
        );
        assert_eq!(
            runtime_d3_status("Runtime D3 status: Enabled (fine-grained)"),
            Some("Enabled (fine-grained)".into())
        );
        assert_eq!(runtime_d3_status("Video Memory: Active"), None);
    }
    #[test]
    fn drm_nodes_reject_connectors() {
        assert!(numeric_node("card12", "card"));
        assert!(!numeric_node("card1-HDMI-A-1", "card"));
        assert!(!numeric_node("card", "card"));
    }
    #[test]
    fn protocol_rejects_extra_fields() {
        assert!(serde_json::from_str::<Request>(r#"{"cmd":"eco","shell":"bad"}"#).is_err());
    }
}
