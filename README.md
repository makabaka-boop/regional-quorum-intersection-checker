# Quorum Analyzer

A Python command-line analyzer for replicated-storage read/write quorum sets.

It does **not** infer intersection safety from weight thresholds alone. It
enumerates feasible online subsets, checks both the weight threshold and every
required datacenter, and compares all feasible read/write pairs.

## Input JSON

```json
{
  "replicas": [
    {"id": "a", "weight": 2, "datacenter": "dc-east", "online": true}
  ],
  "read": {"threshold": 3, "required_datacenters": ["dc-east"]},
  "write": {"threshold": 3, "required_datacenters": ["dc-east"]}
}
```

Rules:

- 2–12 replicas.
- Replica IDs are non-empty, ASCII, and unique.
- Weights are integers from 1 through 9.
- Datacenter names are non-empty ASCII strings.
- `online` must be a JSON boolean; offline replicas cannot be selected.
- Read/write thresholds are positive integers.
- A quorum must cover every required datacenter. Requiring an unknown
  datacenter makes that side impossible.

## Output

When both sides are possible but a disjoint pair exists, the result is unsafe
and includes that actual pair:

```json
{
  "safe": false,
  "minimum_intersection_size": null,
  "disjoint_counterexample": {
    "read": ["a"],
    "write": ["b"],
    "intersection_size": 0
  }
}
```

When every feasible read/write pair intersects, `safe` is `true` and the minimum
intersection cardinality is reported. If either side has no feasible quorum, the
result is unsafe and no counterexample is fabricated.

## Disjoint counterexample tie-breaking

Among disjoint feasible pairs:

1. choose the pair with the fewest total distinct members (`|R| + |W|`, equal to
   `|R ∪ W|` because they are disjoint);
2. then compare the read side's sorted ID list lexicographically;
3. then compare the write side's sorted ID list lexicographically.

Lexicographic comparison uses Python/ASCII string order.

## Run locally

Python 3.10+ is required.

```bash
python3 -m quorum_analyzer example.json
cat example.json | python3 -m quorum_analyzer --indent 2
python3 -m quorum_analyzer --json-string '{"replicas": ...}'
```

Invalid input exits with status `2` and an `error:` message on stderr.

## Compose service

The Compose service wraps the same one-shot command and receives JSON on stdin:

```bash
docker compose build quorum
docker compose run --rm -T quorum < example.json
```

## Algorithm and tests

There are at most 12 replicas, hence 4096 masks. The implementation uses bit
masks and subset dynamic programming for weights and datacenter coverage. It
first enumerates disjoint pairs by traversing write masks in each read mask's
online complement. Only if none exists does it compute the minimum positive
intersection.

Install test dependencies (`pytest`) and run:

```bash
python3 -m pytest -q
```

`tests/test_quorum.py` contains an independent straightforward enumerator and
cross-checks it against the implementation. It exhaustively enumerates small
n=2/n=3 instances and adds randomized n=4 instances covering offline replicas,
datacenter requirements, weighted thresholds, and equal-weight ties.
