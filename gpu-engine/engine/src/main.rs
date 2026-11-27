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
    os::{fd::AsRawFd, unix::fs::MetadataExt},
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
    /// "nvidia" or "amd".
    vendor: &'static str,
    /// NVIDIA device minor (/dev/nvidiaN); None for AMD.
    minor: Option<u32>,
}

/// One display-class PCI device as seen by discover().
struct Gpu {
    addr: String,
    path: PathBuf,
    vendor: Option<String>,
    names: Vec<String>,
    panel: bool,
}

const TOPOLOGY: &str = "Requires exactly two GPUs: one driving the internal panel, and an NVIDIA \
     or AMD offload GPU as the other";
const NOT_IGPU: &str = "The GPU driving the internal panel is not the processor's integrated GPU";

// Deliberately narrow support: exactly two GPUs, and we block the one that is not
// driving the internal panel (eDP). Do not guess on desktops or with a MUX on the dGPU.
// - One of them is NVIDIA: it is the offload GPU. The other GPU must have a connected
//   eDP (the NVIDIA card's own eDP status is not looked at), and the NVIDIA GPU must
//   not be the boot GPU.
// - No NVIDIA: exactly one GPU has a connected eDP; the other is the offload GPU and
//   must be AMD on amdgpu and not the boot GPU. The panel GPU must be the processor's
//   integrated GPU: Intel, or AMD on amdgpu that `integrated` (asked with the panel
//   GPU's render node, never the offload GPU's) reports as an APU. Otherwise the
//   "panel" GPU could be a dGPU behind a MUX and we would block the iGPU.
fn discover(root: &Path, integrated: &dyn Fn(&Path) -> Result<bool>) -> Result<Device> {
    let mut gpus = Vec::new();
    for entry in fs::read_dir(root.join("sys/bus/pci/devices"))? {
        let entry = entry?;
        let p = entry.path();
        if !read(p.join("class")).is_some_and(|s| s.starts_with("0x03")) {
            continue;
        }
        let nodes = fs::read_dir(p.join("drm"))?.collect::<std::io::Result<Vec<_>>>()?;
        let names: Vec<String> = nodes
            .iter()
            .map(|n| n.file_name().to_string_lossy().into_owned())
            .collect();
        let mut panel = false;
        for card in names.iter().filter(|s| numeric_node(s, "card")) {
            for connector in fs::read_dir(root.join("sys/class/drm"))? {
                let connector = connector?;
                if connector
                    .file_name()
                    .to_string_lossy()
                    .starts_with(&format!("{card}-eDP-"))
                    && read(connector.path().join("status")).as_deref() == Some("connected")
                {
                    panel = true;
                }
            }
        }
        gpus.push(Gpu {
            addr: entry.file_name().to_string_lossy().into_owned(),
            vendor: read(p.join("vendor")),
            path: p,
            names,
            panel,
        });
    }
    ensure!(gpus.len() == 2, TOPOLOGY);
    let nvidia: Vec<usize> = (0..2)
        .filter(|&i| gpus[i].vendor.as_deref() == Some("0x10de"))
        .collect();
    let (target, vendor, minor) = if let &[nv] = nvidia.as_slice() {
        // Same rule as before AMD support: nothing about the NVIDIA card's own eDP.
        let (target, other) = (&gpus[nv], &gpus[1 - nv]);
        ensure!(other.panel, TOPOLOGY);
        ensure!(
            read(target.path.join("boot_vga")).as_deref() == Some("0"),
            "NVIDIA GPU is primary or its primary status is unknown"
        );
        let info = read(root.join(format!(
            "proc/driver/nvidia/gpus/{}/information",
            target.addr
        )))
        .context("NVIDIA driver is not ready")?;
        let minor: u32 = info
            .lines()
            .find_map(|s| s.strip_prefix("Device Minor:"))
            .context("NVIDIA minor is missing")?
            .trim()
            .parse()?;
        (target, "nvidia", Some(minor))
    } else {
        ensure!(nvidia.is_empty(), TOPOLOGY);
        ensure!(gpus.iter().filter(|g| g.panel).count() == 1, TOPOLOGY);
        let (panel, target) = if gpus[0].panel {
            (&gpus[0], &gpus[1])
        } else {
            (&gpus[1], &gpus[0])
        };
        ensure!(
            target.vendor.as_deref() == Some("0x1002"),
            "The offload GPU is neither NVIDIA nor AMD"
        );
        // The kernel creates boot_vga only for VGA-class devices (0x0300xx). A Radeon
        // exposed as a Display controller (0x0380xx) has none and cannot be the boot GPU.
        // An existing but unreadable file is still refused.
        let boot_vga = target.path.join("boot_vga");
        let not_primary = match read(&boot_vga) {
            Some(v) => v == "0",
            None => {
                fs::symlink_metadata(&boot_vga).is_err_and(|e| e.kind() == io::ErrorKind::NotFound)
                    && read(target.path.join("class")).is_some_and(|c| c.starts_with("0x0380"))
            }
        };
        ensure!(
            not_primary,
            "AMD GPU is primary or its primary status is unknown"
        );
        ensure!(on_amdgpu(target), "AMD GPU is not on the amdgpu driver");
        match panel.vendor.as_deref() {
            Some("0x8086") => {}
            Some("0x1002") if on_amdgpu(panel) => {
                let render = root.join("dev/dri").join(drm_node(panel, "renderD")?);
                let apu = integrated(&render)
                    .context("Cannot ask the panel GPU whether it is integrated")?;
                ensure!(apu, NOT_IGPU);
            }
            _ => bail!(NOT_IGPU),
        }
        (target, "amd", None)
    };
    Ok(Device {
        pci: target.addr.clone(),
        card: drm_node(target, "card")?,
        render: drm_node(target, "renderD")?,
        vendor,
        minor,
    })
}
fn drm_node(gpu: &Gpu, prefix: &str) -> Result<String> {
    gpu.names
        .iter()
        .find(|s| numeric_node(s, prefix))
        .cloned()
        .context("Missing DRM node")
}
fn on_amdgpu(gpu: &Gpu) -> bool {
    fs::canonicalize(gpu.path.join("driver"))
        .ok()
        .as_deref()
        .and_then(Path::file_name)
        .is_some_and(|n| n == "amdgpu")
}
/// Whether the amdgpu device behind this render node is an APU (the processor's
/// integrated GPU). Only ever called on the GPU driving the panel, which is awake.
/// Numbers from the kernel uapi header include/uapi/drm/amdgpu_drm.h
/// (also /usr/include/libdrm/amdgpu_drm.h).
fn amdgpu_is_integrated(render: &Path) -> Result<bool> {
    /// DRM_IOW(DRM_COMMAND_BASE + DRM_AMDGPU_INFO, struct drm_amdgpu_info), 32-byte arg.
    const DRM_IOCTL_AMDGPU_INFO: u32 = 0x4020_6445;
    const AMDGPU_INFO_DEV_INFO: u32 = 0x16;
    /// offsetof(struct drm_amdgpu_info_device, ids_flags).
    const IDS_FLAGS_OFFSET: usize = 136;
    const AMDGPU_IDS_FLAGS_FUSION: u64 = 0x1;
    /// struct drm_amdgpu_info; the fields are read by the kernel, not by us.
    #[repr(C)]
    #[allow(dead_code)]
    struct DrmAmdgpuInfo {
        return_pointer: u64,
        return_size: u32,
        query: u32,
        /// The query-specific union; unused for AMDGPU_INFO_DEV_INFO.
        union_pad: [u64; 2],
    }
    let file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(render)
        .with_context(|| format!("Cannot open {}", render.display()))?;
    let mut reply = [0u8; 1024];
    let request = DrmAmdgpuInfo {
        return_pointer: reply.as_mut_ptr() as u64,
        return_size: reply.len() as u32,
        query: AMDGPU_INFO_DEV_INFO,
        union_pad: [0; 2],
    };
    // SAFETY: `file` is an open DRM render node; `request` is a correctly laid out
    // struct drm_amdgpu_info that outlives the call, and its return_pointer points to
    // `reply`, which is writable for the return_size bytes the kernel may copy into it.
    let rc = unsafe {
        libc::ioctl(
            file.as_raw_fd(),
            DRM_IOCTL_AMDGPU_INFO as _,
            &request as *const DrmAmdgpuInfo,
        )
    };
    if rc != 0 {
        return Err(io::Error::last_os_error()).context("DRM_IOCTL_AMDGPU_INFO failed");
    }
    let mut flags = [0u8; 8];
    flags.copy_from_slice(&reply[IDS_FLAGS_OFFSET..IDS_FLAGS_OFFSET + 8]);
    Ok(u64::from_ne_bytes(flags) & AMDGPU_IDS_FLAGS_FUSION != 0)
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
        let device = discover(Path::new("/"), &amdgpu_is_integrated)?;
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
            "A display is connected to the dGPU; refusing Eco"
        );
        if self.mode == 0 {
            return Ok(());
        }
        self.standard()?;
        self.device = discover(Path::new("/"), &amdgpu_is_integrated)?;
        let pci_list = read_pci_devices()?;
        // For AMD the minor is None: card, render, PCI (+ audio), DRM sysfs and hwmon only.
        let keys = get_inodes(
            self.device.render.trim_start_matches("renderD").parse()?,
            self.device.card.trim_start_matches("card").parse()?,
            &self.device.pci,
            &None,
            &pci_list,
            self.device.minor,
        )?;
        self.keys = keys;
        let nvidia = self.device.vendor == "nvidia";
        let result = (|| -> Result<()> {
            for key in &self.keys {
                self.blocker.block_inode(*key, 1)?;
            }
            // Enable shared NVIDIA control-file block only for this single-NVIDIA topology.
            // These nodes otherwise let Vulkan/NVML wake the GPU despite DRM blocking.
            // AMD has no such shared control nodes.
            let control_nodes: &[&str] = if nvidia {
                &[
                    "nvidiactl",
                    "nvidia-modeset",
                    "nvidia-uvm",
                    "nvidia-uvm-tools",
                ]
            } else {
                &[]
            };
            for &name in control_nodes {
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
        // NVIDIA-only report; for AMD the kernel's runtime PM status below is the source.
        let rtd3 = if self.device.vendor == "nvidia" {
            read(format!(
                "/proc/driver/nvidia/gpus/{}/power",
                self.device.pci
            ))
            .and_then(|s| runtime_d3_status(&s))
        } else {
            None
        };
        json!({"rtd3_status":rtd3, "backend":"ebpf", "policy":if self.mode == 0 {"eco"} else {"standard"}, "device":self.device, "runtime_status":read(p.join("power/runtime_status")), "power_state":read(p.join("power_state")), "users":users})
    }
}
// The list of dGPU users is informational. A process can hold hundreds of thousands of file
// descriptors, so the scan stops after this long instead of delaying the reply past boreasd's
// request timeout (which would make boreasd drop the helper and the Eco block with it).
const USERS_SCAN_BUDGET: Duration = Duration::from_secs(2);
fn device_users(device: &Device) -> Vec<String> {
    let mut nodes = vec![
        format!("/dev/dri/{}", device.card),
        format!("/dev/dri/{}", device.render),
    ];
    if let Some(minor) = device.minor {
        nodes.push(format!("/dev/nvidia{minor}"));
    }
    device_users_in(Path::new("/proc"), &nodes, USERS_SCAN_BUDGET)
}
fn device_users_in(proc: &Path, nodes: &[String], budget: Duration) -> Vec<String> {
    let start = Instant::now();
    let mut users = Vec::new();
    if let Ok(entries) = fs::read_dir(proc) {
        for e in entries.flatten() {
            if start.elapsed() > budget {
                break;
            }
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
                    start.elapsed() <= budget
                        && fs::read_link(fd.path())
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
    json!({"protocol_version": 1, "capabilities": ["status", "eco", "standard", "amd"]})
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
    use std::cell::RefCell;
    /// NVIDIA layouts and Intel panels must never ask the amdgpu integrated query.
    fn never(p: &Path) -> Result<bool> {
        panic!("integrated query must not be called (asked for {})", p.display())
    }
    fn err(r: Result<Device>) -> String {
        format!("{:#}", r.unwrap_err())
    }
    #[test]
    fn device_users_finds_holders_and_stops_at_the_budget() {
        let f = Fixture::new();
        let proc = f.0.join("proc");
        for (pid, comm, target) in [("100", "game", "/dev/nvidia0"), ("200", "shell", "/dev/null")] {
            fs::create_dir_all(proc.join(pid).join("fd")).unwrap();
            fs::write(proc.join(pid).join("comm"), format!("{comm}\n")).unwrap();
            std::os::unix::fs::symlink(target, proc.join(pid).join("fd/3")).unwrap();
        }
        fs::create_dir_all(proc.join("self")).unwrap();
        let nodes = ["/dev/nvidia0".to_string()];
        assert_eq!(
            device_users_in(&proc, &nodes, Duration::from_secs(5)),
            vec!["game(100)".to_string()]
        );
        // a budget already spent: no scan, an empty (informational) list instead of a late reply
        assert!(device_users_in(&proc, &nodes, Duration::ZERO).is_empty());
    }
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
        /// Bind a PCI device's `driver` symlink to sys/bus/pci/drivers/<driver>.
        fn bind(&self, addr: &str, driver: &str) {
            let dir = self.0.join(format!("sys/bus/pci/drivers/{driver}"));
            fs::create_dir_all(&dir).unwrap();
            let link = self.0.join(format!("sys/bus/pci/devices/{addr}/driver"));
            let _ = fs::remove_file(&link);
            std::os::unix::fs::symlink(dir, link).unwrap();
        }
        // ASUS Strix G15 Advantage style: Ryzen iGPU (panel) + RX 6800M, both 0x1002.
        fn amd_laptop(&self) {
            for (addr, card, render, boot_vga) in [
                ("0000:03:00.0", "card0", "renderD128", "0"),
                ("0000:08:00.0", "card1", "renderD129", "1"),
            ] {
                self.put(&format!("sys/bus/pci/devices/{addr}/class"), "0x038000");
                self.put(&format!("sys/bus/pci/devices/{addr}/vendor"), "0x1002");
                self.put(&format!("sys/bus/pci/devices/{addr}/boot_vga"), boot_vga);
                for node in [card, render] {
                    fs::create_dir_all(
                        self.0
                            .join(format!("sys/bus/pci/devices/{addr}/drm/{node}")),
                    )
                    .unwrap();
                }
                self.bind(addr, "amdgpu");
            }
            self.put("sys/class/drm/card1-eDP-1/status", "connected");
            self.put("sys/class/drm/card0-DP-1/status", "disconnected");
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
        assert_eq!(discover(&f.0, &never).unwrap().pci, "0000:01:00.0");
    }
    #[test]
    fn rejects_primary_nvidia() {
        let f = Fixture::new();
        f.laptop();
        f.put("sys/bus/pci/devices/0000:01:00.0/boot_vga", "1");
        assert!(discover(&f.0, &never).is_err());
    }
    #[test]
    fn rejects_missing_internal_panel() {
        let f = Fixture::new();
        f.laptop();
        f.put("sys/class/drm/card1-eDP-1/status", "disconnected");
        assert!(discover(&f.0, &never).is_err());
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
        assert!(discover(&f.0, &never).is_err());
    }
    #[test]
    fn rejects_missing_render_node() {
        let f = Fixture::new();
        f.laptop();
        fs::remove_dir(f.0.join("sys/bus/pci/devices/0000:01:00.0/drm/renderD128")).unwrap();
        assert!(discover(&f.0, &never).is_err());
    }
    #[test]
    fn rejects_third_gpu() {
        let f = Fixture::new();
        f.laptop();
        f.put("sys/bus/pci/devices/0000:07:00.0/class", "0x030000");
        f.put("sys/bus/pci/devices/0000:07:00.0/vendor", "0x1002");
        fs::create_dir_all(f.0.join("sys/bus/pci/devices/0000:07:00.0/drm")).unwrap();
        assert!(discover(&f.0, &never).is_err());
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
    fn nvidia_offload_reports_vendor_and_minor() {
        let f = Fixture::new();
        f.laptop();
        let d = discover(&f.0, &never).unwrap();
        assert_eq!(d.vendor, "nvidia");
        assert_eq!(d.minor, Some(0));
        assert_eq!((d.card.as_str(), d.render.as_str()), ("card0", "renderD128"));
    }
    #[test]
    fn nvidia_card_own_edp_is_ignored() {
        // Accepted before AMD support: the NVIDIA card's own eDP status is not looked at.
        let f = Fixture::new();
        f.laptop();
        f.put("sys/class/drm/card0-eDP-1/status", "connected");
        assert_eq!(discover(&f.0, &never).unwrap().pci, "0000:01:00.0");
    }
    #[test]
    fn rejects_nvidia_driving_panel() {
        let f = Fixture::new();
        f.laptop();
        // Panel moved to the NVIDIA card; the AMD iGPU would otherwise qualify as a target.
        f.put("sys/class/drm/card1-eDP-1/status", "disconnected");
        f.put("sys/class/drm/card0-eDP-1/status", "connected");
        f.put("sys/bus/pci/devices/0000:06:00.0/boot_vga", "0");
        f.bind("0000:06:00.0", "amdgpu");
        assert!(err(discover(&f.0, &never)).contains("driving the internal panel"));
    }
    #[test]
    fn rejects_primary_nvidia_with_reason() {
        let f = Fixture::new();
        f.laptop();
        f.put("sys/bus/pci/devices/0000:01:00.0/boot_vga", "1");
        assert!(err(discover(&f.0, &never)).contains("NVIDIA GPU is primary"));
    }
    #[test]
    fn identifies_amd_offload_gpu() {
        let f = Fixture::new();
        f.amd_laptop();
        let asked = RefCell::new(Vec::new());
        let query = |p: &Path| -> Result<bool> {
            asked.borrow_mut().push(p.to_path_buf());
            Ok(true)
        };
        let d = discover(&f.0, &query).unwrap();
        assert_eq!(d.pci, "0000:03:00.0");
        assert_eq!(d.card, "card0");
        assert_eq!(d.render, "renderD128");
        assert_eq!(d.vendor, "amd");
        assert_eq!(d.minor, None);
        // Asked once, with the panel GPU's render node, never the target's.
        assert_eq!(*asked.borrow(), vec![f.0.join("dev/dri/renderD129")]);
        let v = serde_json::to_value(&d).unwrap();
        assert_eq!(v["vendor"], "amd");
        assert!(v["minor"].is_null());
    }
    #[test]
    fn rejects_amd_panel_gpu_that_is_not_integrated() {
        // MUX on the dGPU: the dGPU (boot_vga 1) drives the eDP and the iGPU (boot_vga 0,
        // nothing connected) looks like an offload GPU. Only the query tells them apart.
        let f = Fixture::new();
        f.amd_laptop();
        let query = |p: &Path| -> Result<bool> {
            assert!(p.ends_with("renderD129"), "{}", p.display());
            Ok(false)
        };
        assert!(err(discover(&f.0, &query)).contains("not the processor's integrated GPU"));
    }
    #[test]
    fn rejects_amd_when_integrated_query_fails() {
        let f = Fixture::new();
        f.amd_laptop();
        let query = |_: &Path| -> Result<bool> { bail!("ioctl failed") };
        assert!(err(discover(&f.0, &query)).contains("Cannot ask the panel GPU"));
    }
    #[test]
    fn rejects_amd_panel_gpu_not_on_amdgpu() {
        let f = Fixture::new();
        f.amd_laptop();
        f.bind("0000:08:00.0", "vfio-pci");
        assert!(err(discover(&f.0, &never)).contains("not the processor's integrated GPU"));
    }
    #[test]
    fn accepts_intel_panel_with_amd_offload_without_query() {
        let f = Fixture::new();
        f.amd_laptop();
        f.put("sys/bus/pci/devices/0000:08:00.0/vendor", "0x8086");
        let d = discover(&f.0, &never).unwrap();
        assert_eq!((d.pci.as_str(), d.vendor, d.minor), ("0000:03:00.0", "amd", None));
    }
    #[test]
    fn rejects_offload_gpu_that_is_neither_nvidia_nor_amd() {
        let f = Fixture::new();
        f.amd_laptop();
        f.put("sys/bus/pci/devices/0000:03:00.0/vendor", "0x8086");
        assert!(err(discover(&f.0, &never)).contains("neither NVIDIA nor AMD"));
    }
    #[test]
    fn rejects_amd_not_on_amdgpu() {
        let f = Fixture::new();
        f.amd_laptop();
        f.bind("0000:03:00.0", "vfio-pci");
        assert!(err(discover(&f.0, &never)).contains("not on the amdgpu driver"));
        fs::remove_file(f.0.join("sys/bus/pci/devices/0000:03:00.0/driver")).unwrap();
        assert!(err(discover(&f.0, &never)).contains("not on the amdgpu driver"));
    }
    #[test]
    fn rejects_amd_without_internal_panel() {
        let f = Fixture::new();
        f.amd_laptop();
        f.put("sys/class/drm/card1-eDP-1/status", "disconnected");
        assert!(err(discover(&f.0, &never)).contains("driving the internal panel"));
    }
    #[test]
    fn rejects_primary_amd_offload() {
        let f = Fixture::new();
        f.amd_laptop();
        f.put("sys/bus/pci/devices/0000:03:00.0/boot_vga", "1");
        assert!(err(discover(&f.0, &never)).contains("AMD GPU is primary"));
    }
    #[test]
    fn rejects_vga_class_amd_offload_without_boot_vga() {
        // A VGA-class (0x0300xx) device always has boot_vga; missing means unknown.
        let f = Fixture::new();
        f.amd_laptop();
        f.put("sys/bus/pci/devices/0000:03:00.0/class", "0x030000");
        fs::remove_file(f.0.join("sys/bus/pci/devices/0000:03:00.0/boot_vga")).unwrap();
        assert!(err(discover(&f.0, &never)).contains("AMD GPU is primary"));
    }
    #[test]
    fn accepts_display_class_amd_offload_without_boot_vga() {
        // Display controller (0x0380xx): the kernel creates no boot_vga file.
        let f = Fixture::new();
        f.amd_laptop();
        f.put("sys/bus/pci/devices/0000:03:00.0/class", "0x038000");
        fs::remove_file(f.0.join("sys/bus/pci/devices/0000:03:00.0/boot_vga")).unwrap();
        let d = discover(&f.0, &|_: &Path| -> Result<bool> { Ok(true) }).unwrap();
        assert_eq!((d.pci.as_str(), d.vendor), ("0000:03:00.0", "amd"));
    }
    #[test]
    fn rejects_amd_offload_with_unreadable_boot_vga() {
        // Exists but cannot be read (here: a directory), even on a 0x0380 device.
        let f = Fixture::new();
        f.amd_laptop();
        let boot_vga = f.0.join("sys/bus/pci/devices/0000:03:00.0/boot_vga");
        fs::remove_file(&boot_vga).unwrap();
        fs::create_dir(&boot_vga).unwrap();
        assert!(err(discover(&f.0, &never)).contains("AMD GPU is primary"));
    }
    #[test]
    fn rejects_amd_panel_on_both_gpus() {
        let f = Fixture::new();
        f.amd_laptop();
        f.put("sys/class/drm/card0-eDP-1/status", "connected");
        assert!(err(discover(&f.0, &never)).contains("driving the internal panel"));
    }
    #[test]
    #[ignore = "real hardware: asks amdgpu on /dev/dri/renderD128 of this machine"]
    fn real_amdgpu_integrated_query() {
        let r = amdgpu_is_integrated(Path::new("/dev/dri/renderD128"));
        println!("renderD128 integrated: {r:?}");
        assert!(r.is_ok());
    }
    #[test]
    fn protocol_info_advertises_amd() {
        let info = protocol_info();
        let caps = info["capabilities"].as_array().unwrap();
        for cap in ["status", "eco", "standard", "amd"] {
            assert!(caps.iter().any(|c| c == cap), "{cap}");
        }
        assert_eq!(info["protocol_version"], 1);
    }
    #[test]
    fn protocol_rejects_extra_fields() {
        assert!(serde_json::from_str::<Request>(r#"{"cmd":"eco","shell":"bad"}"#).is_err());
    }
}
