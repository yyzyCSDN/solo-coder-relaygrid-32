
import pytest

from relaygrid.ev.charging import (
    ChargingCommitment,
    ChargingRequest,
    firm_capacity_kw,
    reschedule,
)


def test_firm_capacity_constant_headroom():
    cap = firm_capacity_kw(100, [20, 50, 120])
    assert cap == [80, 50, 0]


def test_firm_capacity_per_slot_headroom():
    cap = firm_capacity_kw([100, 60, 10], [20, 50, 5])
    assert cap == [80, 10, 5]


def test_firm_capacity_rejects_bad_inputs():
    with pytest.raises(ValueError): firm_capacity_kw(100, [])
    with pytest.raises(ValueError): firm_capacity_kw(100, [-1])
    with pytest.raises(ValueError): firm_capacity_kw(-1, [1])
    with pytest.raises(ValueError): firm_capacity_kw([1, 2], [1])


def test_reschedule_fills_earliest_slots_to_deadline():
    reqs = [ChargingRequest('a', 10, 20, deadline_h=3)]
    res = reschedule(reqs, [10, 10, 10, 10])
    c = res.commitments['a']
    assert c.slots == (10.0, 10.0, 0.0, 0.0)
    assert c.energy_kwh == 20
    assert res.gaps == {}


def test_reschedule_respects_arrival_window():
    reqs = [ChargingRequest('a', 10, 20, deadline_h=4, arrival_h=2)]
    res = reschedule(reqs, [10, 10, 10, 10])
    assert res.commitments['a'].slots == (0.0, 0.0, 10.0, 10.0)


def test_locked_request_is_preserved_when_margin_shrinks():
    # a is already locked into slot 0; margin drops so only 6 kW is left there.
    reqs = [
        ChargingRequest('a', 10, 10, deadline_h=2, locked=True, committed_kw=(10.0, 0.0)),
        ChargingRequest('b', 10, 10, deadline_h=2),
    ]
    res = reschedule(reqs, [6, 10])
    assert res.commitments['a'].slots == (10.0, 0.0)  # locked, untouched
    assert res.commitments['b'].slots == (0.0, 10.0)  # rearranged around a
    assert res.gaps == {}


def test_locked_request_keeps_slot_that_would_otherwise_be_taken():
    # b arrives later with an earlier deadline; a is unlocked so it yields.
    reqs = [
        ChargingRequest('a', 10, 20, deadline_h=4),
        ChargingRequest('b', 10, 10, deadline_h=2),
    ]
    res = reschedule(reqs, [10, 10, 10, 10])
    assert res.commitments['b'].slots == (10.0, 0.0, 0.0, 0.0)
    assert res.commitments['a'].slots == (0.0, 10.0, 10.0, 0.0)


def test_unlocked_request_is_rearranged_not_squeezed_out_of_locked():
    # Same situation, but a is locked: b must not take a's slot.
    reqs = [
        ChargingRequest('a', 10, 10, deadline_h=2, locked=True, committed_kw=(10.0, 0.0)),
        ChargingRequest('b', 10, 10, deadline_h=2),
    ]
    res = reschedule(reqs, [10, 10])
    assert res.commitments['a'].slots == (10.0, 0.0)
    assert res.commitments['b'].slots == (0.0, 10.0)


def test_gap_returned_when_energy_cannot_be_met():
    reqs = [ChargingRequest('a', 10, 25, deadline_h=3)]
    res = reschedule(reqs, [10, 10, 0])
    assert res.commitments['a'].energy_kwh == 20
    assert res.gaps == {'a': 5.0}


def test_gap_per_vehicle_with_locked_priority():
    # Slot 0 is reserved by locked a; b only gets slot 1 and misses 5 kWh.
    reqs = [
        ChargingRequest('a', 10, 10, deadline_h=1, locked=True, committed_kw=(10.0, 0.0)),
        ChargingRequest('b', 10, 15, deadline_h=2),
    ]
    res = reschedule(reqs, [10, 10])
    assert res.gaps == {'b': 5.0}
    assert res.commitments['a'].energy_kwh == 10


def test_empty_requests():
    res = reschedule([], [10, 10])
    assert res.commitments == {} and res.gaps == {}


def test_reschedule_rejects_bad_inputs():
    with pytest.raises(ValueError): reschedule([], [])
    with pytest.raises(ValueError): reschedule([], [10], slot_duration_h=0)
    with pytest.raises(ValueError):
        reschedule([ChargingRequest('a', 10, 10, deadline_h=1),
                    ChargingRequest('a', 10, 10, deadline_h=2)], [10])
    with pytest.raises(ValueError):
        reschedule([ChargingRequest('a', 10, 10, deadline_h=1, arrival_h=2)], [10])
    with pytest.raises(ValueError):
        reschedule([ChargingRequest('a', 10, 10, deadline_h=2, locked=True,
                                    committed_kw=(5.0, 5.0, 5.0))], [10, 10])
    with pytest.raises(ValueError):
        reschedule([ChargingRequest('a', 10, 10, deadline_h=2, locked=True,
                                    committed_kw=(15.0, 0.0))], [10, 10])


def test_commitment_is_a_dataclass():
    c = ChargingCommitment('a', (1.0,), 1.0, True)
    assert c.vehicle_id == 'a' and c.locked
