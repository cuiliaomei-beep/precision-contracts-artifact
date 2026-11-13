"""Query projection preserving typed semantic prerequisites and distinct observations."""
from .semantic_graph import SemanticDependencyGraph
from ..core.ir import EventType

class AnalysisNode:
    def __init__(self,event):self.event,self.id=event,event.id
    def __hash__(self):return hash(self.id)
    def __eq__(self,other):return isinstance(other,AnalysisNode) and self.id==other.id
    def __repr__(self):return f'Node({self.id})'

class AnalysisEdge:
    def __init__(self,src,dst,dep_type='',weight=1):self.src,self.dst,self.type,self.weight=src,dst,dep_type,weight
    def __hash__(self):return hash((self.src.id,self.dst.id,self.type))
    def __eq__(self,other):return isinstance(other,AnalysisEdge) and(self.src.id,self.dst.id,self.type)==(other.src.id,other.dst.id,other.type)

def observation_key(event):return f'{event.component}::{event.attributes["point"]}'

class AnalysisGraph:
    def __init__(self,program,query_points=None):
        self.program=program;self.semantic_graph=SemanticDependencyGraph(program);self.nodes={};self.edges=set()
        obs=[e for e in program.events if e.type==EventType.OBSERVE]
        if query_points is None:self.query_points={e.id for e in obs}
        else:
            self.query_points=set()
            for query in query_points:
                if query in self.semantic_graph.event_map:
                    if self.semantic_graph.event_map[query].type!=EventType.OBSERVE:raise ValueError(f'Query {query} is not an observation')
                    self.query_points.add(query);continue
                matches=[e for e in obs if observation_key(e)==query or e.attributes['point']==query]
                if not matches:raise KeyError(f'Missing observation {query}')
                if len({e.component for e in matches})>1:raise ValueError(f'Ambiguous observation {query}; use component::point')
                self.query_points.update(e.id for e in matches)
        if not self.query_points:raise ValueError('No observations selected')
        incoming={e.id:[] for e in program.events}
        for edge in self.semantic_graph.edges:incoming[edge.dst.id].append(edge.src.id)
        reached=set();todo=list(self.query_points)
        while todo:
            eid=todo.pop()
            if eid in reached:continue
            reached.add(eid);todo.extend(incoming[eid])
        self.nodes={e.id:AnalysisNode(e) for e in program.events if e.id in reached}
        for edge in self.semantic_graph.edges:
            if edge.src.id in self.nodes and edge.dst.id in self.nodes:self.edges.add(AnalysisEdge(self.nodes[edge.src.id],self.nodes[edge.dst.id],edge.type))
        self._dependencies={n:[] for n in self.nodes};self._dependents={n:[] for n in self.nodes}
        for e in sorted(self.edges,key=lambda e:(e.src.id,e.dst.id,e.type)):
            if e.src not in self._dependencies[e.dst.id]:self._dependencies[e.dst.id].append(e.src)
            if e.dst not in self._dependents[e.src.id]:self._dependents[e.src.id].append(e.dst)
    def get_node(self,event_id):return self.nodes.get(event_id)
    def get_dependencies(self,node):return self._dependencies[node if isinstance(node,str) else node.id]
    def get_dependents(self,node):return self._dependents[node if isinstance(node,str) else node.id]
    def get_cross_component_edges(self):return[e for e in self.edges if e.src.event.component!=e.dst.event.component]
    def get_node_count(self):return len(self.nodes)
    def get_edge_count(self):return len(self.edges)
    def get_components(self):return{n.event.component for n in self.nodes.values()}
    def get_component_nodes(self,component):return[n for n in self.nodes.values() if n.event.component==component]
    def get_edge_density(self):return len(self.edges)/(len(self.nodes)*(len(self.nodes)-1)) if len(self.nodes)>1 else 0
    def get_statistics(self):return{'nodes':len(self.nodes),'edges':len(self.edges),'components':len(self.get_components()),'cross_component_edges':len(self.get_cross_component_edges()),'edge_density':self.get_edge_density(),'max_fan_out':max(map(len,self._dependents.values()),default=0),'max_fan_in':max(map(len,self._dependencies.values()),default=0),'query_points':len(self.query_points)}
    def to_dict(self):return{'nodes':[{'id':n.id,'event_type':n.event.type.value,'component':n.event.component} for n in self.nodes.values()],'edges':[{'src':e.src.id,'dst':e.dst.id,'type':e.type} for e in sorted(self.edges,key=lambda e:(e.src.id,e.dst.id,e.type))],'statistics':self.get_statistics()}
