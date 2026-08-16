"""Validated MILP reconstruction of the retail staff-scheduling coursework.

The submitted notebook is preserved in the repository. This module fixes three
material issues found during the portfolio audit:

1. worker availability is enforced directly;
2. the daily skill rule requires at least one assigned worker whose skill meets
   the day's requirement;
3. fairness is represented by the workload range max(days worked)-min(days
   worked), rather than by an objective that algebraically collapses to total
   assignments minus a constant.

Optimization is lexicographic:
- Stage 1: minimize temporary-worker days.
- Cost scenario: among minimum-temp schedules, minimize regular labor cost.
- Fairness scenario: among minimum-temp schedules, minimize workload range;
  among equally fair schedules, minimize regular labor cost.

SciPy's MILP interface uses the HiGHS solver.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp


def load_data(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _base_problem(data: dict):
    workers = list(data["workers"])
    days = list(data["shifts_per_day"])
    n_w, n_d = len(workers), len(days)

    idx_x = {
        (w, d): i * n_d + j
        for i, w in enumerate(workers)
        for j, d in enumerate(days)
    }
    offset = n_w * n_d
    idx_extra = {d: offset + j for j, d in enumerate(days)}
    n = offset + n_d

    lower = np.zeros(n)
    upper = np.ones(n)
    integrality = np.ones(n, dtype=int)

    for d in days:
        upper[idx_extra[d]] = data["shifts_per_day"][d]["shift_limit"]

    # x[w,d] = 0 whenever the worker is unavailable.
    for w in workers:
        available = set(data["workers"][w]["availability"])
        for d in days:
            if d not in available:
                upper[idx_x[w, d]] = 0

    rows, lows, highs = [], [], []

    # Headcount requirement.
    for d in days:
        row = np.zeros(n)
        for w in workers:
            row[idx_x[w, d]] = 1
        row[idx_extra[d]] = 1
        required = data["shifts_per_day"][d]["shift_limit"]
        rows.append(row)
        lows.append(required)
        highs.append(required)

    # Maximum 3 workdays in any consecutive 4-day window.
    for w in workers:
        for start in range(n_d - 3):
            row = np.zeros(n)
            for j in range(start, start + 4):
                row[idx_x[w, days[j]]] = 1
            rows.append(row)
            lows.append(-np.inf)
            highs.append(3)

    # At least one assigned regular worker meets the day's skill requirement.
    for d in days:
        required_skill = data["shifts_per_day"][d]["skill_required"]
        qualified = [
            w
            for w in workers
            if data["workers"][w]["skill_level"] >= required_skill
        ]
        if not qualified:
            raise ValueError(f"No worker can satisfy the skill requirement for {d}.")
        row = np.zeros(n)
        for w in qualified:
            row[idx_x[w, d]] = 1
        rows.append(row)
        lows.append(1)
        highs.append(np.inf)

    return (
        workers,
        days,
        idx_x,
        idx_extra,
        lower,
        upper,
        integrality,
        rows,
        lows,
        highs,
    )


def _solve(c, integrality, lower, upper, rows, lows, highs):
    result = milp(
        c,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(np.vstack(rows), lows, highs),
    )
    if not result.success:
        raise RuntimeError(result.message)
    return result


def solve(data: dict, scenario: str = "cost") -> dict:
    (
        workers,
        days,
        idx_x,
        idx_extra,
        lower,
        upper,
        integrality,
        rows,
        lows,
        highs,
    ) = _base_problem(data)

    n = len(lower)

    # Stage 1: minimum temporary-worker days.
    c_temp = np.zeros(n)
    for d in days:
        c_temp[idx_extra[d]] = 1
    stage1 = _solve(c_temp, integrality, lower, upper, rows, lows, highs)
    min_temp = int(round(stage1.fun))

    # Fix the stage-1 optimum for subsequent comparisons.
    temp_row = np.zeros(n)
    for d in days:
        temp_row[idx_extra[d]] = 1
    rows2, lows2, highs2 = list(rows), list(lows), list(highs)
    rows2.append(temp_row)
    lows2.append(min_temp)
    highs2.append(min_temp)

    if scenario == "cost":
        c = np.zeros(n)
        for w in workers:
            salary = data["workers"][w]["salary"]
            for d in days:
                c[idx_x[w, d]] = salary
        result = _solve(c, integrality, lower, upper, rows2, lows2, highs2)
        vector = result.x

    elif scenario == "fairness":
        # Add integer maximum/minimum workload variables.
        idx_max, idx_min = n, n + 1
        lower_f = np.r_[lower, 0, 0]
        upper_f = np.r_[upper, len(days), len(days)]
        integrality_f = np.r_[integrality, 1, 1]

        rows_f, lows_f, highs_f = [], [], []
        for row, lo, hi in zip(rows2, lows2, highs2):
            extended = np.zeros(n + 2)
            extended[:n] = row
            rows_f.append(extended)
            lows_f.append(lo)
            highs_f.append(hi)

        for w in workers:
            # workload_w <= max_workload
            row = np.zeros(n + 2)
            for d in days:
                row[idx_x[w, d]] = 1
            row[idx_max] = -1
            rows_f.append(row)
            lows_f.append(-np.inf)
            highs_f.append(0)

            # min_workload <= workload_w
            row = np.zeros(n + 2)
            row[idx_min] = 1
            for d in days:
                row[idx_x[w, d]] -= 1
            rows_f.append(row)
            lows_f.append(-np.inf)
            highs_f.append(0)

        c_range = np.zeros(n + 2)
        c_range[idx_max] = 1
        c_range[idx_min] = -1
        stage_fair = _solve(
            c_range, integrality_f, lower_f, upper_f, rows_f, lows_f, highs_f
        )
        optimal_range = int(round(stage_fair.fun))

        # Among equally fair schedules, minimize labor cost.
        row = np.zeros(n + 2)
        row[idx_max] = 1
        row[idx_min] = -1
        rows_f.append(row)
        lows_f.append(-np.inf)
        highs_f.append(optimal_range)

        c_cost = np.zeros(n + 2)
        for w in workers:
            salary = data["workers"][w]["salary"]
            for d in days:
                c_cost[idx_x[w, d]] = salary

        result = _solve(
            c_cost, integrality_f, lower_f, upper_f, rows_f, lows_f, highs_f
        )
        vector = result.x[:n]

    else:
        raise ValueError("scenario must be 'cost' or 'fairness'")

    schedule = pd.DataFrame(0, index=workers, columns=days, dtype=int)
    for w in workers:
        for d in days:
            schedule.loc[w, d] = int(round(vector[idx_x[w, d]]))

    extras = pd.Series(
        {d: int(round(vector[idx_extra[d]])) for d in days},
        name="temporary_workers",
    )
    workload = schedule.sum(axis=1).astype(int)
    labor_cost = int(
        sum(workload[w] * data["workers"][w]["salary"] for w in workers)
    )

    # Validation checks.
    for d in days:
        assert (
            schedule[d].sum() + extras[d]
            == data["shifts_per_day"][d]["shift_limit"]
        )
        required_skill = data["shifts_per_day"][d]["skill_required"]
        assert any(
            schedule.loc[w, d] == 1
            and data["workers"][w]["skill_level"] >= required_skill
            for w in workers
        )
    for w in workers:
        available = set(data["workers"][w]["availability"])
        assert all(schedule.loc[w, d] == 0 for d in days if d not in available)
        for start in range(len(days) - 3):
            assert schedule.loc[w, days[start : start + 4]].sum() <= 3

    return {
        "scenario": scenario,
        "minimum_temporary_worker_days": min_temp,
        "labor_cost": labor_cost,
        "workload_range": int(workload.max() - workload.min()),
        "workload": workload,
        "schedule": schedule,
        "temporary_workers": extras,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="Data.json")
    parser.add_argument("--scenario", choices=["cost", "fairness"], default="cost")
    parser.add_argument("--output-dir", default="results/generated")
    args = parser.parse_args()

    result = solve(load_data(args.data), args.scenario)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["schedule"].to_csv(out / f"{args.scenario}_schedule.csv")
    result["workload"].rename("days_worked").to_csv(
        out / f"{args.scenario}_workload.csv", header=True
    )
    result["temporary_workers"].to_csv(
        out / f"{args.scenario}_temporary_workers.csv", header=True
    )

    print(
        {
            "scenario": result["scenario"],
            "minimum_temporary_worker_days": result[
                "minimum_temporary_worker_days"
            ],
            "labor_cost": result["labor_cost"],
            "workload_range": result["workload_range"],
        }
    )


if __name__ == "__main__":
    main()
