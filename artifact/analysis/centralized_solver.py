"""Independent monotone event equations and a pure reusable transfer interface.

This module does not import or execute the concrete interpreter/oracle. Each
equation consumes only explicit predecessor outputs. Order carries local state;
message edges carry field payloads; may-read-from edges carry write effects.
"""
from collections import deque
from itertools import product
import math
from ..core.ir import EventType
from .domain import AbstractDomain,AbstractState,AbstractValue,AbstractMemoryRegion,scalar_key,FrozenConstants
from .projection import observation_key

class StaticAnalysisError(ValueError):pass

class AnalysisResult:
    def __init__(self):self.domain=AbstractDomain();self.iterations=0;self.converged=False;self.transfer_steps=0

class CentralizedSolver:
    def __init__(self,program,analysis_graph):
        self.program,self.analysis_graph=program,analysis_graph
        self.fixed_points={};self.iteration_count=0;self.max_iterations=100000;self.convergence_achieved=False
        self.transfer_steps=0;self.worklist=deque();self.analysis_results={}
        self._sg=analysis_graph.semantic_graph
        self._incoming={n:[] for n in analysis_graph.nodes}
        for edge in analysis_graph.edges:self._incoming[edge.dst.id].append(edge)
        scalars=[]
        def collect(x):
            if isinstance(x,dict):
                if 'var' in x:return
                if 'op' in x:
                    for a in x.get('args',[]):collect(a)
            elif isinstance(x,(str,bool,int,float)) or x is None:scalars.append(x)
        for e in program.events:
            a=e.attributes
            for k in('value','expr','condition'):
                if k in a:collect(a[k])
            for v in a.get('data',{}).values():collect(v)
        for state in program.initial_state.local_states.values():scalars.extend(state.values.values())
        for state in program.initial_state.memory_regions.values():scalars.extend(state.values.values())
        for s in scalars:scalar_key(s)
        nums=[abs(v) for v in scalars if type(v) is int]
        self.numeric_bound=max(16,max(nums,default=1)+1)*(len(program.events)+1)**2
        strings=[v for v in scalars if type(v) is str]
        self.string_alphabet=set(''.join(strings));self.string_bound=max(1,max(map(len,strings),default=1))*(len(program.events)+1)
        self.constant_universe={'integer_interval':[-self.numeric_bound,self.numeric_bound],
            'string_alphabet':sorted(self.string_alphabet),'maximum_string_length':self.string_bound,
            'null':True,'booleans':[False,True],'finite_ieee_floats':True,
            'out_of_universe':'top','maximum_finite_set_size':AbstractValue.MAX_CONSTANTS,'initialized_before_iteration':True}
    @staticmethod
    def bottom_state():return AbstractDomain()
    def _bounded(self,values,tainted=False):
        vals=[]
        for v in values:
            scalar_key(v)
            valid=(v is None or type(v) is bool or(type(v) is int and abs(v)<=self.numeric_bound) or
                (type(v) is float and math.isfinite(v)) or(type(v) is str and len(v)<=self.string_bound and set(v)<=self.string_alphabet))
            if not valid:return AbstractValue(None,tainted)
            vals.append(v)
        return AbstractValue(vals,tainted)
    def _expr(self,expr,state):
        if not isinstance(expr,dict):return self._bounded([expr])
        if set(expr)=={'var'}:
            try:return state.get(expr['var'])
            except KeyError as ex:raise StaticAnalysisError(str(ex)) from ex
        if 'op' not in expr or set(expr)-{'op','args'}:raise StaticAnalysisError(f'Unsupported expression {expr}')
        op=expr['op'];args=[self._expr(a,state) for a in expr.get('args',[])]
        arity=1 if op=='not' else 2
        if op not in('add','sub','mul','gt','ge','lt','le','eq','ne','and','or','not') or len(args)!=arity:raise StaticAnalysisError(f'Unsupported operator/arity {expr}')
        if any(a.is_bottom() for a in args):return AbstractValue.bottom()
        taint=any(a.tainted for a in args)
        if any(a.is_top() for a in args):return AbstractValue([False,True],taint) if op in('gt','ge','lt','le','eq','ne','and','or','not') else AbstractValue(None,taint)
        result=[]
        for values in product(*(a.constants for a in args)):
            a=values[0];b=values[1] if len(values)>1 else None
            try:
                if op=='add':v=a+b
                elif op=='sub':v=a-b
                elif op=='mul':v=a*b
                elif op=='gt':v=a>b
                elif op=='ge':v=a>=b
                elif op=='lt':v=a<b
                elif op=='le':v=a<=b
                elif op=='eq':v=type(a) is type(b) and a==b
                elif op=='ne':v=not(type(a) is type(b) and a==b)
                elif op=='and':v=bool(a) and bool(b)
                elif op=='or':v=bool(a) or bool(b)
                else:v=not bool(a)
            except(TypeError,ValueError,OverflowError) as ex:raise StaticAnalysisError(f'Invalid abstract operation {expr}: {ex}') from ex
            result.append(v)
            if len(result)>1024:return AbstractValue(None,taint)
        return self._bounded(result,taint)
    def _initial(self,component):
        out=AbstractDomain(True);concrete=self.program.initial_state.local_states[component]
        taints=getattr(self.program.initial_state,'local_taints',{}).get(component,{})
        out.set_local(component,AbstractState(component,{k:self._bounded([v],taints.get(k,False)) for k,v in concrete.values.items()}));return out
    def _carry(self,before,component):
        out=AbstractDomain(before.reachable)
        if before.reachable:out.set_local(component,before.get_local(component));out.decisions=dict(before.decisions)
        return out
    def evaluate_node(self,node,predecessor_states):
        """Pure F_node(predmap): no hidden access to self.fixed_points."""
        node=self.analysis_graph.nodes[node] if isinstance(node,str) else node
        event=node.event;attrs=event.attributes;component=event.component
        required={x.id for x in self.analysis_graph.get_dependencies(node)}
        if not required<=predecessor_states.keys():raise StaticAnalysisError(f'Missing predecessor states at {node.id}: {required-predecessor_states.keys()}')
        previous=self._sg.previous.get(node.id)
        before=predecessor_states[previous] if previous else self._initial(component)
        if not before.reachable:return self.bottom_state()
        for edge in self._incoming[node.id]:
            if edge.type=='control' and not predecessor_states[edge.src.id].reachable:return self.bottom_state()
        base=self._carry(before,component);state=base.get_local(component)
        outcomes={True}
        if 'guard' in attrs:
            guard=attrs['guard']
            if not isinstance(guard,dict) or set(guard)!={'decision','value'} or type(guard['value']) is not bool:raise StaticAnalysisError(f'Invalid guard {guard}')
            if guard['decision'] not in base.decisions:raise StaticAnalysisError(f'Undefined branch decision {guard["decision"]}')
            d=base.decisions[guard['decision']]
            outcomes={False,True} if d.is_top() else {bool(v)==guard['value'] for v in d.constants}
            if not outcomes:return self.bottom_state()
        if True not in outcomes:return base
        out=self._carry(base,component);kind=event.type
        if kind==EventType.LOCAL_ASSIGN:
            if 'value' not in attrs or not attrs.get('var'):raise StaticAnalysisError(f'Invalid assignment {event.id}')
            value=self._expr(attrs['value'],state)
            if attrs.get('tainted',False):value=AbstractValue(value.constants,True)
            out.set_local(component,state.set(attrs['var'],value))
        elif kind==EventType.SEND:
            if not isinstance(attrs.get('data'),dict):raise StaticAnalysisError(f'Send {event.id} requires explicit data map')
            out.payload={field:self._expr(expr,state) for field,expr in attrs['data'].items()}
        elif kind==EventType.RECEIVE:
            candidates=[predecessor_states[eid] for eid in self._sg.message_sources[node.id]]
            candidates=[x for x in candidates if x.reachable]
            if not candidates:return base if False in outcomes else self.bottom_state()
            fields={}
            for candidate in candidates:
                for field,value in candidate.payload.items():fields[field]=fields.get(field,AbstractValue.bottom()).join(value)
            bindings=attrs.get('bindings',{field:field for field in fields})
            for field,dest in bindings.items():
                # A reachable skipped send has no delivered payload. Schema
                # validation already checked all eventual field bindings.
                if field not in fields:return base if False in outcomes else self.bottom_state()
                state=state.set(dest,fields[field])
            out.set_local(component,state)
        elif kind==EventType.REMOTE_WRITE:
            if 'value' not in attrs:raise StaticAnalysisError(f'Write {event.id} lacks value')
            value=self._expr(attrs['value'],state)
            if attrs.get('tainted',False):value=AbstractValue(value.constants,True)
            for region in self._sg.regions(event):out.set_region(region,AbstractMemoryRegion(region,'',{attrs['var']:value}))
        elif kind==EventType.REMOTE_READ:
            value=AbstractValue.bottom()
            for region in self._sg.initial_read_regions[node.id]:
                initial=self.program.initial_state.memory_regions.get(region)
                if initial is None or attrs['var'] not in initial.values:raise StaticAnalysisError(f'Uninitialized memory {region}.{attrs["var"]} potentially read at {event.id}')
                taint=getattr(self.program.initial_state,'memory_taints',{}).get(region,{}).get(attrs['var'],False)
                value=value.join(self._bounded([initial.values[attrs['var']]],taint))
            for eid in self._sg.read_sources[node.id]:
                pred=predecessor_states[eid]
                if not pred.reachable:continue
                for region in self._sg.regions(event):
                    effect=pred.memory_regions.get(region)
                    if effect is not None:value=value.join(effect.values.get(attrs['var'],AbstractValue.bottom()))
            if value.is_bottom():return base if False in outcomes else self.bottom_state()
            out.set_local(component,state.set(attrs.get('destination',f'_read_{event.id}'),value))
        elif kind==EventType.BRANCH:
            value=self._expr(attrs['condition'],state)
            decision=AbstractValue([False,True],value.tainted) if value.is_top() else AbstractValue([bool(v) for v in value.constants],value.tainted)
            out.decisions[attrs.get('decision',event.id)]=decision
        elif kind==EventType.OBSERVE:out.observed=self._expr(attrs.get('expr',{'var':attrs['point']}),state)
        elif kind not in(EventType.SYNC,EventType.COMPONENT):raise StaticAnalysisError(f'Unsupported event kind {kind}')
        return out.join(base) if False in outcomes else out
    def _transfer_node(self,node,input_domain=None):
        return self.evaluate_node(node,{d.id:self.fixed_points[d.id] for d in self.analysis_graph.get_dependencies(node)})
    def solve(self):
        self.fixed_points={n:self.bottom_state() for n in self.analysis_graph.nodes}
        self.worklist=deque(self.analysis_graph.nodes);queued=set(self.worklist);steps=0
        while self.worklist:
            if steps>=self.max_iterations:raise RuntimeError(f'Static solver iteration limit {self.max_iterations} exhausted; result is not converged')
            nid=self.worklist.popleft();queued.remove(nid);node=self.analysis_graph.nodes[nid]
            candidate=self.evaluate_node(node,{d.id:self.fixed_points[d.id] for d in self.analysis_graph.get_dependencies(node)})
            old=self.fixed_points[nid]
            if not old<=candidate:raise StaticAnalysisError(f'Nonmonotone equation at {nid}')
            if candidate!=old:
                self.fixed_points[nid]=candidate
                for dependent in self.analysis_graph.get_dependents(node):
                    if dependent.id not in queued:self.worklist.append(dependent.id);queued.add(dependent.id)
            steps+=1
        self.iteration_count=steps;self.transfer_steps=steps;self.convergence_achieved=True
        residual=self.check_residual()
        if residual:raise RuntimeError(f'Nonzero residual after convergence: {residual}')
        return self.fixed_points
    def check_residual(self,fixed_points=None):
        fp=self.fixed_points if fixed_points is None else fixed_points
        if set(fp)!=set(self.analysis_graph.nodes):raise StaticAnalysisError('Fixed-point node set mismatch')
        return[n.id for n in self.analysis_graph.nodes.values() if self.evaluate_node(n,{d.id:fp[d.id] for d in self.analysis_graph.get_dependencies(n)})!=fp[n.id]]
    def get_result_at(self,event_id):
        if event_id not in self.fixed_points:raise KeyError(f'Missing analysis state {event_id}')
        return self.fixed_points[event_id]
    def get_observations(self):
        result={}
        for event in self.program.events:
            if event.id not in self.analysis_graph.query_points:continue
            if event.id not in self.fixed_points:raise KeyError(f'Missing observation state {event.id}')
            key=observation_key(event);result[key]=result.get(key,AbstractValue.bottom()).join(self.fixed_points[event.id].observed)
        return result
    def get_observed_abstract(self,point):
        obs=self.get_observations()
        if point in obs:return obs[point]
        matches=[v for k,v in obs.items() if k.split('::',1)[1]==point]
        if len(matches)>1:raise ValueError(f'Ambiguous observation {point}; use component::point')
        if not matches:raise KeyError(f'Missing observation {point}')
        return matches[0]
    get_observed_values_abstract=get_observed_abstract
    def get_statistics(self):return{'iterations':self.iteration_count,'converged':self.convergence_achieved,'nodes_analyzed':len(self.fixed_points),'max_iterations':self.max_iterations,'residual_nodes':self.check_residual() if self.convergence_achieved else None,'constant_universe':self.constant_universe}
    def to_dict(self):return{'statistics':self.get_statistics(),'fixed_points':{k:v.to_dict() for k,v in sorted(self.fixed_points.items())},'observations':{k:v.to_dict() for k,v in self.get_observations().items()}}
