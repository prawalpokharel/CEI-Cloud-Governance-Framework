# Every dashboard was green. Then one service took down forty.

**CloudOptimizer finds the outage hiding in your architecture — and tells
you the cheapest way to remove it.**

---

## The problem nobody's dashboard shows

Here is a monitoring screen from a healthy system:

> CPU 38% · Memory 61% · Latency 72ms · Error rate 0.3% · All pods healthy ✅

And here is what that same system actually looked like, structurally:

```
                IAM (one provider)
                 │
        ┌────────┴────────┐
       API               API
        └───────┬─────────┘
            Service X  ←── 47 services quietly depend on this
          /    |     \
        DB   Queue   Cache
```

Every metric is green. And the system is one small disturbance away from a
very large outage — because over months of normal development, one service
became the thing everything else stands on, and **no tool was watching that
happen.**

This is not hypothetical. In October 2025, a DNS issue on a single database
endpoint in one AWS region cascaded across dozens of "independent" services.
Nine days later, an Azure edge-routing failure took down authentication for
customers who believed they were multi-region. In both cases, the components
were fine. **The architecture was the incident.**

Monitoring answers *"is something failing?"*
Nobody was answering *"has my architecture quietly become fragile?"*

That is the question CloudOptimizer answers — continuously, and before
anything breaks.

---

## What it does, in one paragraph

A small read-only agent observes your Kubernetes clusters and builds the
**live dependency graph** — what actually talks to what, including the
things outside the cluster: your database services, your identity provider,
your payment API. On that graph, CloudOptimizer computes what no
utilization metric can see: which workloads are structurally critical,
which "independent" services secretly share a single point of failure,
what actually breaks when any given piece fails — and what changed since
yesterday.

Then it goes one step further than measurement: it tells you **what to fix
first, and whether the fix is worth the money.**

---

## Three things that make this different

### 1. The predictions are validated against real failure — not vibes

Most architecture tools show you a diagram and ask you to trust it. We
tested ours the hard way: controlled failure injection on a live cluster,
comparing what the graph *predicted* would break against what *actually*
broke.

> **Rank correlation between predicted and measured blast radius: 0.98.
> Recall: 100% — the graph missed zero real dependencies.**

The validation method ships with the product. Any customer can run the same
experiment on their own staging cluster and get their own number. No other
tool in this space offers that.

### 2. It catches the risk your cost tool is creating

Every FinOps tool can find idle capacity: *"remove 30 underutilized
replicas, save $70,000/year."* What none of them ask: **what were those
replicas buying?** Sometimes the answer is "nothing." Sometimes it is "that
was your failover capacity, and you just sold your resilience for 18% of
your compute bill."

CloudOptimizer prices both sides. Expected downtime from a fragility
increase is converted to dollars — the same currency as the savings — so
every recommendation comes with a net:

> *Remove 4 idle replicas: saves $23,000/yr, adds $4,100/yr of expected
> downtime → **net +$18,900, do it.***
>
> *Remove the standby database: saves $31,000/yr, adds $210,000/yr of
> expected downtime → **savings cost more than they save. Don't.***

This is risk-aware FinOps. The savings you keep are the ones that were
actually free.

### 3. It prescribes, not just describes

For every cluster, CloudOptimizer evaluates concrete interventions — add a
replica here, split that overloaded shared service, add an independent
failover for your identity provider, or **do nothing** — and ranks them by
risk reduced per dollar spent. Each recommendation is simulated against
your actual architecture before it is proposed.

And when the honest answer is "your architecture is fine, spend nothing" —
it says exactly that. We verified this live: on a healthy test cluster, the
engine's top recommendation was *do nothing*. A tool that always finds
something to sell you is a tool you learn to ignore.

---

## What this looks like day to day

- **A structural health read** alongside your existing monitoring: one view
  that says *"14 of your services all depend on a single auth endpoint —
  they will fail together"* while every conventional metric is still green.
- **Drift alerts that fire on change, not state**: *"this release made
  `payments-core` load-bearing overnight — 12 services now depend on it and
  it has no disruption budget."* One Slack message, the day it happens, with
  the release that caused it named.
- **A blast-radius check on every pull request**: before a change merges,
  a comment on the PR says what it reaches — *"this one-line config edit
  touches 31 downstream services, three of which have no failover."*
- **Fixes as pull requests**, not cluster changes: when CloudOptimizer can
  fix something safely, it opens a PR in your repo for your team to review.
  The agent in your cluster remains strictly read-only — it cannot change
  anything, which your security team will appreciate.

## What it deliberately does not do

- It does not require an agent with write access. Ever.
- It does not read your secrets, your config contents, or your environment
  variable values. (The redaction is enforced by permissions, not promises.)
- It does not invent numbers. Where data is missing — no metrics server, no
  flow data, an unpriced intervention — it says so instead of guessing.
  Every estimate is labelled as one.

---

## Who this is for

Platform and SRE teams running Kubernetes at the scale where nobody holds
the whole dependency picture in their head anymore — roughly 30+ services —
and the leaders accountable for both the cloud bill and the uptime number.
If your organization has ever had a postmortem containing the sentence
*"we didn't know X depended on Y,"* this product exists for that sentence.

## The one-line version

> **Monitoring tells you when something is failing. CloudOptimizer tells
> you what will fail, what it will take down with it, and the cheapest way
> to change that answer — validated against real failure, priced in real
> dollars.**

---

*Built on patent-pending dependency-graph analysis (USPTO App. No.
19/641,446). Blast-radius predictions validated by controlled failure
injection (Spearman 0.98, recall 1.0, protocol published). Runs read-only
in your cluster; deploys in minutes with Helm.*
