# Request Submission Rules

This document captures the physician request-sheet validation rules gathered so far.

These rules apply to the request submission itself before schedule generation begins.

## Definitions

### Shift block

A block is a group of shifts that must be made available together to count.

Blocks are defined in [Shifts.md](C:\Users\kskob\Dropbox\KEA Projects\Scheduler\Shifts.md):

1. `0600h` block
2. `0900h` / `1000h` / `1200h` block
3. `1400h` / `1500h` / `1600h` / `1700h` block
4. `1800h` / `2000h` block
5. `2400h` block

### Valid block

A block is valid on a given day only if the physician marked the entire block available for that day.

Partial availability within a block does not count toward the submission minimums.

### Valid day

A day is valid only if:

- row 5 for that day is marked with `Z`
- it contains at least 2 valid blocks

### Anchored day

An anchored day is a valid day that includes at least one of:

- a valid `0600h` block
- a valid `2400h` block

### Weekend day

Weekend days are:

- Friday
- Saturday
- Sunday

## Minimum Submission Rules

Let `n` be the number of shifts requested by the physician.

The submission must satisfy all of the following:

1. At least `ceil(n * 1.5)` valid days must be made available.
2. At least `n * 4` valid blocks must be made available in total.
3. At least `ceil(n * 0.6)` valid weekend days must be made available.
4. At least `ceil(n / 2)` anchored days must be made available.

The remaining valid days may be formed from any 2 valid blocks.

## Notes And Assumptions

- Fractional minimums are rounded up.
- Anchored days are also counted among total valid days.
- A day with many valid blocks still counts as only 1 valid day.
- A weekend day only counts toward the weekend minimum if it is also a valid day.
- A day marked with `Z` but containing fewer than 2 full blocks should be flagged as invalid.

## Open Questions

These still need confirmation from the scheduling manual or the current scheduler:

- whether any shift categories are exempt from these request rules
- whether service shifts and flex shifts count differently
- whether nights have separate minimum request requirements
- whether a submission that fails validation is rejected outright or accepted with warnings
