#!/usr/bin/env bash
#
# Builds a release folder: everything the server runs, with nothing to install there.
#
#   scripts/build-release.sh <40-char sha> [output folder, default ./release]
#
#   python/   python-build-standalone CPython 3.13, the baseline x86-64 build (the server's CPU
#             has SSE3 at most), with the locked dependencies installed for the server's glibc
#   app/      the kaskazi package, the bank's documents and their vectors, the prompts, the
#             golden set and the evaluation reports
#   web/      the built React app (index.html and fingerprinted assets/)
#   pages/    the unavailable page
#   REVISION  the sha
#   app/fingerprint.json  the AI fingerprint the release's evaluation report covers
#   MANIFEST.sha256  a checksum of every file, checked by kaskazi-activate
set -euo pipefail

sha="${1:?usage: build-release.sh <sha> [out]}"
out="${2:-release}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "Expected a 40-character sha." >&2; exit 64; }
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

# The runtime: pinned by release and file, checked against the release's own checksums.
PBS_RELEASE=20261026
PBS_FILE="cpython-3.13.18+${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILE//+/%2B}"
cache="${KASKAZI_BUILD_CACHE:-$root/.build-cache}"
mkdir -p "$cache"
if [[ ! -f "$cache/$PBS_FILE" ]]; then
    curl -fsSL --retry 3 -o "$cache/$PBS_FILE.part" "$PBS_URL"
    curl -fsSL --retry 3 -o "$cache/SHA256SUMS-$PBS_RELEASE" \
        "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/SHA256SUMS"
    want="$(awk -v n="$PBS_FILE" '$2 == n {print $1}' "$cache/SHA256SUMS-$PBS_RELEASE")"
    got="$(sha256sum "$cache/$PBS_FILE.part" | cut -d' ' -f1)"
    [[ -n "$want" && "$want" == "$got" ]] || { echo "The Python runtime's checksum doesn't match." >&2; exit 1; }
    mv "$cache/$PBS_FILE.part" "$cache/$PBS_FILE"
fi

rm -rf "$out"
mkdir -p "$out"
tar -xzf "$cache/$PBS_FILE" -C "$out"
py="$out/python/bin/python3"
site="$out/python/lib/python3.13/site-packages"

# Dependencies from the lock file, with hashes, built for the server (Ubuntu 22.04, glibc 2.35).
uv export --frozen --no-dev --no-emit-project --format requirements-txt -o "$cache/requirements.txt" >/dev/null
uv pip install --quiet --python "$py" --break-system-packages --python-platform x86_64-manylinux_2_35 \
    --require-hashes --no-deps -r "$cache/requirements.txt"

# The app, found through a .pth file so the runtime imports it with -I.
mkdir -p "$out/app"
cp -r kaskazi prompts "$out/app/"
mkdir -p "$out/app/corpus" "$out/app/evals"
cp -r corpus/documents corpus/embeddings "$out/app/corpus/"
cp -r evals/golden "$out/app/evals/"  # the release check asks ten of its questions live
[[ -d evals/reports ]] && cp -r evals/reports "$out/app/evals/"
echo "../../../../app" > "$site/kaskazi-app.pth"
# The AI fingerprint of the source this release is built from; CI's gate has found a passing
# golden-set report for it, and the staff console shows which one.
uv run --frozen python -m kaskazi.release fingerprint > "$out/app/fingerprint.json"
find "$out/app" -name __pycache__ -type d -prune -exec rm -rf {} +

# The web app.
(cd web && pnpm install --frozen-lockfile --silent && pnpm -s build)
cp -r web/dist "$out/web"
mkdir -p "$out/pages"
cp deploy/unavailable.html "$out/pages/"

# What a server never needs (Pillow's Tk bridge included: nothing here draws to a Tk window).
rm -f "$site"/PIL/_imagingtk*.so
rm -rf "$out/python/lib/python3.13/"{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3} \
       "$out/python/lib/"{tcl*,tk*,itcl*,thread*} "$site"/pip "$site"/pip-*.dist-info \
       "$out/python/bin/"{idle3*,pip*,pydoc3*,2to3*}

# Bytecode, validated by hash, so services never need to write next to the code.
"$py" -I -m compileall -q -j 0 --invalidation-mode unchecked-hash "$out/app" "$out/python/lib/python3.13" || true

printf '%s' "$sha" > "$out/REVISION"
chmod -R go+rX,go-w "$out"
(cd "$out" && find . -type f ! -name MANIFEST.sha256 -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256)
chmod go+r "$out/MANIFEST.sha256"

echo "Built release ${sha:0:7} in $out ($(du -sh "$out" | cut -f1))"
"$py" -I -m kaskazi.release version
