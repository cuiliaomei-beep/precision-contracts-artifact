"""Finite distributed-event IR with explicit payload bindings and state."""
from enum import Enum
from dataclasses import dataclass, field, replace
from copy import deepcopy
import re


def freeze(value):
    if isinstance(value, dict): return ('dict', tuple(sorted((str(k), freeze(v)) for k, v in value.items())))
    if isinstance(value, (list, tuple)): return ('list', tuple(freeze(v) for v in value))
    if isinstance(value, (set, frozenset)): return ('set', tuple(sorted((freeze(v) for v in value), key=repr)))
    return (type(value).__name__, value)


class EventType(Enum):
    LOCAL_ASSIGN = 'LocalAssign'
    BRANCH = 'Branch'
    OBSERVE = 'Observe'
    SEND = 'Send'
    RECEIVE = 'Receive'
    REMOTE_READ = 'RemoteRead'
    REMOTE_WRITE = 'RemoteWrite'
    SYNC = 'Sync'
    COMPONENT = 'Component'


@dataclass(frozen=True)
class Event:
    id: str
    type: EventType
    component: str
    attributes: dict

    def __hash__(self): return hash((self.id, self.type, self.component, freeze(self.attributes)))
    def __lt__(self, other): return self.id < other.id


@dataclass(frozen=True)
class LocalState:
    component: str
    values: dict
    def update(self, var, val): return LocalState(self.component, {**self.values, var: val})
    def __hash__(self): return hash((self.component, freeze(self.values)))


@dataclass(frozen=True)
class MemoryRegion:
    name: str
    owner: str
    values: dict
    def write(self, var, val): return MemoryRegion(self.name, self.owner, {**self.values, var: val})
    def read(self, var):
        if var not in self.values: raise KeyError(f'Undefined memory cell {self.name}.{var}')
        return self.values[var]
    def __hash__(self): return hash((self.name, self.owner, freeze(self.values)))


@dataclass(frozen=True)
class ProgramState:
    local_states: dict
    memory_regions: dict
    pending_events: list
    ordering_constraints: set
    observations: dict
    local_taints: dict = field(default_factory=dict)
    memory_taints: dict = field(default_factory=dict)
    messages: dict = field(default_factory=dict)
    decisions: dict = field(default_factory=dict)
    observation_taints: dict = field(default_factory=dict)

    def key(self):
        return (freeze({c: s.values for c, s in self.local_states.items()}),
                freeze({r: (s.owner, s.values) for r, s in self.memory_regions.items()}),
                tuple(sorted(e.id for e in self.pending_events)), frozenset(self.ordering_constraints),
                freeze(self.observations), freeze(self.local_taints), freeze(self.memory_taints),
                freeze(self.messages), freeze(self.decisions), freeze(self.observation_taints))
    def __hash__(self): return hash(self.key())
    def update_local(self, component, var, val, tainted=False):
        local = self.local_states.get(component, LocalState(component, {})).update(var, val)
        taints = {**self.local_taints, component: {**self.local_taints.get(component, {}), var: bool(tainted)}}
        return replace(self, local_states={**self.local_states, component: local}, local_taints=taints)
    def update_memory(self, region, var, val, tainted=False, owner=None):
        current = self.memory_regions.get(region, MemoryRegion(region, owner or '', {}))
        if owner is not None and current.owner not in ('', owner): raise ValueError(f'Memory region {region} owned by {current.owner}')
        if owner is not None: current = MemoryRegion(current.name, owner, current.values)
        taints = {**self.memory_taints, region: {**self.memory_taints.get(region, {}), var: bool(tainted)}}
        return replace(self, memory_regions={**self.memory_regions, region: current.write(var, val)}, memory_taints=taints)
    def add_pending(self, event): return replace(self, pending_events=self.pending_events + [event])
    def remove_pending(self, event): return replace(self, pending_events=[e for e in self.pending_events if e.id != event.id])
    def add_constraint(self, src, dst): return replace(self, ordering_constraints=self.ordering_constraints | {(src, dst)})
    def observe(self, point, val, tainted=False):
        obs = {k: list(v) for k, v in self.observations.items()}
        taints = {k: list(v) for k, v in self.observation_taints.items()}
        obs.setdefault(point, []).append(val)
        taints.setdefault(point, []).append(bool(tainted))
        return replace(self, observations=obs, observation_taints=taints)
    def to_dict(self):
        return {'local_states': {c: dict(s.values) for c, s in self.local_states.items()},
                'memory_regions': {r: {'owner': s.owner, 'values': dict(s.values)} for r, s in self.memory_regions.items()},
                'pending_events': [e.id for e in self.pending_events],
                'ordering_constraints': [list(x) for x in sorted(self.ordering_constraints)],
                'observations': deepcopy(self.observations), 'local_taints': deepcopy(self.local_taints),
                'memory_taints': deepcopy(self.memory_taints), 'messages': deepcopy(self.messages),
                'decisions': dict(self.decisions), 'observation_taints': deepcopy(self.observation_taints)}


_DEFAULT = object()


class Program:
    def __init__(self, components):
        if len(set(components)) != len(components): raise ValueError('Duplicate component')
        self.components = list(components)
        self.events = []
        self.event_counter = 0
        self.metadata = {}
        self.initial_state = ProgramState({c: LocalState(c, {}) for c in components}, {}, [], set(), {})
    def new_event(self, evt_type, component, **attributes):
        if component not in self.components: raise ValueError(f'Unknown component {component}')
        evt = Event(f'e{self.event_counter}', evt_type, component, deepcopy(attributes))
        previous = next((e for e in reversed(self.events) if e.component == component), None)
        self.event_counter += 1
        self.events.append(evt)
        if previous: self.initial_state = self.initial_state.add_constraint(previous.id, evt.id)
        return evt
    def add_local_assign(self, component, var, val, tainted=False, guard=None):
        attrs = {'var': var, 'value': val, 'tainted': bool(tainted)}
        if guard is not None: attrs['guard'] = guard
        return self.new_event(EventType.LOCAL_ASSIGN, component, **attrs)
    def add_send(self, src, dst, msg_type, data=None, message_id=None, **attrs):
        message_id = message_id or f'message_{self.event_counter}'
        if any(e.type == EventType.SEND and e.attributes['message_id'] == message_id for e in self.events):
            raise ValueError(f'Duplicate message_id {message_id}')
        return self.new_event(EventType.SEND, src, destination=dst, msg_type=msg_type, data={} if data is None else data,
                              message_id=message_id, **attrs)
    def add_receive(self, component, src, msg_type, message_id=None, bindings=None, **attrs):
        if message_id is None:
            matches = [e for e in self.events if e.type == EventType.SEND and e.component == src and e.attributes['destination'] == component and e.attributes['msg_type'] == msg_type]
            if len(matches) != 1: raise ValueError('Receive needs unambiguous message_id')
            message_id = matches[0].attributes['message_id']
        senders = [e for e in self.events if e.type == EventType.SEND and e.attributes['message_id'] == message_id]
        evt = self.new_event(EventType.RECEIVE, component, source=src, msg_type=msg_type, message_id=message_id,
                             bindings={} if bindings is None else bindings, **attrs)
        if len(senders) == 1: self.initial_state = self.initial_state.add_constraint(senders[0].id, evt.id)
        return evt
    def add_remote_read(self, component, region, var, destination=None, **attrs):
        return self.new_event(EventType.REMOTE_READ, component, region=region, var=var, destination=destination or f'_read_e{self.event_counter}', **attrs)
    def add_remote_write(self, component, region, var, val, **attrs):
        return self.new_event(EventType.REMOTE_WRITE, component, region=region, var=var, value=val, **attrs)
    def add_sync(self, component, **attrs): return self.new_event(EventType.SYNC, component, scope='component', **attrs)
    def add_branch(self, component, condition, decision=None):
        return self.new_event(EventType.BRANCH, component, condition=condition, decision=decision or f'e{self.event_counter}')
    def add_observe(self, component, point, expr=_DEFAULT, **attrs):
        return self.new_event(EventType.OBSERVE, component, point=point, expr={'var': point} if expr is _DEFAULT else expr, **attrs)
    def to_dict(self):
        return {'schema_version': 2, 'components': list(self.components), 'events': [
            {'id': e.id, 'type': e.type.value, 'component': e.component, 'attributes': deepcopy(e.attributes)} for e in self.events],
            'initial_state': self.initial_state.to_dict(), 'metadata': deepcopy(self.metadata)}
    @classmethod
    def from_dict(cls, data):
        prog = cls(data['components'])
        prog.events = [Event(e['id'], EventType(e['type']), e['component'], deepcopy(e['attributes'])) for e in data['events']]
        if len({e.id for e in prog.events}) != len(prog.events): raise ValueError('Duplicate event id')
        prog.event_counter = max([int(e.id[1:]) + 1 for e in prog.events if re.fullmatch(r'e\d+', e.id)] + [len(prog.events)])
        prog.metadata = deepcopy(data.get('metadata', {}))
        initial = data.get('initial_state', {})
        locals_ = initial.get('local_states', {})
        memories = initial.get('memory_regions', {})
        by_id = {e.id: e for e in prog.events}
        constraints = {tuple(x) for x in initial.get('ordering_constraints', [])}
        previous = {}
        for event in prog.events:
            if event.component in previous: constraints.add((previous[event.component], event.id))
            previous[event.component] = event.id
        prog.initial_state = ProgramState(
            {c: LocalState(c, deepcopy(locals_.get(c, {}))) for c in prog.components},
            {r: MemoryRegion(r, value.get('owner', ''), deepcopy(value['values'])) for r, value in memories.items()},
            [by_id[e] for e in initial.get('pending_events', [])], constraints,
            deepcopy(initial.get('observations', {})), deepcopy(initial.get('local_taints', {})),
            deepcopy(initial.get('memory_taints', {})), deepcopy(initial.get('messages', {})),
            dict(initial.get('decisions', {})), deepcopy(initial.get('observation_taints', {})))
        return prog
