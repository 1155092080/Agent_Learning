"""一个故意带内存泄漏的玩具服务（练习用）。"""

_cache = []


def handle_request(request_id: int) -> int:
    data = bytearray(10_000)  # 每个请求分配约 10KB
    _cache.append(data)       # BUG：放进全局列表后永远不释放 -> 内存只增不减
    return len(data)


def main(n: int = 2000) -> None:
    for i in range(n):
        handle_request(i)


if __name__ == "__main__":
    main()
