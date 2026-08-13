"""2D sensitivity matrices for the valuation engine.

Pure, deterministic, no AI/I/O: builds a grid by re-running an existing
valuation function (dcf.run_dcf, comps.run_comps, ...) once per (row, col)
combination with two of its inputs swapped out, and reads off a chosen
scalar output field. Generic over the underlying valuation function so it
works for e.g. WACC-vs-terminal-growth DCF sensitivity or
EBITDA-margin-vs-multiple comps sensitivity.
"""

from __future__ import annotations

from typing import Any, Callable

from backend.schemas.valuation import SensitivityInput, SensitivityResult


def build_sensitivity_matrix(
    base_kwargs: dict[str, Any],
    row_field: str,
    row_values: list[float],
    col_field: str,
    col_values: list[float],
    run_fn: Callable[..., Any],
    output_field: str,
) -> SensitivityResult:
    """Re-run ``run_fn(**{**base_kwargs, row_field: r, col_field: c})`` for
    every (r, c) pair in the grid and collect ``getattr(result, output_field)``.

    ``run_fn`` is expected to be one of the pure valuation entrypoints (e.g.
    ``backend.valuation.dcf.run_dcf`` given a ``DcfInput(**kwargs)`` builder,
    or any callable with the same shape). To keep this module decoupled from
    any one schema, ``base_kwargs`` must be plain kwargs for constructing the
    input object; callers pass a small lambda as ``run_fn`` that builds the
    typed input and calls the underlying valuation function. See tests for
    the exact usage pattern.
    """
    matrix: list[list[float]] = []
    for r in row_values:
        row_out: list[float] = []
        for c in col_values:
            kwargs = dict(base_kwargs)
            kwargs[row_field] = r
            kwargs[col_field] = c
            result = run_fn(**kwargs)
            row_out.append(float(getattr(result, output_field)))
        matrix.append(row_out)

    return SensitivityResult(
        row_label=row_field,
        row_values=list(row_values),
        col_label=col_field,
        col_values=list(col_values),
        matrix=matrix,
    )


def dcf_sensitivity(
    base_dcf_input_kwargs: dict[str, Any],
    row_field: str,
    row_values: list[float],
    col_field: str,
    col_values: list[float],
    output_field: str = "implied_price_per_share",
) -> SensitivityResult:
    """Convenience wrapper: DCF sensitivity (e.g. wacc vs terminal_growth_rate)."""
    from backend.schemas.valuation import DcfInput
    from backend.valuation.dcf import run_dcf

    def _run(**kwargs: Any):
        return run_dcf(DcfInput(**kwargs))

    return build_sensitivity_matrix(base_dcf_input_kwargs, row_field, row_values, col_field, col_values, _run, output_field)


def comps_sensitivity(
    base_comps_input_kwargs: dict[str, Any],
    row_field: str,
    row_values: list[float],
    col_field: str,
    col_values: list[float],
    output_field: str = "implied_price_per_share",
) -> SensitivityResult:
    """Convenience wrapper: comps sensitivity (e.g. target_ebitda vs chosen_multiple_value)."""
    from backend.schemas.valuation import CompsInput
    from backend.valuation.comps import run_comps

    def _run(**kwargs: Any):
        return run_comps(CompsInput(**kwargs))

    return build_sensitivity_matrix(base_comps_input_kwargs, row_field, row_values, col_field, col_values, _run, output_field)
