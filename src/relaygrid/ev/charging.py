from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_EPS=1e-9

@dataclass(frozen=True)
class ChargingRequest:
    vehicle_id: str
    energy_kwh: float
    max_kw: float
    arrival_slot: int=0
    deadline_slot: int=1

@dataclass(frozen=True)
class ChargingCommitment:
    vehicle_id: str
    requested_kwh: float
    power_kw: tuple[float,...]
    locked_power_kw: tuple[float,...]
    scheduled_power_kw: tuple[float,...]
    allocated_kwh: float
    shortfall_kwh: float

@dataclass(frozen=True)
class ChargingSchedule:
    commitments: dict[str,ChargingCommitment]
    shortfalls_kwh: dict[str,float]
    feeder_headroom_kw: tuple[float,...]
    feasible: bool

def allocate_ev(chargers: list[tuple[str,float,float]], feeder_headroom_kw: float):
    ordered=sorted(chargers,key=lambda x:(x[2],x[0]))
    out={}; remaining=max(0.0,feeder_headroom_kw)
    for cid,max_kw,deadline_h in ordered:
        take=min(max_kw,remaining); out[cid]=take; remaining-=take
    return out

def schedule_charging(
    requests: Sequence[ChargingRequest],
    feeder_capacity_kw: float | Sequence[float],
    load_forecast_kw: float | Sequence[float]=0.0,
    *,
    locked_schedule_kw: Mapping[str,Sequence[float]] | None=None,
    slot_hours: float=1.0,
) -> ChargingSchedule:
    """Commit charging power per slot, preserving locked vehicle schedules.

    ``feeder_capacity_kw`` is the feeder limit for each slot. Non-EV load in
    ``load_forecast_kw`` is subtracted to obtain the EV headroom (clamped at
    zero when non-EV load already exceeds the feeder limit). Locked power is
    is consumed first and is never moved; all remaining request energy is
    rearranged with earliest-deadline-first ordering.
    """
    if slot_hours<=0: raise ValueError("slot_hours must be positive")
    requests=list(requests)
    if len({r.vehicle_id for r in requests})!=len(requests):
        raise ValueError("duplicate vehicle_id")

    horizon=max((r.deadline_slot for r in requests),default=0)
    horizon=max(horizon,_sequence_length(feeder_capacity_kw),_sequence_length(load_forecast_kw))
    for values in (locked_schedule_kw or {}).values():
        horizon=max(horizon,_require_sequence(values))
    if horizon<=0:
        return ChargingSchedule({}, {}, (), True)

    capacity=_profile(feeder_capacity_kw,horizon,"feeder_capacity_kw")
    forecast=_profile(load_forecast_kw,horizon,"load_forecast_kw")
    headroom=[max(0.0,capacity[i]-forecast[i]) for i in range(horizon)]

    by_id={r.vehicle_id:r for r in requests}
    locked: dict[str,list[float]]={}
    for vehicle_id,values in (locked_schedule_kw or {}).items():
        row=[float(x) for x in values]
        if any(x<0 for x in row): raise ValueError("locked power cannot be negative")
        row.extend([0.0]*(horizon-len(row)))
        locked[vehicle_id]=row
        request=by_id.get(vehicle_id)
        if request is not None:
            if any(x>request.max_kw+_EPS for x in row):
                raise ValueError("locked power exceeds vehicle maximum")
            if sum(row)*slot_hours>request.energy_kwh+_EPS:
                raise ValueError("locked energy exceeds requested energy")

    for request in requests:
        if not isinstance(request.arrival_slot,int) or isinstance(request.arrival_slot,bool):
            raise TypeError("arrival_slot must be an integer")
        if not isinstance(request.deadline_slot,int) or isinstance(request.deadline_slot,bool):
            raise TypeError("deadline_slot must be an integer")
        if not 0<=request.arrival_slot<request.deadline_slot<=horizon:
            raise ValueError("invalid arrival or deadline slot")
        if request.energy_kwh<0 or request.max_kw<0:
            raise ValueError("energy and maximum power cannot be negative")
        locked_row=locked.setdefault(request.vehicle_id,[0.0]*horizon)
        if any(
            power>_EPS and not request.arrival_slot<=slot<request.deadline_slot
            for slot,power in enumerate(locked_row)
        ):
            raise ValueError("locked power outside arrival window")

    locked_total=[sum(row[t] for row in locked.values()) for t in range(horizon)]
    available=[max(0.0,headroom[t]-locked_total[t]) for t in range(horizon)]
    scheduled={r.vehicle_id:[0.0]*horizon for r in requests}
    remaining={
        r.vehicle_id:max(0.0,r.energy_kwh-sum(locked[r.vehicle_id])*slot_hours)
        for r in requests
    }
    ordered=sorted(requests,key=lambda r:(r.deadline_slot,r.vehicle_id))

    for t in range(horizon):
        for request in ordered:
            if not (request.arrival_slot<=t<request.deadline_slot):
                continue
            energy_left=remaining[request.vehicle_id]
            if energy_left<=_EPS:
                continue
            vehicle_room=max(0.0,request.max_kw-locked[request.vehicle_id][t])
            take=min(vehicle_room,available[t],energy_left/slot_hours)
            if take>_EPS:
                scheduled[request.vehicle_id][t]=take
                available[t]-=take
                remaining[request.vehicle_id]-=take*slot_hours

    commitments={}
    shortfalls={}
    for request in requests:
        vehicle_locked=locked[request.vehicle_id]
        vehicle_scheduled=scheduled[request.vehicle_id]
        power=[vehicle_locked[i]+vehicle_scheduled[i] for i in range(horizon)]
        allocated=sum(power)*slot_hours
        shortfall=max(0.0,request.energy_kwh-allocated)
        commitment=ChargingCommitment(
            request.vehicle_id,
            request.energy_kwh,
            tuple(power),
            tuple(vehicle_locked),
            tuple(vehicle_scheduled),
            allocated,
            shortfall,
        )
        commitments[request.vehicle_id]=commitment
        shortfalls[request.vehicle_id]=shortfall

    return ChargingSchedule(
        commitments,
        shortfalls,
        tuple(headroom),
        all(value<=_EPS for value in shortfalls.values()),
    )

def coincident_peak_kw(charger_kw: list[float], coincidence: float=0.65):
    if not 0<=coincidence<=1: raise ValueError("coincidence out of range")
    return sum(max(0.0,x) for x in charger_kw)*coincidence

def _sequence_length(value) -> int:
    if not _is_sequence(value): return 0
    return len(value)

def _require_sequence(value) -> int:
    if not _is_sequence(value): raise TypeError("expected a sequence")
    return len(value)

def _is_sequence(value) -> bool:
    return isinstance(value,Sequence) and not isinstance(value,(str,bytes))

def _profile(value,horizon:int,name:str) -> list[float]:
    if _is_sequence(value):
        values=[float(x) for x in value]
    else:
        values=[float(value)]*horizon
    if any(x<0 for x in values): raise ValueError(f"{name} cannot be negative")
    values.extend([0.0]*(horizon-len(values)))
    return values
