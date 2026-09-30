"""Time-segmented EV charging capacity commitments.

A feeder's charging headroom moves as the base load forecast changes, so a
single firm number handed out on arrival lets a later vehicle squeeze out a
promise made earlier.  Requests are therefore committed per time slot
against the forecast firm capacity.  Slots that are locked (a vehicle is
counting on them) are never touched when the margin changes; only unlocked
requests are rearranged, earliest deadline first.  Any energy that cannot be
delivered by a vehicle's deadline comes back as a per-vehicle gap.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChargingRequest:
    vehicle_id: str
    max_kw: float
    energy_kwh: float
    deadline_h: float
    arrival_h: float = 0.0
    locked: bool = False
    committed_kw: tuple[float, ...] = ()


@dataclass(frozen=True)
class ChargingCommitment:
    vehicle_id: str
    slots: tuple[float, ...]
    energy_kwh: float
    locked: bool = False


@dataclass(frozen=True)
class RescheduleResult:
    commitments: dict[str, ChargingCommitment]
    gaps: dict[str, float]


def allocate_ev(chargers: list[tuple[str,float,float]], feeder_headroom_kw: float):
    ordered=sorted(chargers,key=lambda x:(x[2],x[0]))
    out={}; remaining=max(0.0,feeder_headroom_kw)
    for cid,max_kw,deadline_h in ordered:
        take=min(max_kw,remaining); out[cid]=take; remaining-=take
    return out

def coincident_peak_kw(charger_kw: list[float], coincidence: float=0.65):
    if not 0<=coincidence<=1: raise ValueError("coincidence out of range")
    return sum(max(0.0,x) for x in charger_kw)*coincidence

def firm_capacity_kw(headroom_kw, forecast_load_kw):
    """Per-slot firm charging capacity in kW.

    ``headroom_kw`` is either a single constant margin or one value per slot;
    the forecast load is subtracted slot by slot and floored at zero (a slot
    where forecast load already exceeds the margin accepts no charging).
    """
    forecast=[float(x) for x in forecast_load_kw]
    if not forecast: raise ValueError("forecast must cover at least one slot")
    if any(x < 0 for x in forecast): raise ValueError("forecast load cannot be negative")
    if isinstance(headroom_kw,(int,float)):
        headroom=[float(headroom_kw)]*len(forecast)
    else:
        headroom=[float(x) for x in headroom_kw]
        if len(headroom)!=len(forecast): raise ValueError("headroom and forecast slot counts differ")
    if any(x < 0 for x in headroom): raise ValueError("feeder headroom cannot be negative")
    return [max(0.0,h-f) for h,f in zip(headroom,forecast)]

def _validate_request(req: ChargingRequest, n_slots: int):
    if req.max_kw < 0: raise ValueError(f"{req.vehicle_id}: negative power limit")
    if req.energy_kwh < 0: raise ValueError(f"{req.vehicle_id}: negative energy request")
    if req.deadline_h <= req.arrival_h: raise ValueError(f"{req.vehicle_id}: deadline must follow arrival")
    if req.committed_kw:
        if len(req.committed_kw)!=n_slots: raise ValueError(f"{req.vehicle_id}: committed slots do not match horizon")
        if any(kw < 0 for kw in req.committed_kw): raise ValueError(f"{req.vehicle_id}: negative committed power")
        if any(kw > req.max_kw + 1e-9 for kw in req.committed_kw): raise ValueError(f"{req.vehicle_id}: committed power exceeds vehicle limit")
        if req.locked and not any(kw > 0 for kw in req.committed_kw): raise ValueError(f"{req.vehicle_id}: locked request has no committed slots")

def _locked_slots(req: ChargingRequest, n_slots: int) -> tuple[float,...]:
    if len(req.committed_kw)==n_slots:
        return tuple(float(x) for x in req.committed_kw)
    return (0.0,)*n_slots

def reschedule(requests: list[ChargingRequest], capacity_kw, slot_duration_h: float=1.0) -> RescheduleResult:
    """Rearrange unlocked charging requests against per-slot firm capacity.

    Locked requests keep their committed slots unchanged (they are reserved
    first).  Unlocked requests are re-allocated into the remaining capacity,
    ordered earliest deadline then vehicle id for determinism, filling
    slots from earliest to latest up to each vehicle's power limit and
    energy need.  Returns the new commitments plus a per-vehicle energy gap
    (kWh) for every request whose need cannot be delivered by its deadline.
    """
    capacity=[float(x) for x in capacity_kw]
    if not capacity: raise ValueError("capacity must cover at least one slot")
    if any(c < 0 for c in capacity): raise ValueError("capacity cannot be negative")
    if slot_duration_h <= 0: raise ValueError("slot duration must be positive")
    n=len(capacity)

    ids=[r.vehicle_id for r in requests]
    if len(set(ids))!=len(ids): raise ValueError("vehicle ids must be unique")
    for r in requests: _validate_request(r,n)

    available=list(capacity)
    commitments: dict[str,ChargingCommitment]={}

    # Locked requests are firm: reserve their slots before anything moves.
    for req in requests:
        if not req.locked: continue
        slots=_locked_slots(req,n)
        for s,kw in enumerate(slots):
            available[s]=max(0.0,available[s]-kw)
        commitments[req.vehicle_id]=ChargingCommitment(req.vehicle_id,slots,sum(slots)*slot_duration_h,True)

    # Rearrange unlocked requests against what remains, earliest deadline first.
    for req in sorted((r for r in requests if not r.locked), key=lambda r:(r.deadline_h,r.vehicle_id)):
        slots=[0.0]*n
        remaining_kw=req.energy_kwh/slot_duration_h
        for s in range(n):
            if remaining_kw <= 1e-12: break
            start=s*slot_duration_h
            if start < req.arrival_h or start >= req.deadline_h: continue
            room=min(req.max_kw,available[s])
            if room <= 0: continue
            take=min(room,remaining_kw)
            slots[s]=take; available[s]-=take; remaining_kw-=take
        energy=sum(slots)*slot_duration_h
        commitments[req.vehicle_id]=ChargingCommitment(req.vehicle_id,tuple(slots),energy,False)

    gaps: dict[str,float]={}
    for req in requests:
        gap=req.energy_kwh-commitments[req.vehicle_id].energy_kwh
        if gap > 1e-9: gaps[req.vehicle_id]=gap
    return RescheduleResult(commitments,gaps)
