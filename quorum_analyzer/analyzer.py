"""Core data model, input validation, and quorum analysis.

The instance has at most 12 replicas, so every subset can be represented by an
integer bit mask.  A feasible quorum is an online-only subset whose weight sum
reaches the side threshold and whose members cover every required datacenter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Replica:
    """One stored copy."""

    id: str
    weight: int
    datacenter: str
    online: bool


@dataclass(frozen=True)
class Side:
    """Requirements for either the read side or write side."""

    threshold: int
    required_datacenters: tuple[str, ...]


@dataclass(frozen=True)
class Instance:
    """A complete quorum analysis instance."""

    replicas: tuple[Replica, ...]
    read: Side
    write: Side


class InvalidInstance(ValueError):
    """Raised when the supplied JSON object is not a valid instance."""


def _ascii_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise InvalidInstance(f"{path} must be a string")
    if not value.isascii():
        raise InvalidInstance(f"{path} must contain only ASCII characters")
    if not allow_empty and not value:
        raise InvalidInstance(f"{path} must not be empty")
    return value


def _strict_int(value: Any, path: str, *, minimum: int) -> int:
    # bool is a subclass of int, but JSON true/false is not an integer here.
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidInstance(f"{path} must be an integer")
    if value < minimum:
        raise InvalidInstance(f"{path} must be >= {minimum}")
    return value


def _strict_bool(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidInstance(f"{path} must be a boolean")
    return value


def _parse_side(raw: Any, name: str) -> Side:
    if not isinstance(raw, Mapping):
        raise InvalidInstance(f"{name} must be an object")

    threshold = _strict_int(raw.get("threshold"), f"{name}.threshold", minimum=1)

    required_raw = raw.get("required_datacenters", [])
    if not isinstance(required_raw, Sequence) or isinstance(required_raw, (str, bytes)):
        raise InvalidInstance(f"{name}.required_datacenters must be a list")

    required = tuple(
        _ascii_string(item, f"{name}.required_datacenters[{index}]")
        for index, item in enumerate(required_raw)
    )
    return Side(threshold=threshold, required_datacenters=required)


def parse_instance(raw: Any) -> Instance:
    """Validate and decode one JSON-compatible instance."""

    if not isinstance(raw, Mapping):
        raise InvalidInstance("instance must be a JSON object")

    replicas_raw = raw.get("replicas")
    if not isinstance(replicas_raw, Sequence) or isinstance(replicas_raw, (str, bytes)):
        raise InvalidInstance("replicas must be a list")
    if not 2 <= len(replicas_raw) <= 12:
        raise InvalidInstance("replicas must contain between 2 and 12 entries")

    replicas: list[Replica] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(replicas_raw):
        if not isinstance(item, Mapping):
            raise InvalidInstance(f"replicas[{index}] must be an object")

        replica_id = _ascii_string(item.get("id"), f"replicas[{index}].id")
        if replica_id in seen_ids:
            raise InvalidInstance(f"duplicate replica id: {replica_id!r}")
        seen_ids.add(replica_id)

        weight = _strict_int(item.get("weight"), f"replicas[{index}].weight", minimum=1)
        if weight > 9:
            raise InvalidInstance(f"replicas[{index}].weight must be <= 9")

        datacenter = _ascii_string(
            item.get("datacenter"), f"replicas[{index}].datacenter"
        )
        online = _strict_bool(item.get("online"), f"replicas[{index}].online")
        replicas.append(Replica(replica_id, weight, datacenter, online))

    return Instance(
        tuple(replicas),
        _parse_side(raw.get("read"), "read"),
        _parse_side(raw.get("write"), "write"),
    )


def _feasible_masks(
    *,
    size: int,
    online_mask: int,
    weights: Sequence[int],
    covered_dcs: Sequence[int],
    side: Side,
    datacenter_bits: Mapping[str, int],
) -> list[int]:
    required_names = set(side.required_datacenters)
    if any(name not in datacenter_bits for name in required_names):
        return []
    required_mask = 0
    for datacenter in required_names:
        required_mask |= datacenter_bits[datacenter]

    masks: list[int] = []
    for mask in range(1 << size):
        if mask & ~online_mask:
            continue
        if (
            weights[mask] >= side.threshold
            and covered_dcs[mask] & required_mask == required_mask
        ):
            masks.append(mask)
    return masks


def analyze(instance: Instance) -> dict[str, Any]:
    """Return the machine-readable analysis result for ``instance``."""

    replicas = instance.replicas
    size = len(replicas)
    all_mask = (1 << size) - 1

    online_mask = 0
    datacenter_bits: dict[str, int] = {}
    for index, replica in enumerate(replicas):
        bit = 1 << index
        if replica.online:
            online_mask |= bit
        dc_bit = 1 << len(datacenter_bits)
        datacenter_bits.setdefault(replica.datacenter, dc_bit)

    # Subset DP avoids summing weights and datacenter sets for each candidate.
    weights = [0] * (1 << size)
    covered_dcs = [0] * (1 << size)
    for mask in range(1, 1 << size):
        low_bit = mask & -mask
        index = low_bit.bit_length() - 1
        previous = mask ^ low_bit
        weights[mask] = weights[previous] + replicas[index].weight
        covered_dcs[mask] = (
            covered_dcs[previous] | datacenter_bits[replicas[index].datacenter]
        )

    read_masks = _feasible_masks(
        size=size,
        online_mask=online_mask,
        weights=weights,
        covered_dcs=covered_dcs,
        side=instance.read,
        datacenter_bits=datacenter_bits,
    )
    write_masks = _feasible_masks(
        size=size,
        online_mask=online_mask,
        weights=weights,
        covered_dcs=covered_dcs,
        side=instance.write,
        datacenter_bits=datacenter_bits,
    )

    result: dict[str, Any] = {
        "read_quorum_possible": bool(read_masks),
        "write_quorum_possible": bool(write_masks),
        "safe": False,
        "minimum_intersection_size": None,
        "disjoint_counterexample": None,
        "reason": None,
    }

    if not read_masks or not write_masks:
        missing: list[str] = []
        if not read_masks:
            missing.append("read")
        if not write_masks:
            missing.append("write")
        result["reason"] = f"no feasible {' and '.join(missing)} quorum"
        return result

    replica_ids = [replica.id for replica in replicas]
    sorted_ids_cache: dict[int, tuple[str, ...]] = {}

    def sorted_ids(mask: int) -> tuple[str, ...]:
        if mask not in sorted_ids_cache:
            sorted_ids_cache[mask] = tuple(
                sorted(replica_ids[index] for index in range(size) if mask & (1 << index))
            )
        return sorted_ids_cache[mask]

    write_set = set(write_masks)

    # First prove or disprove safety by looking only at zero-intersection pairs.
    # For each read mask, enumerate write subsets of its online complement.
    best_disjoint_key: tuple[int, tuple[str, ...], tuple[str, ...]] | None = None
    best_pair: tuple[int, int] | None = None

    for read_mask in read_masks:
        allowed_write_space = online_mask & all_mask & ~read_mask
        write_candidate = allowed_write_space
        while True:
            if write_candidate in write_set:
                key = (
                    (read_mask | write_candidate).bit_count(),
                    sorted_ids(read_mask),
                    sorted_ids(write_candidate),
                )
                if best_disjoint_key is None or key < best_disjoint_key:
                    best_disjoint_key = key
                    best_pair = (read_mask, write_candidate)
            if write_candidate == 0:
                break
            write_candidate = (write_candidate - 1) & allowed_write_space

    if best_pair is not None:
        read_mask, write_mask = best_pair
        result["reason"] = "found feasible disjoint read and write quorums"
        result["disjoint_counterexample"] = {
            "read": list(sorted_ids(read_mask)),
            "write": list(sorted_ids(write_mask)),
            "intersection_size": 0,
        }
        return result

    minimum = size + 1
    for read_mask in read_masks:
        for write_mask in write_masks:
            intersection_size = (read_mask & write_mask).bit_count()
            if intersection_size < minimum:
                minimum = intersection_size

    result["safe"] = True
    result["minimum_intersection_size"] = minimum
    result["reason"] = "every feasible read/write pair intersects"
    return result
