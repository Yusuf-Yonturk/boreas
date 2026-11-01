# Changelog

Boreas shows these notes from the **Release notes** link when a new version is available.
Newest version first.

## 0.1.1

GPU mode fixes, tested on a ROG Strix G713PV.

- Eco → Standard no longer hangs for 30 seconds and fails: the dGPU's PCIe port is woken up first,
  so the switch now takes a few seconds
- Eco can now be applied at boot: nvidia-powerd is stopped before the NVIDIA driver is unloaded
- The apps using the dGPU (KWin, Xwayland, games) are detected correctly, so the "Eco postponed"
  notice now names them
- A firmware error while changing the GPU mode no longer stops the service at boot

## 0.1.0

First public release.

- Performance modes (Silent, Balanced, Turbo) and custom modes
- Custom fan curves per mode (CPU, GPU and mid fan)
- CPU and GPU power limits, CPU temperature limit (Ryzen)
- GPU modes: Eco, Standard, Optimized and Ultimate (MUX switch)
- Aura keyboard lighting and brightness, battery charge limit
- Built-in display refresh rate and panel overdrive (KDE)
- Automatic mode switching on AC / battery, re-apply after sleep
- Light and dark theme, 11 languages
- Update notice with one-click update
