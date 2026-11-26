import tracemalloc

RETAIN = True
BATCHES = 4
SAMPLES = 32
SAMPLE_BYTES = 4096


def process_batch(retained: list[bytearray]) -> None:
    for _ in range(SAMPLES):
        sample = bytearray(SAMPLE_BYTES)
        if RETAIN:
            retained.append(sample)


def main() -> int:
    retained: list[bytearray] = []
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    for _ in range(BATCHES):
        process_batch(retained)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    growth = current - before
    leaky = growth > BATCHES * SAMPLES * SAMPLE_BYTES // 2
    print(f"retained={len(retained)} growth_bytes={growth} peak_bytes={peak}")
    print("LEAK" if leaky else "OK")
    return 10 if leaky else 0


if __name__ == "__main__":
    raise SystemExit(main())
