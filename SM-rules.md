*\_Generated: 2026-03-04 19:54\_*

**\#\# Global Rules (Plain English)**

\- **\*\*Availability hard-block\*\*** (\`HARD\`): Physicians are never assigned to shifts they did not make available.  
\- **\*\*Regular coverage priority\*\*** (\`SOFT-VERY-HIGH\`): Regular shifts are prioritized over call. Unfilled regular shifts carry very large penalty.  
\- **\*\*Call is secondary\*\*** (\`HARD/STRUCTURAL\`): Call only after regulars: Yes.  
\- **\*\*24-hour start-gap\*\*** (\`HARD\`): Consecutive-day start-time ordering enforced: Yes.  
\- **\*\*PM call next-day rest\*\*** (\`HARD\`): Enforced: Yes.  
\- **\*\*Day call next-day rest\*\*** (\`HARD\`): Enforced: Yes.  
\- **\*\*Post-block minimum gap\*\*** (\`HARD (unless matrix override enabled)\`): Minimum start-gap after a one-day break target: 36 hours.  
\- **\*\*Back-to-back same site type\*\*** (\`SOFT/HARD (config-dependent)\`): Hard enforcement: No. Also soft penalty always applied.  
\- **\*\*Default max consecutive days\*\*** (\`HARD\`): Default max consecutive days: 3; enforce hard: Yes; plus-one cap mode: Yes (+1).  
\- **\*\*Calendar weekend cap\*\*** (\`HARD/SOFT MIX\`): Default weekend cap hard-enforced: No; base cap=2; dynamic weekend policy=Yes.  
\- **\*\*0600+2400 share cap\*\*** (\`SOFT/HARD (config-dependent)\`): Hard-enforced: No; default max without request hard-enforced: No; default cap=4.  
\- **\*\*At least one 0600/2400 when eligible\*\*** (\`HARD\`): Enforced hard: Yes.  
\- **\*\*Acute requested floor\*\*** (\`HARD\`): Hard floor enabled: Yes; non-zero acute if eligible: Yes; minimum acute share hard: Yes at 25%.  
\- **\*\*Emergency overflow\*\*** (\`HARD+PENALIZED\`): Enabled: Yes; overflow hard cap=2.  
\- **\*\*Coverage rescue mode\*\*** (\`PROCESS\`): Enabled: Yes; trigger unfilled regular \>= 1\.  
\- **\*\*Force-fill after solve\*\*** (\`PROCESS\`): Enabled: Yes.  
\- **\*\*Lam-Rico combined cap\*\*** (\`HARD\`): Combined regular max groups include K Lam \+ Rico \<= 16, and Lam-Rico \<= 16\.  
\- **\*\*Carryover prefilled exemption\*\*** (\`PROCESS\`): Prefilled exemption before date: 2026-06-01.  
\- **\*\*Template lock mode\*\*** (\`PROCESS\`): Template prefill lock mode: bold\_only (bold\_only means only bold prefilled cells are fixed).  
\- **\*\*Shift relation matrix engine\*\*** (\`HARD+SOFT (if enabled)\`): Enabled: No; day+1 file and day+2 file are configurable; Q near-hard penalty=900000; X can be exempt on locked/preferred=Yes.

**\#\# Individual Physician Rules (Plain English)**

*\_Sorted alphabetically. "Hard" means must not be broken by default. "Soft" means penalty-guided preference.\_*

**\#\#\# Ademola**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Amanda Hanson**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-01", "options": \["1800RA"\]}, {"date": "2026-06-02", "options": \["2000NE"\]}, {"date": "2026-06-06", "options": \["0600RA"\]}, {"date": "2026-06-07", "options": \["0600RA"\]}, {"date": "2026-06-08", "options": \["0600NE"\]}, {"date": "2026-06-12", "options": \["0600RA"\]}, {"date": "2026-06-13", "options": \["0600RA"\]}, {"date": "2026-06-14", "options": \["0600NE"\]}, {"date": "2026-06-18", "options": \["1800RA"\]}, {"date": "2026-06-19", "options": \["2000NE"\]}, {"date": "2026-06-27", "options": \["0600RA"\]}\]\`

**\#\#\# Anderson**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 3}\]\`  
\- Soft: non-acute site preference direction: \`R\`

**\#\#\# Aref Yeung**  
\- Hard: non-acute site preference direction: \`NE\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Rule key: \`pair\_if\_exact\_count\_start\_hour\`: \`\[{"start\_hour": 24, "exact\_count": 2}\]\`

**\#\#\# Bacon**  
\- Soft: disliked start-hour list: \`\[12, 14\]\`  
\- Rule key: \`disliked\_start\_hours\_weight\`: \`280\`  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: conditional max run rule by start-hour threshold: \`\[{"max\_days": 4, "required\_start\_before\_hour": 15}\]\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Hard: if weekend worked, require full Fri/Sat/Sun pattern: \`True\`

**\#\#\# Bly**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 3}\]\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-03", "options": \["0600RI"\]}, {"date": "2026-06-04", "options": \["1200RA"\]}, {"date": "2026-06-05", "options": \["2400NE"\]}, {"date": "2026-06-17", "options": \["0600RI"\]}, {"date": "2026-06-18", "options": \["1200NE"\]}, {"date": "2026-06-19", "options": \["2400NE"\]}\]\`

**\#\#\# BRAUN**  
\- Hard: forbidden sites: \`\["NECHC"\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Rule key: \`must\_schedule\_all\_available\_days\`: \`True\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-07", "options": \["2400RA", "2400RI"\]}, {"date": "2026-06-08", "options": \["2400RA", "2400RI"\]}, {"date": "2026-06-14", "options": \["2400RA", "2400RI"\]}, {"date": "2026-06-15", "options": \["2400RA", "2400RI"\]}\]\`

**\#\#\# Brenneis**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Hard: named pair relationship constraints: \`\[{"other": "Fanaeian", "min\_start\_separation\_same\_day\_hours": 6, "same\_day\_forbidden\_overlap\_start\_hours": \[17, 18, 20, 24\]}\]\`

**\#\#\# Breton**  
\- Rule key: \`forbid\_single\_day\_gap\_between\_workdays\`: \`True\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Rule key: \`min\_days\_between\_same\_start\_hour\`: \`\[{"start\_hour": 24, "min\_days\_between": 3}\]\`  
\- Hard: avoid isolated single-day blocks: \`True\`  
\- Soft: non-acute site preference direction: \`NE\`

**\#\#\# Brown F**  
\- No custom overrides in file.

**\#\#\# Brown T**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Rule key: \`min\_days\_between\_same\_start\_hour\`: \`\[{"start\_hour": 6, "min\_days\_between": 4}\]\`  
\- Hard: avoid isolated single-day blocks: \`True\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft: custom penalty weight for single-day weekend pattern: \`2200\`

**\#\#\# Burkart**  
\- Hard: maximum consecutive calendar days: \`3\`

**\#\#\# Butcher**  
\- No custom overrides in file.

**\#\#\# Carroll**  
\- Hard: maximum consecutive calendar days: \`4\`

**\#\#\# Cenaiko A**  
\- No custom overrides in file.

**\#\#\# Chee**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 3}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Cloutier**  
\- Hard: forbidden sites: \`\["NECHC", "RAH", "FLOAT"\]\`

**\#\#\# Dance**  
\- Rule key: \`cooldown\_after\_start\_hour\`: \`\[{"start\_hour\_min": 16, "cooldown\_days": 3}\]\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Hard: rolling window shift cap: \`\[{"days": 7, "max\_shifts": 3}\]\`  
\- Rule key: \`no\_back\_to\_back\_start\_hour\_min\`: \`16\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-02", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-07", "options": \["DOC"\]}, {"date": "2026-06-08", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-09", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-13", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-14", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-18", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-19", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-20", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-22", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-26", "options": \["0600"\]}\]\`  
\- Rule key: \`preferred\_start\_hour\_groups\`: \`\[{"start\_hours": \[6\], "min\_count": 3, "max\_count": 4, "weight": 900}, {"start\_hours": \[9, 10, 12, 14\], "min\_count": 1, "max\_count": 2, "weight": 700}, {"start\_hours": \[16, 18, 20\], "min\_count": 1, "max\_count": 1, "weight": 700}\]\`

**\#\#\# Davis**  
\- Rule key: \`forbidden\_dates\`: \`\["2026-06-27"\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Hard: conditional max run rule by start-hour threshold: \`\[{"max\_days": 4, "required\_start\_before\_hour": 10}\]\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Rule key: \`min\_days\_between\_same\_start\_hour\`: \`\[{"start\_hour": 16, "min\_days\_between": 2}, {"start\_hour": 17, "min\_days\_between": 2}, {"start\_hour": 18, "min\_days\_between": 2}, {"start\_hour": 20, "min\_days\_between": 2}\]\`  
\- Rule key: \`no\_back\_to\_back\_start\_hour\_min\`: \`16\`  
\- Soft: non-acute site preference direction: \`NE\`

**\#\#\# Deol**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Hard: only these shift start hours are allowed: \`\[6\]\`

**\#\#\# Desrochers**  
\- Rule key: \`increasing\_start\_times\_in\_blocks\`: \`True\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Hard: minimum block length when a block starts: \`2\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Rule key: \`strictly\_increasing\_start\_times\_in\_blocks\`: \`True\`

**\#\#\# Deutscher**  
\- No custom overrides in file.

**\#\#\# Dong**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Soft: preferred start-hour list: \`\[6\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`1200\`

**\#\#\# E Chang**  
\- Hard: forbidden sites: \`\["NECHC"\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Rule key: \`max\_shifts\_override\`: \`6\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-12", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-13", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-14", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}\]\`

**\#\#\# Edgecumbe**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-01", "options": \["0600"\]}, {"date": "2026-06-02", "options": \["0600"\]}, {"date": "2026-06-03", "options": \["0600"\]}\]\`

**\#\#\# Esterhuizen**  
\- Soft: disliked start-hour list: \`\[18, 20\]\`  
\- Rule key: \`disliked\_start\_hours\_weight\`: \`450\`  
\- Hard: forbidden day-to-next-day start-hour pairings: \`\[{"prev\_start\_hour": 20, "next\_start\_hour": 24}\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`R\`

**\#\#\# Example Physician**  
\- Rule key: \`transition\_rules\`: \`\[{"from\_weekday": "Mon", "to\_weekday": "Tue", "allowed\_start\_hours": \[18\]}\]\`

**\#\#\# Fanaeian**  
\- Rule key: \`consecutive\_day\_start\_time\_delta\_max\_hours\`: \`6\`  
\- Hard: if any listed start hours assigned, must appear in consecutive block(s): \`\[{"start\_hours": \[6, 24\]}\]\`  
\- Hard: non-acute site preference direction: \`NE\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Hard: named pair relationship constraints: \`\[{"other": "Brenneis", "min\_start\_separation\_same\_day\_hours": 6, "same\_day\_forbidden\_overlap\_start\_hours": \[17, 18, 20, 24\]}\]\`

**\#\#\# Farfus**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 3}\]\`  
\- Rule key: \`min\_days\_between\_same\_start\_hour\`: \`\[{"start\_hour": 20, "min\_days\_between": 3}\]\`  
\- Rule key: \`min\_start\_hour\`: \`12\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Rule key: \`preferred\_site\_count\_targets\`: \`\[{"site": "NECHC", "min\_count": 1, "weight": 240}\]\`

**\#\#\# FBrown**  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-15", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-16", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}\]\`

**\#\#\# Fisher**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Hard: cap number of shifts for specific start hour(s): \`\[{"start\_hour": 20, "max\_count": 1}\]\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Rule key: \`preferred\_site\_count\_targets\`: \`\[{"category": "INTAKE", "min\_count": 1, "max\_count": 1, "weight": 220}, {"category": "FLOAT", "min\_count": 1, "max\_count": 1, "weight": 220}\]\`

**\#\#\# Francescutti**  
\- Hard: do not exceed requested monthly shifts: \`True\`  
\- Rule key: \`forbidden\_shift\_codes\`: \`\["DOC", "NOC"\]\`  
\- Hard: forbidden sites: \`\["RAH", "FLOAT"\]\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Rule key: \`forbidden\_weekdays\`: \`\["Mon"\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Hard: cap fraction from selected start-hour set: \`\[{"start\_hours": \[6, 9, 10, 12, 14, 15\], "max\_fraction": 0.3}\]\`  
\- Rule key: \`prc\_exemption\_note\`: \`PRC Exemption\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-13", "options": \["0600"\]}, {"date": "2026-06-14", "options": \["0600"\]}, {"date": "2026-06-20", "options": \["0600"\]}, {"date": "2026-06-21", "options": \["0600"\]}, {"date": "2026-06-27", "options": \["0600"\]}, {"date": "2026-06-28", "options": \["0600"\]}\]\`  
\- Soft/Exception: allowed to exceed normal weekend target: \`True\`

**\#\#\# Garcea**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Hard: only these shift start hours are allowed: \`\[6\]\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-06", "options": \["0600"\]}, {"date": "2026-06-07", "options": \["0600"\]}, {"date": "2026-06-08", "options": \["0600"\]}, {"date": "2026-06-09", "options": \["DOC"\]}, {"date": "2026-06-12", "options": \["0600"\]}, {"date": "2026-06-13", "options": \["0600"\]}, {"date": "2026-06-14", "options": \["0600"\]}, {"date": "2026-06-20", "options": \["0600"\]}, {"date": "2026-06-21", "options": \["0600"\]}\]\`  
\- Soft/Exception: allowed to exceed normal weekend target: \`True\`

**\#\#\# Gerber**  
\- Hard: maximum consecutive calendar days: \`5\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 3}\]\`  
\- Soft: non-acute site preference direction: \`NE\`

**\#\#\# Gill**  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`

**\#\#\# Grishin**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Rule key: \`max\_start\_hour\`: \`12\`  
\- Soft: non-acute site preference direction: \`NE\`

**\#\#\# Gunawan**  
\- No custom overrides in file.

**\#\#\# Haager**  
\- Hard: do not exceed requested monthly shifts: \`True\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-02", "options": \["1200RA"\]}, {"date": "2026-06-03", "options": \["1500NE"\]}, {"date": "2026-06-06", "options": \["1200RA"\]}, {"date": "2026-06-07", "options": \["1500NE"\]}, {"date": "2026-06-11", "options": \["1500NE"\]}, {"date": "2026-06-16", "options": \["1500NE"\]}, {"date": "2026-06-17", "options": \["1600"\]}, {"date": "2026-06-20", "options": \["0600RA"\]}, {"date": "2026-06-21", "options": \["0600RA"\]}, {"date": "2026-06-22", "options": \["DOC"\]}\]\`

**\#\#\# Hansen R**  
\- No custom overrides in file.

**\#\#\# Hayward**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-17", "options": \["1200"\]}, {"date": "2026-06-18", "options": \["1200"\]}, {"date": "2026-06-24", "options": \["1200"\]}, {"date": "2026-06-26", "options": \["1200"\]}, {"date": "2026-06-30", "options": \["1200"\]}\]\`

**\#\#\# Hegstrom**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Houston**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-01", "options": \["1600", "1700"\]}, {"date": "2026-06-02", "options": \["1600", "1700"\]}, {"date": "2026-06-03", "options": \["1600", "1700"\]}\]\`  
\- Soft: preferred start-hour list: \`\[6, 9, 10, 12, 14, 15, 24\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`420\`

**\#\#\# J Chang**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: non-acute site preference direction: \`NE\`  
\- Hard: cap acute fraction of assigned shifts: \`0.3\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-03", "options": \["0900NE"\]}, {"date": "2026-06-08", "options": \["1200RA"\]}, {"date": "2026-06-09", "options": \["1200NE"\]}, {"date": "2026-06-13", "options": \["0600RA"\]}, {"date": "2026-06-15", "options": \["0900NE"\]}, {"date": "2026-06-18", "options": \["1200NE"\]}, {"date": "2026-06-22", "options": \["0900NE"\]}\]\`

**\#\#\# Johnston**  
\- Hard: do not exceed requested monthly shifts: \`True\`  
\- Hard: maximum consecutive calendar days: \`5\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Keyes**  
\- Hard: maximum consecutive calendar days: \`1\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Khangura**  
\- Rule key: \`forbidden\_shift\_codes\`: \`\["6RI"\]\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-15", "options": \["0600"\]}, {"date": "2026-06-16", "options": \["0600"\]}, {"date": "2026-06-27", "options": \["0600"\]}, {"date": "2026-06-28", "options": \["0600"\]}\]\`  
\- Soft: preferred start-hour list: \`\[6\]\`

**\#\#\# Kilburn**  
\- No custom overrides in file.

**\#\#\# Kjelland**  
\- Hard: required cooldown off-days around worked day: \`2\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-02", "options": \["0600"\]}, {"date": "2026-06-09", "options": \["0600"\]}, {"date": "2026-06-14", "options": \["0600RA"\]}, {"date": "2026-06-22", "options": \["0600"\]}\]\`  
\- Soft: preferred start-hour list: \`\[6\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`900\`

**\#\#\# Krisik**  
\- Hard: forbidden sites: \`\["INTAKE", "RAH"\]\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Rule key: \`max\_shifts\_override\`: \`8\`  
\- Rule key: \`min\_days\_between\_start\_hour\_pairs\`: \`\[{"prev\_start\_hour\_min": 17, "next\_start\_hour": 6, "min\_days\_between": 4}\]\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-01", "options": \["1500NE"\]}, {"date": "2026-06-02", "options": \["1500NE"\]}, {"date": "2026-06-03", "options": \["1500NE"\]}, {"date": "2026-06-04", "options": \["1500NE"\]}, {"date": "2026-06-13", "options": \["1500NE"\]}, {"date": "2026-06-14", "options": \["1500NE"\]}, {"date": "2026-06-15", "options": \["1500NE"\]}, {"date": "2026-06-16", "options": \["1500NE"\]}, {"date": "2026-06-17", "options": \["1500NE"\]}, {"date": "2026-06-18", "options": \["1500NE"\]}, {"date": "2026-06-26", "options": \["1500NE"\]}, {"date": "2026-06-27", "options": \["1500NE"\]}, {"date": "2026-06-28", "options": \["1500NE"\]}, {"date": "2026-06-29", "options": \["1500NE"\]}, {"date": "2026-06-30", "options": \["1500NE"\]}\]\`  
\- Soft: preferred start-hour list: \`\[15\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`260\`

**\#\#\# Lali**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Rule key: \`max\_shifts\_override\`: \`14\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Hard: only these shift start hours are allowed: \`\[6, 10\]\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-01", "options": \["0600RI"\]}, {"date": "2026-06-02", "options": \["0600RA"\]}, {"date": "2026-06-03", "options": \["1000RI"\]}, {"date": "2026-06-05", "options": \["0600RA"\]}, {"date": "2026-06-07", "options": \["1500NE"\]}, {"date": "2026-06-09", "options": \["0600RI"\]}, {"date": "2026-06-10", "options": \["0600RA"\]}, {"date": "2026-06-16", "options": \["0600RI"\]}, {"date": "2026-06-17", "options": \["0600RA"\]}, {"date": "2026-06-19", "options": \["0600RA"\]}, {"date": "2026-06-20", "options": \["1400RI"\]}, {"date": "2026-06-21", "options": \["1500NE"\]}, {"date": "2026-06-23", "options": \["1000RI"\]}, {"date": "2026-06-26", "options": \["0600RA"\]}\]\`  
\- Hard: weekday-specific allowed starts if working that day: \`\[{"weekday": "Fri", "allowed\_start\_hours": \[6\]}\]\`

**\#\#\# Lam N**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Lam-Rico**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 4}\]\`  
\- Hard: minimum block length when a block starts: \`4\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Hard: only these shift start hours are allowed: \`\[24\]\`  
\- Soft/Exception: allowed to exceed normal weekend target: \`True\`

**\#\#\# Lefebvre**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 4}\]\`  
\- Hard: rolling window shift cap: \`\[{"days": 7, "max\_shifts": 5}\]\`  
\- Hard: minimum block length when a block starts: \`2\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-29", "options": \["2400"\]}, {"date": "2026-06-30", "options": \["2400"\]}\]\`

**\#\#\# Lucyk**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Lung**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Rule key: \`max\_site\_start\_count\_per\_month\`: \`\[{"site": "INTAKE", "start\_hour": 14, "max\_count": 2}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-05", "options": \["2400RI"\]}, {"date": "2026-06-12", "options": \["2400A"\]}, {"date": "2026-06-13", "options": \["2400"\]}\]\`

**\#\#\# MacGougan**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Hard: cap number of shifts for specific start hour(s): \`\[{"start\_hour": 24, "max\_count": 1}\]\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-02", "options": \["1500NE"\]}, {"date": "2026-06-03", "options": \["1800RA"\]}, {"date": "2026-06-04", "options": \["DOC"\]}, {"date": "2026-06-06", "options": \["1200RA"\]}, {"date": "2026-06-07", "options": \["1200NE"\]}, {"date": "2026-06-09", "options": \["1500NE"\]}, {"date": "2026-06-10", "options": \["1600"\]}, {"date": "2026-06-23", "options": \["1500NE"\]}, {"date": "2026-06-24", "options": \["1800RA"\]}, {"date": "2026-06-25", "options": \["1800RA"\]}, {"date": "2026-06-30", "options": \["1500NE"\]}\]\`

**\#\#\# Mackenzie**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# MacLean**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-02", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-06", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-07", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-15", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-16", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-22", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-23", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-28", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-29", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}\]\`  
\- Soft: preferred weekend start-hour list: \`\[6\]\`  
\- Soft: custom weight for weekend start-hour preference: \`320\`

**\#\#\# Mason**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Matemisz**  
\- Hard: maximum consecutive calendar days: \`5\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 3}\]\`  
\- Rule key: \`max\_work\_blocks\`: \`1\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# McKinnon**  
\- Hard: do not exceed requested monthly shifts: \`True\`  
\- Hard: forbidden sites: \`\["INTAKE", "FLOAT", "RAH"\]\`  
\- Hard: these shift start hours are not allowed: \`\[6, 24\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Rule key: \`no\_back\_to\_back\_start\_hour\_min\`: \`16\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Rule key: \`prc\_exemption\_note\`: \`PRC Exemption\`

**\#\#\# Meleshko**  
\- Rule key: \`cooldown\_after\_start\_hour\`: \`\[{"start\_hour\_min": 24, "cooldown\_days": 2}\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`NE\`

**\#\#\# Mithani**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: non-acute site preference direction: \`R\`  
\- Rule key: \`max\_calendar\_weekends\`: \`1\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Rule key: \`max\_site\_count\_per\_month\`: \`\[{"site": "NECHC", "max\_count": 1}\]\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-07", "options": \["1600"\]}, {"date": "2026-06-08", "options": \["1800"\]}, {"date": "2026-06-09", "options": \["1800"\]}, {"date": "2026-06-24", "options": \["0600"\]}\]\`  
\- Hard: weekday-specific allowed starts if working that day: \`\[{"weekday": "Sat", "allowed\_start\_hours": \[6, 10, 12\]}, {"weekday": "Sun", "allowed\_start\_hours": \[6, 10, 12\]}\]\`

**\#\#\# Morrison**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Mrochuk**  
\- Hard: forbidden sites: \`\["INTAKE", "FLOAT"\]\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Hard: only these shift start hours are allowed: \`\[6\]\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-06", "options": \["0600"\]}, {"date": "2026-06-07", "options": \["0600"\]}, {"date": "2026-06-14", "options": \["0600"\]}\]\`

**\#\#\# N Lam**  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-02", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-04", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-05", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-06", "options": \["0600", "0900"\]}\]\`

**\#\#\# Norum**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Rule key: \`max\_start\_hour\`: \`14\`  
\- Soft: non-acute site preference direction: \`NE\`

**\#\#\# Peterson**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-01", "options": \["0600", "0900", "1000"\]}, {"date": "2026-06-08", "options": \["0600", "0900", "1000"\]}, {"date": "2026-06-12", "options": \["0600", "0900", "1000"\]}, {"date": "2026-06-13", "options": \["0600", "0900", "1000"\]}, {"date": "2026-06-14", "options": \["0600", "0900", "1000"\]}\]\`

**\#\#\# Pritchard**  
\- Soft: disliked start-hour list: \`\[18\]\`  
\- Rule key: \`disliked\_start\_hours\_weight\`: \`280\`  
\- Hard: non-acute site preference direction: \`NE\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Hard: avoid isolated single-day blocks: \`True\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Hard: weekday-specific allowed starts if working that day: \`\[{"weekday": "Sat", "allowed\_start\_hours": \[6, 24\]}, {"weekday": "Sun", "allowed\_start\_hours": \[6, 24\]}\]\`

**\#\#\# R Scheirer**  
\- Hard: forbidden sites: \`\["NECHC", "INTAKE"\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Hard: only these shift start hours are allowed: \`\[16, 24\]\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-01", "options": \["1600"\]}, {"date": "2026-06-02", "options": \["2400"\]}, {"date": "2026-06-05", "options": \["2400"\]}, {"date": "2026-06-06", "options": \["2400"\]}, {"date": "2026-06-11", "options": \["2400"\]}, {"date": "2026-06-12", "options": \["2400"\]}, {"date": "2026-06-18", "options": \["2400"\]}, {"date": "2026-06-19", "options": \["2400"\]}\]\`  
\- Soft: preferred start-hour list: \`\[24\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`320\`

**\#\#\# Randhawa**  
\- No custom overrides in file.

**\#\#\# Rawe**  
\- Hard: non-acute site preference direction: \`F\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Hard: avoid isolated single-day blocks: \`True\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Hard: weekday-specific allowed starts if working that day: \`\[{"weekday": "Sat", "allowed\_start\_hours": \[6, 10, 12\]}, {"weekday": "Sun", "allowed\_start\_hours": \[6, 10, 12\]}, {"weekday": "Mon", "allowed\_start\_hours": \[15, 16, 17, 18, 20, 24\]}, {"weekday": "Tue", "allowed\_start\_hours": \[15, 16, 17, 18, 20, 24\]}, {"weekday": "Wed", "allowed\_start\_hours": \[15, 16, 17, 18, 20, 24\]}, {"weekday": "Thu", "allowed\_start\_hours": \[15, 16, 17, 18, 20, 24\]}, {"weekday": "Fri", "allowed\_start\_hours": \[15, 16, 17, 18, 20, 24\]}\]\`

**\#\#\# Reid**  
\- No custom overrides in file.

**\#\#\# Rogers**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 4}\]\`  
\- Rule key: \`min\_start\_hour\`: \`14\`  
\- Soft: non-acute site preference direction: \`R\`

**\#\#\# Rosenblum**  
\- Hard: do not exceed requested monthly shifts: \`True\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Rule key: \`forbidden\_weekdays\`: \`\["Wed"\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Rule key: \`prc\_exemption\_note\`: \`PRC Exemption\`  
\- Hard: weekday-specific allowed starts if working that day: \`\[{"weekday": "Fri", "allowed\_start\_hours": \[6\]}\]\`

**\#\#\# Sachs**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Sajko**  
\- No custom overrides in file.

**\#\#\# Samoraj**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft: preferred start-hour list: \`\[16, 17, 18, 20\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`220\`

**\#\#\# Scheirer O**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Scheirer R**  
\- Hard: forbidden sites: \`\["NECHC", "INTAKE"\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Hard: only these shift start hours are allowed: \`\[16, 24\]\`  
\- Soft: preferred start-hour list: \`\[24\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`320\`

**\#\#\# Schindler**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Schonnop**  
\- Hard: do not exceed requested monthly shifts: \`True\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Rule key: \`prc\_exemption\_note\`: \`PRC Exemption\`

**\#\#\# Sharma**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Rule key: \`min\_days\_between\_start\_hour\_ranges\`: \`\[{"prev\_start\_hour\_min": 15, "next\_start\_hour\_max": 14, "min\_days\_between": 4}\]\`  
\- Soft: non-acute site preference direction: \`NE\`

**\#\#\# Shih**  
\- No custom overrides in file.

**\#\#\# Singh**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 4}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft: preferred start-hour list: \`\[24\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`180\`  
\- Soft/Exception: allowed to exceed normal weekend target: \`True\`

**\#\#\# Skoblenick**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-03", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-12", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-13", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-14", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-17", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-18", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-21", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}, {"date": "2026-06-29", "options": \["0600", "0900", "1000", "1200", "1400", "1500", "1600", "1700", "1800", "2000", "2400", "DOC", "NOC"\]}\]\`  
\- Soft: preferred weekend start-hour list: \`\[24\]\`  
\- Soft: custom weight for weekend start-hour preference: \`220\`

**\#\#\# Smith**  
\- Soft: disliked start-hour list: \`\[12\]\`  
\- Rule key: \`disliked\_start\_hours\_weight\`: \`220\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Rule key: \`min\_days\_between\_start\_hour\_ranges\`: \`\[{"prev\_start\_hour\_min": 15, "next\_start\_hour\_max": 14, "min\_days\_between": 5}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-04", "options": \["1800"\]}, {"date": "2026-06-05", "options": \["1800"\]}, {"date": "2026-06-06", "options": \["1800"\]}, {"date": "2026-06-07", "options": \["1800"\]}, {"date": "2026-06-16", "options": \["0900"\]}, {"date": "2026-06-21", "options": \["0600"\]}\]\`  
\- Soft: preferred start-hour list: \`\[18\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`220\`

**\#\#\# Taylor**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-18", "options": \["1600"\]}, {"date": "2026-06-19", "options": \["1800RA"\]}, {"date": "2026-06-20", "options": \["2400RA"\]}, {"date": "2026-06-21", "options": \["2400RI"\]}\]\`

**\#\#\# TBrown**  
\- Rule key: \`forbidden\_dates\`: \`\["2026-06-19"\]\`

**\#\#\# Thirsk**  
\- Hard exception: allows back-to-back in listed categories: \`\["INTAKE"\]\`  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-03", "options": \["1200"\]}, {"date": "2026-06-06", "options": \["1200"\]}, {"date": "2026-06-07", "options": \["1200"\]}, {"date": "2026-06-13", "options": \["1200"\]}, {"date": "2026-06-26", "options": \["1200"\]}, {"date": "2026-06-27", "options": \["1200"\]}\]\`  
\- Soft: preferred start-hour list: \`\[16\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`220\`  
\- Soft: custom penalty weight for single-day weekend pattern: \`260\`

**\#\#\# Tiessen**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Velji**  
\- Soft: disliked start-hour list: \`\[6\]\`  
\- Rule key: \`disliked\_start\_hours\_weight\`: \`220\`  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 2}\]\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Soft: preferred start-hour list: \`\[24\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`220\`

**\#\#\# Walker**  
\- Hard: maximum consecutive calendar days: \`6\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 4}\]\`  
\- Soft: non-acute site preference direction: \`E\`  
\- Rule key: \`preferred\_start\_hour\_groups\`: \`\[{"start\_hours": \[6, 9, 10, 12\], "min\_count": 2, "max\_count": 3, "weight": 220}, {"start\_hours": \[14, 15, 16, 17, 18, 20\], "min\_count": 2, "max\_count": 3, "weight": 220}, {"start\_hours": \[24\], "min\_count": 2, "max\_count": 3, "weight": 220}\]\`

**\#\#\# Whiteside**  
\- Hard: maximum consecutive calendar days: \`4\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 3}\]\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Williamson**  
\- Rule key: \`forbidden\_shift\_codes\`: \`\["6RI"\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 3}\]\`  
\- Rule key: \`no\_isolated\_start\_hours\`: \`\[24\]\`  
\- Soft: non-acute site preference direction: \`R\`  
\- Soft: preferred start-hour list: \`\[18\]\`  
\- Rule key: \`preferred\_start\_hours\_weight\`: \`200\`  
\- Hard: weekday-specific allowed starts if working that day: \`\[{"weekday": "Sat", "allowed\_start\_hours": \[6\]}, {"weekday": "Sun", "allowed\_start\_hours": \[6\]}\]\`

**\#\#\# Wittmeier**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Soft: preferred weekend start-hour list: \`\[15, 16, 17, 18, 20\]\`  
\- Soft: custom weight for weekend start-hour preference: \`200\`  
\- Hard: weekday-specific allowed starts if working that day: \`\[{"weekday": "Mon", "allowed\_start\_hours": \[6, 9, 10\]}, {"weekday": "Wed", "allowed\_start\_hours": \[6, 9, 10\]}\]\`  
\- Rule key: \`strictly\_increasing\_start\_times\_in\_blocks\`: \`True\`

**\#\#\# Woods**  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Soft: non-acute site preference direction: \`R\`

**\#\#\# Yeung Alex**  
\- Hard: these shift start hours are not allowed: \`\[24\]\`  
\- Hard: maximum consecutive calendar days: \`3\`  
\- Hard: per-physician minimum start-gap override: \`22\`  
\- Soft: non-acute site preference direction: \`E\`

**\#\#\# Zhang**  
\- Hard: do not exceed requested monthly shifts: \`True\`  
\- Hard: maximum consecutive calendar days: \`2\`  
\- Hard: max consecutive runs for specific start hour(s): \`\[{"start\_hour": 24, "max\_consecutive": 1}\]\`  
\- Hard: rolling window shift cap: \`\[{"days": 7, "max\_shifts": 4}\]\`  
\- Rule key: \`max\_site\_count\_per\_month\`: \`\[{"site": "NECHC", "max\_count": 1}\]\`  
\- Soft: non-acute site preference direction: \`NE\`  
\- Rule key: \`prc\_exemption\_note\`: \`PRC Exemption\`  
\- Soft-very-high: date-specific preferred shifts: \`\[{"date": "2026-06-27", "options": \["1800"\]}\]\`

**\#\# Legend (Plain English)**

\- **\*\*Hard rule\*\***: The solver treats this as a must-follow constraint. It should only be broken when an explicit configured exception exists.  
\- **\*\*Soft rule\*\***: The solver may violate this if needed, but pays a weighted penalty; higher weight \= stronger push to follow.  
\- **\*\*Near-hard soft rule\*\***: Technically soft, but penalty is so large it behaves almost like hard unless no better solution exists.  
\- **\*\*Coverage rescue mode\*\***: A second pass that may relax selected constraints to reduce unfilled regular shifts.  
\- **\*\*Force-fill mode\*\***: Post-solve pass to place available physicians into still-unfilled regular shifts while minimizing rule damage.  
\- **\*\*Overflow\*\***: Assigning beyond a physician max shifts; capped and heavily penalized if enabled.  
\- **\*\*Dynamic weekend policy\*\***: Weekend caps adjust based on how many total shifts a physician is assigned.  
\- **\*\*Acute ratio / floor\*\***: Targets minimum/maximum proportion of acute shifts relative to assigned/requested shifts.  
\- **\*\*Early/Late (0600/2400) share cap\*\***: Limits how much of a physician schedule is composed of 0600+2400 shifts, unless exemptions/requests apply.  
\- **\*\*Template prefill lock mode \`all\`\*\***: all prefilled names are fixed and locked.  
\- **\*\*Template prefill lock mode \`bold\_only\`\*\***: only bold prefilled names are fixed and locked.  
\- **\*\*Template prefill lock mode \`none\`\*\***: prefilled names are not treated as locks.  
\- **\*\*Shift relation matrix (if enabled)\*\***:  
 Day+1 matrix applies to next-day assignments.  
 Day+2 matrix applies only when Day+1 is off.  
 \`X\` means forbidden transition (except configured exemptions).  
 \`Q\` means strongly discouraged transition (near-hard penalty).

