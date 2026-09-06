# MILP model

Implementation: [`milp.py`](../src/filling_scheduler/milp.py). Solver: HiGHS. Contract: [assignment](filling_only_20_product_assignment.md).

## Scope and data

Production runs use eligible lines, meet demand exactly and remain within the input horizon. A product has at most one run on each line; a run can pause only outside working time. Changeovers occupy elapsed time and may cross breaks or shift boundaries. Lines have no shared setup resource. The first product on a line needs no setup.

Time is measured in integer ticks of `precisionMinutes` from the horizon start. Calendar boundaries and changeovers must align to this grid. Rates retain their exact input value; output quantities are integers. Fractional unfinished units are not carried across breaks.

| Symbol | Definition |
| --- | --- |
| $I,L,E$ | Positive-demand products, lines, eligible product–line pairs |
| $D_i$ | Required integer quantity of product $i$ |
| $T$ | Horizon length in ticks |
| $[A_k,B_k)$ | Ordered, disjoint working windows, clipped to $[0,T]$; adjacent windows merged |
| $h_k,H,F_k$ | Window length $B_k-A_k$, total working ticks $\sum_k h_k$, preceding working ticks $\sum_{r<k}h_r$ |
| $u_{i\ell}=n_{i\ell}/d_{i\ell}$ | Exact rate for product $i$ on line $\ell$, as a reduced positive fraction: `capacityUnitsPerHour × precisionMinutes / 60` |
| $C_{ij}$ | Changeover duration from product $i$ to product $j$ in ticks |

Zero-demand products are excluded from the MILP. All declared lines remain in the output.

## Variables

Indices $i,j\in I$ denote products, $\ell\in L$ a line, and $k,r\in\{0,\ldots,m-1\}$ working windows in chronological order; $m$ is the number of windows.

For each $(i,\ell)\in E$:

| Variable | Domain | Definition |
| --- | --- | --- |
| $y_{i\ell}$ | $\{0,1\}$ | 1 if product $i$ is assigned to line $\ell$; 0 otherwise |
| $q_{i\ell}$ | $\mathbb Z_{\ge0}$ | Quantity of product $i$ produced on line $\ell$ |
| $p_{i\ell}$ | $\mathbb Z_{\ge0}$ | Working ticks required to produce $q_{i\ell}$ units of product $i$ on line $\ell$ |
| $s_{i\ell}$ | $\mathbb Z\cap[0,T]$ | Start tick of product $i$ on line $\ell$, measured from the horizon start |
| $c_{i\ell}$ | $\mathbb Z\cap[0,T]$ | Completion tick of product $i$ on line $\ell$, measured from the horizon start |
| $f_{i\ell}$ | $\{0,1\}$ | 1 if product $i$ is the first product on line $\ell$; 0 otherwise |
| $g_{i\ell}$ | $\{0,1\}$ | 1 if product $i$ is the last product on line $\ell$; 0 otherwise |
| $z^s_{i\ell k}$ | $\{0,1\}$ | 1 if production of product $i$ on line $\ell$ starts in window $k$; 0 otherwise |
| $z^c_{i\ell k}$ | $\{0,1\}$ | 1 if the last production tick of product $i$ on line $\ell$ is in window $k$; 0 otherwise |
| $\sigma_{i\ell k}$ | $\mathbb R_{\ge0}$ | Offset $s_{i\ell}-A_k$ if $z^s_{i\ell k}=1$; 0 otherwise |
| $\kappa_{i\ell k}$ | $\mathbb R_{\ge0}$ | Offset $c_{i\ell}-A_k$ if $z^c_{i\ell k}=1$; 0 otherwise |

All variables of an unassigned pair are zero.

For $(i,\ell),(j,\ell)\in E$, $i\ne j$, binary $x_{ij\ell}$ equals 1 if product $j$ immediately follows product $i$ on line $\ell$; 0 otherwise. For $\ell\in L$, binary $v_\ell$ equals 1 if line $\ell$ produces at least one product; 0 otherwise.

## Objectives

Solve sequentially, fixing the first objective only after its optimum is proven:

$$
\min F_1=\sum_{\ell,i\ne j} C_{ij}x_{ij\ell},\qquad
\min F_2=\sum_{(i,\ell)\in E}y_{i\ell}-|I|.
$$

$F_1$ is total setup time; $F_2$ counts additional line assignments. The JSON summary instead counts products using more than one line. This implements “as few lines as practical” as a secondary objective; the assignment specifies no numerical tradeoff.

Both passes share the solve budget. An unproven pass stops the sequence. A feasible incumbent is retained and validated; without one, solving fails. Integer objective value $z$ is proven when the finite native lower bound $b$ satisfies $\lceil b-10^{-6}\rceil\ge\operatorname{round}(z)$. Status `OPTIMAL` requires both proofs; empty demand is trivially optimal.

## Demand and duration

Below, pair indices are omitted in formulas applying independently to every eligible pair. Let $m$ be the number of windows and $R=(m-1)_+(d-1)$.

$$
\sum_{\ell:(i,\ell)\in E}q_{i\ell}=D_i,\qquad y\le q\le D_i y,
$$
$$
0\le p\le P y,\qquad dq\le np,\qquad dq\ge np-(n-1+R)y,\qquad c-s\ge p,
$$
$$
P=\min\left(H,\left\lceil\frac{dD_i+R}{n}\right\rceil\right),\qquad
q\le\min\left(D_i,\sum_k\left\lfloor\frac{nh_k}{d}\right\rfloor\right).
$$

For integral rates ($d=1$), these constraints enforce $p=\lceil q/u\rceil$. Fractional rates also require the per-window constraints below.

## Calendar

For every eligible pair:

$$
\sum_k z^s_k=\sum_k z^c_k=y,\qquad
0\le\sigma_k\le(h_k-1)z^s_k,\qquad z^c_k\le\kappa_k\le h_kz^c_k,
$$
$$
s=\sum_k(A_kz^s_k+\sigma_k),\qquad
c=\sum_k(A_kz^c_k+\kappa_k),
$$
$$
p=\sum_k(F_kz^c_k+\kappa_k-F_kz^s_k-\sigma_k).
$$

Only one start and end window can be selected. Integer $s,c$ make their selected offsets integer. Duration counts every working tick between start and completion, excluding breaks. All slots must fit inside the horizon; there is no overtime slack or separate `max_makespan` option.

## Exact fractional capacity

Only pairs with $d>1$ need additional variables $a_{i\ell k}\in[0,1]$ and $q_{i\ell k}\in\mathbb Z_{\ge0}$. Here $a_{i\ell k}$ equals 1 if the run of product $i$ on line $\ell$ traverses window $k$; 0 otherwise. It is declared continuous and made binary by the recurrence below. Quantity $q_{i\ell k}$ is the number of whole units of product $i$ produced on line $\ell$ in window $k$. Expression $t_{i\ell k}$ is the number of working ticks that run occupies in window $k$.

Omitting the fixed pair indices $(i,\ell)$, set $a_{-1}=z^c_{-1}=0$:

$$
a_k=a_{k-1}+z^s_k-z^c_{k-1},\qquad
t_k=h_ka_k-\sigma_k-h_kz^c_k+\kappa_k,
$$
$$
\sum_k q_k=q,\qquad
q_k\le\min(D_i,\lfloor nh_k/d\rfloor),\qquad 2q_k\ge z^s_k+z^c_k,
$$
$$
0\le nt_k-dq_k\le(d-1)a_k+(n-d)z^c_k.
$$

The recurrence makes $a_k$ binary. Nonfinal windows produce $\lfloor nt_k/d\rfloor$ whole units. In the final window, $t_k=\lceil dq_k/n\rceil$. Start and end windows must produce at least one unit. Intermediate windows may contribute zero units at low rates.

## Line sequences and setup

For each eligible pair, with sums over other eligible products on the same line:

$$
f_{i\ell}+\sum_{j\ne i}x_{ji\ell}=y_{i\ell},\qquad
g_{i\ell}+\sum_{j\ne i}x_{ij\ell}=y_{i\ell},
$$
$$
\sum_i f_{i\ell}=\sum_i g_{i\ell}=v_\ell,
$$
$$
s_{j\ell}\ge c_{i\ell}+C_{ij}-(T+C_{ij})(1-x_{ij\ell}).
$$

Positive production duration and temporal precedence exclude cycles. Each used line therefore has one path, without overlap or returning to a completed product. Setup begins at predecessor completion and ends before successor production.

## Output and validation

[`timing.py`](../src/filling_scheduler/timing.py) moves each fixed route to its earliest feasible times, retaining quantities and recomputing fractional-rate durations. It preserves $F_1,F_2$ and cannot increase completion times. Makespan is $\max c_{i\ell}\times\texttt{precisionMinutes}$ after this shift; it has no global optimality claim.

[`schedule.py`](../src/filling_scheduler/schedule.py) emits integer-quantity production slots within working windows and setup slots between runs. Zero-quantity windows are omitted. Timestamps use the input time zone.

[`validation.py`](../src/filling_scheduler/validation.py) independently checks demand, eligibility, exact capacity, grid and horizon bounds, calendar, ordering, setup durations, run continuity and summary totals before any output is published.
