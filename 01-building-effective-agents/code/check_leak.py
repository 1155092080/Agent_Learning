"""泄漏检测器：用 tracemalloc 比较两段负载前后的内存，输出“地面真相”。

用法: python check_leak.py <要检测的 leaky_app.py 的路径>
"""
import importlib.util
import sys
import tracemalloc

THRESHOLD_BYTES = 1_000_000  # 额外跑 2000 个请求后增长超过 1MB 视为泄漏


def load(path):
    spec = importlib.util.spec_from_file_location("target_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    app = load(sys.argv[1])
    tracemalloc.start()
    app.main(500)  # 预热
    before = tracemalloc.take_snapshot()
    app.main(2000)
    after = tracemalloc.take_snapshot()
    growth = sum(s.size_diff for s in after.compare_to(before, "lineno"))
    print(f"内存增长: {growth / 1024:.1f} KB (阈值 {THRESHOLD_BYTES / 1024:.0f} KB)")
    print("增长最多的代码行:")
    for stat in after.compare_to(before, "lineno")[:3]:
        print("  ", stat)
    print("结果: LEAK" if growth > THRESHOLD_BYTES else "结果: PASS")


if __name__ == "__main__":
    main()
