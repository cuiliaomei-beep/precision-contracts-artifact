"""Nonvacuous semantic/domain regressions for repaired independent static solver."""
import unittest
from dataclasses import replace
from artifact.core.ir import Program,EventType
from artifact.analysis.domain import AbstractValue,AbstractDomain
from artifact.analysis.projection import AnalysisGraph
from artifact.analysis.centralized_solver import CentralizedSolver,StaticAnalysisError

def analyze(program):
    solver=CentralizedSolver(program,AnalysisGraph(program));solver.solve();return solver

class DomainRepairTests(unittest.TestCase):
    def test_bottom_top_and_null_distinct_roundtrip(self):
        values=[AbstractValue.bottom(),AbstractValue.top(),AbstractValue([None]),AbstractValue([False,0,True,1])]
        self.assertEqual(len(set(values)),4)
        for value in values:self.assertEqual(value,AbstractValue.from_dict(value.to_dict()))
        self.assertEqual(len(values[-1].constants),4)
    def test_correct_partial_order(self):
        b=AbstractValue.bottom();a=AbstractValue([1]);t=AbstractValue.top()
        self.assertTrue(b<=a);self.assertTrue(a<=t);self.assertFalse(t<=a)
        self.assertTrue(AbstractValue([1])<=AbstractValue([1],True));self.assertFalse(AbstractValue([1],True)<=AbstractValue([1]))
    def test_finite_set_cap_promotes_to_top(self):
        small=AbstractValue(range(64));self.assertFalse(small.is_top())
        large=small.join(AbstractValue([64]));self.assertTrue(large.is_top());self.assertTrue(small<=large)
    def test_domain_roundtrip_and_memory_order(self):
        p=Program(['A']);p.add_local_assign('A','x',None);p.add_observe('A','x')
        s=analyze(p)
        for state in s.fixed_points.values():self.assertEqual(state,AbstractDomain.from_dict(state.to_dict()))
        self.assertEqual(list(s.get_observed_abstract('A::x').constants),[None])

class StaticRepairTests(unittest.TestCase):
    def test_assignment_kill_and_expression(self):
        p=Program(['A']);p.add_local_assign('A','x',1,True);p.add_local_assign('A','x',2)
        p.add_local_assign('A','y',{'op':'add','args':[{'var':'x'},3]});p.add_observe('A','y')
        v=analyze(p).get_observed_abstract('y');self.assertEqual(v.constants,{5});self.assertFalse(v.tainted)
    def test_literal_string_not_variable(self):
        p=Program(['A']);p.add_local_assign('A','x','x');p.add_observe('A','x')
        self.assertEqual(analyze(p).get_observed_abstract('x').constants,{'x'})
    def test_undefined_is_error(self):
        p=Program(['A']);p.add_observe('A','missing')
        with self.assertRaises(StaticAnalysisError):analyze(p)
    def test_query_names_are_component_qualified(self):
        p=Program(['A','B']);p.add_local_assign('A','x',1);p.add_observe('A','x');p.add_local_assign('B','x',2);p.add_observe('B','x')
        s=analyze(p);self.assertEqual(s.get_observed_abstract('A::x').constants,{1});self.assertEqual(s.get_observed_abstract('B::x').constants,{2})
        with self.assertRaises(ValueError):s.get_observed_abstract('x')
        with self.assertRaises(ValueError):AnalysisGraph(p,{'x'})
    def test_rpc_payload_return_and_taint(self):
        p=Program(['C','S']);p.add_local_assign('C','x',4,True)
        p.add_send('C','S','req',{'arg':{'var':'x'}},message_id='call1')
        p.add_receive('S','C','req',message_id='call1',bindings={'arg':'param'})
        p.add_send('S','C','resp',{'ret':{'op':'mul','args':[{'var':'param'},2]}},message_id='return1')
        p.add_receive('C','S','resp',message_id='return1',bindings={'ret':'result'});p.add_observe('C','result')
        s=analyze(p);v=s.get_observed_abstract('result');self.assertEqual(v.constants,{8});self.assertTrue(v.tainted)
        self.assertEqual(s.check_residual(),[])
    def test_payload_not_sender_environment(self):
        p=Program(['A','B']);p.add_local_assign('A','secret',7,True)
        p.add_send('A','B','m',{'field':0},message_id='m');p.add_receive('B','A','m',message_id='m',bindings={'field':'x'});p.add_observe('B','x')
        v=analyze(p).get_observed_abstract('B::x');self.assertEqual(v.constants,{0});self.assertFalse(v.tainted)
    def test_ordering_is_not_value_flow(self):
        p=Program(['A','B']);a=p.add_local_assign('A','x',7,True);b=p.add_local_assign('B','x',3)
        p.initial_state=p.initial_state.add_constraint(a.id,b.id);p.add_observe('B','x')
        v=analyze(p).get_observed_abstract('B::x');self.assertEqual(v.constants,{3});self.assertFalse(v.tainted)
    def test_rma_race_includes_initial_and_write(self):
        p=Program(['W','R']);p.initial_state=p.initial_state.update_memory('mem','x',0)
        p.add_remote_write('W','mem','x',9,tainted=True);p.add_remote_read('R','mem','x',destination='read');p.add_observe('R','read')
        v=analyze(p).get_observed_abstract('read');self.assertEqual(v.constants,{0,9});self.assertTrue(v.tainted)
    def test_rma_ordered_write_excludes_initial(self):
        p=Program(['W','R']);p.initial_state=p.initial_state.update_memory('mem','x',0)
        w=p.add_remote_write('W','mem','x',9);r=p.add_remote_read('R','mem','x',destination='read');p.initial_state=p.initial_state.add_constraint(w.id,r.id);p.add_observe('R','read')
        self.assertEqual(analyze(p).get_observed_abstract('read').constants,{9})
    def test_rma_future_write_excluded(self):
        p=Program(['A']);p.initial_state=p.initial_state.update_memory('mem','x',0)
        p.add_remote_read('A','mem','x',destination='read');p.add_remote_write('A','mem','x',9);p.add_observe('A','read')
        self.assertEqual(analyze(p).get_observed_abstract('read').constants,{0})
    def test_rma_prior_overwritten_write_excluded(self):
        p=Program(['A']);p.initial_state=p.initial_state.update_memory('mem','x',0)
        p.add_remote_write('A','mem','x',8);p.add_remote_write('A','mem','x',9);p.add_remote_read('A','mem','x',destination='read');p.add_observe('A','read')
        self.assertEqual(analyze(p).get_observed_abstract('read').constants,{9})
    def test_rma_conditional_write_does_not_hide_initial(self):
        p=Program(['A']);p.initial_state=p.initial_state.update_memory('mem','x',0)
        p.add_branch('A',False,'b');p.add_remote_write('A','mem','x',9,guard={'decision':'b','value':True})
        p.add_remote_read('A','mem','x',destination='read');p.add_observe('A','read')
        self.assertEqual(analyze(p).get_observed_abstract('read').constants,{0})
    def test_rma_alias_summary_unions_regions(self):
        p=Program(['A']);p.initial_state=p.initial_state.update_memory('one','x',1).update_memory('two','x',2)
        p.add_remote_read('A','one','x',destination='read',possible_regions=['one','two']);p.add_observe('A','read')
        self.assertEqual(analyze(p).get_observed_abstract('read').constants,{1,2})
    def test_known_guard_is_pruned(self):
        p=Program(['A']);p.add_local_assign('A','x',1);p.add_branch('A',False,'b');p.add_local_assign('A','x',9,guard={'decision':'b','value':True});p.add_observe('A','x')
        self.assertEqual(analyze(p).get_observed_abstract('x').constants,{1})
    def test_pure_transfer_matches_full_fixedpoint(self):
        p=Program(['A']);p.add_local_assign('A','x',1);p.add_observe('A','x');s=analyze(p)
        saved=dict(s.fixed_points);s.fixed_points={}
        for n in s.analysis_graph.nodes.values():self.assertEqual(s.evaluate_node(n,{d.id:saved[d.id] for d in s.analysis_graph.get_dependencies(n)}),saved[n.id])
    def test_limit_exhaustion_is_failure(self):
        p=Program(['A']);p.add_local_assign('A','x',1);p.add_observe('A','x');s=CentralizedSolver(p,AnalysisGraph(p));s.max_iterations=1
        with self.assertRaises(RuntimeError):s.solve()
    def test_missing_message_is_failure(self):
        p=Program(['A','B']);p.new_event(EventType.RECEIVE,'B',source='A',msg_type='missing',message_id='none',bindings={'x':'x'});p.add_observe('B','x')
        with self.assertRaises(ValueError):AnalysisGraph(p)
    def test_reachable_send_without_payload_is_not_delivered(self):
        p=Program(['A','B']);p.add_local_assign('A','x',7)
        send=p.add_send('A','B','m',{'field':{'var':'x'}},message_id='m')
        receive=p.add_receive('B','A','m',message_id='m',bindings={'field':'x'});p.add_observe('B','x')
        solver=CentralizedSolver(p,AnalysisGraph(p))
        partial={d.id:AbstractDomain(True) for d in solver.analysis_graph.get_dependencies(receive.id)}
        self.assertFalse(solver.evaluate_node(receive.id,partial).reachable)
        solver.solve();self.assertEqual(solver.get_observed_abstract('B::x').constants,{7})
    def test_guarded_send_receive_skip_preserves_local_state(self):
        p=Program(['A','B']);p.add_branch('A',False,'a_decision')
        p.add_send('A','B','m',{'field':7},message_id='m',guard={'decision':'a_decision','value':True})
        p.add_local_assign('B','x',3);p.add_branch('B',False,'b_decision')
        p.add_receive('B','A','m',message_id='m',bindings={'field':'x'},guard={'decision':'b_decision','value':True})
        p.add_observe('B','x');self.assertEqual(analyze(p).get_observed_abstract('B::x').constants,{3})
    def test_cyclic_may_read_value_equations_terminate(self):
        p=Program(['A','B']);p.initial_state=p.initial_state.update_memory('mem','x',0).update_memory('mem','y',0)
        p.add_remote_read('A','mem','x',destination='a');p.add_remote_write('A','mem','y',{'op':'add','args':[{'var':'a'},1]})
        p.add_remote_read('B','mem','y',destination='b');p.add_remote_write('B','mem','x',{'op':'add','args':[{'var':'b'},1]})
        p.add_observe('A','a');p.add_observe('B','b');solver=analyze(p)
        self.assertTrue(solver.get_observed_abstract('A::a').is_top());self.assertLess(solver.iteration_count,1000)

if __name__=='__main__':unittest.main()
