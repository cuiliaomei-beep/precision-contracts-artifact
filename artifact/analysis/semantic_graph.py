"""Typed semantic dependencies: state order is separate from payload/value flow."""
from collections import defaultdict
from ..core.ir import EventType

class DependencyEdge:
    class EdgeType:
        LOCAL='local';CONTROL='control';MESSAGE='message';MEMORY='memory';ORDERING='ordering'
    def __init__(self,src,dst,edge_type,reason=''):self.src,self.dst,self.type,self.reason=src,dst,edge_type,reason
    def __eq__(self,other):return isinstance(other,DependencyEdge) and(self.src.id,self.dst.id,self.type)==(other.src.id,other.dst.id,other.type)
    def __hash__(self):return hash((self.src.id,self.dst.id,self.type))
    def __repr__(self):return f'{self.src.id} --{self.type}--> {self.dst.id}'

class SemanticDependencyGraph:
    def __init__(self,program):
        self.program=program;self.edges=set();self.event_map={e.id:e for e in program.events}
        if len(self.event_map)!=len(program.events):raise ValueError('Duplicate event IDs')
        self.previous={};self.message_sources={};self.read_sources={};self.initial_read_regions={}
        by_component=defaultdict(list)
        for e in program.events:
            if e.component not in program.components:raise ValueError(f'Unknown component {e.component}')
            by_component[e.component].append(e)
        for events in by_component.values():
            for a,b in zip(events,events[1:]):self.previous[b.id]=a.id;self.edges.add(DependencyEdge(a,b,'ordering','component program order; not payload flow'))
        for a,b in program.initial_state.ordering_constraints:
            if a not in self.event_map or b not in self.event_map:raise ValueError(f'Unknown ordering constraint {a,b}')
            self.edges.add(DependencyEdge(self.event_map[a],self.event_map[b],'control','explicit happens-before'))
        sends=[e for e in program.events if e.type==EventType.SEND]
        ids=[e.attributes['message_id'] for e in sends if 'message_id' in e.attributes]
        if len(ids)!=len(set(ids)):raise ValueError('Message IDs must identify unique sends')
        for r in program.events:
            if r.type!=EventType.RECEIVE:continue
            a=r.attributes
            candidates=[s for s in sends if s.component==a.get('source') and s.attributes.get('destination')==r.component and s.attributes.get('msg_type')==a.get('msg_type') and ('message_id' not in a or s.attributes.get('message_id')==a['message_id'])]
            if not candidates:raise ValueError(f'Unmatched receive {r.id}')
            for field in a.get('bindings',{}):
                if not all(isinstance(s.attributes.get('data'),dict) and field in s.attributes['data'] for s in candidates):raise ValueError(f'Receive {r.id} field {field} not supplied by every matched send')
            self.message_sources[r.id]=[s.id for s in candidates]
            for s in candidates:self.edges.add(DependencyEdge(s,r,'message','explicit payload binding'))
        self.happens_before=self._closure(self.edges)
        if any(a==b for a,b in self.happens_before):raise ValueError('Cyclic mandatory order / message causality')
        writes=[e for e in program.events if e.type==EventType.REMOTE_WRITE]
        for r in program.events:
            if r.type!=EventType.REMOTE_READ:continue
            self.read_sources[r.id]=[];self.initial_read_regions[r.id]=[]
            for region in self.regions(r):
                candidates=[w for w in writes if region in self.regions(w) and w.attributes['var']==r.attributes['var'] and (r.id,w.id) not in self.happens_before]
                mandatory=[w for w in candidates if self.must_execute(w) and self.regions(w)==[region] and (w.id,r.id) in self.happens_before]
                if not mandatory:self.initial_read_regions[r.id].append(region)
                for w in candidates:
                    # A guaranteed intervening write prevents this old value
                    # being the value of an SC atomic read.
                    if any(w.id!=z.id and (w.id,z.id) in self.happens_before for z in mandatory):continue
                    if w.id not in self.read_sources[r.id]:self.read_sources[r.id].append(w.id)
                    self.edges.add(DependencyEdge(w,r,'memory',f'may-read-from {region}.{r.attributes["var"]}'))
    @staticmethod
    def regions(e):
        regions=list(dict.fromkeys(e.attributes.get('possible_regions',[e.attributes['region']])))
        if e.attributes['region'] not in regions:raise ValueError('Concrete region must be included in static possible_regions')
        return regions
    @staticmethod
    def must_execute(e):return 'guard' not in e.attributes
    @staticmethod
    def _closure(edges):
        # Closure is computed before adding may-read-from edges. Such edges
        # express possible data dependence, never mandatory scheduling order.
        closure={(e.src.id,e.dst.id) for e in edges if e.type in('ordering','control','message')}
        changed=True
        while changed:
            extra={(a,d) for a,b in closure for c,d in closure if b==c} - closure
            changed=bool(extra);closure|=extra
        return closure
    def get_dependencies(self,event):
        return{kind:[e.src for e in sorted(self.edges,key=lambda x:(x.src.id,x.dst.id,x.type)) if e.dst.id==event.id and e.type==kind] for kind in('local','control','message','memory','ordering')}
    def get_dependents(self,event):
        return{kind:[e.dst for e in sorted(self.edges,key=lambda x:(x.src.id,x.dst.id,x.type)) if e.src.id==event.id and e.type==kind] for kind in('local','control','message','memory','ordering')}
    def get_transitive_closure(self):return set(self.happens_before)
    def get_edge_count(self):return len(self.edges)
    def get_edges_by_type(self):return{k:sum(e.type==k for e in self.edges) for k in sorted({e.type for e in self.edges})}
    def to_dict(self):return{'nodes':[{'id':e.id,'type':e.type.value,'component':e.component} for e in self.program.events],'edges':[{'src':e.src.id,'dst':e.dst.id,'type':e.type,'reason':e.reason} for e in sorted(self.edges,key=lambda x:(x.src.id,x.dst.id,x.type))],'initial_read_regions':self.initial_read_regions}
