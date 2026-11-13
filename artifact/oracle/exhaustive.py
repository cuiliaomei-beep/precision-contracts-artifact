"""Independent concrete oracle: exact state-DAG exploration and schedule counts."""
from dataclasses import dataclass, field
from collections.abc import MutableSet
from ..core.ir import freeze
from ..core.semantics import ConcreteSemantics, ExecutionTrace, SemanticError


class ConcreteSet(MutableSet):
    """Type-sensitive concrete set; Python's builtin set conflates True and 1.

    Contents live in an independent typed-key map. This uses no abstract-domain
    code, and implements the standard Set interface without builtin-set
    shortcuts that would conflate bool/int or bypass the typed iterator.
    """
    def __init__(self, values=()): self._typed = {freeze(v): v for v in values}
    def __iter__(self): return iter(self._typed.values())
    def __len__(self): return len(self._typed)
    def __contains__(self, value): return freeze(value) in self._typed
    def add(self, value): self._typed[freeze(value)] = value
    def discard(self, value): self._typed.pop(freeze(value), None)
    def update(self, values):
        for value in values: self.add(value)
    def copy(self): return ConcreteSet(self)
    def __eq__(self, other):
        try: return set(self._typed) == {freeze(v) for v in other}
        except TypeError: return False
    def __ne__(self, other): return not self == other
    def __repr__(self): return 'ConcreteSet(' + repr(list(self)) + ')'


@dataclass
class ExplorationResult:
    observations: dict = field(default_factory=dict)
    observation_outcomes: dict = field(default_factory=dict)
    schedule_count: int = 0
    state_count: int = 0
    transition_count: int = 0
    complete: bool = True
    truncated: bool = False
    deadlock_count: int = 0
    deadlock_schedules: int = 0
    pending_event_ids: set = field(default_factory=set)
    terminal_state_count: int = 0
    max_events: int = 0

    @property
    def observation_taints(self):
        return {point: {taint for _, taint in outcomes} for point, outcomes in self.observation_outcomes.items()}

    def __getitem__(self, key): return getattr(self, key)

    def to_dict(self):
        return {'observations': {k: sorted(v, key=repr) for k, v in self.observations.items()},
                'observation_outcomes': {k: [{'value': value, 'tainted': taint} for value, taint in sorted(v, key=repr)] for k, v in self.observation_outcomes.items()},
                'observation_taints': {k: sorted(v) for k, v in self.observation_taints.items()},
                'schedule_count': self.schedule_count, 'state_count': self.state_count,
                'transition_count': self.transition_count, 'complete': self.complete,
                'truncated': self.truncated, 'deadlock_count': self.deadlock_count,
                'deadlock_schedules': self.deadlock_schedules,
                'pending_event_ids': sorted(self.pending_event_ids),
                'terminal_state_count': self.terminal_state_count, 'max_events': self.max_events}


class ExhaustiveScheduler:
    def __init__(self, max_traces=100000, max_states=None):
        self.max_traces, self.max_states = max_traces, max_states
        self.traces, self.observations = [], {}
        self.result = ExplorationResult()

    @staticmethod
    def _record(result, state, multiplicity):
        result.schedule_count += multiplicity
        result.terminal_state_count += 1
        result.pending_event_ids.update(e.id for e in state.pending_events)
        for point, values in state.observations.items():
            result.observations.setdefault(point, ConcreteSet()).update(values)
            taints = state.observation_taints.get(point, [False] * len(values))
            result.observation_outcomes.setdefault(point, ConcreteSet()).update(zip(values, taints))

    def explore(self, program, max_states=None):
        """Explore every reachable (executed-event set, full state) once.

        The execution graph is acyclic: every transition executes one new event.
        Merged prefixes retain arbitrary-precision path multiplicity. This counts
        all legal complete event schedules, rather than counting visited states
        or inventing representative traces. No abstract-analysis module is used.
        """
        ids = {e.id for e in program.events}
        if len(ids) != len(program.events): raise SemanticError('Duplicate event IDs')
        for src, dst in program.initial_state.ordering_constraints:
            if src not in ids or dst not in ids: raise SemanticError(f'Unknown event in constraint {(src,dst)}')
        limit = self.max_states if max_states is None else max_states
        if limit is not None and limit < 1: raise ValueError('max_states must be positive')
        index = {e.id: i for i, e in enumerate(program.events)}
        all_mask = (1 << len(program.events)) - 1
        result = ExplorationResult(max_events=len(program.events))
        state = program.initial_state
        frontier = {(0, state.key()): (state, 1)}
        result.state_count = 1
        while frontier:
            following = {}
            for (mask, _), (state, multiplicity) in frontier.items():
                if mask == all_mask:
                    self._record(result, state, multiplicity)
                    continue
                executed = {event_id for event_id, i in index.items() if mask & (1 << i)}
                legal = ConcreteSemantics.get_legal_next_steps(state, program, executed)
                if not legal:
                    result.deadlock_count += 1
                    result.deadlock_schedules += multiplicity
                for event in legal:
                    child = ConcreteSemantics.execute_event(state, event)
                    child_mask = mask | (1 << index[event.id])
                    key = (child_mask, child.key())
                    result.transition_count += 1
                    if key in following:
                        previous, ways = following[key]
                        following[key] = (previous, ways + multiplicity)
                    else:
                        if limit is not None and result.state_count >= limit:
                            result.complete = False
                            result.truncated = True
                            self.result = result
                            self.observations = result.observations
                            self.traces = []
                            return result
                        following[key] = (child, multiplicity)
                        result.state_count += 1
            frontier = following
        self.result, self.observations, self.traces = result, result.observations, []
        return result

    def generate_all_traces(self, program):
        """Materialize actual traces for small tests, with explicit cutoff status."""
        self.traces = []
        result = ExplorationResult(max_events=len(program.events))
        stopped = False
        def dfs(state, executed, events):
            nonlocal stopped
            if stopped: return
            result.state_count += 1
            if len(executed) == len(program.events):
                if self.max_traces is not None and len(self.traces) >= self.max_traces:
                    result.complete, result.truncated, stopped = False, True, True
                    return
                trace = ExecutionTrace()
                trace.events = list(events)
                trace.execute(program)
                self.traces.append(trace)
                self._record(result, state, 1)
                return
            legal = ConcreteSemantics.get_legal_next_steps(state, program, executed)
            if not legal:
                result.deadlock_count += 1
                result.deadlock_schedules += 1
            for event in legal:
                result.transition_count += 1
                dfs(ConcreteSemantics.execute_event(state, event), executed | {event.id}, events + [event])
        dfs(program.initial_state, set(), [])
        self.result, self.observations = result, result.observations
        return self.traces

    def get_concrete_observation_set(self, point): return ConcreteSet(self.observations.get(point, ()))
    def get_all_observations(self): return {k: v.copy() for k, v in self.observations.items()}
    def get_trace_count(self): return self.result.schedule_count
    def get_trace_statistics(self):
        return {**self.result.to_dict(), 'total_traces': self.result.schedule_count,
                'max_trace_length': self.result.max_events if self.result.schedule_count else 0,
                'min_trace_length': self.result.max_events if self.result.schedule_count else 0,
                'avg_trace_length': self.result.max_events if self.result.schedule_count else 0,
                'observation_points': len(self.observations),
                'max_values_per_point': max(map(len, self.observations.values()), default=0)}
    def to_dict(self):
        return {**self.result.to_dict(), 'total_traces': self.result.schedule_count,
                'statistics': self.get_trace_statistics(),
                'traces': [{'events': [e.id for e in trace.events], 'observations': trace.observations} for trace in self.traces[:100]]}
