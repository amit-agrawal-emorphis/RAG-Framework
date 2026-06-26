"""
Copy packages excluded from Nuitka compilation into the dist so they are
importable at runtime.

Two phases:
  1. Copy every explicitly listed package (nofollow list + known transitive deps).
  2. Auto-discover remaining missing packages by trying to import docling_loader
     from the dist directory alone, then copying whatever is missing.

find_spec() locates packages without executing __init__.py, avoiding ctypes/GPU
side-effects (e.g. llama_cpp loading libllama.so, PIL loading libtiff).
"""

import importlib.util
import shutil
import os
import glob
import subprocess
import sys

DIST = "/dist/launcher.dist"
SITE = "/usr/local/lib/python3.11/dist-packages"

# ── Phase 1: known packages ─────────────────────────────────────────────────
KNOWN = [
    # Nuitka --nofollow-import-to packages
    # torch + torchvision: in nofollow list but were missing here — docling_ibm_models
    # layout_predictor.py imports torch unconditionally at module load time.
    # torchgen: top-level sibling package in the CPU wheel (NOT inside torch/).
    # torch/utils/_python_dispatch.py:13 imports it; missing torchgen → ModuleNotFoundError.
    "torch", "torchvision", "torchgen",
    # sympy + mpmath: required by torch._dynamo.utils (via torch.fx.experimental.symbolic_shapes).
    # torch._dynamo is imported transitively by torchvision and transformers when running
    # with torch >= 2.6; without sympy the entire layout-model import chain fails.
    "sympy", "mpmath",
    # hf_xet: huggingface_hub uses this for optimized model downloads (XET storage).
    # Without it, downloading docling layout model weights raises ValueError.
    "hf_xet",
    "llama_cpp", "PIL", "scipy", "lxml", "rtree",
    "transformers", "huggingface_hub", "tokenizers", "safetensors",
    "accelerate", "docling_ibm_models", "onnxruntime", "cv2",
    # docling PDF backend — DoclingParseV2DocumentBackend imports this at init;
    # if missing, is_valid() returns False and every PDF raises ConversionError.
    "docling_parse",
    # pypdfium2 — used by docling for page-image rendering (figure/table extraction).
    "pypdfium2",
    # pypdfium2_raw — companion C-extension package bundling the actual PDFium binary.
    # pypdfium2/raw.py imports it unconditionally: `from pypdfium2_raw.bindings import *`
    # Must be copied alongside pypdfium2 or every PDF import fails with ModuleNotFoundError.
    "pypdfium2_raw",
    # easyocr + timm — in --nofollow-import-to but were missing from copy list;
    # needed for OCR on image-heavy PDFs.
    "easyocr", "timm",
    # Transitive deps of nofollow packages (not imported by main code directly)
    "diskcache",   # llama_cpp.llama_cache
    # jinja2 is compiled by Nuitka via --include-package=jinja2 (all submodules incl. ext/sandbox)
    "regex",       # transformers.utils.auto_docstring
    "filelock",    # huggingface_hub.utils._fixes
    "fsspec",      # huggingface_hub file operations
    "flatbuffers", # onnxruntime
    "packaging",   # transformers version checks
    "yaml",        # pyyaml — transformers version checks
]

site_pkg_dirs: set = set()


def _copy_pkg(pkg: str) -> bool:
    spec = importlib.util.find_spec(pkg)
    if spec and spec.submodule_search_locations:
        src = spec.submodule_search_locations[0]
        dst = os.path.join(DIST, pkg)
        shutil.copytree(src, dst, dirs_exist_ok=True)
        site_pkg_dirs.add(os.path.dirname(src))
        n = sum(len(fs) for _, _, fs in os.walk(dst))
        print(f"  copied {pkg}  ({n} files)")
        return True
    elif spec and spec.origin and os.path.isfile(spec.origin):
        # Single-file compiled extension (.so / .pyd)
        dst = os.path.join(DIST, os.path.basename(spec.origin))
        if not os.path.exists(dst):
            shutil.copy2(spec.origin, dst)
            site_pkg_dirs.add(os.path.dirname(spec.origin))
            print(f"  copied {pkg}  (single file: {os.path.basename(spec.origin)})")
        return True
    else:
        print(f"  skip {pkg}: not found in site-packages")
        return False


print("=== Phase 1: copying known packages ===")
for pkg in KNOWN:
    _copy_pkg(pkg)

# Copy all *.libs/ siblings (manylinux wheel vendored .so files)
print("\n=== Phase 1b: copying *.libs/ siblings ===")
for sp in site_pkg_dirs:
    for libs_dir in glob.glob(os.path.join(sp, "*.libs")):
        name = os.path.basename(libs_dir)
        dst = os.path.join(DIST, name)
        shutil.copytree(libs_dir, dst, dirs_exist_ok=True)
        print(f"  copied {name}: {len(os.listdir(dst))} files")

# Phase 1c: copy all dist-info metadata dirs so importlib.metadata / pkg_resources
# version checks succeed (transformers checks tqdm, regex, packaging, etc. at startup).
print("\n=== Phase 1c: copying dist-info metadata ===")
dist_info_count = 0
for di in glob.glob(os.path.join(SITE, "*.dist-info")):
    name = os.path.basename(di)
    dst = os.path.join(DIST, name)
    if not os.path.exists(dst):
        shutil.copytree(di, dst)
        dist_info_count += 1
print(f"  copied {dist_info_count} dist-info directories")

# ── Phase 2: auto-discover remaining missing packages ────────────────────────
# Use the actual Nuitka binary (ingest.bin) to surface ModuleNotFoundErrors,
# since frozen modules are only visible to the binary, not to plain python3.11.
print("\n=== Phase 2: auto-discovery (run ingest.bin against test PDF) ===")

_tmp_docs = "/tmp/_copy_pkgs_empty_docs"
_tmp_out  = "/tmp/_copy_pkgs_out"
_fake_model = "/tmp/_fake_model.gguf"
os.makedirs(_tmp_docs, exist_ok=True)
os.makedirs(_tmp_out, exist_ok=True)
if not os.path.exists(_fake_model):
    open(_fake_model, "wb").close()   # zero-byte placeholder — load will fail, but imports run first


def _make_minimal_pdf() -> bytes:
    """Return a minimal valid 1-page PDF with correct xref offsets.

    Having a real PDF in the test docs dir forces ingest.bin to initialise the
    full PDF pipeline (layout model → torch → torchgen …), exposing any missing
    packages that the empty-docs run would never reach.
    """
    objs = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n",
    ]
    header = b"%PDF-1.4\n"
    body = header
    offsets = []
    for obj in objs:
        offsets.append(len(body))
        body += obj
    xref_pos = len(body)
    xref = b"xref\n0 %d\n" % (len(objs) + 1,)
    xref += b"0000000000 65535 f \n"
    for off in offsets:
        xref += b"%010d 00000 n \n" % (off,)
    trailer = (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objs) + 1, xref_pos)
    )
    return body + xref + trailer


_tmp_pdf = os.path.join(_tmp_docs, "test.pdf")
if not os.path.exists(_tmp_pdf):
    with open(_tmp_pdf, "wb") as _f:
        _f.write(_make_minimal_pdf())
    print("  created minimal test.pdf for Phase 2 PDF-path discovery")

auto_added = []
seen_missing: set = set()
MAX_ITER = 40

for iteration in range(MAX_ITER):
    result = subprocess.run(
        [f"{DIST}/ingest.bin",
         "--docs_dir", _tmp_docs,
         "--out_dir",  _tmp_out,
         "--embedding_model", _fake_model],
        capture_output=True, text=True,
        env={**os.environ, "LD_LIBRARY_PATH": f"{DIST}/llama_cpp/lib:{DIST}/_libs"},
        timeout=60,
    )
    out = (result.stdout + result.stderr).strip()

    if "ModuleNotFoundError" not in out and "No module named" not in out:
        print(f"  import chain OK — no module errors  (auto-added {len(auto_added)} packages)")
        break

    if "No module named" not in out:
        print(f"  iter {iteration+1}: unexpected output: {out[:400]}")
        break

    # Extract missing package name from "No module named 'X'" or "ModuleNotFoundError: ..."
    msg = next(
        (l for l in out.splitlines() if "No module named" in l or "ModuleNotFoundError" in l),
        out
    )
    if "'" in msg:
        pkg = msg.split("'")[1].split(".")[0]
    else:
        pkg = msg.split()[-1].split(".")[0]

    if pkg in seen_missing:
        print(f"  iter {iteration+1}: stuck on {pkg!r} — not resolvable, stopping.")
        break
    seen_missing.add(pkg)

    if os.path.exists(os.path.join(DIST, pkg)):
        print(f"  iter {iteration+1}: {pkg!r} is in dist but still fails — stopping.")
        break

    print(f"  iter {iteration+1}: auto-copying {pkg!r}")
    if _copy_pkg(pkg):
        auto_added.append(pkg)
        # Also copy any new *.libs/ siblings this package brought in
        for sp in site_pkg_dirs:
            for libs_dir in glob.glob(os.path.join(sp, "*.libs")):
                name = os.path.basename(libs_dir)
                dst = os.path.join(DIST, name)
                if not os.path.exists(dst):
                    shutil.copytree(libs_dir, dst, dirs_exist_ok=True)
                    print(f"    also copied {name}: {len(os.listdir(dst))} files")
    else:
        print(f"  iter {iteration+1}: {pkg!r} not found — stopping.")
        break

print("\n=== Summary ===")
print(f"Known packages copied: {KNOWN}")
print(f"Auto-discovered packages copied: {auto_added}")
print("done")
