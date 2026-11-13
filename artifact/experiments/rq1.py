"""RQ1: exhaustive concrete schedules versus independent tagged abstract observations.

Missing/invalid observations and incomplete exploration are errors, never Top or
exact. Top covers concrete values but is not exact for a finite observation set.
"""
from __future__ import annotations
import hashlib
import json
from collections.abc import Set
from pathlib import Path
from ..core.ir import Program,EventType
from ..oracle.exhaustive import ExhaustiveScheduler
from ..analysis.projection import AnalysisGraph
from ..analysis.centralized_solver import CentralizedSolver

REPAIR_ROOT=Path(__file__).resolve().parents[2]/"experiments/legacy_repair_20261016"
def canonical(value):return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)
def fingerprint(value):return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
def tokens(values):return {canonical(v) for v in values}
def ordered(values):return sorted(values,key=canonical)

def tagged_abstract(value):
    if hasattr(value,"to_dict"):value=value.to_dict()
    if isinstance(value,(Set,list,tuple)):
        value={"kind":"set" if value else "bottom","constants":ordered(value),"tainted":False}
    if not isinstance(value,dict) or value.get("kind") not in {"top","bottom","set"}:
        raise ValueError("Abstract observation needs explicit top/bottom/set tag; null is not Top")
    tag=value["kind"]
    if type(value.get("tainted")) is not bool:raise ValueError("Abstract tainted flag must be boolean")
    if tag=="top":
        if value.get("constants") not in (None,[]):raise ValueError("Top cannot carry finite constants")
        return {"kind":"top","tainted":value["tainted"]}
    constants=value.get("constants",[])
    if not isinstance(constants,(Set,list,tuple)):raise ValueError("Finite constants must be a collection")
    if tag=="bottom" and constants:raise ValueError("Bottom cannot contain concrete values")
    if tag=="set" and not constants:raise ValueError("Empty set must use bottom tag")
    return {"kind":tag,"constants":ordered(constants),"tainted":value["tainted"]}

class RQ1Result:
    def __init__(self,program_name):
        self.program_name=program_name;self.concrete_observations={};self.abstract_observations={}
        self.concrete_outcomes={};self.expected_points=None;self.soundness_violation=False;self.exact_match=False
        self.schedule_count=0;self.state_count=0;self.complete=False;self.truncated=False;self.deadlock_count=0
        self.build_error=None;self.validation_errors=[];self.program_sha256=None;self.abstract_state={}
        self.abstract_state_sha256=None;self.expected_hand_observations=None;self.hand_expectation_match=None
        self.oracle_report={};self.converged=False
        self.expected_pending=None;self.pending_expectation_match=None
    def check_soundness(self):
        self.exact_match=False;self.soundness_violation=False;self.validation_errors=[]
        expected=set(self.expected_points if self.expected_points is not None else self.concrete_observations)
        if not expected:self.validation_errors.append("No expected observation points; vacuous checks forbidden")
        if set(self.concrete_observations)!=expected:self.validation_errors.append("Concrete observation key mismatch")
        if set(self.abstract_observations)!=expected:self.validation_errors.append("Abstract observation key mismatch (missing or extra)")
        if self.validation_errors:return False
        exact=True
        for point in sorted(expected):
            concrete=self.concrete_observations[point]
            if not isinstance(concrete,(Set,list,tuple)) or not concrete:
                self.validation_errors.append(f"Missing/empty reachable concrete observation {point}");continue
            try:abstract=tagged_abstract(self.abstract_observations[point])
            except (ValueError,TypeError) as error:self.validation_errors.append(f"{point}: {error}");continue
            self.abstract_observations[point]=abstract
            cvals=tokens(concrete)
            if abstract["kind"]=="top":exact=False
            else:
                avals=tokens(abstract["constants"])
                if not cvals<=avals:self.soundness_violation=True
                if cvals!=avals:exact=False
            if point in self.concrete_outcomes:
                outcomes=self.concrete_outcomes[point]
                if not outcomes:self.validation_errors.append(f"Empty concrete value/taint outcomes {point}");continue
                may_taint=any(bool(t) for _,t in outcomes)
                if may_taint and not abstract["tainted"]:self.soundness_violation=True
                if may_taint!=abstract["tainted"]:exact=False
        self.exact_match=bool(exact and not self.validation_errors and not self.soundness_violation)
        return not self.validation_errors and not self.soundness_violation
    def to_dict(self):
        abstract={}
        for key,value in self.abstract_observations.items():
            try:abstract[key]=tagged_abstract(value)
            except Exception:abstract[key]={"kind":"invalid","raw":repr(value)}
        status="error" if self.build_error or self.validation_errors or not self.complete or not self.converged else ("unsound" if self.soundness_violation else "passed")
        return dict(program=self.program_name,program_sha256=self.program_sha256,status=status,
            concrete={k:{"kind":"set","values":ordered(v)} for k,v in self.concrete_observations.items()},
            concrete_outcomes={k:[{"value":v,"tainted":t} for v,t in sorted(vals,key=lambda x:canonical([x[0],x[1]]))] for k,vals in self.concrete_outcomes.items()},
            abstract=abstract,soundness_violation=self.soundness_violation,exact_match=self.exact_match,
            exact_definition="finite value sets and may-taint projection equal exhaustive observations; not relational equality of value/taint correlations",
            schedule_count=self.schedule_count,state_count=self.state_count,oracle_complete=self.complete,
            oracle_truncated=self.truncated,deadlock_count=self.deadlock_count,converged=self.converged,
            build_error=self.build_error,validation_errors=self.validation_errors,oracle=self.oracle_report,
            abstract_state=self.abstract_state,abstract_state_sha256=self.abstract_state_sha256,
            expected_hand_observations=self.expected_hand_observations,hand_expectation_match=self.hand_expectation_match,
            expected_pending=self.expected_pending,pending_expectation_match=self.pending_expectation_match)

class RQ1Experiment:
    def __init__(self,output_dir=None):
        self.output_dir=Path(output_dir) if output_dir else REPAIR_ROOT/"rq1"
        self.output_dir.mkdir(parents=True,exist_ok=True);self.results=[]
    def test_program(self,prog,name,expected_observations=None,expected_pending=None):
        result=RQ1Result(name)
        try:
            result.program_sha256=fingerprint(prog.to_dict())
            result.expected_points={f"{e.component}::{e.attributes['point']}" for e in prog.events if e.type==EventType.OBSERVE}
            if not result.expected_points:raise ValueError("No observation events")
            exploration=ExhaustiveScheduler().explore(prog)
            result.schedule_count=exploration.schedule_count;result.state_count=exploration.state_count
            result.complete=exploration.complete;result.truncated=exploration.truncated
            result.deadlock_count=exploration.deadlock_count
            result.concrete_observations=exploration.observations
            result.concrete_outcomes=exploration.observation_outcomes
            if hasattr(exploration,"to_dict"):result.oracle_report=exploration.to_dict()
            if expected_pending is not None:
                result.expected_pending=expected_pending
                result.pending_expectation_match=set(exploration.pending_event_ids)==set(expected_pending)
                if not result.pending_expectation_match:raise AssertionError("Independent handcase completion expectation differs")
            if not result.complete or result.truncated:raise RuntimeError("Oracle exploration incomplete/truncated")
            if result.schedule_count<=0 or result.deadlock_count:raise RuntimeError("No complete execution or deadlocked legal schedules")
            graph=AnalysisGraph(prog);solver=CentralizedSolver(prog,graph);fixed=solver.solve()
            result.converged=bool(solver.convergence_achieved)
            if not result.converged:raise RuntimeError("Static solver did not converge")
            result.abstract_observations=solver.get_observations()
            result.abstract_state={str(k):v.to_dict() for k,v in sorted(fixed.items())}
            result.abstract_state_sha256=fingerprint(result.abstract_state)
            result.check_soundness()
            if expected_observations is not None:
                result.expected_hand_observations=expected_observations
                actual={p:{"values":ordered(vals),"may_tainted":any(t for _,t in result.concrete_outcomes[p])} for p,vals in result.concrete_observations.items()}
                normalized={}
                for point,expectation in expected_observations.items():
                    if type(expectation.get("may_tainted")) is not bool:
                        raise ValueError("Handcase may_tainted must be a boolean")
                    # Observation values are typed sets. JSON scalar keys retain
                    # True versus 1 and explicit null while ignoring list order.
                    unique={canonical(v):v for v in expectation["values"]}
                    normalized[point]={"values":ordered(unique.values()),"may_tainted":expectation["may_tainted"]}
                result.hand_expectation_match=canonical(actual)==canonical(normalized)
                if not result.hand_expectation_match:raise AssertionError(f"Independent handcase expectation differs: {actual}")
        except Exception as error:result.build_error=f"{type(error).__name__}: {error}"
        return result
    def test_litmus_test(self,test):
        try:return self.test_program(test.get_program(),test.name,getattr(test,"expected_observations",None),getattr(test,"expected_pending",None))
        except Exception as error:
            result=RQ1Result(test.name);result.build_error=f"{type(error).__name__}: {error}";return result
    @staticmethod
    def _summarize_family(name,results):
        return dict(family=name,programs=len(results),violations=sum(r.soundness_violation for r in results),
            exact_matches=sum(r.exact_match and r.to_dict()["status"]=="passed" for r in results),
            build_failures=sum(r.to_dict()["status"]=="error" for r in results),
            complete_oracles=sum(r.complete and not r.truncated for r in results),
            total_schedules=sum(r.schedule_count for r in results),total_states=sum(r.state_count for r in results),
            avg_schedules=sum(r.schedule_count for r in results)/len(results) if results else 0,
            max_schedules=max((r.schedule_count for r in results),default=0))
    def _run_family(self,family,count):
        from scripts.build_legacy_repair_corpus import build_corpus
        indexpath=REPAIR_ROOT/"corpus/rq1/index.json"
        if not indexpath.exists():build_corpus()
        index=json.loads(indexpath.read_text(encoding="utf-8"))
        available=[r for r in index["records"] if r["family"]==family]
        if count<0 or count>len(available):raise ValueError("Requested family count outside serialized corpus")
        selected=[]
        for item in available[:count]:
            record=json.loads((REPAIR_ROOT/item["path"]).read_text(encoding="utf-8"))
            result=self.test_program(Program.from_dict(record["program"]),record["id"],record.get("expected_observations"),record.get("expected_pending"))
            selected.append(result);self.results.append(result)
        return self._summarize_family(family,selected)
    def run_rpc_family(self,count=150):return self._run_family("rpc",count)
    def run_rma_family(self,count=200):return self._run_family("rma",count)
    def run_mixed_family(self,count=100):return self._run_family("mixed",count)
    def run_all(self):
        from scripts.build_legacy_repair_corpus import build_corpus
        index=build_corpus();records=index["rq1"]["records"]
        for item in records:
            record=json.loads((REPAIR_ROOT/item["path"]).read_text(encoding="utf-8"))
            result=self.test_program(Program.from_dict(record["program"]),record["id"],record.get("expected_observations"),record.get("expected_pending"))
            self.results.append(result)
        return self.summary()
    def summary(self):
        return dict(total=len(self.results),violations=sum(r.soundness_violation for r in self.results),
            exact_matches=sum(r.exact_match and r.to_dict()["status"]=="passed" for r in self.results),
            errors=sum(r.to_dict()["status"]=="error" for r in self.results),
            complete_oracles=sum(r.complete and not r.truncated for r in self.results))
    def save_results(self,filename="rq1_results.json"):
        data=dict(results=[r.to_dict() for r in self.results],summary=self.summary())
        (self.output_dir/filename).write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        return data
