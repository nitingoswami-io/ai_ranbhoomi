"""Pipeline stage 2 — writer.

Turns one trend-radar `TrendingResult` (JSON on stdin or via --input) into
a `DraftBundle`: one Draft per non-suppressed topic. The Draft is what
the renderer stage consumes downstream.
"""
__version__ = "0.1.0"
