// Classic semi-naive inclusion-constraint propagation, owner-computes BSP.
// This is a baseline measurement adapter, not a new analysis/partition algorithm.
// Heap field ownership follows allocation-site ownership; dynamic edges are
// discovered from deltas, never imported from the reference solution.
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <map>
#include <numeric>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <sys/resource.h>
#include <omp.h>
using namespace std;
using Clock=chrono::steady_clock;
double seconds(Clock::time_point b){return chrono::duration<double>(Clock::now()-b).count();}
struct Node{int owner,mask,anchor; vector<uint64_t> bits;vector<int> values;};
struct Rule{int other,field;bool load;};
struct Msg{int dst_owner;vector<int> targets,objects;};
struct Request{int object,field,other;bool load;};
int main(int argc,char**argv){try{
 if(argc!=5)throw runtime_error("usage: classic_owner input.txt partition.txt K output_prefix");
 auto wall=Clock::now();ifstream in(argv[1]),pin(argv[2]);int k=stoi(argv[3]);
 if(!in||!pin||k<1)throw runtime_error("bad input");
 int nv,na,nf,nm;in>>nv>>na>>nf>>nm;int initial=nv+na,words=(na+63)/64;
 vector<vector<uint64_t>> masks(nm,vector<uint64_t>(words));
 for(int i=0;i<nm;i++){int count;in>>count;while(count--){int x;in>>x;masks[i][x/64]|=1ULL<<(x%64);}}
 vector<int>varmask(nv),fieldmask(nf),owners(initial);for(int&x:varmask)in>>x;for(int&x:fieldmask)in>>x;
 for(int&x:owners){pin>>x;if(x<0||x>=k)throw runtime_error("invalid owner");}
 vector<Node>nodes;vector<vector<int>>adj;vector<unordered_set<int>>seen;
 vector<vector<Rule>>rules(initial);
 auto addnode=[&](int owner,int mask,int anchor){int id=nodes.size();nodes.push_back({owner,mask,anchor,vector<uint64_t>(words),{}});adj.emplace_back();seen.emplace_back();return id;};
 for(int v=0;v<nv;v++)addnode(owners[v],varmask[v],v);
 for(int a=0;a<na;a++)addnode(owners[nv+a],-1,nv+a);
 int nc;in>>nc;for(int i=0;i<nc;i++){int u,v;in>>u>>v;if(seen[u].insert(v).second)adj[u].push_back(v);}
 int ns;in>>ns;for(int i=0;i<ns;i++){int src,base,f;in>>src>>base>>f;rules[base].push_back({src,f,false});}
 int nl;in>>nl;for(int i=0;i<nl;i++){int base,f,dst;in>>base>>f>>dst;rules[base].push_back({dst,f,true});}
 if(!in)throw runtime_error("truncated input");
 map<pair<int,int>,int>heap;
 vector<vector<int>>delta(initial);vector<int>active;
 for(int a=0;a<na;a++){int v=nv+a;nodes[v].bits[a/64]|=1ULL<<(a%64);nodes[v].values.push_back(a);delta[v].push_back(a);active.push_back(v);}
 vector<uint64_t>worker_ops(k),cross_bytes(k),cross_messages(k),local_messages(k),profile(initial);
 uint64_t rounds=0,newedges=0;double propagate=0,install=0,apply=0;
 double setup=seconds(wall);auto solve=Clock::now();
 omp_set_dynamic(0);omp_set_num_threads(k);
 while(!active.empty()){
  if(++rounds>100000)throw runtime_error("round bound exceeded");
  vector<vector<int>>work(k);for(int u:active)work[nodes[u].owner].push_back(u);
  vector<vector<Msg>>outbox(k);vector<vector<Request>>requests(k);
  auto phase=Clock::now();
  #pragma omp parallel for schedule(static,1)
  for(int w=0;w<k;w++)for(int u:work[w]){
   map<int,vector<int>>groups;
   for(int v:adj[u])groups[nodes[v].owner].push_back(v);
   for(auto&[dw,targets]:groups){
    uint64_t ops=uint64_t(targets.size())*delta[u].size();worker_ops[w]+=ops;profile[nodes[u].anchor]+=ops;
    if(dw!=w){cross_messages[w]++;cross_bytes[w]+=16+4*(targets.size()+delta[u].size());}else local_messages[w]++;
    outbox[w].push_back({dw,move(targets),delta[u]});
   }
   if(u<initial)for(auto&r:rules[u])for(int a:delta[u]){
    requests[w].push_back({a,r.field,r.other,r.load});worker_ops[w]++;profile[nodes[u].anchor]++;
   }
  }
  propagate+=seconds(phase);phase=Clock::now();
  for(int w=0;w<k;w++)for(auto&r:requests[w]){
   pair<int,int>key={r.object,r.field};int h;
   auto it=heap.find(key);
   if(it==heap.end()){
    int a=nv+r.object;h=addnode(nodes[a].owner,fieldmask[r.field],a);heap[key]=h;
   }else h=it->second;
   int u=r.load?h:r.other,v=r.load?r.other:h;
   if(seen[u].insert(v).second){
    adj[u].push_back(v);newedges++;
    if(!nodes[u].values.empty()){
     int sw=nodes[u].owner,dw=nodes[v].owner;
     if(sw!=dw){cross_messages[sw]++;cross_bytes[sw]+=20+4*nodes[u].values.size();}else local_messages[sw]++;
     worker_ops[sw]+=nodes[u].values.size();profile[nodes[u].anchor]+=nodes[u].values.size();
     outbox[sw].push_back({dw,{v},nodes[u].values});
    }
   }
  }
  install+=seconds(phase);phase=Clock::now();
  vector<vector<int>>next(nodes.size());vector<vector<int>>next_active(k);
  #pragma omp parallel for schedule(static,1)
  for(int w=0;w<k;w++){
   for(int sw=0;sw<k;sw++)for(auto&m:outbox[sw])if(m.dst_owner==w)for(int v:m.targets){
    auto&node=nodes[v];
    for(int a:m.objects){
     uint64_t bit=1ULL<<(a%64);int word=a/64;
     if((node.mask<0||(masks[node.mask][word]&bit))&&!(node.bits[word]&bit)){
      node.bits[word]|=bit;node.values.push_back(a);
      if(next[v].empty())next_active[w].push_back(v);next[v].push_back(a);
     }
    }
   }
  }
  active.clear();for(auto&xs:next_active)active.insert(active.end(),xs.begin(),xs.end());
  delta.swap(next);apply+=seconds(phase);
 }
 double solve_seconds=seconds(solve);string prefix=argv[4];
 ofstream pts(prefix+".pts");uint64_t count=0;
 for(int v=0;v<nv;v++){auto xs=nodes[v].values;sort(xs.begin(),xs.end());for(int a:xs){pts<<"V "<<v<<" "<<a<<"\n";count++;}}
 for(auto&[key,h]:heap){auto xs=nodes[h].values;sort(xs.begin(),xs.end());for(int a:xs){pts<<"H "<<key.first<<" "<<key.second<<" "<<a<<"\n";count++;}}
 pts.close();ofstream prof(prefix+".profile");for(auto x:profile)prof<<x<<"\n";
 struct rusage usage;getrusage(RUSAGE_SELF,&usage);
 auto sum=[](auto&v){return accumulate(v.begin(),v.end(),uint64_t(0));};
 cout<<"{\"workers\":"<<k<<",\"initial_nodes\":"<<initial<<",\"heap_slots\":"<<heap.size()
 <<",\"initial_copy_edges\":"<<nc<<",\"dynamic_copy_edges\":"<<newedges<<",\"rounds\":"<<rounds
 <<",\"points_to_facts\":"<<count<<",\"setup_seconds\":"<<setup<<",\"solve_seconds\":"<<solve_seconds
 <<",\"propagate_seconds\":"<<propagate<<",\"install_seconds\":"<<install<<",\"apply_seconds\":"<<apply
 <<",\"through_output_wall_seconds\":"<<seconds(wall)<<",\"peak_rss_kib\":"<<usage.ru_maxrss
 <<",\"logical_cross_bytes\":"<<sum(cross_bytes)<<",\"logical_cross_messages\":"<<sum(cross_messages)
 <<",\"local_messages\":"<<sum(local_messages)<<",\"transfer_attempts\":"<<sum(worker_ops)
 <<",\"max_average_work\":"<<double(*max_element(worker_ops.begin(),worker_ops.end()))*k/max(uint64_t(1),sum(worker_ops))
 <<",\"worker_ops\":[";for(int i=0;i<k;i++){if(i)cout<<",";cout<<worker_ops[i];}cout<<"]}\n";
 return 0;
}catch(exception&e){cerr<<e.what()<<"\n";return 2;}}
