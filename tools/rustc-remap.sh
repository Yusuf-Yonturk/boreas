#!/bin/sh
# rustc wrapper that strips build-machine directory names from boreas-gpu-engine.
#
#   RUSTC_WRAPPER="$PWD/tools/rustc-remap.sh" cargo +1.95.0 build --release ...
#
# cargo runs it as "<wrapper> <rustc> <args...>" and the nested nightly cargo that
# aya-build starts for the embedded BPF object inherits the variable, so both the
# helper and the BPF object's debug info are remapped. rustc applies the last
# matching --remap-path-prefix, so the most specific prefix is listed last.
set -eu
rustc=$1
shift
checkout=${BOREAS_CHECKOUT:-${GITHUB_WORKSPACE:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}}
exec "$rustc" "$@" \
    --remap-path-prefix "${HOME}=/build/user" \
    --remap-path-prefix "${CARGO_HOME:-$HOME/.cargo}=/build/cargo" \
    --remap-path-prefix "${RUSTUP_HOME:-$HOME/.rustup}=/build/rustup" \
    --remap-path-prefix "${checkout}=/build/boreas"
