"""候选池管理工具：去重、筛选、构建剩余候选池。"""

from typing import List, TypeVar, Dict

from ...models.schemas import POI, Hotel
from ...utils.llm_json import parse_selected_indices


T = TypeVar('T', POI, Hotel)


def dedupe_key(item: T) -> str:
    """生成由名称和地址组成的去重键。"""
    return f"{item.name}|{item.address}"


def build_remaining_pool(
    candidates: List[T],
    selected: List[T]
) -> List[T]:
    """从候选列表中移除已选项，构建剩余候选池。"""
    selected_keys = {dedupe_key(item) for item in selected}
    return [item for item in candidates if dedupe_key(item) not in selected_keys]


def select_candidates(
    candidates: List[T],
    llm_response: str,
    fallback_limit: int,
) -> tuple[List[T], List[T]]:
    """按 LLM 返回的 1 起始下标选出候选，返回 (已选, 剩余池)。

    响应无法解析或没有有效下标时，退回到前 ``fallback_limit`` 个。
    """
    indices = parse_selected_indices(
        llm_response, max_index=len(candidates), fallback_limit=fallback_limit,
    )
    selected = [candidates[i - 1] for i in indices]
    return selected, build_remaining_pool(candidates, selected)


def build_balanced_candidates(
    items_by_keyword: Dict[str, List[T]],
    max_total: int = 25,
    per_keyword: int = 5
) -> List[T]:
    """从多个关键词的搜索结果中均衡构造候选池。

    避免单一关键词占满候选池，确保多样性。

    Args:
        items_by_keyword: 按关键词分组的搜索结果
        max_total: 候选池最大总数
        per_keyword: 每个关键词最多取多少个

    Returns:
        均衡的候选列表
    """
    candidates = []
    seen_keys = set()

    # 第一轮：每个关键词取 per_keyword 个
    for items in items_by_keyword.values():
        taken = 0
        for item in items:
            key = dedupe_key(item)
            if key not in seen_keys:
                candidates.append(item)
                seen_keys.add(key)
                taken += 1
                if taken >= per_keyword:
                    break

    # 第二轮：轮询补位，避免靠前的关键词独占剩余容量。
    next_indexes = {keyword: per_keyword for keyword in items_by_keyword}
    while len(candidates) < max_total:
        added = False
        for keyword, items in items_by_keyword.items():
            index = next_indexes[keyword]
            while index < len(items):
                item = items[index]
                index += 1
                key = dedupe_key(item)
                if key not in seen_keys:
                    candidates.append(item)
                    seen_keys.add(key)
                    added = True
                    break
            next_indexes[keyword] = index
            if len(candidates) >= max_total:
                break
        if not added:
            break

    return candidates[:max_total]
