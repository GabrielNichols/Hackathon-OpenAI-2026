"""Deterministic quote scoring and ordering."""

from .scoring import ScoreComponent, ScoreResult, score_quote, score_quotes

__all__ = ["ScoreComponent", "ScoreResult", "score_quote", "score_quotes"]
