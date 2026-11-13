"""Regression checks for formerly vacuous RQ1 success and family corruption."""
import unittest
from artifact.experiments.rq1 import RQ1Result,RQ1Experiment,tagged_abstract,fingerprint
from artifact.generator.litmus_generator import LitmusGenerator
from artifact.core.ir import Program,EventType

class ObservationCheckerTests(unittest.TestCase):
    def result(self,abstract,values=(7,),taint=False):
        r=RQ1Result("checker");r.expected_points={"c::out"}
        r.concrete_observations={"c::out":set(values)}
        r.concrete_outcomes={"c::out":{(v,taint) for v in values}}
        r.abstract_observations={"c::out":abstract};return r
    def test_top_sound_but_not_exact(self):
        r=self.result({"kind":"top","tainted":False})
        self.assertTrue(r.check_soundness());self.assertFalse(r.exact_match)
    def test_missing_is_error_not_top(self):
        r=self.result({"kind":"set","constants":[7],"tainted":False});r.abstract_observations={}
        self.assertFalse(r.check_soundness());self.assertTrue(r.validation_errors);self.assertFalse(r.exact_match)
    def test_python_null_is_not_top(self):
        r=self.result(None);self.assertFalse(r.check_soundness());self.assertFalse(r.exact_match)
    def test_concrete_null_is_finite_value(self):
        r=self.result({"kind":"set","constants":[None],"tainted":False},values=(None,))
        self.assertTrue(r.check_soundness());self.assertTrue(r.exact_match)
    def test_bottom_not_top(self):
        r=self.result({"kind":"bottom","constants":[],"tainted":False})
        self.assertFalse(r.check_soundness());self.assertTrue(r.soundness_violation)
    def test_wrong_concrete_value(self):
        r=self.result({"kind":"set","constants":[8],"tainted":False})
        self.assertFalse(r.check_soundness());self.assertTrue(r.soundness_violation)
    def test_source_preservation(self):
        r=self.result({"kind":"set","constants":[7],"tainted":False},taint=True)
        self.assertFalse(r.check_soundness());self.assertTrue(r.soundness_violation)
    def test_branch_imprecision_not_exact(self):
        r=self.result({"kind":"set","constants":[0,1,2],"tainted":False},values=(0,2))
        self.assertTrue(r.check_soundness());self.assertFalse(r.exact_match)
    def test_no_observations_not_exact(self):
        r=RQ1Result("empty");self.assertFalse(r.check_soundness());self.assertFalse(r.exact_match)
    def test_extra_key_rejected(self):
        r=self.result({"kind":"set","constants":[7],"tainted":False})
        r.abstract_observations["extra"]={"kind":"top","tainted":False}
        self.assertFalse(r.check_soundness())
    def test_spurious_taint_is_imprecise(self):
        r=self.result({"kind":"set","constants":[7],"tainted":True})
        self.assertTrue(r.check_soundness());self.assertFalse(r.exact_match)
    def test_hand_expectation_typed_value_order_ignored(self):
        p=Program(["W","R"]);p.initial_state=p.initial_state.update_memory("mem","x",None)
        p.add_remote_write("W","mem","x",True);p.add_remote_write("W","mem","x",1)
        p.add_remote_read("R","mem","x",destination="read");p.add_observe("R","read")
        result=RQ1Experiment().test_program(p,"typed_expectation",{"R::read":{"values":[None,True,1,1],"may_tainted":False}})
        self.assertIsNone(result.build_error);self.assertTrue(result.hand_expectation_match)
        self.assertEqual(result.to_dict()["status"],"passed");self.assertTrue(result.exact_match)

class GeneratorTests(unittest.TestCase):
    def test_requested_family_preserved_and_mixed_has_both(self):
        for family in ["rpc","rma","mixed"]:
            for p in LitmusGenerator(42).generate_family(family,8):
                kinds={e.type for e in p.events}
                self.assertEqual(EventType.SEND in kinds,family in {"rpc","mixed"})
                self.assertEqual(EventType.REMOTE_READ in kinds,family in {"rma","mixed"})
                self.assertLessEqual(len(p.events),24)
    def test_reproducible_full_program(self):
        one=LitmusGenerator(42).generate_batch(12);two=LitmusGenerator(42).generate_batch(12)
        self.assertEqual([fingerprint(p.to_dict()) for p in one],[fingerprint(p.to_dict()) for p in two])
    def test_initial_memory_and_constraints_roundtrip(self):
        for p in LitmusGenerator(42).generate_family("mixed",4):
            data=p.to_dict();self.assertEqual(Program.from_dict(data).to_dict(),data)
            self.assertTrue(Program.from_dict(data).initial_state.memory_regions)
            self.assertTrue(Program.from_dict(data).initial_state.ordering_constraints)
    def test_observe_communication_result_not_initialized_zero(self):
        for p in LitmusGenerator(42).generate_batch(12):
            observes=[e for e in p.events if e.type==EventType.OBSERVE]
            self.assertTrue(observes)
            for e in observes:self.assertIsInstance(e.attributes.get("expr"),dict)

if __name__=="__main__":unittest.main()
