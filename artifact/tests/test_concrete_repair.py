"""Independent concrete semantics and exact state-DAG oracle regressions."""
import json
import math
import unittest
from artifact.core.ir import Program, ProgramState, EventType
from artifact.core.semantics import ConcreteSemantics, SemanticError, evaluate_expression
from artifact.oracle.exhaustive import ExhaustiveScheduler
from artifact.instantiations.rpc import UnaryRPCInstantiation, RPC_01, RPC_02, RPC_03, RPC_04, RPC_05
from artifact.instantiations.rma import RMAInstantiation, RMA_01, RMA_02, RMA_03, RMA_04, RMA_05, RMA_06, RMA_07


class ConcreteRepairTests(unittest.TestCase):
    def test_hand_expectations_are_nonempty_and_match_full_oracle(self):
        for cls in [RPC_01,RPC_02,RPC_03,RPC_04,RPC_05,RMA_01,RMA_02,RMA_03,RMA_04,RMA_05,RMA_06,RMA_07]:
            with self.subTest(case=cls.__name__):
                test=cls();program=test.get_program();result=ExhaustiveScheduler().explore(program)
                self.assertTrue(result.complete);self.assertFalse(result.truncated)
                self.assertEqual(result.deadlock_count,0);self.assertGreater(result.schedule_count,0)
                self.assertEqual(set(result.observations),set(test.expected_observations))
                for point,expected in test.expected_observations.items():
                    self.assertEqual(result.observations[point],set(expected['values']))
                    self.assertEqual(any(result.observation_taints[point]),expected['may_tainted'])
                self.assertEqual(result.pending_event_ids,set(test.expected_pending))

    def test_dag_multiplicity_matches_enumerated_actual_traces(self):
        p=RMA_02().get_program()
        dag=ExhaustiveScheduler();result=dag.explore(p)
        enumeration=ExhaustiveScheduler(max_traces=None);traces=enumeration.generate_all_traces(p)
        self.assertEqual(result.schedule_count,len(traces))
        self.assertEqual(result.observation_outcomes,enumeration.result.observation_outcomes)
        self.assertEqual(len({tuple(e.id for e in t.events) for t in traces}),len(traces))

    def test_billions_of_schedules_are_counted_without_materializing_them(self):
        p=Program(['a','b','c'])
        for component in p.components:
            for value in range(8):p.add_local_assign(component,'x',value)
        oracle=ExhaustiveScheduler(max_traces=1);result=oracle.explore(p)
        self.assertEqual(result.schedule_count,math.factorial(24)//math.factorial(8)**3)
        self.assertEqual(result.state_count,9**3)
        self.assertEqual(oracle.traces,[])
        self.assertTrue(result.complete)

    def test_cutoffs_are_explicit_and_not_exhaustive(self):
        p=RMA_02().get_program()
        bounded=ExhaustiveScheduler().explore(p,max_states=2)
        self.assertFalse(bounded.complete);self.assertTrue(bounded.truncated)
        legacy=ExhaustiveScheduler(max_traces=2);self.assertEqual(len(legacy.generate_all_traces(p)),2)
        self.assertFalse(legacy.result.complete);self.assertTrue(legacy.result.truncated)

    def test_undefined_is_error_and_explicit_null_is_a_real_value(self):
        p=Program(['a']);p.add_observe('a','undefined')
        with self.assertRaises(SemanticError):ExhaustiveScheduler().explore(p)
        q=Program(['a']);q.add_local_assign('a','x',None);q.add_observe('a','x');q.add_observe('a','literal_null',expr=None)
        result=ExhaustiveScheduler().explore(q)
        self.assertEqual(result.observations,{'a::x':{None},'a::literal_null':{None}})
        self.assertEqual(result.observation_outcomes['a::x'],{(None,False)})

    def test_observation_prefix_is_immutable(self):
        base=Program(['a']).initial_state
        one=base.observe('a::x',1,True)
        two=one.observe('a::x',2,False)
        self.assertEqual(base.observations,{})
        self.assertEqual(one.observations,{'a::x':[1]})
        self.assertEqual(two.observations,{'a::x':[1,2]})
        self.assertEqual(one.observation_taints,{'a::x':[True]})

    def test_boolean_and_integer_observations_remain_distinct(self):
        p=Program(['a','b','c']);p.initial_state=p.initial_state.update_memory('R','x',None,owner='c')
        RMAInstantiation.put(p,'a','c','R','x',True)
        RMAInstantiation.put(p,'b','c','R','x',1)
        RMAInstantiation.get(p,'c','c','R','x',destination='x');p.add_observe('c','x')
        result=ExhaustiveScheduler().explore(p)
        self.assertEqual(len(result.observations['c::x']),3)
        self.assertEqual(len(result.observation_outcomes['c::x']),3)
        self.assertEqual({type(v).__name__ for v in result.observations['c::x']},{'NoneType','bool','int'})

    def test_initial_state_constraints_taints_and_metadata_roundtrip(self):
        p=Program(['a','b']);p.metadata={'case':'roundtrip'}
        p.initial_state=p.initial_state.update_local('a','x',7,True).update_memory('R','x',11,True,owner='b')
        a=p.add_local_assign('a','y',{'var':'x'});b=p.add_observe('b','cell',expr=11)
        p.initial_state=p.initial_state.add_constraint(a.id,b.id)
        q=Program.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual(p.to_dict(),q.to_dict())
        self.assertEqual(hash(p.initial_state),hash(q.initial_state))
        self.assertEqual(q.metadata,p.metadata)

    def test_actual_unique_rpc_payload_and_response_binding(self):
        p=Program(['a','b']);p.add_local_assign('a','x',8,True)
        first=UnaryRPCInstantiation.rpc_call(p,'a','b','same',args={'v':{'var':'x'}},returns='one',return_expr={'op':'mul','args':[{'var':'v'},2]})
        second=UnaryRPCInstantiation.rpc_call(p,'a','b','same',args={'v':4},returns='two')
        p.add_observe('a','one');p.add_observe('a','two')
        self.assertNotEqual(first[0].attributes['message_id'],second[0].attributes['message_id'])
        result=ExhaustiveScheduler().explore(p)
        self.assertEqual(result.observation_outcomes,{'a::one':{(16,True)},'a::two':{(4,False)}})
        with self.assertRaises(ValueError):UnaryRPCInstantiation.rpc_call(Program(['a','b']),'a','b','missing')

    def test_read_samples_access_time_and_wait_only_completes_own_reads(self):
        p=Program(['a','b']);p.initial_state=p.initial_state.update_memory('R','x',1,True,owner='b')
        read=RMAInstantiation.get(p,'a','b','R','x',destination='sample')
        write=RMAInstantiation.put(p,'b','b','R','x',2)
        p.initial_state=p.initial_state.add_constraint(read.id,write.id)
        RMAInstantiation.wait(p,'b');p.add_observe('a','sample')
        result=ExhaustiveScheduler().explore(p)
        self.assertEqual(result.observation_outcomes['a::sample'],{(1,True)})
        self.assertEqual(result.pending_event_ids,{read.id})

    def test_branch_skips_untaken_assignment_and_does_not_add_implicit_taint(self):
        p=RPC_05().get_program();result=ExhaustiveScheduler().explore(p)
        self.assertEqual(result.observation_outcomes['client::z'],{(999,False)})
        self.assertEqual(evaluate_expression(p.initial_state,'client',{'op':'eq','args':[True,1]}),(False,False))

    def test_deadlock_and_empty_program_are_counted_honestly(self):
        p=Program(['a','b']);p.add_receive('a','b','orphan',message_id='missing')
        result=ExhaustiveScheduler().explore(p)
        self.assertTrue(result.complete);self.assertEqual(result.schedule_count,0)
        self.assertEqual(result.deadlock_count,1);self.assertEqual(result.deadlock_schedules,1)
        empty=ExhaustiveScheduler().explore(Program(['a']))
        self.assertEqual(empty.schedule_count,1);self.assertEqual(empty.state_count,1)


if __name__=='__main__':unittest.main()
