# Model audit

This note records the distinction between the original MSc coursework model and the validated portfolio reconstruction.

## Source material

The repository's original notebook is **Retail Store Staff Scheduling.ipynb**. The final coursework input recovered from Google Drive defines 21 planning days, seven workers, daily staffing levels of 2–6 workers, daily skill thresholds of 2–4, worker-specific salary values of 8–12, and skill levels of 2–4.

The previous GitHub `Data.json` did not match the final Drive coursework input. It is preserved at `coursework/original_github_Data.json`; the root `Data.json` now matches the final recovered coursework file.

## Issues found

### 1. Availability

The submitted code creates worker-day decision variables across the full Cartesian product. It does not impose a constraint of the form `x[w,d] = 0` when a worker is unavailable.

The recovered final coursework input happens to list every worker as available on every day, so this omission does not alter the validated solution for this specific dataset. It is still a modelling defect because the model would ignore unavailable days if the input changed.

### 2. Skill constraint

The coursework constraint aggregates skill values across assigned workers. A condition such as

`sum(skill[w] * x[w,d]) >= required_skill[d]`

does not imply that at least one assigned worker individually satisfies the required skill threshold.

The reconstruction uses

`sum(x[w,d] for w if skill[w] >= required_skill[d]) >= 1`.

### 3. Fairness objective

The coursework fairness expression subtracts the same average constant from each worker's total assignments and then sums over workers. Algebraically, this is equivalent to total assignments minus a constant. It therefore does not penalize unequal workloads.

The reconstruction defines fairness as the workload range:

`max_w L[w] - min_w L[w]`, where `L[w] = sum_d x[w,d]`.

### 4. Multi-objective logic

The original notebook mixes temporary-worker usage and regular labor cost and subsequently fixes temporary-worker use through an additional step. The reconstruction makes the priority explicit through lexicographic optimization:

1. minimize temporary-worker days;
2. conditional on the optimum, optimize cost or fairness;
3. for the fairness scenario, minimize regular labor cost among schedules with the minimum workload range.

## Validated results

Both corrected scenarios require zero temporary-worker days.

- Cost priority: regular labor cost = **685**, workload range = **12**.
- Fairness priority: regular labor cost = **725**, workload range = **1**.

The results were independently checked against the daily staffing constraints, skill constraints, four-day rest windows and availability restrictions implemented in the reconstruction.

## Provenance rule

The original notebook remains unchanged as historical coursework. Portfolio-facing claims and results are based only on `src/solve_staff_schedule.py` and the committed validated output tables.
