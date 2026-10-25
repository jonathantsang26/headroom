# GET expected-value priors for SPP flowgates

Companion to `config/get_priors.yaml` (version `spp-get-priors-2026-10-13.2`; research revision 2 after an audit, plus the interface change below). The file passes `headroom.model.ev.load_priors`.

**Since this research.** Two changes to the EV layer postdate it:

- EV now uses forward rent, R_f × months_binding/12 × a backtest ratio draw, not the observed 2025 rent (docs/FORWARD_RENT.md).
- EV values the decision a planner can make: learn which GETs apply, then deploy the applicable GET with the highest expected net value E[u]E[h]E[R] − E[C], or nothing. It no longer floors each draw at zero, which assumed the realized relief, haircut, cost and next year's rent were known in advance. `ev_recommended_get` is the GET contributing most to that value; `recommended_get` stays the evidence rule's physical assignment (the fragility noted in section 10, item 2 is gone).
- Interfaces: the relief-size proxy that section 4.4 and 6.3 folded into applicability is now a separate `relief_scale` (DLR 0.35, TO 0.5), and applicability for interfaces is the probability alone (DLR 0.30, was 0.10; TO 0.30, was 0.15). Folding a relief discount into the probability charged the cost only where the GET was drawn applicable, so it undercharged cost.
- C_TO's lo and hi pair the extremes of system cost and the number of flowgates N (lo: low cost over high N; hi: high cost over low N). That is a joint-extreme envelope, which section 2 avoids for DLR and APFC; its ends are wider than comparable quantiles.

The sensitivity table in section 9 was computed on point 2025 rent with a scratch re-implementation. The real run's sensitivity is in README Status.

These priors are cited judgement, not observations. The skeptic pass rated every source behind them **with_caveats**, except apfc-28, which it rated yes. The caveat that matters for each number is in the citation table (section 12). Revision 2 answers the audit of revision 1; section 11 maps each audit item to what changed.

## 1. The priors at a glance

EV for flowgate f and GET g is `EV_g = P(applicable | class) x u_g x h x R_f - C_g`, where R_f is the observed 2025 SPP day-ahead (DA) congestion rent.

| Prior | lo | mode | hi | Triangle mean | Basis in one line |
|---|---|---|---|---|---|
| h, haircut (shared by all GETs) | 0.10 | 0.35 | 0.53 | 0.327 | lo: derived floor from SPP's TO market re-run. mode: judgement from shadow-price decline. hi: set by the to-35 cap, u.hi x h.hi <= 0.08 |
| u, DLR (increment over AAR) | 0.02 | 0.08 | 0.15 | 0.083 | Emergency-basis gain over AAR of about 0.10, x 0.8 for DA forecasts; hi is the terminal ceiling converted to an over-AAR figure |
| u, APFC | 0.03 | 0.08 | 0.15 | 0.087 | DOE's 10/17/25% corridor assumption, shaded to binding hours and preventive setpoints (judgement) |
| u, TO (effective) | 0.02 | 0.07 | 0.15 | 0.080 | Mode back-solved: 0.025 / 0.35, from SPP's TO market re-run. lo and hi are built as per-event relief x coverage x saturation |
| C, DLR ($M/yr) | 0.034 | 0.10 | 0.26 | 0.131 | Capex $0.15M / $0.6M / $1.5M at 12 yr, plus O&M of $0.016M / $0.031M / $0.08M per year |
| C, APFC ($M/yr) | 0.16 | 0.86 | 4.3 | 1.77 | Capex $1.5M / $8M / $40M at 20 yr, plus O&M at 2% of capex per year |
| C, TO ($M/yr) | 0.05 | 0.175 | 0.87 | 0.365 | SPP-wide system cost of $1M / $5M / $15M per year over N = 100 / 50 / 20 flowgates, plus a variable cost of $0.04M / $0.075M / $0.12M per flowgate |

**Applicability by evidence class.** Class counts and 2025 rent shares come from the pinned snapshot (`data/raw/spp/2026-10-13.r2`, my count, read-only).

| Class (n flowgates, share of 2025 DA rent) | DLR | APFC | TO |
|---|---|---|---|
| line_base (170, 1.5%) | 0.45 | 0.35 | 0.15 |
| line_n1 (633, 63.5%) | 0.40 | 0.40 | 0.30 |
| line_n1_episodic (879, 6.7%) | 0.35 | 0.35 | 0.35 |
| line_radial (0, inert) | 0.40 | 0.00 | 0.00 |
| transformer_base (80, 0.6%) | 0.00 | 0.15 | 0.15 |
| transformer_n1 (299, 22.9%) | 0.00 | 0.20 | 0.30 |
| interface (11, 4.8%) | 0.30 (relief × 0.35) | 0.25 | 0.30 (relief × 0.5) |
| loopflow (0, inert) | 0.30 | 0.35 | 0.30 |

No 2025 SPP flowgate falls in line_radial or loopflow, so those values do not affect today's ranking. They are set only to make the file complete and defensible.

**Net effect of revision 2.** Against revision 1, the sum of mean EV falls from $31.0M/yr to $15.6M/yr. Flowgates with mean EV above $1k fall from 238 to 161, and 5 of the 103 shortlist members change (section 9). The drop comes from two things: the haircut cap (h.hi falls from 0.70 to 0.53, and every u.hi falls to 0.15), and costs that are now escalated to 2025$ with the omitted cost items added back.

## 2. Conventions

- **Money.** All costs are in 2025 USD. Source figures were escalated with approximate CPI-U ratios. These ratios are judgement, not taken from the evidence: 2010-13 to 2025 about 1.40; 2016 about 1.34; 2018 about 1.28; 2021 about 1.19; 2022 about 1.10; 2024 about 1.03. Each figure is quoted with its nominal year, and with its 2025$ value where that value is used.
- **Discount rate: 6% real** for all three GETs. This is judgement. It is close to the real rates the evidence implies: EPRI's 8% nominal with 2% escalation (apfc-8), and the 5.9% real used in the VNI regulatory test, which the apfc-44 verifier relied on. DOE Liftoff uses 8% without saying whether it is real or nominal.
  - At 7% real, C rises by 2.9% / 3.8% / 3.8% for DLR (lo/mode/hi), 6.7% for APFC, and under 1% for TO, where only the one-time Liftoff part is annualized. This is second-order.
- **Capital recovery factors (CRF) at 6% real:** 10 yr 0.1359; 12 yr 0.1193; 15 yr 0.1030; 20 yr 0.0872; 25 yr 0.0782. At 7%: 12 yr 0.1259; 20 yr 0.0944.
- **Lives.**
  - DLR and TO: 12 years (range 10-15). DOE 2022 says sensors and software are refreshed every 10-15 years and assumes 12-year depreciation (dlr-77, syntheses-11, apfc-64).
  - APFC hardware: 20 years (range 12-25). This is judgement. EPRI's 25 years is a book life (apfc-8). DOE's 12 years is an accounting choice (apfc-64). No engineering life was found for a SmartValve-class device (negatives).
- **How C is built.** lo, mode and hi all use the mid-life value: 12 years for DLR and TO, 20 years for APFC. Capex and O&M take their lo, mode and hi values together, because bigger deployments cost more to run.
  - Revision 1 also paired the shortest life with the hi end and the longest with the lo end. That made the lo and hi of C joint-extreme envelopes, wider than the u and h triangles and not at comparable quantiles. Life is now a sensitivity: at 10 years DLR's C is 0.036 / 0.113 / 0.284 (section 9).
- **What h means.** ev.py values relief at shadow price x MW, which is the first-order production-cost saving. So h = realized production-cost saving / (u x R_f).
  - Ratios of rent reduction measure something else: a transfer between parties. None of them is used as an anchor for h. A few are quoted as directional cross-checks and labelled as such.
- **Schema limits, fixed by the validated priors shape.**
  - There is one haircut for all GETs.
  - u, h and C are drawn independently per draw and shared across flowgates.
  - u is shared across evidence classes.

  These limits drive three workarounds, each flagged where it is used: the TO calibration (section 6.1), the cap enforced through h.hi (section 3), and relief-size proxies inside P for interfaces (sections 4.4 and 6.3).

## 3. Shared realization haircut h: lo 0.10, mode 0.35, hi 0.53

**ev.py draws h once per draw for every GET, and independently of u. True h falls as u rises.** The first-order value u x R is exact for small relief. It overstates once relief exceeds the margin δ at which the constraint stops binding in a given hour: the shadow price falls, and other constraints bind.

**lo = 0.10 (judgement: a derived floor).**
- SPP's real-time (RT) market re-run of topology optimization saved about 3% (2-5%) of the initial RT rent on the relieved constraints (to-29, slide 21; n = 4 hand-picked severe snapshots, with 25-85 units fixed, so the authors call it a lower bound).
- Dividing by the study's average flow relief of 26-31% at fixed dispatch (slide 17) gives about 0.10 (0.065-0.19). That relief is averaged over all 17 focus constraints and 20 cases. It is not the relief of the 4 market-simulated cases, which is not broken out.
- The to-29 verifier notes that the ratio also folds in a conversion from rent to production cost. Penalty-priced RT rent inflates the denominator.
- It is a floor for DA R_f, because DA rent is rarely priced at breach. In 2025 only 54 of 135,820 DA binding hours were breached (my count from the snapshot's `breached_hours` column), while to-29's snapshots were "severe or extreme" and about half were breaching (to-3 notes).
- syntheses-75 (DOE's relay of the same study) is no longer cited for h. Its verifier says the 3% is u x h, not h.

**Double use of to-29, acknowledged.** to-29 measures only the product u x h, about 0.03 per event. It sets h.lo by assuming the physical per-event relief (u about 0.28), and it sets u_TO's mode by assuming h = 0.35 (section 6.1). These are two decompositions of the same product. Because ev.py draws u and h independently, it pairs them inconsistently, and to-29 identifies neither factor on its own.

**mode = 0.35 (judgement), resting on the h(u) shadow-price-decline argument.**
- **Worked example (my judgement, not a source).** Let δ be uniform on 0-0.10 and u = 0.08. h is about 0.31 if the shadow price falls linearly to zero as relief reaches δ, and about 0.60 if it stays flat and then drops (a step). On heavily congested flowgates, with δ uniform on 0-0.30, the same calculation gives 0.62-0.87.
- dlr-13 shows that δ is often below 0.10: in ERCOT DA what-if runs, +5% capacity removed up to 60% of target-line rent. This is a rent basis, and the verifier says it is not h.
- **Directional only, not anchors:**
  - dlr-85: net system rent saving / target-line rent removed = 0.38. This is a rent basis and can stand only for the spillover part of h.
  - apfc-9: APC saving / rent **reduction** = 0.32-0.37 for 4-17 devices (Table 3-7). Against base system rent of 589 M$, the ratio is only about 0.06-0.12.

**hi = 0.53 (judgement), set by a joint cap.**
- In PJM DA simulations, the production-cost cost of congestion was about 8.3% of rent (to-35, slides 15 and 19; PJM 2010, weekly system totals, not per flowgate). So u x h summed over flowgates cannot exceed about 0.08.
- ev.py cannot enforce this jointly, so h.hi is lowered until each single GET satisfies u.hi x h.hi <= 0.08: 0.15 x 0.53 = 0.0795.
- **The envelope argument alone would allow more.** For small u, h tends to 1 (apfc-9's single PAC gives 0.92, directional only).
- **This is the biggest single lever in revision 2.** The triangle mean falls from 0.383 to 0.327, which cuts every EV by about 15%. With the cap not enforced (h.hi = 0.70), sum EV is $20.1M rather than $15.6M, and one shortlist member changes (section 9).
- **Two checks, per draw and in aggregate:**
  - Per draw, no single GET's u x h exceeds 0.08 (0.0% of draws). Combination exceeds it in 8.4% of draws (section 7).
  - In aggregate, the gross value of the option chosen in each draw, summed over flowgates, is 1.0% of total 2025 rent, far inside the cap. to-35's own system-wide TO result, production-cost savings of about 3% of DA rent with every branch switchable and viability assumed, is also an upper bound that this aggregate respects.

## 4. Dynamic Line Rating

### 4.1 AAR baseline: which increment, and how AAR shrinks DLR's value

- **Today's baseline is seasonal.** The 2025 R_f was earned under SPP's four seasonal Normal and Emergency ratings (aar-32).
  - AAR becomes SPP's baseline on 2027-04-01. The FERC deferral is recorded on SPP's RR tracking sheet (spp.org/documents/67824, per the AAR-1 skeptic notes).
  - The date could slip again: East stakeholder readiness stood at 53% against a 67% threshold.
- **Use the increment over AAR.** A DLR deployed now has a life of 10-15 years, and more than 90% of it falls after April 2027.
  - Every usable u data point is measured over AAR (dlr-7, dlr-33, aar-2), or over an irradiance-adjusted rating, ILR (aar-18).
  - Gains over static or seasonal ratings were refuted as the wrong baseline (Oncor 30-70%, NYPA 30-44% and the like).
  - Over today's ratings the increment would be larger. aar-2's DLR is +33.8% over the seasonal rating in summer, against +16.3% over AAR. A sensitivity at 0.05 / 0.13 / 0.30 is in section 9.
- **How AAR reduces DLR's increment.**
  - **(a) Baseline shift.** On the same Oncor lines, DLR beat static ratings by 30-70% but beat AAR by only 6-14% (dlr-7).
  - **(b) Post-2027 rent shrinks.** On conductor-limited lines, AAR itself relieves part of R_f:
    - fleet-wide first-order AAR-only potential of about 7-10% of MISO RT congestion value: 9.9% in 2023, 7.1% in 2024 and 9.8% in 2025 (aar-8, Table 11). This assumes h = 1, so it is an upper bound;
    - about 4.7% achieved on the facilities in two TOs' programs (aar-5);
    - at night, at most 11% x the night share of binding hours (aar-6);
    - about zero on transformers (aar-32), and less on interfaces and terminal-limited lines (aar-35; the Exception rule in aar-32).

    This shrink has no slot in the priors file. It lowers DLR's EV on applicable lines by roughly 5-15% (judgement), which is second-order against the uncertainty in u. **The model therefore slightly overstates DLR's value after 2027.**
  - **(c) No AAR option.** AAR's marginal cost per flowgate is about zero (aar-28), and AAR is mandated, so there is no EV_AAR option.

### 4.2 Relief share u_DLR: 0.02 / 0.08 / 0.15

**Basis: emergency ratings.** In 2025, the N-1 classes held 98% of line rent: (1,530.2 + 160.7) / 1,727.6 $M across line_n1, line_n1_episodic and line_base (my count). Across all N-1 classes they held 93.1% of total rent. These flowgates bind on emergency ratings.

**Anchors over AAR, all measured over all hours:**
- **Emergency, used directly.** PPL reports 8.5-16.5% (dlr-33). This is a utility self-report relayed by FERC, and the averaging is unstated.
- **Normal ratings, x 0.6-0.8 (judgement) to put them on an emergency basis:**
  - Oncor: 6-14% (345 kV) and 8-12% (138 kV). These are quarterly averages, capped at 125% of static and conditioned upward (dlr-7).
  - National Grid MA: 16.3%. This is one line, normal ratings only, with no ratings ceiling. It includes the 22-27% of hours when DLR is below AAR (aar-2).
  - PPL: about 17% (dlr-33).
  - Converted, these give 0.04-0.14.
- **Low cross-check.** For NREL's median line at the median hour, DLR is +8% over ILR (aar-18, slide 26). ILR uses 0.61 m/s static wind and measured irradiance, so it is not an Order 881 AAR. The verifier says DLR's gain over an Order 881 AAR would be larger.

**mode = central about 0.10 x one factor of about 0.8 for the DA forecast margin = 0.08 (judgement).** aar-18's ratings are real-time with no forecast margin, while SPP's AARs are forecast over 240 hours (aar-32). The coincidence of wind and binding hours is taken to net to about 1 (judgement): wind-export binding raises DLR when SPP constraints bind, and hot, calm summer-peak binding lowers it (dlr-21, syntheses-20, aar-37).

This fixes revision 1's inconsistency. The YAML and this report now use the same chain: the emergency adjustment is applied only to the normal-rating anchors, and then a single DA factor is applied.

**hi = 0.15 (judgement): the terminal ceiling, converted to an over-AAR figure.**
- Oncor capped DLR at 125% of static, with next limiting elements at about 133% of static (dlr-10). AAR sits 15.1% above static in summer and 2.7% in winter (aar-2). So the cap leaves only about 9-16% over AAR in summer, and about 22-30% in winter.
- AES terminal headroom is about 11-17% of static, as derived by the verifier where it can be computed (dlr-51).
- NREL's 20%-of-hours point, +26% over ILR (aar-18), passed through the same 0.6-0.8 and 0.8 factors gives 0.12-0.17.
- The value 0.15 also keeps u.hi x h.hi <= 0.08 (section 3). Windy, winter-binding lines could exceed it; a sensitivity at hi = 0.20 is in section 9.

**lo = 0.02 (judgement).** This covers sheltered lines, or lines binding in hot, calm hours, where DLR adds little over AAR. Like-for-like, DLR was below AAR in 22% of summer hours and 27% of winter hours (aar-2, Table 5 p.8). For NREL's median line, DLR was at least 7% below ILR in about 20% of hours (aar-18, slide 26). DLR was below undefined "traditional" ratings 20-40% of the time (aar-37, p.4). The lo also covers the risk that a DLR below AAR lowers the limit, since in SPP's precedence ICCP/DLR ranks above AAR (aar-32).

### 4.3 Cost C_DLR: 0.034 / 0.10 / 0.26 $M/yr

| | lo | mode | hi |
|---|---|---|---|
| Capex per deployment (one monitored line), 2025$ | $0.15M | $0.6M | $1.5M |
| Life | 12 yr | 12 yr | 12 yr |
| O&M + software + allocated integration, $M/yr | 0.016 | 0.031 | 0.08 |
| C at 6% real, $M/yr | 0.034 | 0.10 | 0.26 |

**Capex anchors, nominal, then 2025$:**
- PJM estimate: about $0.5M in 2018$, about $0.64M in 2025$, for an 18-mile 500 kV line. It excludes the station upgrade the line needed (dlr-86).
- PPL: under $0.5M per **two-circuit** corridor in about 2021-22$, which is under $0.6M in 2025$ and under $0.3M per circuit (dlr-35). PPL's about $1M covered three segments with 18 sensors (dlr-32, same cluster).
- Liftoff template: about $0.6M per line in 2024$, illustrative (syntheses-60).
- MISO TOs: $0.1-0.2M per line in 2021$, which is $0.12-0.24M in 2025$. This is ex-ante and at the low end (dlr-40).
- DOE NY: about $0.1-0.2M per segment, planning (syntheses-89).
- BPA: over $1M per line in about 2021$, over $1.2M in 2025$ (dlr-40).
- Oncor demonstration: about $0.6M per circuit in 2010-13$, about $0.85M in 2025$, including validation gear and demo overheads (dlr-17).
- APG: about $1.4M per ~100 km line (dlr-55). Line length is CEII.
- RMI: about $0.32M per planning overload (syntheses-114).

**Capex values.** The mode is $0.6M, which is the escalated central anchors ($0.55-0.64M). The lo is $0.15M, from the MISO TO and DOE NY figures. The hi is $1.5M, which is judgement above BPA's figure and Oncor's escalated figure, allowing for long lines. Per-mile figures (dlr-24, dlr-46) cannot be converted, because SPP line lengths are CEII, so they are cross-checks only.

**O&M:**
- lo and core: Liftoff gives 20% of the $70k licence plus 10% of the $15k of sensors and telecoms, which is $15.5k per line per year in 2024$, about $0.016M in 2025$ (syntheses-61). This is illustrative.
- mode: Liftoff plus a judgement of about $0.015M per year for allocated FTE and EMS integration.
- hi: $0.08M per year (judgement). This is an upper tail that charges part of an SPP TO's program cost to one deployment: an EMS upgrade of up to $1M, or $0.1-0.5M per year of extra SCADA communications. Those are per-TO costs, not per-flowgate costs (dlr-40). DOE's $250k per year is per 16-segment strategy (dlr-79).

Terminal-equipment upgrades are not in C. Terminal limits are handled through P (section 4.4).

### 4.4 Applicability

- **Gate.** DLR gives relief only if the overhead conductor is the limiting element (aar-35, dlr-87).
- **line_base 0.45 (judgement, within 0.3-0.6):**
  - DOE says the conductor limits "about 42% of the time" and transformers nearly 10%. This cites a 1996 New York report second-hand and is weak (dlr-80). Removing transformers gives about 47% of line cases (derived: 0.42 / 0.90).
  - In a screened set of 10 lines, 8 were conductor-limited and 2 needed a relay or breaker replacement first (dlr-51). This is a post-screen share and not a base rate.
  - PJM's showcase congested line was limited by station equipment (dlr-87).
- **line_n1 0.40 (judgement): about 0.9 x line_base.** Lightly loaded pre-contingency lines defeat older sensor-based DLR (dlr-15; stale technology, weak). DLR still worked on an SPP N-1 161 kV flowgate (dlr-82).
- **line_n1_episodic 0.35 (judgement).** These are outage-driven episodes on lines that are otherwise lightly loaded, and DLR suits persistent needs (dlr-21).
- **Transformers 0.** Line ratings do not apply to transformers that are not in series with a line (aar-32).
- **Interface 0.10 (judgement) = about 0.3 x about 0.35.**
  - The 0.3 is the chance that a conductor DLR can act on sets the multi-element limit (aar-35; the Exception rule in aar-32; dlr-3).
  - The 0.35 is **a relief-size proxy, not a probability**: one line's DLR lifts a multi-element limit only partly. It sits in P because u is shared across classes.

## 5. Advanced Power Flow Control

### 5.1 Relief share u_APFC: 0.03 / 0.08 / 0.15

There is no usable measured relief during binding hours.

- **mode 0.08 (judgement).**
  - DOE Liftoff's modelling assumption is a 10/17/25% average increase in corridor capacity (apfc-46, fn 16 and App. F p.83).
  - Its provenance is DOE, the WATT Coalition (an advocacy group of GET vendors) and industry interviews. It is applied to the 50% of lines DOE assumes are viable, so it is conditional on applicability, like ev.py's u.
  - Shaded to the 10% low case for binding hours (judgement). This is also the apfc-46 skeptic's advice, because parallel paths are loaded in those hours too.
  - Then x about 0.8 (judgement): on N-1 flowgates only the preventive setpoint counts (apfc-16). There is also a control-regime risk: PARs run to schedules rather than to the market moved power the efficient way in only 2-3% of NYISO DA hours in 2014 (apfc-11).
- **lo 0.03.** EPRI's SPP planning cases needed about 1-13% flow relief, sized to need (apfc-1; cherry-picked viable cases).
- **hi 0.15 (judgement).** DOE's high case of 25%, x about 0.6 for binding hours and preventive setpoints.
  - The structural cap is angle-control saturation above about 15 deg (apfc-12, Table 3-8).
  - The value also keeps u.hi x h.hi <= 0.08.
  - apfc-56 is no longer used as relief support. The source gives no relief for its 2,000-unit case. Linearly extrapolating the 90-unit pilot (about 1% flow) would put 2,000 units near 20%, but that is judgement on a line of unknown length.

### 5.2 Cost C_APFC: 0.16 / 0.86 / 4.3 $M/yr

| | lo | mode | hi |
|---|---|---|---|
| Capex per deployment (one flowgate), 2025$ | $1.5M | $8M | $40M |
| Life | 20 yr | 20 yr | 20 yr |
| O&M | 2%/yr | 2%/yr | 2%/yr |
| C at 6% real, $M/yr (capex x 0.1072) | 0.16 | 0.86 | 4.3 |

**The evidence is bimodal.**
- **Planning and vendor estimates:**
  - EPRI's SPP deferral cases: $1.1-5.2M in 2015-18$, about $1.4-7.1M in 2025$. These are low-biased by EPRI's own statement (apfc-4).
  - EPRI VIC median: about $2.5M, derived from Table 3-18, with no dollar year (apfc-7).
  - RMI: about $1.67M per overload in 2024$, a derived average priced with a vendor script (apfc-39).
  - DOE NY: about $0.2-3.5M per site (syntheses-89).
- **As-built and utility figures, an order of magnitude higher:**
  - VNI: A$45M, about US$31M in 2022$ (FX of about 0.69 is derived) and about $34M in 2025$. Under the model's unit, this is **one deployment relieving one cut-set constraint, so about $34M per flowgate**. It is about US$15M per line (apfc-44).
  - PG&E: an unbuilt line at about $33M in 2016$, about $44M in 2025$ (apfc-56).
  - Sand Bar PST: $48-66M, a life-extension project, not flowgate relief (apfc-36).

**Capex values (judgement).**
- The mode is $8M, between the escalated planning group and the single as-built per-flowgate point.
- The hi is $40M, between VNI and PG&E. The new-apfc skeptic called VNI "high-middle" and PG&E the high bound.
- The lo is $1.5M (RMI; apfc-4 escalated).
- A single 345 kV PAC at $19.4M in 2018$, about $25M in 2025$ (apfc-6), sits inside the range.
- Revision 1 had a mode of $6M and a hi of $25M. It quoted VNI at about US$15M per line, which halved the per-flowgate anchor, and it left the anchors in nominal dollars.

**O&M at 2%/yr is judgement for hardware upkeep.** PG&E's software, OS and cellular costs were about $5k/yr for 90 DSRs, with physical maintenance unpriced (apfc-56). DOE's $250k of ongoing O&M is per GETs strategy, a portfolio of 12 PFCs (apfc-64 notes). Both sit well below 2% of capex. At 1%, C would fall about 9%.

**Price per kVA.** The $150-300/kVA FACTS price (syntheses-13) is uncited and was not used for C.

**u and C are physically coupled, but ev.py draws them independently.** Pairing a high u with a low C overstates EV. Under these priors APFC wins only above about $31M of rent at the modes, so the effect on the ranking is small.

### 5.3 Applicability

- **Gate.** APFC needs a meshed parallel path with spare headroom (apfc-28, the one item rated yes; syntheses-112; apfc-42). Radial lines get 0.
- **line_n1 0.40 = judgement of about 0.5 x judgement of about 0.8.**
  - Binding after a branch outage implies a loop exists. About 0.5 of loops have spare parallel headroom.
    - Context for that 0.5: Liftoff App. F, printed p.83, assumes 50% of US lines are "economically viable", meaning overloaded and tied to lines with spare capacity. This is a Guidehouse assumption, US-wide, not SPP. It is recorded under apfc-46 and apfc-51.
    - It is consistent with the 0.5 but is not its basis. The skeptic refuted it as a P in its own record (syntheses-43), because it is unconditional.
  - The x 0.8 covers equipment space: APFC works only on overhead lines with space (apfc-61). It also covers setpoint management (apfc-11).
  - EPRI found SPP N-1 load-pocket projects with no alternative path (apfc-13). Both N-0 and N-1 are eligible (syntheses-112).
- **Transformers 0.15-0.20 (judgement).** In EPRI's SPP Case 2, a phase-angle controller cleared a transformer-outage overload where variable-impedance devices could not (apfc-2, apfc-4). Support is thin.
- **Interface 0.25 (judgement).** VNI relieved a cut-set (apfc-44), but interfaces are often voltage- or stability-limited (apfc-13, apfc-61).
- **Moderate.** DOE rates APFC's applicability moderate, below TO's wide (apfc-51; qualitative only).

## 6. Topology Optimization

### 6.1 Relief share u_TO, entered as an effective share: 0.02 / 0.07 / 0.15

**Effective u = per-event flow relief x coverage x saturation.**
- Coverage is the share of binding hours in which a switch is valid and invoked. Cimarron's switch was used "on several occasions" (to-9). MISO's implemented switches were mostly temporary and tied to outages (to-49). SPP may revert a switch (to-82).
- Saturation is needed because relief of 0.2-0.3 exceeds the typical unbinding margin (section 3).

**Per-event flow relief at fixed dispatch, where a switch exists:**
- SPP's 20-snapshot study: 31% for feasible solutions (19 of 20) and 26% for preferred ones (14 of 20). The preferred figure is truncated at a 10% floor (to-3).
- DARCLAANOFTS: over 20% (to-4). Stonewall-Tupelo: 24% (to-5). Cimarron: about 20%, recomputed from SPP chart data (to-9).
- to-8: about 0.40 and 0.28 on two incidental constraints in one 5-minute snapshot. The relief is derived on an assumed initial loading of about 100%.
- SPP Oklahoma: 10-23% (to-11). MISO: 1-25% (to-12/13/14/15).
- ISO-NE: a stated 31% average (slides 6-7, 9). The chart's mean is about 24% and its median about 15%, by the verifier's digitization (to-23).
- Vendor envelope: 5-25% (to-24). Interfaces: 3-12.3% (to-20).

**mode 0.07, back-solved = 0.025 / h.mode 0.35.**
- The 0.025 starts from to-29's per-event u x h of about 0.03: RT production-cost savings over initial RT rent, n = 4 (slide 21).
- x judgement coverage of about 0.6, because to-29's 3% applies only in the snapshots where the switch is active.
- x judgement of about 1.4, because the authors call the result a lower bound (units fixed) and the denominator is penalty-priced RT rent.
- Without that uplift the mode would be 0.05. That variant changes no shortlist member (section 9).
- **Cross-check (judgement):** a per-event 0.20 x coverage 0.6 = 0.12 before saturation, x saturation of about 0.6 = 0.072.

**lo 0.02 (judgement).** A per-event 0.07 (MISO outage cases: 1-10% re-routed, to-13; 7-8%, to-14) x coverage of about 0.3, with saturation near 1 at small relief. to-29's 2% lower bound is no longer cited for u. It is a u x h quantity, and multiplying it by h again would haircut twice.

**hi 0.15 (judgement).** A per-event 0.25-0.40 (to-3's 31% feasible mean; to-8's 0.40 in a single case) x coverage of about 0.9 x saturation of about 0.5 gives 0.33 x 0.9 x 0.5 = 0.15. The physical per-event hi is about 0.45. The value also keeps u.hi x h.hi <= 0.08.

**Joint calibration with the shared h.** The audit asked for TO's own h, or for u_TO x h to stay inside to-29's 2-5% band at every h. **Neither is possible under the validated schema:**
- There is one haircut. With h independent of u, for any fixed u the product spans h.hi / h.lo = 5.3x, while the band [0.02, 0.05] spans only 2.5x.
- What the calibration achieves instead:
  - It is centred: u_TO x h has mean 0.026 and median 0.025.
  - 63% of draws fall inside 2-5%, 34% below and 3% above.
  - At the mode u, the product is 0.007 at h.lo, 0.025 at h.mode and 0.037 at h.hi.
- The 0.007 corner is below to-29's per-event band but inside the coverage-adjusted annual band of 0.3 x 0.02 = 0.006 to 0.9 x 0.05 = 0.045. It still understates TO's value in those draws, because at h.lo the TO-consistent u would be the physical 0.28.
- The proper fix is a per-GET haircut slot, or a correlated u-h draw, in ev.py (section 10).
- Using the physical triangle (0.05 / 0.20 / 0.45) instead raises flowgates with positive mean EV from 161 to 236, and changes 9 shortlist members (section 9).

**Pairing caution.** to-3's 26% (10% floor, hand-picked severe snapshots) was not paired with to-83's 0.55 (5% floor).

### 6.2 Cost C_TO: 0.05 / 0.175 / 0.87 $M/yr

No per-flowgate cost exists. C_TO = system cost / N + variable cost per active flowgate.

**System cost, about $1.0M / $5M / $15M per year SPP-wide (judgement):**
- **lo $1.0M/yr.** Derived from DOE Liftoff: a $1.5M licence plus a 2x data uplift is $4.5M one-time; x CRF(6%, 12 yr) plus 4 FTE x $120k gives $1.02M/yr (to-65, to-66). This is a small-IOU archetype, and the source includes no recurring software fee.
- **mode $5M/yr.**
  - NESO's vendor service is £1-5M/yr TSO-wide (to-63). The mid is about $4M at about 1.33 $/£ (judgement).
  - Liftoff's data uplift and staff add about $0.84M/yr: $3.0M x CRF(6%, 12 yr) plus 4 FTE x $120k. The $1.5M licence is left out here because it duplicates the vendor fee. The total, about $4.8M, is rounded to $5M.
  - Cross-check: MISO's $20M one-time GRIP proposal is about $2.4M/yr at 6%/12 yr, or $2.1-4.7M/yr over 15-5 years (derived; to-68; a proposal-stage budget that includes R&D).
- **hi $15M/yr.** NESO's high vendor fee of about $6.7M/yr, plus part of the integration labour NESO puts at about 70% of total cost of ownership (TCO) for ADMS-type systems (to-63). Applying the full 70% would give about $22M/yr.
  - The full share is not applied at the mode for three reasons. It is an ADMS analogy. It appears to be a lifetime TCO share dominated by one-time implementation. And Liftoff's one-time $4.5M and 4 FTE, which are already in the mode, cover part of it.
  - Revision 1 stated the 70% but did not apply any of it. Section 9 gives both bounds: vendor fee only, and the full ADMS share.

**N relieved flowgates, about 100 / 50 / 20 (judgement).**
- SPP's pilot found preferred solutions for 55 of 100 flowgates congested in real time (to-83).
- MISO implemented 9 reconfigurations in about 20 months (to-49).
- SPP's process has monthly caps (to-82).

**Variable cost per active flowgate, $0.04M / $0.075M / $0.12M per year:**
- **ISO review labour.** ERCOT's NPRR1198 puts it at $180-220k/yr ERCOT-wide (1 FTE) for about 4 EAPs, derived as about $45-55k per plan-year (to-70). The mode uses that rate.
  - The lo uses the variable-hours rate: 1,440 of 1,880 FTE hours, about $34-42k per plan. SPP's marginal ISO cost could be lower, because SPP already has a reconfiguration process (to-82). That is judgement, and the lo is not taken below the variable-hours rate.
- **Breaker wear.** $10-100 per switching cycle (to-73) x a judgement of about 200 cycles per year gives about $0.002M / $0.01M / $0.02M.
- **One-time study.** About $43k per overload, derived as RMI's $3.1M / 72 overloads (the source applies the fee per project, not per overload). Annualized, that is about $0.005M/yr (to-67, syntheses-116).
- **TO/TOP engineering.** Unpriced in every source. Judgement: $0 / $0.01M / $0.04M.
- **Not included.** ERCOT's one-time ISO setup cost of $50-80k (to-69) is probably not incurred by SPP, which already has a process.

**The denominator N decides whether median flowgates clear.**
- At N = 10, by the same formula, C would be 1.0/10 + 0.04 = 0.14, 5/10 + 0.075 = 0.575 and 15/10 + 0.12 = 1.62. That cuts 11 flowgates from the positive-EV set and changes 6 of 103 shortlist members (section 9).
- Revision 1's row labelled "N = 10" was correct only at its hi. Its lo and mode were not N = 10.

### 6.3 Applicability

- **line_n1 and transformer_n1: 0.30.**
  - **Technical feasibility, 0.55.** SPP's pilot found preferred single-switch solutions for 55 of 100 flowgates congested in real time (to-83). The pilot required at least 5% N-1 loading reduction, switching at 230 kV or below, and at most 30 MW radialized, so 0.55 is a **strict-screen lower bound** on feasibility. PJM gave 5 of 10 (to-85).
  - **Approval and implementation, x 0.56.** In MISO, 10 of 16 requests passed market review, and 9 of those 10 were implemented (to-49).
    - **The reliability stage (16 of 32) is excluded.** Judgement: to-83's preferred solutions had already passed SPP's N-1 checks, and the to-49 skeptic advises against multiplying that stage by 0.55 at full weight.
    - **Cross-check:** MISO's end-to-end rate is 9/32 = 0.28, or 0.28-0.31 after right-censoring (MISO RSC 2025, slide 4). That is P(implemented | vendor-screened submission), not P(applicable). The strict-screen bias in to-83 and the vendor screen in to-49 partly offset.
  - SPP adds production-cost, TCR and TOP tests, monthly caps and possible reversion (to-82).
  - Result: 0.55 x 0.56 = 0.31, entered as 0.30. Transformer N-1 constraints were solved at Hawthorn and Nashua (to-3, to-84) and at Cimarron (to-9).
- **line_n1_episodic 0.35 (judgement).** Outage-driven congestion is where MISO's implemented reconfigurations concentrate (to-49), and temporary switching fits episodes (to-12, to-14).
- **line_base and transformer_base 0.15 (judgement).** There is no SPP base-case evidence: every constraint in the SPP study is an N-1 form (to-84). TO solutions were found mostly for P1/P2/P4/P7 contingencies (syntheses-113, to-85).
- **Radial 0.** TO works only on meshed grids (syntheses-113).
- **Interface 0.15 = judgement of about 0.3 x about 0.5.** The 0.5 is **a relief-size proxy, not a probability**: boundary relief is 4.1-8.4% with switching only and 3-12.3% with existing PSTs, under severe outages (to-20), against about 0.2-0.3 for single elements (to-3). to-20 is u evidence. It sits in P only because u is shared across classes.

## 7. Combination (DLR+APFC, computed by ev.py)

There are no Combination priors. ev.py builds it as u_c = 1-(1-u_dlr)(1-u_apfc), with cost_dlr + cost_apfc and the shared h.

- **Additivity.** DOE NY found DLR and PFC nearly additive: 182 vs 184 GWh, and 0.79-1.08 across metrics (syntheses-7). That supports ev.py's rule.
- **Bias against the cap, quantified for revision 2.**
  - Combination's u_c x h averages 0.053, about 2x TO's 0.026 at comparable relief. It exceeds the 0.08 cap in 8.4% of draws; revision 1 had 38%.
  - TO's u was shaded for saturation, and Combination's was not.
  - Combination is the best option in draws that carry $1.8M of the $15.6M/yr sum of mean EV. Disabling it lowers the sum by only $0.5M (3%), because DLR or APFC fills in. It changes no shortlist member, and it flips 4 of the 103 recommendations from "none" to DLR.
- **The section 3 cap checks are per draw (single GETs) and aggregate (all options), not per flowgate.**
- **Excluded.** The Brattle/WATT SPP portfolio cost was used only as a cross-check: $90M of capex plus $10M/yr, with TO included and the figures supplied by vendors (to-74, to-75). It comes to about $0.3M/yr per hardware site all-in.

## 8. Vendor claims flagged

- **LineVision co-authored:** aar-35, aar-2, dlr-51, dlr-46, dlr-50, dlr-85, dlr-86, dlr-87 and dlr-82, plus the RMI DLR inputs (syntheses-114).
- **Smart Wires:**
  - device data and pricing behind apfc-4 and apfc-7 (vendor calculator);
  - apfc-39 and syntheses-112 (vendor script and data);
  - probably apfc-56;
  - Transgrid's attribution of all 170 MW to SmartValves (apfc-44 notes).
- **WATT Coalition (GET vendors):** the sources behind apfc-46, plus to-74 and to-75.
- **NewGrid/Brattle (P. Ruiz):** essentially all TO quantities (to-3/4/5/8/9/11/12/13/14/15/20/23/24/29/35/49/68/73/84; syntheses-57/75/76/78/113).
  - **Closest to independent:** SPP staff co-authorship and EMS validation (to-3, to-83); SPP chart data recomputed by the verifier (to-9); MISO's own counts (to-49); National Grid's own assessment (to-20); and the NESO/EY and ERCOT costs (to-63, to-69, to-70).
  - **Drift between the vendor's own documents** (new-to notes): Chub Lake 25% vs 56%; Alliant 18%/54% vs 49%/81%; MISO's $3.6M vs the vendor's $24.5M for Glenworth.
- **Utility self-reports relayed by FERC:** PPL (dlr-32/33/34/35).

## 9. Sensitivity: which priors move the ranking

**Break-even rent, given the GET applies:** R* = C / (u x h).

| GET | R* at modes | R* at triangle means | Flowgates with R_f > R* (mode) |
|---|---|---|---|
| DLR | $3.6M | $4.8M | 133 |
| TO | $7.1M | $14.0M | 64 |
| APFC | $31M | $63M | 11 |

For scale, the 2025 R_f quantiles are: median $0.07M; q75 $0.39M; q90 $1.95M; q95 $4.4M (the top-5% shortlist cut); q99 $23M.

**Scratch re-run of ev.py's EV mean.** This is a numpy re-implementation (a scratch script used during the research; not in the repo): point rent with no forward band, 4,000 draws and the same element-shared applicability draws. It is not the model's output.

The shortlist is the top 103 flowgates by mean EV, and "changed" counts the members that leave the base shortlist. The class mix columns are line_n1 / transformer_n1 / line_n1_episodic / interface.

| Variant | Flowgates with mean EV > $1k | Sum of mean EV ($M/yr) | Shortlist members changed (of 103) | Top-103 class mix |
|---|---|---|---|---|
| Base (this file) | 161 | 15.6 | 0 | 79 / 15 / 4 / 5 |
| Revision 1 file, for reference | 238 | 31.0 | 5 | n/a |
| Combination disabled | 161 | 15.1 | 0 | 79 / 15 / 4 / 5 |
| h x 0.5 | 84 | 5.2 | 1 | 78 / 15 / 4 / 6 |
| h = 0.05 / 0.20 / 0.50 | 135 | 10.5 | 2 | 78 / 15 / 4 / 6 |
| h.hi = 0.70 (cap not enforced) | 186 | 20.1 | 1 | 80 / 15 / 4 / 4 |
| h x 1.5 (capped at 1) | 215 | 28.7 | 1 | 80 / 15 / 4 / 4 |
| All C x 2 | 98 | 10.3 | 1 | 78 / 15 / 4 / 6 |
| All C x 0.5 | 233 | 21.7 | 1 | 80 / 15 / 4 / 4 |
| P_DLR(line_*) x 0.5 | 140 | 11.7 | 5 | 74 / 19 / 4 / 6 |
| P_DLR(line_*) + 0.25 | 177 | 20.6 | 2 | 80 / 14 / 5 / 4 |
| P_TO all x 2 (capped at 1) | 163 | 19.9 | 4 | 75 / 19 / 4 / 5 |
| P_TO(transformer_n1) = 0.6 | 162 | 17.2 | 5 | 76 / 20 / 3 / 4 |
| P_interface = 0.5 for all GETs | 161 | 16.8 | 2 | 77 / 15 / 4 / 7 |
| u_TO physical 0.05 / 0.20 / 0.45 | 236 | 36.7 | 9 | 71 / 24 / 3 / 5 |
| u_TO mode 0.05 (coverage, no lower-bound uplift) | 159 | 15.1 | 0 | 79 / 15 / 4 / 5 |
| C_TO at N = 10 (0.14 / 0.575 / 1.62) | 150 | 13.5 | 6 | 84 / 10 / 5 / 4 |
| C_TO, full ADMS integration share (system 4.4 / 13 / 22; C 0.084 / 0.335 / 1.22) | 153 | 14.5 | 3 | 81 / 13 / 5 / 4 |
| C_TO, vendor fee only (system 1.0 / 4.0 / 6.7; C 0.05 / 0.155 / 0.455) | 165 | 16.9 | 4 | 76 / 19 / 3 / 5 |
| C_TO of revision 1 (0.035 / 0.12 / 0.50) | 168 | 17.0 | 5 | 75 / 20 / 3 / 5 |
| u_DLR over seasonal 0.05 / 0.13 / 0.30 | 242 | 28.3 | 6 | 84 / 10 / 5 / 4 |
| u_DLR hi = 0.20 | 185 | 18.3 | 2 | 81 / 14 / 4 / 4 |
| C_DLR at 10-yr life (0.036 / 0.113 / 0.284) | 155 | 15.3 | 1 | 78 / 16 / 4 / 5 |
| C_DLR joint-extreme build (0.031 / 0.10 / 0.284) | 160 | 15.5 | 0 | 79 / 15 / 4 / 5 |
| C_APFC hi at VNI scale ($34M x 0.1072 = 3.6) | 161 | 15.9 | 0 | 79 / 15 / 4 / 5 |

How to read the re-run:

- **R_f dominates the order within a class.** The Spearman correlation of mean EV with R_f over the top 300 is 0.96, and 89% of the EV top-103 are also in the R_f top-103.
- **h and C set magnitudes and the positive-EV frontier, not the shortlist.**
  - h x 0.5 cuts sum EV by two-thirds but changes 1 shortlist member.
  - All C x 2 changes 1 member.
- **Cross-class order is the most prior-sensitive part.**
  - Mean EV per dollar of rent (R_f > $5M) is 1.19% for line_n1, 0.41% for transformer_n1 and 0.38% for interface.
  - That gap comes from P_DLR(line) being nonzero while P_DLR(transformer) is 0, set against P_TO(transformer_n1) x u_TO.
  - The largest changes in shortlist membership are 9 of 103 (8.7%) for physical u_TO, 6 (5.8%) for C_TO at N = 10, 6 (5.8%) for the DLR baseline choice, and 5 (4.9%) each for P_DLR(line) x 0.5 and P_TO(transformer_n1) = 0.6.
- **Recommendation labels are the most fragile output; see section 10, item 2.**

## 10. Biggest uncertainties, the priors the ranking is most sensitive to, and model-structure flags

**What the ranking is most sensitive to.**
- **Shortlist membership:** u_TO (effective vs physical), the TO cost denominator N, the DLR baseline (over AAR vs over seasonal), and P_DLR for lines vs P_TO for transformer N-1.
- **Magnitudes and the positive-EV frontier:** h first, then C, u_TO and the DLR baseline.

1. **h scales every EV, and h.hi is now set by a cap rather than by evidence about h.**
   - The production-cost evidence is n = 1-4 per point, mostly RT or simulated.
   - h is calibrated with the h(u) argument (section 3), not measured on SPP DA rent.
   - If to-29's floor of about 0.1 were the truth for every GET, EV would roughly fall by two-thirds, as in the h x 0.5 row.
2. **The recommendation output is fragile.**
   - No P exceeds 0.45, so in the scratch re-run "No cost-effective GET" was the option most often best for all of the top 103 flowgates, even though their mean EV is positive. ev.py's recommendation is the plurality option, so it will often read "none" under these priors.
   - Consider reporting p_best shares, or the best option conditional on applicability.
   - Raising P_DLR(line_*) by 0.25 flips 42 of the 103 to DLR, and doubling P_TO flips 17 to TO.
3. **P_DLR for lines (the conductor-limited share)** rests on a 30-year-old, second-hand 42% (dlr-80) plus single cases. It is the main lever on line vs transformer ordering.
4. **TO's effective u and cost denominator.**
   - Coverage, saturation, the lower-bound uplift, N and the integration share are all judgement.
   - All the TO evidence runs through one vendor.
   - The to-29 product is used twice (section 3).
5. **APFC cost is bimodal and its u and C are coupled.** Planning estimates run $1-7M; as-built and utility figures run $34-44M per flowgate. APFC rarely wins below about $31M of rent.
6. **DLR's gain over AAR in SPP binding hours** has no SPP measurement: there was no SPP DLR deployment in 2019-2026 (negatives). The sign of the wind correlation differs between wind-export and summer-peak constraints.
7. **AAR timing and the post-2027 shrink in R_f** are not in the model (section 4.1). The model uses 2025 pre-AAR rent.
8. **Structural limits:**
   - One shared h, drawn independently of u. This forces the TO workaround and the cap enforced through h.hi, and it leaves Combination above the cap in 8.4% of draws.
   - **A per-GET haircut slot, or a u-h correlation, is the recommended model change.**
   - P absorbs class-specific relief differences for interfaces, because u is shared across classes.
   - The linear u x R cannot represent rent that vanishes at u = 0.05-0.10 (dlr-13, dlr-34). For production-cost value this effect sits inside h.
   - Most of the evidence is RT or simulated, while R_f is DA.

## 11. Change log: audit items and resolutions

| Audit item | Resolution |
|---|---|
| haircut.lo: 0.10 presented as h; syntheses-75 cited; "DA breaches rare" uncited | Labelled judgement: a derived floor = to-29 slide 21 / slide 17 all-case flow relief, noting the rent-to-production-cost conversion. syntheses-75 dropped for h. The breach claim is now cited to my snapshot count (54 of 135,820). |
| TO relief_share.mode: not 0.20 x 0.6; double use of to-29; 0.007 at h.lo | The comment now says mode = 0.025 / 0.35, back-solved, with coverage and lower-bound uplift explicit. 0.20 x 0.6 x 0.6 kept only as a judgement cross-check. Double use acknowledged (sections 3 and 6.1). The "inside 2-5% at every h" target is infeasible with one shared h; the calibration is centred and its in-band shares are reported (section 6.1). |
| haircut.hi: to-35 misread as a cap on h; u.hi x h.hi = 0.14 > 0.08 | Restated as a joint cap. h.hi lowered from 0.70 to 0.53, with every u.hi = 0.15, so u.hi x h.hi = 0.0795. apfc-9's 0.92 is directional only. Aggregate check: 1.0% of rent. |
| haircut.mode: anchors on a rent basis | 0.35 labelled judgement on the h(u) argument. dlr-85 (rent, spillover only), apfc-9 (APC / rent reduction, Table 3-7; ~0.06-0.12 vs base rent) and dlr-13 (rent, not h) are directional only. |
| TO relief_share.lo: coverage unlabelled; to-29's 2% used in u | Coverage labelled judgement. to-29 removed from u. Anchors are to-13 and to-14. |
| TO relief_share.hi: to-23 misquoted; arithmetic did not reach 0.20 | to-23 quoted as a stated 31% average (chart mean ~24%, median ~15%, verifier). Per-event range 0.25-0.40 (to-3, to-8). Saturation 0.5 stated, so the factors multiply to 0.15. hi lowered from 0.20 to 0.15 for the cap. |
| DLR mode: 86% uncited; aar-18 basis; emergency double count | Now 98% of line rent, computed from the repo data. aar-18 restated as median hour, median line, vs ILR. The emergency factor applied only to normal-rating anchors; dlr-33's emergency figure used directly. |
| DLR C lo: O&M uncited | Liftoff's ~$0.016M/yr (syntheses-61) is now the lo O&M. |
| DLR C hi: $0.08M unlabelled; SPP EMS cost is per TO | Labelled judgement as an upper tail of program cost. Capex hi anchored on BPA and Oncor (escalated). |
| DLR C mode: Liftoff O&M basis misstated; nominal anchors | O&M restated as Liftoff ~$0.016M plus judgement ~$0.015M. dlr-35 is per corridor (<$0.3M per circuit). Anchors escalated; capex mode raised from $0.5M to $0.6M, giving C 0.10. |
| TO C lo: $1.5M/yr unsupported; N and variable unlabelled; $43k derived | System lo is now $1.0M/yr, derived from to-65/66. N and variable labelled judgement. $43k marked as derived and per project. |
| TO C mode: derived figures presented as source values | to-70, to-73 and to-68 rewritten as derivations. |
| APFC P line_n1: 0.5 misattributed to apfc-61 | 0.5 is now judgement, with Liftoff App. F p.83 (apfc-46/51) as context only, because syntheses-43 refuted it as a P. apfc-61 is kept only for space. 0.8 labelled judgement. |
| APFC C mode: "no O&M datum"; VNI per line; FX and apfc-7 median not marked derived | apfc-56 and DOE O&M quoted. VNI quoted as US$31M per cut-set = per flowgate. FX and median marked as derived. Capex mode raised from $6M to $8M and hi from $25M to $40M (escalation plus the per-flowgate anchor). |
| APFC u hi: apfc-56 relief not stated | apfc-56 removed as relief support. apfc-12 kept as the cap. hi lowered from 0.20 to 0.15 for the to-35 cap. |
| APFC u mode: shading unlabelled; provenance | Shading and the x 0.8 labelled judgement. DOE/WATT/interview provenance and the conditioning on viable lines added. |
| TO P line_n1: reliability stage dropped silently; to-83 bias | Exclusion stated as judgement. MISO end-to-end 0.28-0.31 given as a cross-check. to-83 noted as a strict-screen lower bound. Value lowered from 0.35 to 0.30 (0.55 x 0.56). transformer_n1 follows. episodic lowered from 0.40 to 0.35. |
| TO and DLR P interface: relief size folded into P | Tagged "relief-size proxy, not a probability". to-20 cited as u evidence. |
| DLR u lo: baselines | Rewritten. aar-2 (22%/27% below AAR) added as the like-for-like datum. |
| DLR u hi: static vs AAR | Converted to an over-AAR figure (9-16% summer, 22-30% winter). hi lowered from 0.20 to 0.15. |
| DLR P line_base: 47% derived; dlr-51 post-screen | Both marked. 0.45 labelled judgement within 0.3-0.6. |
| s.6.2/s.9: N = 10 row wrong | Recomputed with the formula (0.14 / 0.575 / 1.62): 6 of 103 changed. |
| s.9: Jaccard read as share changed | Changed members are now reported directly. |
| DLR hi through the same adjustments | Done (0.12-0.17). Set to 0.15. |
| DLR mode: YAML vs md mismatch | One chain in both documents (section 4.2). |
| TO TCO adder unapplied | Partly applied (system mode $5M, hi $15M). Both bounds shown in section 9, with the reason for not applying the full share. |
| s.2 nominal anchors | Escalated with the s.2 factors, quoted with their nominal years. |
| 86% includes transformers | Replaced by 98% of line rent (93.1% of all rent across all N-1 classes). |
| TO variable mode below the sum of its components | Raised to $0.075M. lo at the variable-hours ISO rate. |
| TO u hi YAML comment | Saturation added. to-8 and the physical 0.45 cited. |
| s.3/s.7 Combination cap | Quantified: 8.4% of draws above 0.08, mean 0.053, 3% of sum EV, 0 shortlist changes. Cap checks stated as per draw and aggregate. |
| s.2 discount rate "4-6%" | Recomputed for the new C: DLR 2.9-3.8%, APFC 6.7%, TO under 1%. |
| s.4.1 aar-8 "5-10%" | Now 7-10% (Table 11). |
| s.9 "APFC C hi at VNI scale (6.0)" | Relabelled VNI at $34M x 0.1072 = 3.6. The base hi of 4.3 is now PG&E/VNI scale. |
| s.6.2 to-69 called SPP's | Reworded as ERCOT's setup cost, probably not incurred by SPP. |
| s.6.1 0.006 | Recomputed (0.007 / 0.025 / 0.037 at the mode u). |
| C lo/hi joint extremes | All C now built at mid-life; joint-extreme and 10-year variants given in section 9. |

## 12. Citation table (per number)

URLs are as verified. The Liftoff host is dead, so the Wayback copy is given. "Derived" marks my arithmetic or the verifier's, not a figure the source states.

| Number used | Evidence id | Title | Publisher | Year | URL | Locator | Skeptic caveat (short) |
|---|---|---|---|---|---|---|---|
| h lo (savings ~3% (2-5%) of relieved RT rent; 26-31% all-case flow relief) | to-29 | Transmission Topology Optimization: Congestion Relief in Operations and Operations Planning | The Brattle Group / NewGrid (SPP MWG) | 2018 | https://www.brattle.com/wp-content/uploads/2021/05/14679_brattle_newgrid_topology_optimization_study_-_spp_mwg_20181010.pdf | slides 17, 20-21 | n=4, severe snapshots, units fixed (lower bound), RT; vendor-affiliated; u x h, not h |
| DA breach count (54 of 135,820 binding hours); class rent shares (98% of line rent N-1; 93.1% of all rent) | (repo data) | SPP congestion by constraint, 2025 monthly files (Headroom snapshot 2026-10-13.r2) | Southwest Power Pool | 2025 | https://portal.spp.org/file-browser-api/download/congestion-by-constraint | normalized/congestion.csv: breached_hours, hours_binding, rent_musd; my counts | observed data; class assignment by Headroom's evidence_class rule |
| h mode, directional (net/own rent 0.38) | dlr-85 | Simulating the Economic Impact of a DLR Project in an RTO Environment | PJM, AEP, LineVision (CIGRE-US NC) | 2018 | https://cigre-usnc.org/wp-content/uploads/2018/10/1C_4_B2_Murphy.pdf | Table 1, p.6 | n=1, rent basis; spillover only; do not stack |
| h mode, directional (+5% removes up to 60% of target rent) | dlr-13 | DLR Systems for Transmission Lines: Topical Report (SGDP) | US DOE OE | 2014 | https://www.energy.gov/sites/prod/files/2016/10/f34/SGDP_Transmission_DLR_Topical_Report_04-25-14.pdf | Sec 5.2, printed p.44 | not h; DAM what-if reruns, 2011 |
| h mode/hi, directional (APC / rent reduction 0.92 for 1 device, 0.32-0.37 for 4-17; base rent 589 M$) | apfc-9 | Benefits and Value of New Power Flow Controllers (3002013930) | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | Table 3-7, PDF p.86 | different quantity; no u; directional only |
| h hi cap (production-cost cost of congestion ~8.3% of DA rent; TO savings ~3% of rent) | to-35 | Transmission Topology Optimization: Simulation of Impacts in PJM Day-Ahead Markets | NewGrid et al. (FERC AD10-12-007) | 2016 | https://www.ferc.gov/sites/default/files/2020-08/T-4B-2-Ruiz.pdf | slides 11, 15, 19 (bars decoded by verifier) | PJM 2010, system-wide weekly totals; viability assumed |
| DLR u (6-14% / 8-12% over AAR); DLR hi cap (125% / ~133% of static) | dlr-7; dlr-10 | DLR Systems for Transmission Lines: Topical Report (SGDP) | US DOE OE | 2014 | https://www.energy.gov/sites/prod/files/2016/10/f34/SGDP_Transmission_DLR_Topical_Report_04-25-14.pdf | Table 2 p.19; Sec 4.2.2 p.33; Table 6, fn 9 p.28 | all-hours quarterly; upward-conditioned; one utility's policy; cap is % of static |
| DLR u mode (17% normal; 8.5-16.5% emergency over AAR) | dlr-33 | Implementation of DLR, ANOPR RM24-6-000 (89 FR 57690) | FERC | 2024 | https://www.govinfo.gov/content/pkg/FR-2024-07-15/pdf/2024-14666.pdf | P 57 (89 FR 57698) | PPL self-report via FERC; averaging unstated |
| DLR u (16.3% over AAR; below AAR 22%/27% of hours; AAR +15.1%/+2.7% over static) | aar-2 | An Empirical Analysis of the Operational Efficiencies and Risks Associated with Static, Ambient Adjusted, and DLR Methodologies | CIGRE-US NC (LineVision / National Grid) | 2021 | https://cigre-usnc.org/wp-content/uploads/2021/11/An-Empirical-Analysis-of-the-Operational-Efficiencies-and-Risks-Associated-with-Line-Rating-Methodologies.pdf | Summary p.2; Tables 4-5 p.8 | n=1, normal ratings, no ceiling; vendor co-author |
| DLR u (median line vs ILR: -7% at 20% of hours, +8% at the median, +26% at 20%) | aar-18 | Hourly DLR for existing transmission across CONUS (preliminary), NREL/PR-6A40-91599 | NREL | 2024 | https://www.osti.gov/servlets/purl/2476805 | slides 14, 26, 32 | modelled, real-time, vs ILR not AAR, preliminary |
| DLR u lo (DLR below traditional ratings 20-40% of the time) | aar-37 | Dynamic Line Ratings (Quick Insights 3002030550) | EPRI | 2024 | https://restservice.epri.com/publicdownload/000000003002030550/0/Product | p.4 | all hours; baseline undefined |
| DLR u direction (wind regions; DLR below SLR in summer) | syntheses-20; dlr-75; dlr-76 | GETs: A Case Study on Ratepayer Impact | US DOE | 2022 | https://www.energy.gov/sites/default/files/2022-04/Grid%20Enhancing%20Technologies%20-%20A%20Case%20Study%20on%20Ratepayer%20Impact%20-%20February%202022%20CLEAN%20as%20of%20032322.pdf | printed pp.40-45; Table 19 | simulated NYISO; no DLR floor; direction only |
| DLR u (headroom when not needed; hot/calm hours) | dlr-21; dlr-22 | DLR Topical Report (SGDP) | US DOE OE | 2014 | https://www.energy.gov/sites/prod/files/2016/10/f34/SGDP_Transmission_DLR_Topical_Report_04-25-14.pdf | printed p.41; p.37 | qualitative; NYPA example hypothetical |
| DLR baseline (four seasons; 240 hourly forecast AARs; transformers excluded; Exception) | aar-32 | SPP Operational Ratings Methodology | SPP | 2025 | https://spp.org/documents/71513/spp%20operational%20ratings%20methodology.pdf | printed pp.3, 5, 6, 8-9 | pre-final draft; gate structure |
| AAR shrink (AAR-only first-order 9.9% / 7.1% / 9.8% of RT congestion value) | aar-8 | 2024 State of the Market Report for MISO (and 2025 SOM) | Potomac Economics | 2025 | https://potomaceconomics.com/wp-content/uploads/2025/06/2024-MISO-SOM_Report_Body_Final.pdf | p.67 Table 11 (2025 SOM p.68) | first-order h=1; RT; temperature only; MISO |
| AAR shrink (4.7% achieved on 170 facilities; night +11%) | aar-5; aar-6 | Comments of Potomac Economics, RM20-16-000 | Potomac Economics | 2021 | https://www.potomaceconomics.com/wp-content/uploads/2022/01/20210323-5024_PE-Comments-on-TX-Line-Rating-NOPR.pdf | p.9 Table 2; p.15 | first-order; South mixes emergency lever; calculation |
| C_AAR = 0 per flowgate | aar-28 | Order No. 881 (87 FR 2244) | FERC | 2021 | https://www.govinfo.gov/content/pkg/FR-2022-01-13/pdf/2021-27735.pdf | P 49, P 94 | no EV_AAR option |
| DLR P gate (conductor-limited only; ratings ceiling) | aar-35 | An Empirical Analysis ... (CIGRE-US) | CIGRE-US NC | 2021 | https://cigre-usnc.org/wp-content/uploads/2021/11/An-Empirical-Analysis-of-the-Operational-Efficiencies-and-Risks-Associated-with-Line-Rating-Methodologies.pdf | Sec 1, p.3 | no frequency given; vendor co-author |
| DLR P line_base (42% conductor, ~10% transformer; 47% derived) | dlr-80 | GETs: A Case Study on Ratepayer Impact | US DOE | 2022 | https://www.energy.gov/sites/default/files/2022-04/Grid%20Enhancing%20Technologies%20-%20A%20Case%20Study%20on%20Ratepayer%20Impact%20-%20February%202022%20CLEAN%20as%20of%20032322.pdf | printed p.7; App A.2.1 p.82 | 1996 NY via DOE; denominator undefined; stale |
| DLR P (8 of 10 after screen; terminal headroom ~11-17% of static, derived) | dlr-51 | Lessons from first deployment of DLR: 42 LineVision sensors on 5 diverse AES lines | AES / LineVision | 2024 | https://www.aes.com/sites/aes.com/files/2024-04/AES-LineVision-Case-Study-2024.pdf | Table 1, p.6 | post-screen; not a base rate; vendor co-author |
| DLR P (showcase line station-limited) | dlr-87 | Simulating the Economic Impact of a DLR Project ... | PJM, AEP, LineVision | 2018 | https://cigre-usnc.org/wp-content/uploads/2018/10/1C_4_B2_Murphy.pdf | pp.4-5 | n=1 |
| DLR P N-1 (sensor DLR fails at low loading) | dlr-15 | DLR Topical Report (SGDP) | US DOE OE | 2014 | https://www.energy.gov/sites/prod/files/2016/10/f34/SGDP_Transmission_DLR_Topical_Report_04-25-14.pdf | printed p.v, p.10 | stale technology |
| DLR P interface (thermal vs stability by length) | dlr-3 | Dynamic Line Rating, Report to Congress | US DOE | 2019 | https://www.energy.gov/sites/prod/files/2019/08/f66/Congressional_DLR_Report_June2019_final_508_0.pdf | printed pp.23-24, Fig 4 | rule of thumb; length is CEII |
| DLR P N-1 (SPP N-1 161 kV flowgate DLR-applicable) | dlr-82 | Economic Impacts of DLR on a Congested Line in SPP | SPP, AEP, LineVision (CIGRE-US) | 2018 | https://cigre-usnc.org/wp-content/uploads/2018/10/1C_3_B2_Mehraban.pdf | pp.1, 5 | not h; 300 RT minutes; best case |
| DLR capex mode (<$0.5M per two-circuit 230 kV corridor) | dlr-35 | Implementation of DLR, ANOPR RM24-6-000 | FERC | 2024 | https://www.govinfo.gov/content/pkg/FR-2024-07-15/pdf/2024-14666.pdf | P 74 (89 FR 57700) | upper bound; scope unstated |
| DLR capex (~$1M for 3 segments, 18 sensors) | dlr-32 | ANOPR RM24-6-000 | FERC | 2024 | https://www.govinfo.gov/content/pkg/FR-2024-07-15/pdf/2024-14666.pdf | P 57 | PPL cluster duplicate |
| DLR capex mode (~$0.5M per line, 2018$) | dlr-86 | Simulating the Economic Impact of a DLR Project ... | PJM, AEP, LineVision | 2018 | https://cigre-usnc.org/wp-content/uploads/2018/10/1C_4_B2_Murphy.pdf | Conclusion, p.7 | estimate; excludes station upgrade |
| DLR capex lo/hi and O&M hi ($0.1-0.2M/line MISO TOs; BPA >$1M; SPP EMS up to $1M or $0.1-0.5M/yr SCADA) | dlr-40 | Implementation of DLR, NOI AD22-5-000 (cited in ANOPR RM24-6 P 123 fn 155) | FERC | 2022 | https://www.ferc.gov/sites/default/files/2022-02/AD22-5-000.pdf | NOI p.10, fn 24 | third-hand, ex-ante, thin; SPP costs per TO |
| DLR capex hi (~$1.4M per ~100 km, APG) | dlr-55 | A Guide to Case Studies of GETs (INL/MIS-22-69711) | INL | 2022 | https://inldigitallibrary.inl.gov/sites/sti/sti/Sort_64025.pdf | printed p.23 | secondary; scope unstated |
| DLR capex hi (Oncor $4.833M / 8 circuits, derived ~$0.6M) | dlr-17 | DLR Topical Report (SGDP) | US DOE OE | 2014 | https://www.energy.gov/sites/prod/files/2016/10/f34/SGDP_Transmission_DLR_Topical_Report_04-25-14.pdf | Table 2, printed p.18 | demo; validation gear |
| DLR capex cross-check ($17-56k/mi) | dlr-24 | Case Study: Oncor's DLR Project | US DOE | 2014 | https://www.energy.gov/sites/prod/files/2014/08/f18/Oncor-SGIG-case-study-May2014.pdf | Table 2, p.5 | per mile; length CEII |
| DLR capex cross-check ($45k/mi incl. 20 yr software) | dlr-46; dlr-50 | Lessons from first deployment of DLR (AES) | AES / LineVision | 2024 | https://www.aes.com/sites/aes.com/files/2024-04/AES-LineVision-Case-Study-2024.pdf | Table 4, fn 12, p.14 | vendor; per mile; horizon is not a life |
| DLR O&M lo/mode ($70k licence x 20% + $15k hardware x 10% = $15.5k/line; FTE $120k); capex template (~$0.6M/line) | syntheses-61; syntheses-60 | Pathways to Commercial Liftoff: Innovative Grid Deployment | US DOE | 2024 | https://web.archive.org/web/20250122191308id_/https://liftoff.energy.gov/wp-content/uploads/2024/04/Liftoff_Innovative-Grid-Deployment_Final_4.15.pdf | printed p.87 Key Cost Inputs; Table 11 p.84 | illustrative BCA inputs; "% of CAPEX" for software is an interpretation |
| DLR capex cross-check (~$0.32M per overload) | syntheses-114 | GETting Interconnected in PJM | RMI (Quanta) | 2024 | https://rmi.org/wp-content/uploads/dlm_uploads/2024/02/GETs_insight_brief_v3.pdf | Exhibit ES2 p.6; Exhibit 8 p.20 | per planning overload; LineVision input |
| DLR and PFC capex cross-check (DLR region ~$2M; PFC $7-28M) | syntheses-89 | A Guide to Case Studies of GETs | INL | 2023 | https://inl.gov/content/uploads/2023/03/A-Guide-to-Case-Studies-for-Grid-Enhancing-Technologies.pdf | p.8 | box midpoints of DOE Fig 25; planning |
| DLR/TO life 10-15 yr, 12 central; O&M $250k per strategy | dlr-77; syntheses-11; apfc-64; dlr-79 | GETs: A Case Study on Ratepayer Impact | US DOE | 2022 | https://www.energy.gov/sites/default/files/2022-04/Grid%20Enhancing%20Technologies%20-%20A%20Case%20Study%20on%20Ratepayer%20Impact%20-%20February%202022%20CLEAN%20as%20of%20032322.pdf | printed p.60; p.64 | uncited, generic; per strategy |
| APFC u mode/hi (10/17/25%; applied to the 50% of lines assumed viable) | apfc-46 | Pathways to Commercial Liftoff | US DOE | 2024 | https://web.archive.org/web/20250122191308id_/https://liftoff.energy.gov/wp-content/uploads/2024/04/Liftoff_Innovative-Grid-Deployment_Final_4.15.pdf | fn 16 printed p.15; App F printed p.83 | national assumption; DOE/WATT/interviews |
| APFC u lo (1-13% flow relief) | apfc-1 | Benefits and Value of New PFCs (3002013930) | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | Table 4-1, PDF p.114 | sized to need; cherry-picked |
| APFC u hi cap (saturation above ~15 deg) | apfc-12 | Benefits and Value of New PFCs | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | Table 3-8, PDF pp.89-90 | PJM portfolio; text says "3%", table 3.8% |
| APFC u mode (preventive setpoint only) | apfc-16 | Optimized PFC Device Siting with Coupled Production Cost / AC Powerflow Modeling | Telos Energy / CIGRE-US NC | 2023 | https://cigre-usnc.org/wp-content/uploads/2024/06/Optimized-Power-Flow-Control-Device-Siting-with-Coupled-Production-Cost-AC-Powerflow-Modeling-Paper.pdf | p.2 | convention, not evidence |
| APFC u mode (setpoint-regime risk) | apfc-11 | Benefits and Value of New PFCs (relaying NYISO 2014 SOM) | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | PDF p.70 | not h; secondary |
| APFC capex lo ($1.1-5.2M per SPP case) | apfc-4 | Benefits and Value of New PFCs | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | Exec Table 2, PDF p.17 | cherry-picked; vendor calculator |
| APFC capex (a $19.4M PAC sits inside the range) | apfc-6 | Benefits and Value of New PFCs | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | Table 3-16, PDF p.103 | prototype cost curve; PJM |
| APFC capex (VIC median ~$2.5M, derived) | apfc-7 | Benefits and Value of New PFCs | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | Table 3-18, PDF p.106 | vendor calculator; superseded DSR |
| APFC capex lo (~$1.67M per overload, derived average) | apfc-39 | GETting Interconnected in PJM | RMI (Quanta) | 2024 | https://rmi.org/wp-content/uploads/dlm_uploads/2024/02/GETs_insight_brief_v3.pdf | Exhibit 8, p.20 | vendor script; interconnection overloads |
| APFC capex mode/hi (A$45M as-built, one cut-set, 170 MW) | apfc-44 | Victoria to NSW Interconnector upgrade | Transgrid | 2022 | https://transgrid.com.au/VNI | project page; 2020 PACR Table 4 | cut-set; Australian costs; 170 MW credited to the whole option |
| APFC capex hi (~$33M for ~2,000 DSRs); O&M (~$5k/yr per 90 DSRs) | apfc-56 | Improving Transmission Operation with Advanced Technologies (and PG&E EPIC 1.09C) | Brattle / Grid Strategies | 2019 | https://www.brattle.com/wp-content/uploads/2021/05/16634_improving_transmission_operating_with_advanced_technologies.pdf | printed p.11, fn 41 | never built; 2016$; vendor pricing likely |
| APFC capex beyond hi ($47.7-66.3M) | apfc-36 | Sand Bar PST Asset Condition (PAC) | ISO-NE / VELCO | 2024 | https://www.iso-ne.com/static-assets/documents/100008/a04_2024_02_28_pac_sand_bar_pst_asset_condition_presentation.pdf | slide 15 | PST life extension, not flowgate relief |
| APFC $/kVA (not used for C) | syntheses-13 | GETs: A Case Study on Ratepayer Impact | US DOE | 2022 | https://www.energy.gov/sites/default/files/2022-04/Grid%20Enhancing%20Technologies%20-%20A%20Case%20Study%20on%20Ratepayer%20Impact%20-%20February%202022%20CLEAN%20as%20of%20032322.pdf | printed p.87, App A.3.2 | uncited price |
| APFC life (25 yr book, 8% nominal) | apfc-8 | Benefits and Value of New PFCs | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | fn 3, PDF p.104 | book life; nominal rate |
| APFC P (no parallel path, no relief) | apfc-28 | Use of PFCs to Enhance Transmission Network Utilisation on the Irish Network | EirGrid et al. (arXiv) | 2019 | https://arxiv.org/pdf/1911.06677 | Sec 3.2.3, Fig 6, p.8 | skeptic: yes |
| APFC P (meshed only; N-0 and N-1) | syntheses-112 | GETting Interconnected in PJM | RMI (Quanta) | 2024 | https://rmi.org/wp-content/uploads/dlm_uploads/2024/02/GETs_insight_brief_v3.pdf | p.14 | qualitative |
| APFC P (no SPP alternative path; interfaces voltage/stability) | apfc-13 | Benefits and Value of New PFCs | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | PDF pp.136-137; p.3-19 | "a few" projects; no rate |
| APFC P (shifts congestion; no PJM APFC in service) | apfc-42 | APFC GETs Technical Reference Guide | PJM | 2024 | https://www.pjm.com/-/media/DotCom/about-pjm/advanced-technologies/apfc.pdf | pp.3, 6 | planning guidance |
| APFC P (overhead with space only) | apfc-61 | Advanced Transmission Technologies | US DOE | 2020 | https://www.energy.gov/sites/default/files/2021/02/f82/Advanced%20Transmission%20Technologies%20Report%20-%20final%20as%20of%2012.3%20-%20FOR%20PUBLIC.pdf | printed pp.19-20 | qualitative |
| APFC P (moderate applicability; 50% viable, context only) | apfc-51 | Pathways to Commercial Liftoff | US DOE | 2024 | https://web.archive.org/web/20250122191308id_/https://liftoff.energy.gov/wp-content/uploads/2024/04/Liftoff_Innovative-Grid-Deployment_Final_4.15.pdf | printed p.24; App F p.83 | interviews; ordering only; 50% refuted as a P (syntheses-43) |
| APFC P transformer (PAC cleared a transformer-outage overload) | apfc-2 | Benefits and Value of New PFCs | EPRI | 2018 | https://restservice.epri.com/publicdownload/000000003002013930/0/Product | PDF pp.117-119 | image-only relief; planning snapshot |
| TO u (31% feasible 19/20; 26% preferred 14/20) | to-3 | Application of Topology Optimization in Real-Time Operations | CIGRE-US NC (SPP, NewGrid, Brattle) | 2019 | https://cigre-usnc.org/wp-content/uploads/2019/10/4B_3.pdf | pp.2, 5 | conditional; selected snapshots; fixed dispatch |
| TO u (DARCLAANOFTS >20%; Stonewall-Tupelo 24%); TO P loopflow | to-4; to-5; syntheses-76 | TTO Case Studies in SPP and ERCOT (FERC AD10-12-011) | NewGrid with SPP / ERCOT | 2020 | https://ferc.gov/sites/default/files/2020-06/W3-1_Ruiz_et_al.pdf | slides 15, 19 | showcase single events |
| TO u (Cimarron ~20%, recomputed from SPP chart) | to-9; syntheses-57 | Topology Optimization Case Studies | NewGrid (Brattle-hosted) | 2024 | https://brattle.com/wp-content/uploads/2024/06/Topology-Optimization-Case-Studies.pdf | p.3 case 4 | one event; lowered effective limit |
| TO u (10-23% Oklahoma) | to-11 | Topology Optimization Case Studies | NewGrid | 2024 | https://brattle.com/wp-content/uploads/2024/06/Topology-Optimization-Case-Studies.pdf | p.5 | ex-ante; Cimarron still binding in 2025 |
| TO u (5-25% envelope) | to-24 | Topology Optimization Case Studies | NewGrid | 2024 | https://brattle.com/wp-content/uploads/2024/06/Topology-Optimization-Case-Studies.pdf | p.1 | vendor marketing summary |
| TO u hi (~0.40 / ~0.28 derived on two incidental constraints) | to-8 | TTO Applications in SPP (FERC AD10-12-009) | NewGrid | 2018 | https://www.ferc.gov/sites/default/files/2020-08/T4-2_Ruiz.pdf | slides 27, 29 | one 5-min snapshot; assumes ~100% initial loading |
| TO u lo (1-10%; 7-8%); MISO cases (25%, ~25%); breaker wear $10-100/cycle | to-13; to-14; to-12; to-15; to-73 | Congestion Mitigation with TO: Case Studies and a Path Toward Implementation | The Brattle Group | 2021 | https://www.brattle.com/wp-content/uploads/2021/08/Congestion-Mitigation-with-Topology-Optimization-Case-Studies-and-a-Path-Toward-Implementation.pdf | slides 4, 14-17 | outage-specific; vendor-reported; cost footnote unsourced |
| TO u interface (3-12.3%; 4.1-8.4% switching only) | to-20 | Transmission Topology Optimization (NG NIA_NGET0169) | The Brattle Group / National Grid | 2017 | https://www.brattle.com/wp-content/uploads/2017/10/7204_transmission_topology_optimization.pdf | slide 12; NG closedown pp.2-3 | GB boundaries under severe outages |
| TO u interface (as much as 12.3%) | syntheses-78 | Advanced Transmission Technologies | US DOE | 2020 | https://www.energy.gov/sites/default/files/2021/02/f82/Advanced%20Transmission%20Technologies%20Report%20-%20final%20as%20of%2012.3%20-%20FOR%20PUBLIC.pdf | printed p.13 | top of range; secondary |
| TO u hi (stated 31% average; chart mean ~24%, median ~15% by verifier digitization) | to-23 | TTO: A Software GET (ISO-NE PAC) | NewGrid / Brattle | 2025 | https://www.brattle.com/wp-content/uploads/2025/06/Transmission-Topology-Optimization-A-Software-Grid-Enhancing-Technology.pdf | slides 6-7, 9 | offline outage cases; skewed; selected |
| TO P (55 of 100 RT-congested flowgates) | to-83 | TTO Case Studies in SPP and ERCOT | NewGrid with SPP / ERCOT | 2020 | https://ferc.gov/sites/default/files/2020-06/W3-1_Ruiz_et_al.pdf | slide 13 | 2018 RT; strict reliability screen; lower bound |
| TO P (5 of 10; P1/P2/P4/P7; meshed only) | to-85; syntheses-113 | GETting Interconnected in PJM | RMI (Quanta; TO by NewGrid) | 2024 | https://rmi.org/wp-content/uploads/dlm_uploads/2024/02/GETs_insight_brief_v3.pdf | p.15 | n=10; planning overloads |
| TO P (solution traits; all study constraints '(flo)') | to-84 | TTO: Congestion Relief in Operations and Operations Planning | Brattle / NewGrid (SPP MWG) | 2018 | https://www.brattle.com/wp-content/uploads/2021/05/14679_brattle_newgrid_topology_optimization_study_-_spp_mwg_20181010.pdf | slides 16-18 | duplicates to-3 rates |
| TO P approval (32 submitted, 16 reliability, 10 market, 9 implemented) | to-49 | MISO Economic Reconfiguration Process | Alliant, NewGrid, Orsted, Minnesota Power (MISO RSC) | 2025 | https://cdn.misoenergy.org/20251009%20RSC%20Item%2011%20Reconfiguration%20for%20Cost%20Congestion%20Process%20Enhancements721553.pdf | slide 4 | funnel, not h; MISO; right-censored |
| TO P (reliability, production-cost, TCR tests; TOP approval; caps) | to-82 | Tariff Revisions to Implement Economic Topology Optimization (ER26-2592) | SPP | 2026 | https://spp.org/documents/76758/20260521_revisions%20to%20implement%20economic%20topology%20optimizaton_er26-2592-000.pdf | pp.14-16 | no approval rate yet |
| TO C mode/hi (vendor service £1-5M/yr TSO-wide; ADMS ~70% of TCO is labour) | to-63 | Network Topology Optimisation (NIA2_NESO087) | NESO with EY | 2025 | https://www.neso.energy/document/373681/download | p.18 | by analogy; excludes integration labour; allocate over N |
| TO C lo ($1.5M licence + 2x data uplift; 4 FTE; $1.0M/yr derived) | to-65; to-66 | Pathways to Commercial Liftoff | US DOE | 2024 | https://web.archive.org/web/20250122191308id_/https://liftoff.energy.gov/wp-content/uploads/2024/04/Liftoff_Innovative-Grid-Deployment_Final_4.15.pdf | printed p.87; Table 11 p.84 | one assumption set; per system; no recurring software fee |
| TO C cross-check ($20M one-time RTO-wide proposal; ~$2.4M/yr derived) | to-68 | Congestion Mitigation with Transmission Reconfigurations | NewGrid / Alliant (OMS) | 2024 | https://newgridinc.com/wp-content/uploads/2024/03/NewGrid_Congestion-Mitigation-with-Reconfigurations_OMS-MWG-TWG_20240311_FINAL_website.pdf | slide 12 | proposal; order of magnitude only |
| TO C variable ($180-220k/yr for ~4 EAPs, derived $45-55k per plan-year; variable-hours rate ~$34-42k derived); one-time $50-80k (not used) | to-70; to-69 | NPRR1198 TAC Report (and Impact Analysis) | ERCOT TAC | 2024 | https://www.ercot.com/files/docs/2024/05/23/1198NPRR-24%20TAC%20Report%20052224.docx | p.1 | ERCOT staffing; pre-implementation estimate |
| TO C variable (~$43k per overload, derived; fee per project) | to-67; syntheses-116 | GETting Interconnected in PJM | RMI (Quanta) | 2024 | https://rmi.org/wp-content/uploads/dlm_uploads/2024/02/GETs_insight_brief_v3.pdf | Exhibit 8 and note, p.20 | floor on the one-time part only |
| Combination cross-check ($90M + $10M/yr portfolio) | to-74; to-75 | Unlocking the Queue with GETs: SPP | Brattle for WATT Coalition | 2021 | https://watt-transmission.org/wp-content/uploads/2021/02/Brattle__Unlocking-the-Queue-with-Grid-Enhancing-Technologies__Final-Report_Public-Version.pdf90.pdf | slide 12 | vendor-supplied; includes TO |
| Combination additivity (182 vs 184 GWh) | syntheses-7 | GETs: A Case Study on Ratepayer Impact | US DOE | 2022 | https://www.energy.gov/sites/default/files/2022-04/Grid%20Enhancing%20Technologies%20-%20A%20Case%20Study%20on%20Ratepayer%20Impact%20-%20February%202022%20CLEAN%20as%20of%20032322.pdf | Table 16, printed p.51 | additivity, not h |
| DLR outcome context (PPL target rent before and after) | dlr-34 | ANOPR RM24-6-000 | FERC | 2024 | https://www.govinfo.gov/content/pkg/FR-2024-07-15/pdf/2024-14666.pdf | P 57, P 73 | confounded; not h |

## 13. Scope notes

- Only items on the skeptic's USABLE list were used. Refuted items were used for no number, including:
  - the over-static DLR gains;
  - the Liftoff national shares (the 50% viable figure appears only as context, labelled as refuted);
  - the vendor TO dollar counterfactuals;
  - the Brattle/WATT benefit figures.
- **One evidence point per study cluster:**
  - PPL: dlr-35 heads the capex points; dlr-33 sets u; dlr-32 and dlr-34 are context.
  - Murphy 2018: dlr-85 (directional h), dlr-86 (capex), dlr-87 (P).
  - SGDP 2014: dlr-7, dlr-10, dlr-13, dlr-15, dlr-17, dlr-21 and dlr-22, each used for a different parameter.
  - EPRI's SPP cases: apfc-4 heads cost; apfc-1 and apfc-2 are used for u and P.
  - SPP's 2018 TO study and pilot: to-3 (u), to-29 (h floor and the u_TO calibration, double use acknowledged), to-83 (P), to-84 (class coverage).
  - DOE 2022 lifetime: one entry (dlr-77, syntheses-11, apfc-64).
- **Research scratch** (not in the repo): the verified evidence records behind each id, the class, rent and breach counts from the pinned snapshot, the scratch EV re-run of section 9, and revision 1's values. The evidence ids (dlr-*, aar-*, apfc-*, to-*, syntheses-*) name records that a fetch-and-verify pass and a skeptic pass checked against the source documents; section 12 gives each one's title, publisher, year, URL and locator.
