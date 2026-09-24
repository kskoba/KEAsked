# Weighted soft-preference ranking — design discussion (not started)

**Status**: early design thinking only, 2026-09-23. Explicitly not to be built yet — this is a "sit with it" document, not an implementation plan. Triggered by Aref Yeung's real ranked-preference list (below), sent while discussing having each roster member rank their soft preferences by importance.

Aref's actual list, verbatim:

> 1) Paired nights or none at all. 4pts
> 2) NE over RAZ (but I'll do RAZ if needed) 3pts
> 3) Moving forward in time if possible but not backwards (prefer 1200 to 1500 (like this!) more then 1200 to 1200 (OK with this), definitely don't love 1800 to 1700 (Barf) 2pts
> 4) Singles or double, 3SIAR if needed. 1pt

## The core tension: weighting is not the same as guaranteeing

This connects directly to the RScheirer anchor-fulfillment bug from 2026-09-22 (see `kea-oct2026-scheduling-rules` project memory / git history around commit `4a6bb27`). A flat, or weighted, **sum** objective is fairness-blind at the individual level — it optimizes the *aggregate*, not any one physician's outcome. Scaling a term's coefficient by a stated "7/10" doesn't change that structurally; it only changes how much that one term contributes to the same shared pot everything else gets summed into.

**Does a 7/10 always beat a 2/10?** Not with a naive weighted sum. A 7 can still lose to a *combination* of other terms stacked against it in the same giant objective (other physicians' preferences, fill-rate, group-balance, every existing soft rule), especially since those terms currently span wildly different numeric ranges — fill bonuses run ~1000-1500, existing soft rules ~15-40 (`_ANCHOR_FULFILLMENT_BONUS = 90`, `_SWING_PENALTY = 35`, `_WEEKEND_CLUMP_PENALTY = 15`, etc. — see `generator_cpsat.py`). A "7" only means something relative to everything else it's competing with in the sum; on its own it's a bigger nudge, not a promise.

A real guarantee ("7 reliably wins a head-to-head conflict with a 2") needs something structurally different — the same lexicographic-tier technique built for the anchor-floor fix: process claims in priority order, lock in the winner, move to the next. That's architecturally heavier than a coefficient tweak, and it raises a question with no purely mathematical answer: is physician A's 7 on rule X worth more than physician B's 6 on rule Y? That's a policy call, not a solver design choice.

## Two categories of preference — only one can be made "intrinsic"

**(A) Personal-structure preferences** — about the shape of *your own* month: sequence pattern, rest, clustering, call-linkage. These genuinely can be close to intrinsic. Weighting them mostly tunes how hard the solver works for that one person's own pattern, without directly taking something scarce from anyone else — aside from the ordinary fact that any shift given to you is a shift not given to someone else that day, which is true regardless of weighting.

**(B) Shared-resource preferences** — *which* site, *which* anchor type, *which* specific weekday. These **cannot** be made intrinsic — not a solver limitation, arithmetic. There's a fixed monthly supply of (say) NE shifts; a higher weight pulling more NE toward one physician is mechanically fewer NE shifts for whoever else wanted them. Weighting can only decide *how* that trade-off resolves (statistical tilt vs. hard priority-order vs. a fairness ceiling on any one person), never *whether* it happens.

## Aref's list, mapped

| # | Preference | Category | Notes |
|---|---|---|---|
| 1 | Paired nights or none (4pts) | (A) personal structure | Possibly **already free** — the existing hard rule (`HC-13b` in `generator_cpsat.py`) already requires "every night has an adjacent night" as the *default* for anyone without `prefer_singleton_nights` set. His 4 points might really mean "never let this get overridden under pressure," not something wholly new. Worth checking his actual roster flags before assuming this needs new machinery. |
| 2 | NE over RAZ, will do RAZ if needed (3pts) | (B) shared resource | The "I'll do X if needed" framing matters — a soft *ranked* preference between two things he's willing to do either way, not a hard exclusion. Maps to a weighted bonus term (reward NE over RAZ), not `forbidden_sites`. `RAZ` as a site code hasn't been resolved to an exact `(time, site)` pair — seen in other physicians' free text too (Keyes, Edgecumbe) but never pinned down; would need that before implementing. |
| 3 | Forward-cascading, graded, not backward (2pts) | (A) personal structure | Needs a genuinely new mechanism. The existing swing-penalty (`_SWING_PENALTY`, `_SHIFT_SWING_MAX_HOURS`) is direction-agnostic and only fires on *large* jumps — doesn't distinguish forward/same/backward, and doesn't grade by degree the way he's describing (prefers *forward more than* same, actively dislikes backward). |
| 4 | Singles/doubles, 3 SIAR acceptable ceiling (1pt) | (A) personal structure | Mostly already covered by his own `max_consecutive_shifts` cap (confirm it's actually 3). The residual ask — discourage *habitually* hitting his own ceiling even though it's allowed — is narrow and new, distinct from the existing flat `run_penalty_terms` (currently only fires at 4+ regardless of the physician's own cap). |

Three of his four are personal-structure, only #2 is genuinely shared-resource. Worth checking whether that pattern holds across the roster generally — if most physicians' real priorities turn out to be mostly about their own sequence rather than contested site/anchor allocation, that changes how urgent the harder (B)-category problem actually is.

## Suggested next thinking, not a plan

1. **Category (A) first.** No cross-physician fairness mechanism needed at all — it doesn't compete for a scarce resource. Mechanically the easiest: same pattern already used for every soft rule in `generator_cpsat.py`, coefficient scaled by `points / 10` instead of a fixed constant. Would cover 3 of Aref's 4 asks on its own.

2. **Defer category (B)** until there's an actual answer to "does a higher number always win" — not really a technical question, a values question about how a contested resource gets allocated between people who both want it.

3. **sked's existing 10-point survey budget** (`SURVEY_POINTS_BUDGET`, `SURVEY_MAX_WEIGHTED_REQUESTS` in `src/lib/survey.ts`) is *ipsative* — it forces trade-offs within one person's own list, making their own ranking meaningful. It does not by itself make weights comparable *across* physicians. A 7 from someone who rarely asks for anything and a 7 from someone who always spends their full budget might not mean the same thing, and nothing today distinguishes them.

4. **Different feature from the row-6 per-day parsing** already on the roadmap (`docs/feature-roadmap.md` item 1) — that's *which day* a preference applies to; this is *how strongly* a preference type matters. Complementary, keep scoped separately.

## Open questions to resolve before any implementation

- What does a stated weight actually promise, concretely? (Tilt vs. guarantee vs. something in between.)
- If guarantee-style (lexicographic), how are weights compared *across* physicians and *across different rule types* — is "7 on NE-preference" commensurable with "6 on forward-cascading"?
- Should there be a per-physician ceiling so one heavily-weighted preference set can't dominate the objective at the expense of everyone else's fill rate / other soft terms?
- Resolve `RAZ` and any other informal site/shift vernacular to real `(time, site)` codes before any site-preference weighting can be implemented literally.
