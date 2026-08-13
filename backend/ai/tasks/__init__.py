"""Structured AI tasks.

Each module here is a single-purpose function: build a prompt from real
structured data (never invented), call an AIAdapter, parse the response into
a typed Pydantic result, and provide an explicit ABSTAIN path when evidence
is insufficient. See docs/ai-governance.md for the rules these enforce.
"""
