"""RQ2 with real event equations, complete maps and fail-closed checking.

Worker execution is a randomized discrete-event simulation. Independent batch
programs can run in OS processes, but worker count is not a speedup measurement.
"""
from pathlib import Path
import hashlib
import json
from ..analysis.projection import AnalysisGraph
from ..analysis.centralized_solver import CentralizedSolver
from ..analysis.domain import AbstractDomain
from ..distributed.coordinator import DistributedCoordinator
from ..partition.strategies import create_partition_strategy
from ..generator.litmus_generator import LitmusGenerator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / 'experiments/legacy_repair_20261016/rq2_compat'
STRATEGIES = ['single', 'component', 'random', 'scc', 'communication']

def canonical_states(states):
    return {key: value.to_dict() for key, value in sorted(states.items())}

def state_hash(states):
    data = canonical_states(states)
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()

def check_final_map(actual, reference, graph, kernel):
    """Never infer success from a hash, empty run list, or missing states."""
    if not actual or set(actual) != set(graph.nodes) or set(reference) != set(graph.nodes):
        raise ValueError('Missing or empty final node map')
    if any(not isinstance(value, AbstractDomain) for value in actual.values()):
        raise TypeError('Untyped final analysis state')
    residual = kernel.check_residual(actual)
    different = [key for key in reference if actual[key] != reference[key]]
    return dict(equal=not different and not residual, different_nodes=different,
                residual_nodes=residual, compared_nodes=len(reference))

class TopologyExperiment:
    def __init__(self, program_name, partition_name, num_workers, schedule_seed):
        self.program_name, self.partition_name = program_name, partition_name
        self.num_workers, self.schedule_seed = num_workers, schedule_seed
        self.result_hash = ''; self.error = ''; self.status = 'not_run'
        self.states = {}; self.statistics = {}; self.validation = {}
        self.message_count = self.byte_count = self.cross_partition_edges = 0
        self.execution_time = self.load_imbalance = 0
        self.ownership = {}
    def to_dict(self):
        return dict(program=self.program_name, partition=self.partition_name,
                    workers=self.num_workers, schedule_seed=self.schedule_seed,
                    status=self.status, error=self.error, result_hash=self.result_hash,
                    states=canonical_states(self.states), ownership=self.ownership,
                    messages=self.message_count, bytes=self.byte_count, time=self.execution_time,
                    cross_edges=self.cross_partition_edges, imbalance=self.load_imbalance,
                    statistics=self.statistics, validation=self.validation)

class RQ2Experiment:
    def __init__(self, output_dir=DEFAULT_OUTPUT):
        self.output_dir = Path(output_dir); self.output_dir.mkdir(parents=True, exist_ok=True)
        self.experiments = []
    def run_centralized_reference(self, prog):
        graph = AnalysisGraph(prog); kernel = CentralizedSolver(prog, graph)
        return kernel.solve()
    def run_distributed_variant(self, prog, partition_name, num_workers, schedule_seed,
                                *, graph=None, kernel=None, reference=None, buckets=None):
        exp = TopologyExperiment(getattr(prog, 'name', 'program'), partition_name, num_workers, schedule_seed)
        try:
            graph = graph if graph is not None else AnalysisGraph(prog)
            kernel = kernel if kernel is not None else CentralizedSolver(prog, graph)
            reference = reference if reference is not None else kernel.solve()
            partition_fn = (lambda g, n: buckets) if buckets is not None else self._get_partition_fn(partition_name, 42)
            coordinator = DistributedCoordinator(graph, num_workers, partition_fn,
                schedule_seed=schedule_seed, duplicate_messages=bool(schedule_seed % 2))
            exp.states = coordinator.solve(kernel.evaluate_node)
            exp.statistics = coordinator.get_statistics(); exp.ownership = dict(coordinator.owner)
            exp.validation = check_final_map(exp.states, reference, graph, kernel)
            exp.result_hash = state_hash(exp.states)
            exp.status = 'passed' if exp.validation['equal'] else 'mismatch'
            exp.message_count = exp.statistics['total_messages']; exp.byte_count = exp.statistics['total_bytes']
            exp.cross_partition_edges = exp.statistics['cross_partition_edges']
            exp.execution_time = exp.statistics['execution_time']
            exp.load_imbalance = exp.statistics['load_balance']['imbalance_ratio']
        except Exception as error:
            exp.error = f'{type(error).__name__}: {error}'; exp.status = 'error'
        return exp
    def compare_topologies(self, prog, partition_names, num_workers, num_schedules=5):
        if not partition_names or num_schedules < 1: raise ValueError('No planned configurations')
        graph = AnalysisGraph(prog); kernel = CentralizedSolver(prog, graph); reference = kernel.solve()
        result = dict(reference=state_hash(reference), reference_states=canonical_states(reference),
                      topologies={}, mismatches=0, errors=0, successes=0, attempted=0)
        for name in partition_names:
            buckets = self._get_partition_fn(name, 42)(graph, num_workers); runs=[]
            for seed in range(42, 42+num_schedules):
                exp = self.run_distributed_variant(prog,name,num_workers,seed,
                    graph=graph,kernel=kernel,reference=reference,buckets=buckets)
                self.experiments.append(exp); runs.append(exp.to_dict())
                result['attempted'] += 1
                result['errors'] += exp.status == 'error'
                result['mismatches'] += exp.status == 'mismatch'
                result['successes'] += exp.status == 'passed'
            result['topologies'][name] = dict(runs=runs, attempted=len(runs),
                errors=sum(r['status']=='error' for r in runs),
                mismatches=sum(r['status']=='mismatch' for r in runs))
        planned = len(partition_names)*num_schedules
        result['status'] = 'passed' if result['successes']==planned else 'failed'
        return result
    def run_benchmark(self, num_programs=10, partition_names=None, num_workers=4, num_schedules=5):
        if num_programs < 1: raise ValueError('No planned programs')
        names = STRATEGIES if partition_names is None else partition_names
        results=[self.compare_topologies(p,names,num_workers,num_schedules)
                 for p in LitmusGenerator(42).generate_batch(num_programs)]
        planned=num_programs*len(names)*num_schedules
        summary=dict(total_programs=num_programs,partition_strategies=names,num_workers=num_workers,
            schedules_per_topology=num_schedules,total_runs=planned,
            attempted_runs=sum(r['attempted'] for r in results),
            successful_runs=sum(r['successes'] for r in results),
            total_mismatches=sum(r['mismatches'] for r in results),
            total_errors=sum(r['errors'] for r in results),results=results,
            execution_model='discrete-event worker/message simulation')
        summary['status']='passed' if summary['successful_runs']==planned else 'failed'
        return summary
    def _hash_results(self, fixed_points): return state_hash(fixed_points)
    def _get_partition_fn(self, strategy_name, seed=42):
        strategy=create_partition_strategy(strategy_name)
        return lambda graph,n: strategy.partition(graph,n,seed=seed) if strategy_name=='random' else strategy.partition(graph,n)
    def save_results(self, summary, filename='rq2_results.json'):
        path=self.output_dir/filename
        path.write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        return path
