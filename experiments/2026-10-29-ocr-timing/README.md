# 2026-10-29 — RapidOCR timing, GPU vs CPU

## Setup

- Laptop: RTX 2050 4 GB (driver 581.83), i5-13420H, Windows 11.
- `rapidocr` 3.10.0 with its default PP-OCRv6 det/cls/rec ONNX models, `onnxruntime-gpu` 1.31.1 (CUDA 13 build), cuDNN 9.27, cuBLAS 13.8.
- Input: one synthetic 1920x1080 dark-theme editor frame (`ocr_timing.py`), with 3 random fake secrets in `.env`-style lines. The whole frame goes through det, cls and rec. There's no tiling or dirty-region cropping.
- 3 warm-up runs, then 20 timed runs per engine. Same frame every run.
- Run: `.venv\Scripts\python experiments\2026-10-29-ocr-timing\ocr_timing.py`

## Results

| Engine | Median | p95 | Boxes | Exact secret reads |
|---|---|---|---|---|
| GPU (CUDA EP) | 2994 ms | 3146 ms | 98 | 2/3 |
| CPU | 28813 ms | 30795 ms | 98 | 2/3 |

The CPU row is contended: another CPU-heavy process (a bench validation script) ran during part of the measurement. Re-measure on an idle machine before quoting it.

GPU per-stage breakdown (separate run, 10 timed runs, medians from RapidOCR's `elapse_list`):

| `cudnn_conv_algo_search` | Total | det | cls | rec |
|---|---|---|---|---|
| EXHAUSTIVE (RapidOCR default) | 3221 ms | 375 ms | 572 ms | 2251 ms |
| HEURISTIC | 2828 ms | 342 ms | 512 ms | 1997 ms |

All three sessions report `['CUDAExecutionProvider', 'CPUExecutionProvider']`, and the process appears in `nvidia-smi` as a compute app.

## Conclusion

- The GPU is about 10x faster than the (contended) CPU on a full 1080p frame. Both are far over a ~500 ms per-frame deadline when the whole frame is OCR'd. Dirty-region OCR (CLAUDE.md §5.3) is required, not an optimisation.
- Recognition dominates: about 70% of GPU time for 98 text boxes. Next leads are the rec batch size (`Rec.rec_batch_num`, default 6), skipping cls (screen text is upright), and CPU-side crop and pre-processing cost. None of these has been measured yet.
- HEURISTIC cuDNN search is about 12% faster here. It should matter more in the real pipeline, where every dirty region has a new input shape and EXHAUSTIVE re-runs its search for each new shape.
- One fake secret was not read exactly by either engine. This is a single frame, so it's no basis for an accuracy claim; VEIL-Bench measures accuracy.
