from __future__ import annotations
"""DIST-X04 operational adoption projections over canonical distributed authorities."""
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

DELIVERY_STATES=("live","delayed","reconnecting","degraded","reconciled")
PROVIDERS=("local-subprocess","parallel-federation","nested-federation","chroot","proot","oci","ssh-foreground","ssh-detached")

@dataclass(frozen=True, slots=True)
class DeliveryHealth:
    state:str; lag_events:int=0; backlog:int=0; reconnects:int=0; reconciled:bool=False
    def as_dict(self): return {"state":self.state,"lag_events":self.lag_events,"backlog":self.backlog,"reconnects":self.reconnects,"reconciled":self.reconciled}

def delivery_health(*, connected:bool, lag_events:int=0, backlog:int=0, reconnecting:bool=False, terminal_reconciled:bool=False)->DeliveryHealth:
    if terminal_reconciled: state="reconciled"
    elif reconnecting: state="reconnecting"
    elif not connected: state="degraded"
    elif lag_events or backlog: state="delayed"
    else: state="live"
    return DeliveryHealth(state,max(0,lag_events),max(0,backlog),0 if not reconnecting else 1,terminal_reconciled)

def historical_child_projection(child:Mapping[str,Any])->dict[str,Any]:
    """Project durable child truth; never manufacture live/replay state."""
    return {"mode":"historical","live_replay":False,"run_id":str(child.get("run_id") or ""),"status":str(child.get("status") or "unknown"),"repository":str(child.get("repository") or ""),"target":str(child.get("target") or ""),"workflow":str(child.get("workflow") or ""),"evidence_digest":str(child.get("evidence_digest") or "")}

def opaque_safe_event(event:Mapping[str,Any], *, known_types:Iterable[str])->dict[str,Any]:
    typ=str(event.get("event_type") or "unknown")
    if typ in set(known_types): return dict(event)
    return {"event_type":"opaque-safe","original_event_type":typ,"event_id":str(event.get("event_id") or ""),"sequence":event.get("sequence"),"run_id":str(event.get("run_id") or ""),"opaque":True}

def backpressure_policy(*, backlog:int, capacity:int=1024)->dict[str,Any]:
    ratio=max(0,backlog)/max(1,capacity)
    state="normal" if ratio < .5 else "bounded" if ratio < .9 else "catch-up"
    return {"capacity":max(1,capacity),"backlog":max(0,backlog),"pressure":state,"drop_semantic_events":False,"raw_stream_policy":"reference-only"}

def machine_child_receipt(child:Mapping[str,Any])->dict[str,Any]:
    return {"run_id":str(child.get("run_id") or ""),"parent_run_id":str(child.get("parent_run_id") or ""),"status":str(child.get("status") or "unknown"),"delivery_health":dict(child.get("delivery_health") or {}),"receipt":dict(child.get("receipt") or {}),"retention":dict(child.get("retention") or {})}

def provider_observation_migration(record:Mapping[str,Any])->dict[str,Any]:
    legacy=record.get("provider_observation")
    canonical=record.get("provider_binding") or record.get("provider_attestation")
    return {"deprecated_field":"provider_observation","legacy_present":legacy is not None,"canonical_present":canonical is not None,"migration":"canonical-provider-binding" if canonical is not None else "legacy-read-only"}

def retention_projection(*, run_id:str, pinned:bool, retained:bool, reason:str="")->dict[str,Any]:
    return {"run_id":run_id,"pinned":bool(pinned),"retained":bool(retained),"reason":reason,"deletion_allowed":not (pinned or retained)}

def doctor_links(*, run_id:str, child_run_id:str="")->dict[str,str]:
    base=f"run://{run_id}"
    return {"run":base,"hierarchy":base+"/children","delivery":base+"/delivery","explain":base+"/explain","child":f"run://{child_run_id}" if child_run_id else ""}

def dogfood_matrix(observed:Iterable[str])->dict[str,Any]:
    seen=set(observed); missing=[p for p in PROVIDERS if p not in seen]
    return {"required":list(PROVIDERS),"observed":sorted(seen),"missing":missing,"complete":not missing}

def obligation_disposition(rows:Iterable[Mapping[str,Any]])->dict[str,Any]:
    out=[]; unresolved=[]
    for row in rows:
        item={"id":str(row.get("id") or ""),"owner":str(row.get("owner") or ""),"source":str(row.get("source") or ""),"status":str(row.get("status") or "unresolved")}
        out.append(item)
        if item["status"] not in {"delivered","retained-authority","deferred-owned"}: unresolved.append(item["id"])
    return {"obligations":out,"unresolved":unresolved,"complete":not unresolved}
