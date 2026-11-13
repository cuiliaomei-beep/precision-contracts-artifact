"""Concrete execution independent of all abstract domains and graph solvers."""
from dataclasses import replace
from copy import deepcopy
from .ir import EventType


class SemanticError(ValueError): pass


def evaluate_expression(state, component, expression):
    if expression is None or isinstance(expression, (str, bool, int, float)): return expression, False
    if not isinstance(expression, dict): raise SemanticError(f'Unsupported expression {expression!r}')
    if set(expression) == {'var'}:
        name = expression['var']
        local = state.local_states.get(component)
        if local is None or name not in local.values: raise SemanticError(f'Undefined local {component}.{name}')
        return local.values[name], bool(state.local_taints.get(component, {}).get(name, False))
    if set(expression) != {'op', 'args'} or not isinstance(expression['args'], list): raise SemanticError(f'Malformed expression {expression!r}')
    evaluated = [evaluate_expression(state, component, arg) for arg in expression['args']]
    values = [v for v, _ in evaluated]
    tainted = any(t for _, t in evaluated)
    op = expression['op']
    arity = 1 if op == 'not' else 2
    if len(values) != arity: raise SemanticError(f'{op} needs {arity} operands')
    try:
        if op == 'add': value = values[0] + values[1]
        elif op == 'sub': value = values[0] - values[1]
        elif op == 'mul': value = values[0] * values[1]
        elif op == 'gt': value = values[0] > values[1]
        elif op == 'ge': value = values[0] >= values[1]
        elif op == 'lt': value = values[0] < values[1]
        elif op == 'le': value = values[0] <= values[1]
        elif op == 'eq': value = type(values[0]) is type(values[1]) and values[0] == values[1]
        elif op == 'ne': value = not (type(values[0]) is type(values[1]) and values[0] == values[1])
        elif op == 'and': value = bool(values[0]) and bool(values[1])
        elif op == 'or': value = bool(values[0]) or bool(values[1])
        elif op == 'not': value = not bool(values[0])
        else: raise SemanticError(f'Unsupported operator {op}')
    except (TypeError, ArithmeticError) as error: raise SemanticError(f'Invalid operands for {op}: {values!r}') from error
    return value, tainted


class ConcreteSemantics:
    @staticmethod
    def execute_event(state, event):
        attrs = event.attributes
        guard = attrs.get('guard')
        if guard is not None:
            decision = guard['decision']
            if decision not in state.decisions: raise SemanticError(f'Unknown guard decision {decision}')
            if state.decisions[decision] != guard['value']: return state
        if event.type == EventType.LOCAL_ASSIGN:
            value, taint = evaluate_expression(state, event.component, attrs['value'])
            return state.update_local(event.component, attrs['var'], value, taint or attrs.get('tainted', False))
        if event.type == EventType.SEND:
            message_id = attrs['message_id']
            if message_id in state.messages: raise SemanticError(f'Duplicate message {message_id}')
            evaluated = {field: evaluate_expression(state, event.component, expr) for field, expr in attrs['data'].items()}
            payload = {'source': event.component, 'destination': attrs['destination'], 'msg_type': attrs['msg_type'],
                       'values': {k: v for k, (v, _) in evaluated.items()}, 'taints': {k: t for k, (_, t) in evaluated.items()}}
            return replace(state, messages={**state.messages, message_id: payload})
        if event.type == EventType.RECEIVE:
            message_id = attrs['message_id']
            if message_id not in state.messages: raise SemanticError(f'Missing message {message_id}')
            payload = state.messages[message_id]
            if (payload['source'], payload['destination'], payload['msg_type']) != (attrs['source'], event.component, attrs['msg_type']): raise SemanticError(f'Mismatched message endpoint {message_id}')
            result = state
            for field, variable in attrs['bindings'].items():
                if field not in payload['values']: raise SemanticError(f'Missing payload field {message_id}.{field}')
                result = result.update_local(event.component, variable, payload['values'][field], payload['taints'][field])
            return replace(result, messages={k: v for k, v in result.messages.items() if k != message_id})
        if event.type == EventType.REMOTE_WRITE:
            value, taint = evaluate_expression(state, event.component, attrs['value'])
            return state.update_memory(attrs['region'], attrs['var'], value, taint or attrs.get('tainted', False))
        if event.type == EventType.REMOTE_READ:
            region, variable = attrs['region'], attrs['var']
            if region not in state.memory_regions or variable not in state.memory_regions[region].values: raise SemanticError(f'Undefined memory {region}.{variable}')
            value = state.memory_regions[region].values[variable]
            taint = state.memory_taints.get(region, {}).get(variable, False)
            return state.update_local(event.component, attrs.get('destination', '_read_' + event.id), value, taint).add_pending(event)
        if event.type == EventType.SYNC:
            if attrs.get('scope', 'component') != 'component': raise SemanticError('Unsupported synchronization scope')
            return replace(state, pending_events=[e for e in state.pending_events if e.component != event.component])
        if event.type == EventType.BRANCH:
            value, _ = evaluate_expression(state, event.component, attrs['condition'])
            decision = attrs.get('decision', event.id)
            if decision in state.decisions: raise SemanticError(f'Duplicate decision {decision}')
            return replace(state, decisions={**state.decisions, decision: bool(value)})
        if event.type == EventType.OBSERVE:
            value, taint = evaluate_expression(state, event.component, attrs.get('expr', {'var': attrs['point']}))
            return state.observe(f"{event.component}::{attrs['point']}", value, taint)
        if event.type == EventType.COMPONENT: return state
        raise SemanticError(f'Unsupported event {event.type}')
    @staticmethod
    def execute_trace(program, trace):
        state, executed = program.initial_state, set()
        for event in trace:
            if event not in ConcreteSemantics.get_legal_next_steps(state, program, executed): raise SemanticError(f'Illegal next event {event.id}')
            state = ConcreteSemantics.execute_event(state, event)
            executed.add(event.id)
        return state
    @staticmethod
    def get_legal_next_steps(state, program, executed):
        predecessors = {}
        for src, dst in state.ordering_constraints: predecessors.setdefault(dst, set()).add(src)
        previous = {}
        for event in program.events:
            if event.component in previous: predecessors.setdefault(event.id, set()).add(previous[event.component])
            previous[event.component] = event.id
        legal = []
        for event in program.events:
            if event.id in executed or not predecessors.get(event.id, set()) <= executed: continue
            if event.type == EventType.RECEIVE:
                guard = event.attributes.get('guard')
                skipped = guard and guard['decision'] in state.decisions and state.decisions[guard['decision']] != guard['value']
                if not skipped and event.attributes.get('message_id') not in state.messages: continue
            legal.append(event)
        return legal
    @staticmethod
    def get_observed_values(state, point): return set(state.observations.get(point, []))


class ExecutionTrace:
    def __init__(self):
        self.events, self.final_state, self.observations = [], None, {}
    def add_event(self, event): self.events.append(event)
    def execute(self, program):
        self.final_state = ConcreteSemantics.execute_trace(program, self.events)
        self.observations = deepcopy(self.final_state.observations)
    def get_observation(self, point):
        if point not in self.observations or not self.observations[point]: raise SemanticError(f'Missing observation {point}')
        return self.observations[point][-1]
    def __repr__(self): return 'ExecutionTrace(' + ' → '.join(e.id for e in self.events) + ')'
