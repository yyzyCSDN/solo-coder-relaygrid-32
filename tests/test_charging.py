from relaygrid.ev.charging import ChargingRequest, schedule_charging


def test_unlocked_requests_rearranged_by_deadline_when_margin_changes():
    urgent=ChargingRequest("urgent",energy_kwh=10,max_kw=10,arrival_slot=0,deadline_slot=2)
    later=ChargingRequest("later",energy_kwh=10,max_kw=10,arrival_slot=0,deadline_slot=3)

    plan=schedule_charging([urgent,later],feeder_capacity_kw=[5,10,10],slot_hours=1)

    assert plan.commitments["urgent"].power_kw==(5,5,0)
    assert plan.commitments["later"].power_kw==(0,5,5)
    assert plan.feasible


def test_locked_periods_remain_unchanged_even_when_urgent_request_arrives():
    locked=ChargingRequest("locked",energy_kwh=20,max_kw=10,arrival_slot=0,deadline_slot=3)
    urgent=ChargingRequest("urgent",energy_kwh=10,max_kw=10,arrival_slot=1,deadline_slot=3)

    plan=schedule_charging(
        [locked,urgent],
        feeder_capacity_kw=[10,10,20],
        locked_schedule_kw={"locked":[0,10,0]},
        slot_hours=1,
    )

    assert plan.commitments["locked"].locked_power_kw==(0,10,0)
    assert plan.commitments["locked"].scheduled_power_kw==(10,0,0)
    assert plan.commitments["urgent"].power_kw==(0,0,10)
    assert plan.feasible


def test_load_forecast_reduces_available_feeder_headroom():
    request=ChargingRequest("ev",energy_kwh=15,max_kw=10,deadline_slot=2)

    plan=schedule_charging(
        [request],
        feeder_capacity_kw=[10,10],
        load_forecast_kw=[4,7],
        slot_hours=1,
    )

    assert plan.feeder_headroom_kw==(6,3)
    assert plan.commitments["ev"].power_kw==(6,3)
    assert plan.shortfalls_kwh=={"ev":6}
    assert not plan.feasible


def test_locked_overload_is_preserved_and_reported_as_vehicle_gap():
    request=ChargingRequest("ev",energy_kwh=10,max_kw=10,arrival_slot=0,deadline_slot=2)

    plan=schedule_charging(
        [request],
        feeder_capacity_kw=[5,0],
        load_forecast_kw=0,
        locked_schedule_kw={"ev":[8,0]},
        slot_hours=1,
    )

    assert plan.commitments["ev"].locked_power_kw==(8,0)
    assert plan.commitments["ev"].scheduled_power_kw==(0,0)
    assert plan.commitments["ev"].power_kw==(8,0)
    assert plan.commitments["ev"].shortfall_kwh==2
    assert not plan.feasible


def test_vehicle_maximum_caps_each_unlocked_slot():
    request=ChargingRequest("ev",energy_kwh=10,max_kw=4,deadline_slot=2)

    plan=schedule_charging([request],feeder_capacity_kw=10,slot_hours=1)

    assert plan.commitments["ev"].power_kw==(4,4)
    assert plan.shortfalls_kwh=={"ev":2}
