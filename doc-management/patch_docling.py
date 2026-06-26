"""Patch docling and torch-ecosystem source files to make optional imports safe.

In addition to docling's VLM/ASR features, several packages in the nofollow list
import torch._dynamo at module load time even though we never use torch.compile.
These patches wrap those imports in try/except so the binary can start without the
full dynamo dependency chain (which requires sympy, cProfile, pstats, etc.).

docling 2.89 added VLM/ASR features that import torch and transformers
unconditionally at module load time. These features are never used in our
PDF-only ingestion pipeline, but the imports crash the binary at startup
because torch is excluded from the Nuitka build (too large, ~2 GB).

Patches are applied BEFORE Nuitka compilation so the frozen binary has the
try/except wrappers, not the original hard imports.
"""
import os

SITE = "/usr/local/lib/python3.11/dist-packages"


def patch(path, replacements):
    full = os.path.join(SITE, path)
    if not os.path.exists(full):
        print(f"SKIP (not found): {path}")
        return
    with open(full) as f:
        content = f.read()
    changed = False
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new, 1)
            changed = True
    with open(full, "w") as f:
        f.write(content)
    print(f"{'patched' if changed else 'already patched'}: {os.path.basename(full)}")


# ── granite_vision.py ─────────────────────────────────────────────────────────
patch(
    "docling/models/stages/chart_extraction/granite_vision.py",
    [
        (
            "import torch\n",
            "try:\n    import torch\nexcept ImportError:\n    torch = None\n",
        ),
        (
            "from transformers import AutoModelForImageTextToText, AutoProcessor\n",
            "try:\n    from transformers import AutoModelForImageTextToText, AutoProcessor\n"
            "except Exception:\n    AutoModelForImageTextToText = None\n    AutoProcessor = None\n",
        ),
    ],
)

# ── pipeline_options_vlm_model.py ─────────────────────────────────────────────
# Imported at module load time via pipeline_options -> asr_model_specs chain.
patch(
    "docling/datamodel/pipeline_options_vlm_model.py",
    [
        (
            "from transformers import StoppingCriteria\n",
            "try:\n    from transformers import StoppingCriteria\n"
            "except Exception:\n    class StoppingCriteria:\n        pass\n",
        ),
    ],
)

# ── table_structure_model_v2.py ───────────────────────────────────────────────
patch(
    "docling/models/stages/table_structure/table_structure_model_v2.py",
    [
        (
            "import torch\n",
            "try:\n    import torch\nexcept ImportError:\n    torch = None\n",
        ),
        (
            "import torchvision.transforms as T  # type: ignore[import-untyped]\n",
            "try:\n    import torchvision.transforms as T\nexcept ImportError:\n    T = None\n",
        ),
        (
            "from transformers import AutoTokenizer\n",
            "try:\n    from transformers import AutoTokenizer\nexcept Exception:\n    AutoTokenizer = None\n",
        ),
    ],
)

# ── transformers_engine.py ───────────────────────────────────────────────────
patch(
    "docling/models/inference_engines/vlm/transformers_engine.py",
    [
        (
            "import torch\n",
            "try:\n    import torch\nexcept ImportError:\n    torch = None\n",
        ),
    ],
)

# ── hf_transformers_model.py ─────────────────────────────────────────────────
patch(
    "docling/models/vlm_pipeline_models/hf_transformers_model.py",
    [
        (
            "from transformers import StoppingCriteria, StoppingCriteriaList, StopStringCriteria\n",
            "try:\n    from transformers import StoppingCriteria, StoppingCriteriaList, StopStringCriteria\n"
            "except Exception:\n"
            "    class StoppingCriteria: pass\n"
            "    class StoppingCriteriaList: pass\n"
            "    class StopStringCriteria: pass\n",
        ),
    ],
)

# ── mlx_model.py ─────────────────────────────────────────────────────────────
patch(
    "docling/models/vlm_pipeline_models/mlx_model.py",
    [
        (
            "from transformers import StoppingCriteria\n",
            "try:\n    from transformers import StoppingCriteria\nexcept Exception:\n    class StoppingCriteria: pass\n",
        ),
    ],
)

# ── nuextract_transformers_model.py ──────────────────────────────────────────
patch(
    "docling/models/extraction/nuextract_transformers_model.py",
    [
        (
            "from transformers import AutoModelForImageTextToText, AutoProcessor, GenerationConfig\n",
            "try:\n    from transformers import AutoModelForImageTextToText, AutoProcessor, GenerationConfig\n"
            "except Exception:\n"
            "    AutoModelForImageTextToText = None\n"
            "    AutoProcessor = None\n"
            "    GenerationConfig = None\n",
        ),
    ],
)

# ── generation_utils.py ──────────────────────────────────────────────────────
patch(
    "docling/models/utils/generation_utils.py",
    [
        (
            "from transformers import StoppingCriteria\n",
            "try:\n    from transformers import StoppingCriteria\nexcept Exception:\n    class StoppingCriteria: pass\n",
        ),
    ],
)

# ── torchvision/ops/roi_align.py ─────────────────────────────────────────────
# torchvision.ops.roi_align imports is_compile_supported from torch._dynamo.utils.
# This triggers the full dynamo init chain (aot_compile → convert_frame → cProfile
# → sympy etc.) whenever torchvision is imported — which happens via image_processing
# for RTDetrImageProcessor. We don't use torch.compile so is_compile_supported can
# safely return False when dynamo is unavailable.
patch(
    "torchvision/ops/roi_align.py",
    [
        (
            "from torch._dynamo.utils import is_compile_supported\n",
            "try:\n"
            "    from torch._dynamo.utils import is_compile_supported\n"
            "except (ImportError, ModuleNotFoundError):\n"
            "    def is_compile_supported(*a, **kw): return False\n",
        ),
    ],
)

# ── transformers/masking_utils.py ─────────────────────────────────────────────
# torch >= 2.6: masking_utils imports TransformGetItemToIndex from torch._dynamo.
# torch._dynamo.convert_frame imports cProfile/pstats (stdlib) which Nuitka doesn't
# include (they're hidden inside the nofollow torch package). Wrap in try/except;
# TransformGetItemToIndex is only used for text-generation mask computation which
# our PDF-only pipeline never invokes.
patch(
    "transformers/masking_utils.py",
    [
        (
            "if _is_torch_greater_or_equal_than_2_6:\n"
            "    from torch._dynamo._trace_wrapped_higher_order_op import TransformGetItemToIndex\n",
            "if _is_torch_greater_or_equal_than_2_6:\n"
            "    try:\n"
            "        from torch._dynamo._trace_wrapped_higher_order_op import TransformGetItemToIndex\n"
            "    except (ImportError, ModuleNotFoundError):\n"
            "        TransformGetItemToIndex = None\n",
        ),
    ],
)
