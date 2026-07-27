//! Embed Windows VERSIONINFO into `knaif.exe`.
//!
//! Without this the built exe reports a **blank `FileVersion`** and an empty Properties → Details
//! tab (F4 of the Windows installer polish plan). That tab is exactly what a cautious user opens
//! after the SmartScreen prompt an unsigned binary produces, so an empty one costs trust at the
//! moment it matters most.
//!
//! `FileVersion` and `ProductVersion` come from `CARGO_PKG_VERSION` automatically, so the workspace
//! version stays the single source of truth and this file never needs bumping.
//!
//! The icon is `media/knaif.ico`, generated from the square `media/logo-square.png` mark and
//! carrying 16/24/32/48/64/128/256 so Windows picks per context rather than rescaling one size.
//! Rerun-if-changed is declared on it: without that, editing the icon would not rebuild the exe.

fn main() {
    // A build script is compiled and run for the HOST, so `cfg!(windows)` describes the machine
    // doing the build, not the machine the binary is for. `CARGO_CFG_TARGET_OS` is the target —
    // the distinction only shows up when cross-compiling, which is precisely when a host-based
    // check would embed nothing (or try to embed on a Linux target) without anyone noticing.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }

    // Cargo only reruns a build script when something it declares has changed. Without this, a new
    // icon would leave the previously embedded one in the exe until an unrelated change forced a
    // rebuild — the kind of staleness that shows up much later, in a shipped artifact.
    println!("cargo:rerun-if-changed=../../media/knaif.ico");

    let mut res = winresource::WindowsResource::new();
    res.set_icon("../../media/knaif.ico");
    res.set("CompanyName", "Blackdeep Technologies Ltd.")
        .set("ProductName", "knaif")
        .set(
            "FileDescription",
            "knaif — natural language to validated action plans",
        )
        .set(
            "LegalCopyright",
            "Copyright 2026 Blackdeep Technologies Ltd.",
        );

    // Never fail the build over metadata. Embedding needs a resource compiler (rc.exe on Windows,
    // windres when cross-compiling); on a box without one, a warning and a plain exe beats an
    // unbuildable CLI. The smoke test still checks the binary runs and reports its version.
    if let Err(e) = res.compile() {
        println!("cargo:warning=VERSIONINFO not embedded into knaif.exe: {e}");
    }
}
