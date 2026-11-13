"""Deterministic family-preserving litmus programs with real communication observations."""
from __future__ import annotations
import copy
import random
from enum import Enum
from typing import Optional
from ..core.ir import Program
from ..instantiations.rpc import UnaryRPCInstantiation
from ..instantiations.rma import RMAInstantiation

class CommunicationType(Enum):
    RPC="rpc"
    RMA="rma"
    MIXED="mixed"
class SynchronizationType(Enum):
    SYNC="sync"
    NOSYNC="nosync"
def var(name):return {"var":name}
def op(name,*args):return {"op":name,"args":list(args)}

class LitmusConfig:
    def __init__(self):
        self.components=2;self.writers=1;self.readers=1
        self.comm_type=CommunicationType.RPC;self.sync=SynchronizationType.NOSYNC
        self.has_branch=False;self.alias="must";self.events_limit=24;self.seed:Optional[int]=None
    def randomize(self,rng=None):
        rng=rng or random.Random(self.seed)
        self.components=rng.choice([2,3,4]);self.writers=rng.choice([1,2]);self.readers=rng.choice([1,2])
        self.comm_type=rng.choice(list(CommunicationType));self.sync=rng.choice(list(SynchronizationType))
        self.has_branch=rng.choice([True,False]);self.alias=rng.choice(["must","may"])
        return self
    def __repr__(self):return f"LitmusConfig({self.comm_type.value},seed={self.seed},sync={self.sync.value},alias={self.alias},branch={self.has_branch})"

class LitmusGenerator:
    def __init__(self,seed=42):self.seed=seed;self.test_count=0
    def generate(self,config):
        if config.components<2:raise ValueError("Communication requires at least two components")
        seed=config.seed if config.seed is not None else self.seed+self.test_count
        rng=random.Random(seed);p=Program([f"c{i}" for i in range(config.components)])
        p.generator_metadata=dict(seed=seed,family=config.comm_type.value,synchronization=config.sync.value,
            alias=config.alias,branch=config.has_branch,requested_components=config.components,
            requested_writers=config.writers,requested_readers=config.readers)
        p.metadata['generator']=dict(p.generator_metadata)
        if config.comm_type==CommunicationType.RPC:self._rpc(p,config,rng)
        elif config.comm_type==CommunicationType.RMA:self._rma(p,config,rng)
        elif config.comm_type==CommunicationType.MIXED:self._mixed(p,config,rng)
        else:raise ValueError("Unknown communication family")
        if len(p.events)>config.events_limit:raise ValueError(f"{len(p.events)} events exceeds {config.events_limit}; no truncation")
        self.test_count+=1;return p
    @staticmethod
    def _payload(p,c,value,branch,rng,prefix):
        p.add_local_assign(c,prefix+"_source",value,tainted=bool(rng.randrange(2)))
        if not branch:return var(prefix+"_source")
        decision=prefix+"_decision"
        p.add_branch(c,op("gt",var(prefix+"_source"),value+rng.choice([-1,1])),decision=decision)
        delta=rng.randint(1,7)
        p.add_local_assign(c,prefix+"_payload",op("add",var(prefix+"_source"),delta),guard={"decision":decision,"value":True})
        p.add_local_assign(c,prefix+"_payload",op("sub",var(prefix+"_source"),delta),guard={"decision":decision,"value":False})
        return var(prefix+"_payload")
    def _rpc(self,p,cfg,rng):
        for i in range(min(cfg.writers,2)):
            caller=p.components[0] if i==0 else p.components[-1]
            callee=p.components[1] if caller!=p.components[1] else p.components[0]
            expr=self._payload(p,caller,rng.randint(1,999999),cfg.has_branch and i==0,rng,f"rpc{i}")
            returned=op("add",var(f"arg{i}"),rng.randint(-9,9))
            if rng.randrange(5)==0:returned=rng.randint(-100,100)
            UnaryRPCInstantiation.rpc_call(p,caller,callee,f"generated{i}",args={f"arg{i}":expr},returns=f"result{i}",return_expr=returned)
            p.add_observe(caller,f"rpc_result{i}",expr=var(f"result{i}"))
            if cfg.readers>1:p.add_observe(callee,f"rpc_argument{i}",expr=var(f"arg{i}"))
    def _rma(self,p,cfg,rng):
        base=rng.randint(-30,30)
        p.initial_state=p.initial_state.update_memory("R","data",base)
        p.initial_state=p.initial_state.update_memory("S","data",base-7)
        writes=[]
        for i in range(min(cfg.writers,2)):
            writer=p.components[i]
            payload=self._payload(p,writer,rng.randint(100,999999),cfg.has_branch and i==0,rng,f"w{i}")
            actual="R" if cfg.alias=="must" or rng.randrange(2)==0 else "S"
            w=RMAInstantiation.put(p,writer,p.components[-1],actual,"data",payload)
            if cfg.alias=="may":w.attributes["possible_regions"]=["R","S"]
            writes.append(w)
        for i in range(min(cfg.readers,2)):
            reader=p.components[-1-i]
            if cfg.sync==SynchronizationType.SYNC:
                fence=RMAInstantiation.fence(p,reader)
                for w in writes:p.initial_state=p.initial_state.add_constraint(w.id,fence.id)
            read=RMAInstantiation.get(p,reader,p.components[-1],"R","data",destination=f"read{i}")
            if cfg.alias=="may":read.attributes["possible_regions"]=["R","S"]
            p.add_observe(reader,f"rma_result{i}",expr=var(f"read{i}"))
    def _mixed(self,p,cfg,rng):
        caller,server=p.components[0],p.components[1]
        p.initial_state=p.initial_state.update_memory("R","data",rng.randint(-20,20))
        p.initial_state=p.initial_state.update_memory("S","data",rng.randint(-50,-25))
        payload=self._payload(p,caller,rng.randint(100,999999),cfg.has_branch,rng,"mixed")
        UnaryRPCInstantiation.rpc_call(p,caller,server,"mixed_call",args={"arg":payload},returns="reply",return_expr=op("add",var("arg"),rng.randint(1,9)))
        w=RMAInstantiation.put(p,caller,server,"R","data",var("reply"))
        if cfg.alias=="may":w.attributes["possible_regions"]=["R","S"]
        reader=p.components[-1]
        if cfg.sync==SynchronizationType.SYNC:
            fence=RMAInstantiation.fence(p,reader)
            p.initial_state=p.initial_state.add_constraint(w.id,fence.id)
        read=RMAInstantiation.get(p,reader,server,"R","data",destination="mixed_read")
        if cfg.alias=="may":read.attributes["possible_regions"]=["R","S"]
        p.add_observe(reader,"mixed_result",expr=var("mixed_read"))
        p.add_observe(caller,"mixed_rpc_result",expr=var("reply"))
    def generate_batch(self,count,config=None):
        programs=[]
        for i in range(count):
            seed=self.seed+i;rng=random.Random(seed)
            if config is None:
                current=LitmusConfig().randomize(rng);current.comm_type=list(CommunicationType)[i%3]
            else:
                current=copy.deepcopy(config);family=current.comm_type
                current.randomize(rng);current.comm_type=family
            current.seed=seed;programs.append(self.generate(current))
        return programs
    def generate_family(self,family,count):
        cfg=LitmusConfig();cfg.comm_type=CommunicationType(family)
        return self.generate_batch(count,cfg)
    @staticmethod
    def generate_parameters():
        return dict(components=[2,3,4],writers=[1,2],readers=[1,2],communication=[c.value for c in CommunicationType],
            synchronization=["sync","nosync"],alias=["must","may"],branch=[True,False],events_limit=24)
