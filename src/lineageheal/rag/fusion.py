"""Reciprocal rank fusion: merging several ranked lists into one (FR-KB-02).

Vector distances and full-text scores are on unrelated scales, so adding them would let one
side drown the other. Fusion uses only each item's position in each list: an item scores
1 / (k + rank) per list, summed. Appearing near the top of both lists wins; appearing high in
just one still counts.
"""

from collections.abc import Hashable, Sequence

# 60 is the constant from the original paper; it flattens the gap between the first few ranks
# so one list's top place cannot outweigh two lists' middling ones.
RRF_K = 60


def reciprocal_rank_fusion[T: Hashable](
    rankings: Sequence[Sequence[T]], *, k: int = RRF_K
) -> list[tuple[T, float]]:
    """Merge ranked lists, best first, each item with its fused score.

    Args:
        rankings: Each list ordered best first. An item may appear in some lists and not others.
        k: The rank-smoothing constant.

    Returns:
        Every item that appears anywhere, highest score first. Ties keep the order in which the
        items were first seen, so the result is the same on every run.
    """
    scores: dict[T, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda pair: -pair[1])
