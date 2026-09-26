"""Pytest tests, including independent bit-mask enumeration cross-checks."""

import itertools
import json
import random

import pytest

from quorum_analyzer.analyzer import analyze, parse_instance
from quorum_analyzer.cli import main as cli_main


def make_instance(replicas, read_threshold=1, write_threshold=1,
                  read_dcs=(), write_dcs=()):
    """Build a valid input payload from compact replica tuples."""
    return {
        "replicas": [
            {
                "id": replica_id,
                "weight": weight,
                "datacenter": datacenter,
                "online": online,
            }
            for replica_id, weight, datacenter, online in replicas
        ],
        "read": {
            "threshold": read_threshold,
            "required_datacenters": list(read_dcs),
        },
        "write": {
            "threshold": write_threshold,
            "required_datacenters": list(write_dcs),
        },
    }


def reference_analyze(payload):
    """Independent straightforward enumerator used to cross-check analyzer."""
    replicas = payload["replicas"]
    n = len(replicas)

    def feasible(side):
        result = []
        required = set(side["required_datacenters"])
        for mask in range(1 << n):
            members = [i for i in range(n) if mask & (1 << i)]
            if any(not replicas[i]["online"] for i in members):
                continue
            if sum(replicas[i]["weight"] for i in members) < side["threshold"]:
                continue
            covered = {replicas[i]["datacenter"] for i in members}
            if required <= covered:
                result.append(mask)
        return result

    reads = feasible(payload["read"])
    writes = feasible(payload["write"])
    if not reads or not writes:
        return {
            "read_quorum_possible": bool(reads),
            "write_quorum_possible": bool(writes),
            "safe": False,
            "minimum_intersection_size": None,
            "disjoint_counterexample": None,
        }

    disjoint = []
    minimum = n + 1
    for read_mask in reads:
        for write_mask in writes:
            intersection = (read_mask & write_mask).bit_count()
            minimum = min(minimum, intersection)
            if intersection == 0:
                disjoint.append((read_mask, write_mask))

    if not disjoint:
        return {
            "read_quorum_possible": True,
            "write_quorum_possible": True,
            "safe": True,
            "minimum_intersection_size": minimum,
            "disjoint_counterexample": None,
        }

    def ids(mask):
        return tuple(
            sorted(replicas[i]["id"] for i in range(n) if mask & (1 << i))
        )

    read_mask, write_mask = min(
        disjoint,
        key=lambda pair: ((pair[0] | pair[1]).bit_count(), ids(pair[0]), ids(pair[1])),
    )
    return {
        "read_quorum_possible": True,
        "write_quorum_possible": True,
        "safe": False,
        "minimum_intersection_size": None,
        "disjoint_counterexample": {
            "read": list(ids(read_mask)),
            "write": list(ids(write_mask)),
            "intersection_size": 0,
        },
    }


def assert_matches_reference(payload):
    actual = analyze(parse_instance(payload))
    expected = reference_analyze(payload)
    for key, value in expected.items():
        assert actual[key] == value
    return actual


def test_offline_replica_singleton_quorums_are_disjoint():
    payload = make_instance(
        [
            ("a", 1, "east", True),
            ("b", 1, "west", False),
            ("c", 1, "west", True),
        ],
    )
    result = assert_matches_reference(payload)
    assert result == {
        "read_quorum_possible": True,
        "write_quorum_possible": True,
        "safe": False,
        "minimum_intersection_size": None,
        "disjoint_counterexample": {
            "read": ["a"],
            "write": ["c"],
            "intersection_size": 0,
        },
        "reason": "found feasible disjoint read and write quorums",
    }


def test_offline_only_available_member_makes_quorum_impossible():
    payload = make_instance(
        [
            ("a", 1, "east", False),
            ("b", 1, "east", True),
        ],
        read_threshold=2,
        write_threshold=2,
    )
    result = assert_matches_reference(payload)
    assert result["read_quorum_possible"] is False
    assert result["write_quorum_possible"] is False
    assert result["safe"] is False
    assert result["disjoint_counterexample"] is None
    assert result["minimum_intersection_size"] is None
    assert result["reason"] == "no feasible read and write quorum"


def test_no_read_quorum_is_not_reported_safe():
    payload = make_instance(
        [
            ("a", 1, "east", True),
            ("b", 1, "west", True),
        ],
        read_threshold=3,
        write_threshold=1,
    )
    result = assert_matches_reference(payload)
    assert result["read_quorum_possible"] is False
    assert result["write_quorum_possible"] is True
    assert result["safe"] is False
    assert result["reason"] == "no feasible read quorum"


def test_datacenter_constraints_can_force_intersection():
    payload = make_instance(
        [
            ("a", 1, "east", True),
            ("b", 1, "west", True),
            ("c", 1, "north", True),
        ],
        read_dcs=("east", "west"),
        write_dcs=("west", "north"),
    )
    result = assert_matches_reference(payload)
    assert result["safe"] is True
    assert result["minimum_intersection_size"] == 1


def test_datacenter_constraints_can_leave_disjoint_quorums():
    payload = make_instance(
        [
            ("a", 1, "east", True),
            ("b", 1, "west", True),
            ("c", 1, "east", True),
            ("d", 1, "west", True),
        ],
        read_dcs=("east",),
        write_dcs=("west",),
    )
    result = assert_matches_reference(payload)
    assert result["safe"] is False
    assert result["disjoint_counterexample"] == {
        "read": ["a"],
        "write": ["b"],
        "intersection_size": 0,
    }


def test_unknown_required_datacenter_makes_quorum_impossible():
    payload = make_instance(
        [
            ("a", 2, "east", True),
            ("b", 2, "west", True),
        ],
        read_dcs=("mars",),
        write_dcs=(),
    )
    result = assert_matches_reference(payload)
    assert result["read_quorum_possible"] is False
    assert result["write_quorum_possible"] is True
    assert result["safe"] is False


def test_weights_require_sum_not_single_weight():
    payload = make_instance(
        [
            ("a", 2, "east", True),
            ("b", 1, "east", True),
            ("c", 2, "east", True),
        ],
        read_threshold=2,
        write_threshold=2,
    )
    result = assert_matches_reference(payload)
    assert result["safe"] is False
    # Weight-2 replicas alone reach the threshold; the weight-1 replica cannot.
    assert result["disjoint_counterexample"] == {
        "read": ["a"],
        "write": ["c"],
        "intersection_size": 0,
    }


def test_equal_weight_tie_is_decided_by_sorted_id_lists():
    payload = make_instance(
        [
            ("z", 1, "east", True),
            ("y", 1, "east", True),
            ("x", 1, "east", True),
            ("w", 1, "east", True),
        ],
        read_threshold=2,
        write_threshold=2,
    )
    result = assert_matches_reference(payload)
    assert result["disjoint_counterexample"] == {
        "read": ["w", "x"],
        "write": ["y", "z"],
        "intersection_size": 0,
    }


def test_exhaustive_small_equal_weight_instances():
    # n=2 and n=3, every online/offline state, both datacenter layouts, and
    # thresholds that are either always easy (1) or sometimes impossible (n+1).
    case_count = 0
    for n in (2, 3):
        online_states = itertools.product((False, True), repeat=n)
        for online in online_states:
            dc_choices = itertools.product(("east", "west"), repeat=n)
            for dcs in dc_choices:
                required_sets = [(), ("east",), ("west",), ("east", "west")]
                for read_required, write_required in itertools.product(
                    required_sets, repeat=2
                ):
                    for read_threshold, write_threshold in itertools.product(
                        (1, n + 1), repeat=2
                    ):
                        payload = make_instance(
                            [
                                (f"r{i}", 1, dcs[i], online[i])
                                for i in range(n)
                            ],
                            read_threshold=read_threshold,
                            write_threshold=write_threshold,
                            read_dcs=read_required,
                            write_dcs=write_required,
                        )
                        assert_matches_reference(payload)
                        case_count += 1
    assert case_count == 5_120


def test_random_small_instances_with_varied_weights():
    rng = random.Random(20260926)
    for _ in range(100):
        n = rng.randint(2, 4)
        replicas = []
        for index in range(n):
            replicas.append((
                f"r{index}",
                rng.randint(1, 9),
                f"dc{rng.randrange(3)}",
                rng.choice((False, True)),
            ))

        known_dcs = ["dc0", "dc1", "dc2"]
        payload = make_instance(
            replicas,
            read_threshold=rng.randint(1, n * 3),
            write_threshold=rng.randint(1, n * 3),
            read_dcs=rng.sample(known_dcs, rng.randrange(3)),
            write_dcs=rng.sample(known_dcs, rng.randrange(3)),
        )
        assert_matches_reference(payload)


@pytest.mark.parametrize(
    "payload,message",
    [
        ({"replicas": [], "read": {}, "write": {}}, "between 2 and 12"),
        (
            make_instance([("a", 1, "x", True), ("a", 1, "x", True)]),
            "duplicate replica id",
        ),
        (
            make_instance([("a", 0, "x", True), ("b", 1, "x", True)]),
            "weight",
        ),
        (
            make_instance([("a", 1, "x", 1), ("b", 1, "x", True)]),
            "online",
        ),
    ],
)
def test_invalid_inputs(payload, message):
    with pytest.raises(ValueError, match=message):
        parse_instance(payload)


def test_cli_reads_stdin_and_emits_json(monkeypatch, capsys):
    payload = make_instance(
        [("a", 1, "east", True), ("b", 1, "west", True)],
        read_threshold=1,
        write_threshold=1,
    )
    monkeypatch.setattr("quorum_analyzer.cli.sys.stdin", _FakeTextIO(json.dumps(payload)))
    assert cli_main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["safe"] is False
    assert output["disjoint_counterexample"]["intersection_size"] == 0


def test_cli_json_string(monkeypatch, capsys):
    payload = make_instance(
        [
            ("a", 1, "east", True),
            ("b", 1, "west", True),
            ("c", 1, "north", True),
        ],
        read_dcs=("east", "west"),
        write_dcs=("west", "north"),
    )
    assert cli_main(["--json-string", json.dumps(payload)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["safe"] is True
    assert output["minimum_intersection_size"] == 1


def test_cli_invalid_json_returns_2(capsys):
    assert cli_main(["--json-string", "{not json"]) == 2
    error = capsys.readouterr().err
    assert "invalid JSON" in error


class _FakeTextIO:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text
