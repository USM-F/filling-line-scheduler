# MILP model

The following sections describe the default two-objective workflow. The experimental extension is specified at the end.

Implementation: [`milp.py`](../src/filling_scheduler/milp.py). Solver: HiGHS. Contract: [assignment](filling_only_20_product_assignment.md).

## According to the task

- Produces the full required quantity for every product — [constraint (2)](#c2).
- Assigns products only to eligible filling lines — [constraint (1)](#c1).
- Respects line capacity, working shifts, and breaks — [constraints (3)–(4)](#c3) and [(9)–(11)](#c9) for capacity, [(5)–(8)](#c5) for the calendar and horizon, [(12)–(14)](#c12) for line exclusivity.
- Reserves changeover time before switching products on the same line — [constraint (14)](#c14).
- Places changeover time in the gap between shifts, during lunch breaks, or during shift working hours — [constraint (14)](#c14) uses elapsed time; working-window constraints [(5)–(7)](#c5) apply only to production.
- Minimizes total changeover time — [objective (O1)](#o1), first priority.
- Keeps each product on as few lines as practical — [objective (O2)](#o2), second priority.
- Avoids fragmented sequences such as A-B-A-B-A — [constraints (12)–(14)](#c12) form a path with at most one run per product–line pair; [(5)–(7)](#c5) permit pauses within a run only outside working time.

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

<a id="c1"></a>

**Constraint (1): eligibility.**

$$
y_{i\ell}=q_{i\ell}=0\qquad\forall(i,\ell)\in(I\times L)\setminus E.
\tag{1}
$$

These fixed-zero variables are omitted from the implementation.

## Objectives

Solve sequentially, fixing the first objective only after its optimum is proven:

<a id="o1"></a>

$$
\min F_1=\sum_{\ell,i\ne j} C_{ij}x_{ij\ell}.
\tag{O1}
$$

<a id="o2"></a>

$$
\min F_2=\sum_{(i,\ell)\in E}y_{i\ell}-|I|.
\tag{O2}
$$

$F_1$ is total setup time; $F_2$ counts additional line assignments. The JSON summary instead counts products using more than one line. This implements “as few lines as practical” as a secondary objective; the assignment specifies no numerical tradeoff.

Both passes share the solve budget. An unproven pass stops the sequence. A feasible incumbent is retained and validated; without one, solving fails. Integer objective value $z$ is proven when the finite native lower bound $b$ satisfies $\lceil b-10^{-6}\rceil\ge\operatorname{round}(z)$. Status `OPTIMAL` requires both proofs; empty demand is trivially optimal.

## Demand and duration

Below, pair indices are omitted in formulas applying independently to every eligible pair. Let $m$ be the number of windows and $R=(m-1)_+(d-1)$.

<a id="c2"></a>

$$
\sum_{\ell:(i,\ell)\in E}q_{i\ell}=D_i\qquad\forall i\in I.
\tag{2}
$$

<a id="c3"></a>

$$
\begin{gathered}
y\le q\le D_i y,\qquad 0\le p\le P y,\qquad c-s\ge p,\\
dq\le np,\qquad dq\ge np-(n-1+R)y.
\end{gathered}
\tag{3}
$$

<a id="c4"></a>

$$
P=\min\left(H,\left\lceil\frac{dD_i+R}{n}\right\rceil\right),\qquad
q\le\min\left(D_i,\sum_k\left\lfloor\frac{nh_k}{d}\right\rfloor\right).
\tag{4}
$$

For integral rates ($d=1$), these constraints enforce $p=\lceil q/u\rceil$. Fractional rates also require the per-window constraints below.

## Calendar

For every eligible pair:

<a id="c5"></a>

$$
\sum_k z^s_k=\sum_k z^c_k=y,\qquad
0\le\sigma_k\le(h_k-1)z^s_k,\qquad z^c_k\le\kappa_k\le h_kz^c_k,
\tag{5}
$$

<a id="c6"></a>

$$
s=\sum_k(A_kz^s_k+\sigma_k),\qquad
c=\sum_k(A_kz^c_k+\kappa_k),
\tag{6}
$$

<a id="c7"></a>

$$
p=\sum_k(F_kz^c_k+\kappa_k-F_kz^s_k-\sigma_k).
\tag{7}
$$

<a id="c8"></a>

$$
0\le s_{i\ell},c_{i\ell}\le T\qquad\forall(i,\ell)\in E.
\tag{8}
$$

Only one start and end window can be selected. Integer $s,c$ make their selected offsets integer. Duration counts every working tick between start and completion, excluding breaks. All slots must fit inside the horizon; there is no overtime slack or separate `max_makespan` option.

## Exact fractional capacity

Only pairs with $d>1$ need additional variables $a_{i\ell k}\in[0,1]$ and $q_{i\ell k}\in\mathbb Z_{\ge0}$. Here $a_{i\ell k}$ equals 1 if the run of product $i$ on line $\ell$ traverses window $k$; 0 otherwise. It is declared continuous and made binary by the recurrence below. Quantity $q_{i\ell k}$ is the number of whole units of product $i$ produced on line $\ell$ in window $k$. Expression $t_{i\ell k}$ is the number of working ticks that run occupies in window $k$.

Omitting the fixed pair indices $(i,\ell)$, set $a_{-1}=z^c_{-1}=0$:

<a id="c9"></a>

$$
a_k=a_{k-1}+z^s_k-z^c_{k-1},\qquad
t_k=h_ka_k-\sigma_k-h_kz^c_k+\kappa_k,
\tag{9}
$$

<a id="c10"></a>

$$
\sum_k q_k=q,\qquad
q_k\le\min(D_i,\lfloor nh_k/d\rfloor),\qquad 2q_k\ge z^s_k+z^c_k,
\tag{10}
$$

<a id="c11"></a>

$$
0\le nt_k-dq_k\le(d-1)a_k+(n-d)z^c_k.
\tag{11}
$$

The recurrence makes $a_k$ binary. Nonfinal windows produce $\lfloor nt_k/d\rfloor$ whole units. In the final window, $t_k=\lceil dq_k/n\rceil$. Start and end windows must produce at least one unit. Intermediate windows may contribute zero units at low rates.

## Line sequences and setup

For each eligible pair, with sums over other eligible products on the same line:

<a id="c12"></a>

$$
f_{i\ell}+\sum_{j\ne i}x_{ji\ell}=y_{i\ell},\qquad
g_{i\ell}+\sum_{j\ne i}x_{ij\ell}=y_{i\ell},
\tag{12}
$$

<a id="c13"></a>

$$
\sum_i f_{i\ell}=\sum_i g_{i\ell}=v_\ell,
\tag{13}
$$

<a id="c14"></a>

$$
s_{j\ell}\ge c_{i\ell}+C_{ij}-(T+C_{ij})(1-x_{ij\ell}).
\tag{14}
$$

Positive production duration and temporal precedence exclude cycles. Each used line therefore has one path, without overlap or returning to a completed product. Setup begins at predecessor completion and ends before successor production.

## Output and validation

[`timing.py`](../src/filling_scheduler/timing.py) moves each fixed route to its earliest feasible times, retaining quantities and recomputing fractional-rate durations. It preserves $F_1,F_2$ and cannot increase completion times. Makespan is $\max c_{i\ell}\times\texttt{precisionMinutes}$ after this shift; it has no global optimality claim.

[`schedule.py`](../src/filling_scheduler/schedule.py) emits integer-quantity production slots within working windows and setup slots between runs. Zero-quantity windows are omitted. Timestamps use the input time zone.

[`validation.py`](../src/filling_scheduler/validation.py) independently checks demand, eligibility, exact capacity, grid and horizon bounds, calendar, ordering, setup durations, run continuity and summary totals before any output is published.

## Experimental extension

The model adds integer $M\in[0,T]$ with $M\ge c_{i\ell}$. `--timing-mode exact` solves $F_1,F_2,F_3=M,F_4=\sum_{i,\ell}s_{i\ell}$ lexicographically, fixing each proven optimum. `heuristic` (default) solves only $F_1,F_2$ and left-shifts; `none` skips the shift. `OPTIMAL` requires proof of every selected objective.

Weighted mode minimizes one expression $\sum_{\nu=1}^4 w_\nu F_\nu+w_5W$, with explicit finite nonnegative $w_1,\dots,w_4$ (at least one positive) and optional $w_5\ge0$. It uses exact timing and skips the left shift. A weighted optimum is reported only when HiGHS returns a feasible optimum with zero gap; integer-objective bound rounding is inapplicable.

If $w_5>0$, setup placement becomes a decision. For every assigned successor, let $\delta_{i\ell}=y_{i\ell}-f_{i\ell}$ indicate whether product $i$ on line $\ell$ has a predecessor (1 if yes, 0 otherwise), and let $b_{i\ell},e_{i\ell}$ be its integer setup start and end ticks. Omitting pair indices, define $D^{setup}=\sum_j C_{ji}x_{ji\ell}$. Enforce $0\le b,e\le T\delta$, $e=b+D^{setup}$, $e\le s$, and $b\ge c_j-T(1-x_{ji\ell})$ for each predecessor candidate.

At each endpoint $t\in\{b,e\}$, one interval between consecutive calendar boundaries is selected (sum of selection binaries equals $\delta$). A bounded continuous offset represents $t$ within that interval. Its cumulative working-time coordinate $\phi(t)$ equals working ticks before the interval plus the offset on working intervals, or only preceding working ticks on nonworking intervals. Thus $W=\sum(\phi(e)-\phi(b))$ counts exactly the working ticks occupied by setup. Materialization uses the chosen setup start; validation recomputes $W$ from physical slots.
