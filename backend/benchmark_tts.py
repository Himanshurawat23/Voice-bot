#!/usr/bin/env python3
"""
TTS Latency Benchmark
=====================
Measures time-to-first-chunk (TTFC), total synthesis time, and real-time factor (RTF)
for three CPU-friendly TTS models:
  1. PocketTTS   (already installed)
  2. Kokoro-82M  (pip install kokoro soundfile)
  3. Chatterbox-Nano (pip install chatterbox-tts)

Usage:
    python benchmark_tts.py                    # Run all available models
    python benchmark_tts.py --models pocket    # Run PocketTTS only
    python benchmark_tts.py --models kokoro    # Run Kokoro only
    python benchmark_tts.py --models chatterbox  # Run Chatterbox-Nano only
    python benchmark_tts.py --models pocket kokoro chatterbox  # Run all
    python benchmark_tts.py --runs 5           # 5 runs per sentence (default=3)
"""

import argparse
import time
import sys
import io
import json
import importlib
from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np

# ──────────────────────────── Constants ────────────────────────────
SAMPLE_RATE = 24000  # All three models output 24 kHz

TEST_SENTENCES = [
    "Hello, how are you doing today?",
    "The quick brown fox jumps over the lazy dog.",
    "Artificial intelligence is transforming the way we interact with technology.",
    "Welcome to our voice assistant. How can I help you?",
    "Namaste, aaj ka mausam bahut accha hai.",  # Hindi test
]

# ──────────────────────────── Result DTO ────────────────────────────
@dataclass
class BenchmarkResult:
    model: str
    sentence: str
    run: int
    load_time_s: float  # model load time (only first run)
    ttfc_ms: float      # time to first audio chunk (ms)
    total_time_ms: float  # total synthesis time (ms)
    audio_duration_s: float  # duration of generated audio
    rtf: float          # Real-Time Factor (lower is better; <1 means faster than real-time)
    error: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════
#  MODEL RUNNERS
# ═══════════════════════════════════════════════════════════════════

# ─────────────── 1. PocketTTS ───────────────
def run_pocket_tts(sentence: str) -> dict:
    """Benchmark PocketTTS (already installed in venv)."""
    from pocket_tts import PocketTTS

    t_start = time.perf_counter()
    model = PocketTTS()
    load_time = time.perf_counter() - t_start

    ttfc = None
    chunks = []

    t_synth_start = time.perf_counter()
    for chunk in model.tts_stream(sentence, voice="af_heart"):
        if ttfc is None:
            ttfc = (time.perf_counter() - t_synth_start) * 1000
        if isinstance(chunk, np.ndarray):
            chunks.append(chunk)
        elif hasattr(chunk, 'numpy'):
            chunks.append(chunk.numpy())
        else:
            chunks.append(np.frombuffer(chunk, dtype=np.float32))
    total_time = (time.perf_counter() - t_synth_start) * 1000

    audio = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
    duration = len(audio) / SAMPLE_RATE
    rtf = (total_time / 1000) / duration if duration > 0 else float('inf')

    return {
        "load_time_s": load_time,
        "ttfc_ms": ttfc or 0,
        "total_time_ms": total_time,
        "audio_duration_s": duration,
        "rtf": rtf,
    }


# ─────────────── 2. Kokoro-82M ───────────────
def run_kokoro(sentence: str) -> dict:
    """Benchmark Kokoro-82M via the official `kokoro` library."""
    # pyrefly: ignore [missing-import]
    from kokoro import KPipeline

    t_start = time.perf_counter()
    pipeline = KPipeline(lang_code='a')  # American English
    load_time = time.perf_counter() - t_start

    ttfc = None
    chunks = []

    t_synth_start = time.perf_counter()
    for _gs, _ps, audio in pipeline(sentence, voice='af_heart', speed=1.0):
        if ttfc is None:
            ttfc = (time.perf_counter() - t_synth_start) * 1000
        if isinstance(audio, np.ndarray):
            chunks.append(audio)
        else:
            chunks.append(np.array(audio, dtype=np.float32))
    total_time = (time.perf_counter() - t_synth_start) * 1000

    audio = np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)
    duration = len(audio) / SAMPLE_RATE
    rtf = (total_time / 1000) / duration if duration > 0 else float('inf')

    return {
        "load_time_s": load_time,
        "ttfc_ms": ttfc or 0,
        "total_time_ms": total_time,
        "audio_duration_s": duration,
        "rtf": rtf,
    }


# ─────────────── 3. Chatterbox-Nano ───────────────
def run_chatterbox(sentence: str) -> dict:
    """Benchmark Chatterbox-Nano on CPU."""
    import torch

    # Monkey-patch: resemble-perth has no ARM macOS bindings,
    # so PerthImplicitWatermarker is None. Replace with DummyWatermarker.
    import perth
    if perth.PerthImplicitWatermarker is None:
        perth.PerthImplicitWatermarker = perth.DummyWatermarker

    # pyrefly: ignore [missing-import]
    from chatterbox.tts import ChatterboxTTS

    t_start = time.perf_counter()
    model = ChatterboxTTS.from_pretrained(device="cpu")
    load_time = time.perf_counter() - t_start

    ttfc = None

    t_synth_start = time.perf_counter()
    with torch.no_grad():
        wav = model.generate(sentence)
    total_time = (time.perf_counter() - t_synth_start) * 1000
    ttfc = total_time  # Chatterbox doesn't stream, TTFC == total

    if isinstance(wav, torch.Tensor):
        audio = wav.squeeze().cpu().numpy()
    else:
        audio = np.array(wav, dtype=np.float32)

    sr = getattr(model, 'sr', SAMPLE_RATE)
    duration = len(audio) / sr
    rtf = (total_time / 1000) / duration if duration > 0 else float('inf')

    return {
        "load_time_s": load_time,
        "ttfc_ms": ttfc,
        "total_time_ms": total_time,
        "audio_duration_s": duration,
        "rtf": rtf,
    }


# ═══════════════════════════════════════════════════════════════════
#  REGISTRY
# ═══════════════════════════════════════════════════════════════════
MODEL_REGISTRY = {
    "pocket": {
        "name": "PocketTTS",
        "runner": run_pocket_tts,
        "check": lambda: importlib.import_module("pocket_tts"),
    },
    "kokoro": {
        "name": "Kokoro-82M",
        "runner": run_kokoro,
        "check": lambda: importlib.import_module("kokoro"),
    },
    "chatterbox": {
        "name": "Chatterbox-Nano",
        "runner": run_chatterbox,
        "check": lambda: importlib.import_module("chatterbox"),
    },
}


# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="TTS Latency Benchmark")
    parser.add_argument(
        "--models", nargs="+", default=list(MODEL_REGISTRY.keys()),
        choices=list(MODEL_REGISTRY.keys()),
        help="Models to benchmark",
    )
    parser.add_argument("--runs", type=int, default=3, help="Runs per sentence (default 3)")
    parser.add_argument("--output", type=str, default=None, help="Save JSON results to file")
    args = parser.parse_args()

    # ── Check availability ──
    available = {}
    for key in args.models:
        entry = MODEL_REGISTRY[key]
        try:
            entry["check"]()
            available[key] = entry
            print(f"  ✅  {entry['name']} — available")
        except ImportError:
            print(f"  ❌  {entry['name']} — NOT INSTALLED (skipping)")

    if not available:
        print("\n⚠️  No models available to benchmark. Install at least one:")
        print("   pip install pocket-tts")
        print("   pip install kokoro soundfile")
        print("   pip install chatterbox-tts")
        sys.exit(1)

    results: list[BenchmarkResult] = []

    for key, entry in available.items():
        name = entry["name"]
        runner = entry["runner"]
        print(f"\n{'='*60}")
        print(f"  Benchmarking: {name}")
        print(f"{'='*60}")

        for si, sentence in enumerate(TEST_SENTENCES):
            short = sentence[:50] + ("..." if len(sentence) > 50 else "")
            for run_i in range(args.runs):
                sys.stdout.write(f"  [{si+1}/{len(TEST_SENTENCES)}] run {run_i+1}/{args.runs} — {short}  ")
                sys.stdout.flush()

                try:
                    metrics = runner(sentence)
                    r = BenchmarkResult(
                        model=name,
                        sentence=sentence,
                        run=run_i + 1,
                        **metrics,
                    )
                    results.append(r)
                    print(
                        f"TTFC={r.ttfc_ms:.0f}ms  "
                        f"Total={r.total_time_ms:.0f}ms  "
                        f"Audio={r.audio_duration_s:.2f}s  "
                        f"RTF={r.rtf:.3f}"
                    )
                except Exception as e:
                    results.append(BenchmarkResult(
                        model=name, sentence=sentence, run=run_i + 1,
                        load_time_s=0, ttfc_ms=0, total_time_ms=0,
                        audio_duration_s=0, rtf=0, error=str(e),
                    ))
                    print(f"ERROR: {e}")

    # ── Summary table ──
    print(f"\n\n{'='*80}")
    print("  SUMMARY (averaged across all sentences & runs)")
    print(f"{'='*80}")
    print(f"  {'Model':<20} {'Avg TTFC':>10} {'Avg Total':>12} {'Avg RTF':>10} {'Runs':>6}")
    print(f"  {'-'*20} {'-'*10} {'-'*12} {'-'*10} {'-'*6}")

    for key in available:
        name = MODEL_REGISTRY[key]["name"]
        model_results = [r for r in results if r.model == name and r.error is None]
        if not model_results:
            print(f"  {name:<20} {'—':>10} {'—':>12} {'—':>10} {'0':>6}")
            continue
        avg_ttfc = sum(r.ttfc_ms for r in model_results) / len(model_results)
        avg_total = sum(r.total_time_ms for r in model_results) / len(model_results)
        avg_rtf = sum(r.rtf for r in model_results) / len(model_results)
        print(
            f"  {name:<20} {avg_ttfc:>8.0f}ms {avg_total:>10.0f}ms {avg_rtf:>10.3f} {len(model_results):>6}"
        )

    print(f"\n  RTF < 1.0 = faster than real-time (good)")
    print(f"  TTFC = time to first audio chunk (lower = more responsive)\n")

    # ── Save JSON ──
    if args.output:
        with open(args.output, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
        print(f"  💾 Results saved to {args.output}")


if __name__ == "__main__":
    main()
