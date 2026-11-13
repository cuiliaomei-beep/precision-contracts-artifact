"""Tagged constants x may-taint domain. Bottom, Top and concrete null differ."""
from collections.abc import Set
from enum import Enum
import json

def scalar_key(value):
    if value is None:return ('null','null')
    if type(value) not in (bool,int,float,str):raise TypeError(f'Expected JSON scalar, got {value!r}')
    return(type(value).__name__,json.dumps(value,ensure_ascii=False,allow_nan=False))

class FrozenConstants(Set):
    """Type-sensitive immutable scalar set: True is distinct from integer 1."""
    def __init__(self,values=()):self._values={scalar_key(v):v for v in values}
    def __iter__(self):return iter(self._values.values())
    def __len__(self):return len(self._values)
    def __contains__(self,value):return scalar_key(value) in self._values
    def __hash__(self):return hash(frozenset(self._values))
    def __eq__(self,other):return isinstance(other,Set) and set(self._values)=={scalar_key(v) for v in other}
    def __or__(self,other):return FrozenConstants(list(self)+list(other))
    def __and__(self,other):return FrozenConstants(v for v in self if v in FrozenConstants(other))
    def __le__(self,other):return all(v in FrozenConstants(other) for v in self)
    def sorted_values(self):return[self._values[k] for k in sorted(self._values)]
    def __repr__(self):return repr(self.sorted_values())

class Taint(Enum):
    CLEAN='clean'
    TAINTED='tainted'

class AbstractValue:
    MAX_CONSTANTS=64
    def __init__(self,constants=None,tainted=False):
        self.constants=None if constants is None else FrozenConstants(constants)
        if self.constants is not None and len(self.constants)>self.MAX_CONSTANTS:self.constants=None
        self.tainted=bool(tainted) if self.constants is None or len(self.constants) else False
    @classmethod
    def bottom(cls):return cls(())
    @classmethod
    def top(cls):return cls(None,True)
    def is_top(self):return self.constants is None
    def is_bottom(self):return self.constants is not None and len(self.constants)==0
    def join(self,other):return AbstractValue(None if self.is_top() or other.is_top() else self.constants|other.constants,self.tainted or other.tainted)
    def meet(self,other):return AbstractValue(other.constants if self.is_top() else self.constants if other.is_top() else self.constants&other.constants,self.tainted and other.tainted)
    def __le__(self,other):
        if self.is_bottom():return True
        if other.is_bottom():return False
        if self.is_top() and not other.is_top():return False
        return(other.is_top() or self.constants<=other.constants) and(not self.tainted or other.tainted)
    def __eq__(self,other):return isinstance(other,AbstractValue) and self.constants==other.constants and self.tainted==other.tainted
    def __hash__(self):return hash((self.constants,self.tainted))
    def __repr__(self):return str(self.to_dict())
    def to_dict(self):return{'kind':'top' if self.is_top() else 'bottom' if self.is_bottom() else 'set','constants':None if self.is_top() else self.constants.sorted_values(),'tainted':self.tainted}
    @classmethod
    def from_dict(cls,data):
        kind=data.get('kind')
        if kind not in('top','bottom','set'):raise ValueError('Missing explicit value kind')
        if kind=='top':return cls(None,data['tainted'])
        if kind=='bottom':
            if data.get('constants')!=[]:raise ValueError('Bottom requires empty constants')
            return cls.bottom()
        if not isinstance(data.get('constants'),list) or not data['constants']:raise ValueError('Set requires constants')
        return cls(data['constants'],data['tainted'])

def join_values(a,b):return{k:a.get(k,AbstractValue.bottom()).join(b.get(k,AbstractValue.bottom())) for k in sorted(a.keys()|b.keys())}

class AbstractState:
    def __init__(self,component,values=None):self.component,self.values=component,dict(values or {})
    def get(self,var):
        if var not in self.values:raise KeyError(f'Undefined abstract variable {self.component}.{var}')
        return self.values[var]
    def set(self,var,val):return AbstractState(self.component,{**self.values,var:val})
    def join(self,other):
        if self.component!=other.component:raise ValueError('Different components')
        return AbstractState(self.component,join_values(self.values,other.values))
    widen=join
    def __le__(self,other):return self.component==other.component and all(v<=other.values.get(k,AbstractValue.bottom()) for k,v in self.values.items())
    def __eq__(self,other):return isinstance(other,AbstractState) and self.component==other.component and self.values==other.values
    def __hash__(self):return hash((self.component,frozenset(self.values.items())))
    def to_dict(self):return{'component':self.component,'values':{k:v.to_dict() for k,v in sorted(self.values.items())}}

class AbstractMemoryRegion:
    def __init__(self,name,owner,values=None):self.name,self.owner,self.values=name,owner,dict(values or {})
    def get(self,var):
        if var not in self.values:raise KeyError(f'Undefined abstract memory {self.name}.{var}')
        return self.values[var]
    def set(self,var,val):return AbstractMemoryRegion(self.name,self.owner,{**self.values,var:val})
    def __eq__(self,other):return isinstance(other,AbstractMemoryRegion) and(self.name,self.owner,self.values)==(other.name,other.owner,other.values)
    def __hash__(self):return hash((self.name,self.owner,frozenset(self.values.items())))

class AbstractDomain:
    """One event equation output; empty constructor is unreachable bottom."""
    def __init__(self,reachable=False):
        self.reachable=bool(reachable);self.local_states={};self.memory_regions={};self.decisions={};self.payload={};self.observed=AbstractValue.bottom()
    def set_local(self,c,state):self.local_states[c]=state
    def get_local(self,c):return self.local_states.get(c,AbstractState(c))
    def set_region(self,name,region):self.memory_regions[name]=region
    def get_region(self,name):return self.memory_regions.get(name,AbstractMemoryRegion(name,''))
    @staticmethod
    def copy(other):return AbstractDomain.from_dict(other.to_dict())
    def join(self,other):
        if not self.reachable:return other
        if not other.reachable:return self
        out=AbstractDomain(True)
        for c in sorted(self.local_states.keys()|other.local_states.keys()):out.set_local(c,self.get_local(c).join(other.get_local(c)))
        for r in sorted(self.memory_regions.keys()|other.memory_regions.keys()):
            a,b=self.get_region(r),other.get_region(r);out.set_region(r,AbstractMemoryRegion(r,a.owner or b.owner,join_values(a.values,b.values)))
        out.decisions=join_values(self.decisions,other.decisions);out.payload=join_values(self.payload,other.payload);out.observed=self.observed.join(other.observed);return out
    def __le__(self,other):
        if not self.reachable:return True
        if not other.reachable:return False
        if any(not(s<=other.get_local(c)) for c,s in self.local_states.items()):return False
        for r,m in self.memory_regions.items():
            if any(not(v<=other.get_region(r).values.get(k,AbstractValue.bottom())) for k,v in m.values.items()):return False
        return all(v<=other.decisions.get(k,AbstractValue.bottom()) for k,v in self.decisions.items()) and all(v<=other.payload.get(k,AbstractValue.bottom()) for k,v in self.payload.items()) and self.observed<=other.observed
    def __eq__(self,other):return isinstance(other,AbstractDomain) and(self.reachable,self.local_states,self.memory_regions,self.decisions,self.payload,self.observed)==(other.reachable,other.local_states,other.memory_regions,other.decisions,other.payload,other.observed)
    def __hash__(self):return hash(json.dumps(self.to_dict(),sort_keys=True,ensure_ascii=False))
    def to_dict(self):return{'reachable':self.reachable,'local_states':{k:v.to_dict() for k,v in sorted(self.local_states.items())},'memory_regions':{k:{'name':v.name,'owner':v.owner,'values':{a:b.to_dict() for a,b in sorted(v.values.items())}} for k,v in sorted(self.memory_regions.items())},'decisions':{k:v.to_dict() for k,v in sorted(self.decisions.items())},'payload':{k:v.to_dict() for k,v in sorted(self.payload.items())},'observed':self.observed.to_dict()}
    @classmethod
    def from_dict(cls,data):
        out=cls(data['reachable'])
        for c,s in data['local_states'].items():out.set_local(c,AbstractState(c,{k:AbstractValue.from_dict(v) for k,v in s['values'].items()}))
        for r,s in data['memory_regions'].items():out.set_region(r,AbstractMemoryRegion(r,s.get('owner',''),{k:AbstractValue.from_dict(v) for k,v in s['values'].items()}))
        out.decisions={k:AbstractValue.from_dict(v) for k,v in data['decisions'].items()};out.payload={k:AbstractValue.from_dict(v) for k,v in data['payload'].items()};out.observed=AbstractValue.from_dict(data['observed']);return out
