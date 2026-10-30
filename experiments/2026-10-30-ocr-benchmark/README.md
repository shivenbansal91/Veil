# 2026-10-30 — GPU readiness and RapidOCR benchmark

## Setup

- RTX 2050 4 GB (driver 581.83), i5-13420H, Windows 11. Machine otherwise idle (unlike the 2026-10-29 CPU run).
- `onnxruntime-gpu` 1.31.1 (CUDA 13), `rapidocr` 3.10.0 with its default PP-OCRv6 models (det small, cls mobile, rec small, `lang_type: ch`).
- Inputs come from VEIL-Bench (`bench/scenario.py`), seed 1, 1920x1080: the editor frame and the terminal frame with the most planted secrets. Consolas 14 px, dark themes, fake secrets generated at runtime.
- "dirty 3 rows" = a full-width strip of 3 text rows around the longest secret (about 1920x60). That's the realistic per-change workload once only changed lines are OCR'd (CLAUDE.md §5.3).
- Accuracy per secret (a wrapped secret counts per visible part): *exact* = the value appears verbatim in an OCR line; *CER* = character error rate of the best-aligned window.
- 2 warm-up runs, then 10 timed runs (GPU) or 3 (CPU). Medians.
- Run: `gpu_check.py`, then `ocr_bench.py [config-name-filter]`.

## GPU readiness (`gpu_check.py`)

| Check | Result |
|---|---|
| Providers | `TensorrtExecutionProvider, CUDAExecutionProvider, CPUExecutionProvider` |
| Real model on CUDA (RapidOCR det, 1280x736 input) | runs; max abs diff vs CPU 4.6e-4 |
| det model latency | GPU 39.6 ms, CPU 545.2 ms |
| RapidOCR det / cls / rec sessions | all `CUDAExecutionProvider` |
| RapidOCR end to end | read `GITHUB_TOKEN=ghp_FAKE0000example` exactly |
| GPU memory | 220 MiB idle → about 1.8–1.95 GiB with one RapidOCR engine loaded |

The `No registered plugin EP device found for 'CUDAExecutionProvider'` warning is harmless (see CLAUDE.md §12.1).

## Results (`ocr_bench.py`)

| Config | editor full | editor dirty | terminal full | terminal dirty | exact (all inputs) |
|---|---|---|---|---|---|
| CPU default | 4908 ms | 2482 ms | 3866 ms | 2783 ms | all |
| CPU no-cls | 4250 | 2110 | 3487 | 2441 | all |
| CPU no-cls det-max-1920 | 4282 | 1522 | 3357 | 1722 | all |
| GPU default | 2084 | 319 | 1560 | 373 | all |
| GPU no-cls HEURISTIC | 1860 | 301 | 1199 | 371 | all |
| GPU no-cls HEURISTIC rec-batch-24 | 1359 | 292 | 1526 | 373 | 3/4 terminal full (CER 0.006) |
| **GPU no-cls HEURISTIC det-max-1920** | **1730** | **195** | **1232** | **253** | all |

GPU memory with the engine loaded: about 1.95 GiB for every config except rec-batch-24, which used **3.8 GiB** of the 4 GiB.

## What we learned

1. **Full frames can't meet a 500 ms deadline on this laptop**, not even on GPU (1.2–1.7 s). The live path must OCR changed regions only. Confirmed again.
2. **RapidOCR's detector upscales small inputs.** Default `Det.limit_type: min, limit_side_len: 736` scales any image whose *short* side is under 736 px up to 736. A 1920x60 strip becomes about 23000x736 before detection, which made strips nearly as slow as full frames. `limit_type: max, limit_side_len: 1920` (only ever downscale) cut GPU strip time from 300–370 ms to 195–253 ms with identical accuracy.
3. **cls (text-angle classifier) is wasted work on screens**: text is upright. Turning it off saves about 10–20%.
4. **cuDNN HEURISTIC vs EXHAUSTIVE:** small gain here. It matters more live, where every strip has a new shape and EXHAUSTIVE re-searches for each one.
5. **Recognition dominates and scales with line length.** A full-width code line becomes a very wide rec input (rec resizes to 48 px high, keeping aspect). That's why even a 6-box strip costs about 200 ms on GPU and 1.5 s on CPU. Not yet measured: the lighter `mobile` rec model (needs a model download), and cropping strips to the changed columns.
6. **CPU is not a real-time option** (1.5 s+ per strip). It's fine for rehearsal mode (Stage 3, offline).
7. **Accuracy is 100% exact here, and that is not a claim about real screens.** Clean 14 px Consolas on flat dark backgrounds is the easy case. Before trusting accuracy numbers, VEIL-Bench needs harder rendering: smaller fonts, syntax highlighting, display scaling, and video-compression artefacts.

## Recommended settings for the RTX 2050 (starting point for Stage 2)

```python
RapidOCR(
    params={
        "EngineConfig.onnxruntime.use_cuda": True,
        "EngineConfig.onnxruntime.cuda_ep_cfg.cudnn_conv_algo_search": "HEURISTIC",
        "Global.use_cls": False,
        "Det.limit_type": "max",
        "Det.limit_side_len": 1920,
        # Rec.rec_batch_num stays at the default 6: 24 nearly filled the 4 GB card
    }
)
```

- One engine per process (about 1.7 GiB of VRAM), leaving about 2 GiB of headroom.
- OCR dirty strips, not frames. About 200–250 ms per strip leaves room for one or two changed strips per frame inside 500 ms. A scroll changes everything: shift existing masks by DXGI move rects and re-verify (§5.3). Until that exists, a scroll means freeze, which the gate already does on a deadline miss.
- Deadline: 500 ms is plausible for typing and small changes. It is not plausible for scrolls or window switches without move-rect tracking. Measure in Stage 2 before fixing a number (§16).
