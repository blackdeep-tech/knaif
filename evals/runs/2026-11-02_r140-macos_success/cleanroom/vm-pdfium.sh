#!/bin/bash
# the smallest compress on macOS 12, the CPU copy of the .zip's tree: it rasterizes through PDFium
cd ~/room && rm -rf pdfium && mkdir pdfium && ditto -x -k knaif-1.4.0-macos-arm64.zip pdfium && cp sample.pdf pdfium/
rm -f pdfium/knaif-1.4.0-macos-arm64/bin/libggml-metal.*
find pdfium -name 'libpdfium*.dylib' -exec sh -c 'echo "$1"; otool -l "$1" | grep -A4 LC_BUILD_VERSION' _ {} \;
cd pdfium && ./knaif-1.4.0-macos-arm64/bin/knaif run documents --yes --model ../knaif-qwen3-4b-v2-q4_k_m.gguf -- compress sample.pdf to the smallest size
echo "exit $?"
ls -l *.pdf
