# Third-party license notices

These files are copied verbatim into every packaged artifact's `licenses/` directory
(`installers/package.sh`) so a shipped knaif carries attribution for everything it bundles.

| File | Covers | When bundled |
|------|--------|--------------|
| `THIRD-PARTY-RUST.txt` | All Rust crates compiled into `knaif` (incl. `llama-cpp-2`/`-sys-2`), with full license texts. **Generated** by `just licenses` (cargo-about). | always |
| `llama.cpp-LICENSE.txt` | The llama.cpp / ggml C/C++ inference library, statically linked. MIT. | `cpu`/`vulkan`/`cuda` builds |
| `PDFium-LICENSE.txt` | PDFium rendering library (BSD-3-Clause), for documents rasterize/OCR. | `pdfium` builds *(added when PDFium is bundled)* |
| `NVIDIA-CUDA-EULA.txt` | NVIDIA CUDA redistributable terms (cudart/cublas/cublasLt DLLs). Copied verbatim from `$CUDA_PATH/EULA.txt` (CUDA v13.3). Refresh it when the bundled toolkit version changes. | `cuda` builds |

Regenerate the Rust report after changing dependencies:

```
just licenses
```

The project's own license is `LICENSE` at the repo root (shipped at the artifact root, not here).
