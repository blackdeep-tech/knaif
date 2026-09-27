# Keep the builder's paths out of a Windows build's binaries. Sourced by build_native_kind.sh.
#
# Rust embeds source paths as panic locations, and C/C++/CUDA embed them through `__FILE__`. Every
# crate built out of the cargo registry therefore carries `<home>\.cargo\registry\src\...`, which
# names whoever built it; 1.1.0's Windows artifacts shipped ~1,200 of them. The Linux artifacts are
# built in a container whose paths name nobody, so this is Windows-only.
#
#   path_hygiene_env <cargo-home> <checkout>     (Windows paths) -> KEY=VALUE lines
#
# CARGO_ENCODED_RUSTFLAGS takes \x1f-separated flags, so the value is exact. CFLAGS/CXXFLAGS/
# CUDAFLAGS are split on whitespace by the cc crate and CMake, so a path with a space is refused
# rather than silently split into two broken flags. Trimming without a trailing backslash keeps
# the value from escaping a quote on its way through CMake; `__FILE__` then starts at `\registry`.
# installers/package.sh runs scripts/check_no_local_paths.py on the staged tree either way.

path_hygiene_env() {
  local cargo_home="$1" root="$2" p
  for p in "$cargo_home" "$root"; do
    case "$p" in
      *" "*)
        echo "path_hygiene: '$p' contains a space; the C/CUDA trim flags would split it." >&2
        echo "  Build from a checkout and a CARGO_HOME without spaces." >&2
        return 1
        ;;
    esac
  done
  cargo_home="${cargo_home%\\}"
  root="${root%\\}"
  printf 'CARGO_ENCODED_RUSTFLAGS=--remap-path-prefix=%s=/cargo\x1f--remap-path-prefix=%s=/knaif\n' \
    "$cargo_home" "$root"
  printf 'CFLAGS=/d1trimfile:%s /d1trimfile:%s\n' "$cargo_home" "$root"
  printf 'CXXFLAGS=/d1trimfile:%s /d1trimfile:%s\n' "$cargo_home" "$root"
  printf 'CUDAFLAGS=-Xcompiler=/d1trimfile:%s -Xcompiler=/d1trimfile:%s\n' "$cargo_home" "$root"
}
