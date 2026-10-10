#!/usr/bin/env bash
# Fetch the PDFium shared library used by dw-pdf.
#
# D1 (docs/DECISIONS.md) picks PDFium because it is BSD-3 / MIT-licensed and
# cargo-deny rejects AGPL mechanically. The binary is not vendored: it is
# downloaded from the upstream release and pinned by checksum here.
#
# Usage:
#   scripts/fetch_pdfium.sh              # install into designwins/vendor/pdfium
#   PDFIUM_LIB_PATH=/x/libpdfium.dylib cargo test -p dw-pdf
set -euo pipefail

VERSION="7881"                       # matches pdfium-render feature pdfium_7881
TARBALL_SHA256="52e94ca5aa8847934330daf3f8150c190682c5ca93831468794f8b90d4392e40"
BASE_URL="https://github.com/bblanchon/pdfium-binaries/releases/download/chromium/${VERSION}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${REPO_ROOT}/designwins/vendor/pdfium"
CACHE="${DEST}/cache"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  ASSET="pdfium-mac-arm64.tgz" ;;
  Darwin-x86_64) ASSET="pdfium-mac-x64.tgz" ;;
  Linux-x86_64)  ASSET="pdfium-linux-x64.tgz" ;;
  Linux-aarch64) ASSET="pdfium-linux-arm64.tgz" ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "${DEST}/lib" "${CACHE}"

if [[ ! -f "${DEST}/lib/libpdfium.dylib" && ! -f "${DEST}/lib/libpdfium.so" ]]; then
  echo "fetching ${ASSET} (pdfium ${VERSION})"
  curl -sSL -o "${CACHE}/${ASSET}" "${BASE_URL}/${ASSET}"
  echo "${TARBALL_SHA256}  ${CACHE}/${ASSET}" | shasum -a 256 -c -
  tar xzf "${CACHE}/${ASSET}" -C "${DEST}"
fi

echo "pdfium at ${DEST}/lib/"
ls -la "${DEST}/lib/"
