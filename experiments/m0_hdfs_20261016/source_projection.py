"""Classic exception-aware relational local-variable projection over real IR.

The relation is a set of whole environments indexed by a finite heap world and
request context. This class drops dead columns inside each tuple and never
replaces tuples by independent field marginals. Heap fields are retained. It is
a source-derived strong local baseline, not a novel boundary synthesizer.
"""
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'novelty_audit'))
import finite_jimple


def locals_in(expression):
    if isinstance(expression, list):
        return set().union(*(locals_in(x) for x in expression))
    if not isinstance(expression, dict):
        return set()
    if expression.get('op') == 'local':
        return {expression['name']}
    return set().union(*(locals_in(v) for v in expression.values()))


def unit_use_def(unit):
    kind = unit['kind']
    if kind in ('identity', 'assign'):
        if unit['left']['op'] != 'local':
            raise ValueError('This projection baseline only supports the declared local-assignment slice')
        return locals_in(unit['right']), {unit['left']['name']}
    if kind == 'invoke':
        return locals_in(unit['value']), set()
    if kind == 'if':
        return locals_in(unit['condition']), set()
    if kind in ('return', 'throw'):
        return locals_in(unit['value']), set()
    if kind in ('goto', 'return_void', 'nop'):
        return set(), set()
    raise ValueError('Unsupported unit: ' + kind)


class Liveness:
    def __init__(self, ir):
        start = time.perf_counter()
        self.live = {}
        self.schemas = {}
        self.iterations = 0
        for method in ir['methods']:
            units = method['units']
            lives = [set() for _ in units]
            uses_defs = [unit_use_def(u) for u in units]
            while True:
                changed = False
                for index in range(len(units) - 1, -1, -1):
                    unit = units[index]
                    uses, definitions = uses_defs[index]
                    normal = set().union(*(lives[t] for t in unit['successors']))
                    exceptional = set().union(*(lives[t] for t in unit['exceptional_successors']))
                    # An assignment may throw before defining its left local;
                    # kill applies only to the normal successor flow.
                    updated = uses | (normal - definitions) | exceptional
                    if updated != lives[index]:
                        lives[index] = updated
                        changed = True
                self.iterations += 1
                if not changed:
                    break
            self.live[method['signature']] = lives
            self.schemas[method['signature']] = [sorted(x) for x in lives]
        self.seconds = time.perf_counter() - start

    def certificate(self):
        return {'method': 'ordinary backward local liveness with exceptional CFG; no query answers used',
                'method_schemas': self.schemas, 'fixpoint_iterations': self.iterations,
                'compilation_seconds': self.seconds,
                'scope': 'All source guards/calls/returns retained; dead local columns only; heap/contracts unchanged'}


class MeasuredInterpreter(finite_jimple.Interpreter):
    def __init__(self, ir, heap, collector, liveness=None):
        super().__init__(ir, heap)
        self.collector = collector
        self.liveness = liveness

    def before_step(self, method, environment, pc):
        key = method['signature']
        if self.liveness is not None:
            needed = self.liveness.live[key][pc]
            for name in list(environment):
                if name not in needed:
                    del environment[name]
        self.collector['program_points'] += 1
        self.collector['local_cells_at_points'] += len(environment)
        self.collector['peak_locals_in_one_frame'] = max(
            self.collector['peak_locals_in_one_frame'], len(environment))


def run(ir, data, liveness=None):
    measures = {'program_points': 0, 'local_cells_at_points': 0, 'peak_locals_in_one_frame': 0}

    def factory(actual_ir, heap):
        return MeasuredInterpreter(actual_ir, heap, measures, liveness)

    start = time.perf_counter()
    result = finite_jimple.predict(ir, data, interpreter_factory=factory)
    measures['seconds'] = time.perf_counter() - start
    measures['metric_scope'] = 'Logical local-environment cells across source points, excluding heap and caller stacks; not wire traffic or total RSS'
    return result, measures
