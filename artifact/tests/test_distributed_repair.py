"""Regressions that detect vacuous checks, dropped flow and fake schedules."""
import unittest
from artifact.generator.litmus_generator import LitmusGenerator
from artifact.analysis.projection import AnalysisGraph
from artifact.analysis.centralized_solver import CentralizedSolver
from artifact.analysis.domain import AbstractDomain
from artifact.distributed.coordinator import DistributedCoordinator
from artifact.experiments.rq2 import RQ2Experiment,check_final_map

class DistributedRepairTests(unittest.TestCase):
    def setUp(self):
        self.program=LitmusGenerator(44).generate_family('mixed',1)[0]
        self.graph=AnalysisGraph(self.program);self.kernel=CentralizedSolver(self.program,self.graph)
        self.reference=self.kernel.solve();self.experiment=RQ2Experiment()
    def test_all_worker_counts_placements_and_schedules(self):
        for count in [1,2,4,8]:
            result=self.experiment.compare_topologies(self.program,['single','component','random','scc','communication'],count,3)
            self.assertEqual(result['successes'],15);self.assertEqual(result['errors'],0)
            self.assertEqual(result['mismatches'],0)
    def test_messages_are_nonempty_serialized_states_and_schedules_vary(self):
        results=[self.experiment.run_distributed_variant(self.program,'random',4,s) for s in range(42,52)]
        self.assertTrue(all(r.status=='passed' for r in results))
        self.assertTrue(all(r.message_count>0 and r.byte_count>0 for r in results))
        self.assertGreater(len({r.statistics['schedule_trace_sha256'] for r in results}),1)
        self.assertGreater(sum(r.statistics['duplicate_messages'] for r in results),0)
    def test_empty_map_or_missing_node_is_rejected(self):
        with self.assertRaises(ValueError):check_final_map({},self.reference,self.graph,self.kernel)
        incomplete=dict(self.reference);incomplete.pop(next(iter(incomplete)))
        with self.assertRaises(ValueError):check_final_map(incomplete,self.reference,self.graph,self.kernel)
    def test_dropped_flow_has_nonzero_residual_and_mismatch(self):
        forged=dict(self.reference);nid=next(n for n in forged if forged[n].reachable)
        forged[nid]=AbstractDomain(reachable=False)
        validation=check_final_map(forged,self.reference,self.graph,self.kernel)
        self.assertFalse(validation['equal']);self.assertIn(nid,validation['different_nodes'])
        self.assertTrue(validation['residual_nodes'])
    def test_failure_is_counted_and_topology_remains_present(self):
        self.experiment.run_distributed_variant=lambda *args,**kw:self._failed(*args[:4])
        result=self.experiment.compare_topologies(self.program,['single'],4,3)
        self.assertEqual(result['errors'],3);self.assertEqual(result['successes'],0)
        self.assertEqual(result['status'],'failed');self.assertEqual(len(result['topologies']['single']['runs']),3)
    @staticmethod
    def _failed(program,name,count,seed):
        from artifact.experiments.rq2 import TopologyExperiment
        r=TopologyExperiment('injected_failure',name,count,seed);r.status='error';r.error='injected failure';return r
    def test_omitted_or_duplicate_ownership_rejected(self):
        with self.assertRaises(ValueError):DistributedCoordinator(self.graph,2,lambda g,n:[[],[]])
        nodes=list(self.graph.nodes.values())
        with self.assertRaises(ValueError):DistributedCoordinator(self.graph,2,lambda g,n:[nodes,nodes])
    def test_early_termination_raises(self):
        coordinator=DistributedCoordinator(self.graph,4,self.experiment._get_partition_fn('random'),max_steps=1)
        with self.assertRaises(RuntimeError):coordinator.solve(self.kernel.evaluate_node)
    def test_override_seed_metadata_matches_executed_schedule(self):
        partition=self.experiment._get_partition_fn('random')
        coordinator=DistributedCoordinator(self.graph,4,partition,schedule_seed=42)
        first=coordinator.solve(self.kernel.evaluate_node,schedule_seed=99)
        second=DistributedCoordinator(self.graph,4,partition,schedule_seed=99)
        self.assertEqual(first,second.solve(self.kernel.evaluate_node))
        self.assertEqual(coordinator.get_statistics()['schedule_seed'],99)
        self.assertEqual(coordinator.trace_hash,second.trace_hash)

if __name__=='__main__':unittest.main()
