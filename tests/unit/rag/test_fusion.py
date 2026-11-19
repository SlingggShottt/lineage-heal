"""FR-KB-02: reciprocal rank fusion merges rankings by position, not by score."""

import pytest

from lineageheal.rag.fusion import RRF_K, reciprocal_rank_fusion


def test_an_item_in_both_lists_beats_an_item_that_tops_only_one() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["x", "b", "y"]])

    assert fused[0][0] == "b"


def test_the_score_is_the_sum_of_one_over_k_plus_rank() -> None:
    fused = dict(reciprocal_rank_fusion([["a", "b"], ["b"]]))

    assert fused["a"] == pytest.approx(1 / (RRF_K + 1))
    assert fused["b"] == pytest.approx(1 / (RRF_K + 2) + 1 / (RRF_K + 1))


def test_every_item_from_any_list_appears_once() -> None:
    fused = reciprocal_rank_fusion([["a", "b"], ["c", "a"]])

    assert sorted(item for item, _ in fused) == ["a", "b", "c"]


def test_ties_keep_the_order_first_seen_so_results_repeat() -> None:
    first = reciprocal_rank_fusion([["a", "b"], ["b", "a"]])

    assert [item for item, _ in first] == ["a", "b"]
    assert first == reciprocal_rank_fusion([["a", "b"], ["b", "a"]])


def test_no_lists_or_empty_lists_give_nothing() -> None:
    assert reciprocal_rank_fusion([]) == []
    assert reciprocal_rank_fusion([[], []]) == []
