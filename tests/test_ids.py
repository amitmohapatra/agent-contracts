"""Identifier helpers: deterministic ids are how a retried step writes once."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest

from trellis.contracts import new_id, now, safe_id, stable_id


def test_now_is_timezone_aware_utc() -> None:
    stamp = now()
    assert stamp.tzinfo is UTC
    assert abs(stamp - datetime.now(UTC)) < timedelta(seconds=5)


def test_new_ids_are_fresh_and_keep_their_prefix() -> None:
    first, second = new_id("run_"), new_id("run_")
    assert first != second
    assert re.fullmatch(r"run_[0-9a-f]{32}", first)
    assert re.fullmatch(r"[0-9a-f]{32}", new_id())


@pytest.mark.parametrize(
    ("value", "cleaned"),
    [
        ("billing-agent_v2.1:prod", "billing-agent_v2.1:prod"),  # the alphabet is kept
        ("billing agent/v2", "billing-agent-v2"),
        ("--.edge.--", "edge"),  # leading and trailing separators are stripped
        ("", "x"),  # never empty
        ("///", "x"),
        (42, "42"),
        (None, "None"),
    ],
)
def test_safe_id_coerces_anything_into_the_shared_alphabet(value: object, cleaned: str) -> None:
    assert safe_id(value) == cleaned


def test_safe_id_is_bounded() -> None:
    assert safe_id("a" * 500) == "a" * 200
    assert safe_id("abcdef", max_len=3) == "abc"


def test_stable_id_is_deterministic_and_sensitive_to_every_part() -> None:
    key = stable_id("acme", "thr", 3)
    assert key == stable_id("acme", "thr", 3)
    assert re.fullmatch(r"[0-9a-f]{32}", key)  # 16 bytes by default
    assert key != stable_id("acme", "thr", 4)
    assert key != stable_id("acme", 3, "thr")  # order matters
    assert stable_id("ab", "c") != stable_id("a", "bc")  # parts are separated, not joined
    assert stable_id("acme", "3") == stable_id("acme", 3)  # parts are compared as text


def test_stable_id_takes_a_prefix_and_a_size() -> None:
    assert stable_id("x", prefix="fb_").startswith("fb_")
    assert len(stable_id("x", size=8)) == 16
    # None and "" both hash as empty: only their position distinguishes them from a gap
    assert stable_id(None, "a") == stable_id("", "a")
    assert stable_id(None, "a") != stable_id("a")
