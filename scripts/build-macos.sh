#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="${1:-Release}"
case "$CONFIG" in Release) OPT=(-O2) ;; Debug) OPT=(-O0 -g) ;; *) echo 'Usage: build-macos.sh [Release|Debug]' >&2; exit 2 ;; esac
[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || { echo 'Requires an Apple Silicon Mac.' >&2; exit 1; }
ACIS="${ACIS_ROOT:-$ROOT/2026.1.0.1_acis_all_mac_m64/2026.1.0.1_acis_mac_m64}"
IOP="${INTEROP_ROOT:-$ROOT/2026.1.0.1_iop_mac_m64}"
OUT="$ROOT/build/macos/$CONFIG"
SDK_INCLUDE="$ROOT/build/macos/sdk"
ACIS_BIN="$ACIS/macos_m64/code/bin"
IOP_BIN="$IOP/macos_m64/code/bin"
for required in "$ACIS/include/kernapi.hxx" "$ACIS_BIN/libSpaACIS.dylib" "$IOP/include/SPAIDocument.h" "$IOP_BIN/libSPAIInterop.dylib" "$ROOT/_deps/vendor/nlohmann/json.hpp"; do
    [[ -f "$required" ]] || { echo "Missing dependency: $required" >&2; exit 1; }
done
if [[ ! -f "$ROOT/systems/dbcad/CAD_DB/spatial_license.h" && -f "$ROOT/spatial_license.h" ]]; then
    cp "$ROOT/spatial_license.h" "$ROOT/systems/dbcad/CAD_DB/spatial_license.h"
fi
[[ -f "$ROOT/systems/dbcad/CAD_DB/spatial_license.h" ]] || { echo 'Missing systems/dbcad/CAD_DB/spatial_license.h' >&2; exit 1; }
mkdir -p "$SDK_INCLUDE/acis" "$OUT"
# Keep the Mac header layout separate from a possible Windows TeamKit in _deps.
ln -sfn "$ACIS/include" "$SDK_INCLUDE/acis/include"
# Downloaded SDK binaries can carry quarantine even though they are valid local
# development inputs. Clear that attribute only on these runtime binaries.
python3 - "$ACIS_BIN" "$IOP_BIN" <<'PY'
import os
import stat
import subprocess
import sys
from pathlib import Path
for directory in sys.argv[1:]:
    for path in Path(directory).iterdir():
        if not path.is_file():
            continue
        if path.suffix != ".dylib" and not os.access(path, os.X_OK):
            continue
        if subprocess.run(["xattr", "-p", "com.apple.quarantine", str(path)], capture_output=True).returncode:
            continue
        mode = path.stat().st_mode
        try:
            path.chmod(mode | stat.S_IWUSR)
            subprocess.run(["xattr", "-d", "com.apple.quarantine", str(path)], check=True)
        finally:
            path.chmod(mode)
PY
# SDK install names use @executable_path; preserve the vendor runtime layout
# through local symlinks, including InterOp child converters and resources.
for bin in "$IOP_BIN" "$ACIS_BIN"; do
    for item in "$bin"/*; do
        ln -sfn "$item" "$OUT/$(basename "$item")"
    done
done
for item in resources reffiles; do
    [[ ! -d "$IOP/macos_m64/$item" ]] || ln -sfn "$IOP/macos_m64/$item" "$OUT/$item"
done
FLAGS=(-std=c++20 -stdlib=libc++ -arch arm64 -mmacosx-version-min=13.3 -DSPA_NO_AUTO_LINK "${OPT[@]}" -I"$ROOT/systems/dbcad/CAD_DB" -I"$SDK_INCLUDE" -I"$ROOT/_deps" -I"$IOP/include")
SOURCES=(systems/dbcad/CAD_DB/acis.cpp systems/dbcad/CAD_DB/common.cpp systems/dbcad/CAD_DB/kernel/mesh_extractor.cpp systems/dbcad/CAD_DB/kernel/glb_writer.cpp systems/dbcad/CAD_DB/worker/main.cpp systems/dbcad/CAD_DB/worker/session.cpp systems/dbcad/CAD_DB/worker/entity_graph_codec.cpp)
OBJECTS=()
for source in "${SOURCES[@]}"; do
    object="$OUT/${source//\//_}.o"
    echo "Compiling $source"
    xcrun clang++ "${FLAGS[@]}" -c "$ROOT/$source" -o "$object"
    OBJECTS+=("$object")
done
LIBS=(-L"$ACIS_BIN" -L"$IOP_BIN" -lSpaACIS -lSPAIInterop -lSPAIAcis -lSPAIopUtilities -Wl,-rpath,"$IOP_BIN" -Wl,-rpath,"$ACIS_BIN" -Wl,-stack_size,0x400000)
xcrun clang++ -arch arm64 "${OBJECTS[@]}" "${LIBS[@]}" -o "$OUT/dbcad_worker.bin"
xcrun clang++ "${FLAGS[@]}" "$ROOT/scripts/tests/mesh_tests.cpp" "${OBJECTS[0]}" "${OBJECTS[1]}" "${OBJECTS[2]}" "${OBJECTS[3]}" "${LIBS[@]}" -o "$OUT/mesh_tests.bin"
for target in dbcad_worker mesh_tests; do
    cat > "$OUT/$target" <<'SH'
#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# Some vendor dependencies use bare dylib install names rather than @rpath.
export DYLD_LIBRARY_PATH="$HERE${DYLD_LIBRARY_PATH:+:$DYLD_LIBRARY_PATH}"
exec "$HERE/$(basename "$0").bin" "$@"
SH
    chmod +x "$OUT/$target"
done
echo "Built $OUT/dbcad_worker and mesh_tests"
