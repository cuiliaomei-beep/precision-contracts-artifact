import com.google.gson.*;
import soot.*;
import soot.jimple.*;
import soot.options.Options;
import soot.toolkits.graph.ExceptionalUnitGraph;
import soot.toolkits.scalar.SimpleLocalDefs;
import java.nio.file.*;
import java.util.*;

/** Query-projected explicit data provenance from real class-file Jimple.
 * No transport-framework body is traversed: deployment/RPC signatures are checked
 * at this boundary, and all data-producing operations on the query slice must
 * have a body or an explicit, recorded summary. */
public final class ValueFlowExporter {
  static final Gson GSON=new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
  final JsonObject cfg;
  final LinkedHashMap<String,Map<String,Object>> nodes=new LinkedHashMap<>();
  final LinkedHashMap<String,Map<String,Object>> edges=new LinkedHashMap<>();
  final List<Map<String,Object>> evidence=new ArrayList<>();
  final List<String> summaries=new ArrayList<>();
  final Map<String,String> resolved=new HashMap<>();
  final Map<String,Frame> frames=new HashMap<>();
  final Set<String> activeMethods=new HashSet<>();
  final Map<String,String> rpcResults=new HashMap<>();
  final Map<String,Object> metadata=new LinkedHashMap<>();
  int rpcCount=0;
  String text(String key) { return cfg.get(key).getAsString(); }
  String text(String key,String def) { return cfg.has(key)?cfg.get(key).getAsString():def; }
  ValueFlowExporter(JsonObject config) { cfg=config; }
  static class Incomplete extends RuntimeException { Incomplete(String s){super(s);} }
  class Frame {
    final SootMethod method;
    final Body body;
    final String context,component;
    final List<String> params;
    final IdentityHashMap<Unit,Integer> indices=new IdentityHashMap<>();
    final SimpleLocalDefs defs;
    Frame(SootMethod m,String c,String comp,List<String> p) {
      method=m;context=c;component=comp;params=p;
      if(!m.isConcrete() || m.isPhantom()) throw new Incomplete("Missing concrete body: "+m);
      body=m.retrieveActiveBody();
      int i=0;for(Unit u:body.getUnits())indices.put(u,i++);
      defs=new SimpleLocalDefs(new ExceptionalUnitGraph(body));
      validateMutableSummaries(this);
      frames.put(c+"|"+m.getSignature(),this);
    }
    String loc(Unit u){return context+"|"+method.getSignature()+"@"+indices.get(u);}
  }
  boolean mutableSummaryType(Type t){String s=t.toString();return s.equals("java.lang.StringBuilder") || (cfg.has("protobuf") && s.endsWith("$Builder") &&
    (s.equals(cfg.getAsJsonObject("protobuf").getAsJsonObject("request").get("class").getAsString()+"$Builder")||s.equals(cfg.getAsJsonObject("protobuf").getAsJsonObject("response").get("class").getAsString()+"$Builder")));}
  void validateMutableSummaries(Frame f){
    for(Unit u:f.body.getUnits()){
      if(u instanceof AssignStmt){Value l=((AssignStmt)u).getLeftOp(),r=((AssignStmt)u).getRightOp();
        if(mutableSummaryType(r.getType()) && (r instanceof Local || r instanceof CastExpr))throw new Incomplete("Aliased mutable builder needs heap model: "+f.loc(u)+" "+u);
        if(mutableSummaryType(r.getType()) && !(l instanceof Local))throw new Incomplete("Escaping mutable builder needs heap model: "+f.loc(u));
      }
      if(u instanceof Stmt && ((Stmt)u).containsInvokeExpr()){
        InvokeExpr e=((Stmt)u).getInvokeExpr();String owner=e.getMethodRef().getDeclaringClass().getName(),name=e.getMethodRef().getName();
        if(owner.equals("java.lang.StringBuilder")){
          if(name.equals("append") && u instanceof InvokeStmt)throw new Incomplete("Nonfluent StringBuilder append needs heap model: "+f.loc(u));
          if(name.equals("<init>") && e.getArgCount()>0 && !(e.getArg(0) instanceof Constant))throw new Incomplete("Nonconstant StringBuilder constructor needs heap model: "+f.loc(u));
        }
        if(cfg.has("protobuf") && owner.endsWith("$Builder") && name.startsWith("set") && u instanceof InvokeStmt)
          throw new Incomplete("Nonfluent protobuf setter needs heap model: "+f.loc(u));
        for(Value arg:e.getArgs())if(mutableSummaryType(arg.getType()))throw new Incomplete("Builder passed to another method needs heap model: "+f.loc(u));
      }
    }
  }
  String node(Frame f,Unit u,String suffix,String kind){
    String id=f.loc(u)+":"+suffix;
    if(!nodes.containsKey(id)){
      Map<String,Object> n=new LinkedHashMap<>();
      n.put("id",id);n.put("component",f.component);n.put("method",f.method.getSignature());
      n.put("statement",u.toString());n.put("line",u.getJavaSourceStartLineNumber());
      n.put("unit_index",f.indices.get(u));n.put("context",f.context);n.put("kind",kind);
      nodes.put(id,n);
    }return id;
  }
  void edge(String src,String dst,String kind,String callsite){
    if(src==null||dst==null)throw new Incomplete("Null graph endpoint");
    String id=src+"->"+dst+"#"+kind;
    if(edges.containsKey(id))return;
    Map<String,Object> e=new LinkedHashMap<>();e.put("id",id);e.put("src",src);e.put("dst",dst);e.put("kind",kind);
    if(callsite!=null)e.put("rpc_callsite",callsite);edges.put(id,e);
  }
  void edge(String src,String dst,String kind){edge(src,dst,kind,null);}
  void summary(String s){if(!summaries.contains(s))summaries.add(s);}
  SootMethod method(String signature){
    String cn=signature.substring(1,signature.indexOf(':'));
    Scene.v().forceResolve(cn,SootClass.BODIES);
    try{return Scene.v().getMethod(signature);}catch(Exception ex){throw new Incomplete("Cannot resolve "+signature);}
  }
  String val(Value v,Unit at,Frame f){
    String key=f.loc(at)+"|"+v.getClass().getName()+"|"+v.toString();
    if(resolved.containsKey(key))return resolved.get(key);
    String n=node(f,at,"value["+v+"]","value");resolved.put(key,n);
    if(v instanceof Constant || v instanceof NewExpr){nodes.get(n).put("kind",v instanceof NewExpr?"allocation":"constant");return n;}
    if(v instanceof Local){
      List<Unit> ds=f.defs.getDefsOfAt((Local)v,at);
      if(ds.isEmpty())throw new Incomplete("No reaching definition: "+f.loc(at)+" "+v);
      for(Unit d:ds)edge(definition(d,f),n,"local_use");return n;
    }
    if(v instanceof ParameterRef){
      int i=((ParameterRef)v).getIndex();
      if(i>=f.params.size())throw new Incomplete("Unbound entry/query parameter "+i+" at "+f.loc(at));
      edge(f.params.get(i),n,"formal_parameter");return n;
    }
    if(v instanceof ThisRef){nodes.get(n).put("kind","receiver_identity");return n;}
    if(v instanceof CastExpr){edge(val(((CastExpr)v).getOp(),at,f),n,"cast");return n;}
    if(v instanceof BinopExpr){
      edge(val(((BinopExpr)v).getOp1(),at,f),n,"operation");edge(val(((BinopExpr)v).getOp2(),at,f),n,"operation");return n;
    }
    if(v instanceof UnopExpr){edge(val(((UnopExpr)v).getOp(),at,f),n,"operation");return n;}
    if(v instanceof InvokeExpr){invoke((InvokeExpr)v,at,f,n);return n;}
    if(v instanceof StaticFieldRef){
      SootField field=((StaticFieldRef)v).getField();
      if(field.isFinal() && field.getTags().stream().anyMatch(t->t.getName().endsWith("ConstantValueTag"))){nodes.get(n).put("kind","constant");return n;}
    }
    throw new Incomplete("Unsupported query data operation "+v.getClass().getName()+" at "+f.loc(at)+": "+v);
  }
  String definition(Unit d,Frame f){
    String key=f.loc(d)+"|definition";
    if(resolved.containsKey(key))return resolved.get(key);
    String n=node(f,d,"definition","definition");resolved.put(key,n);
    if(!(d instanceof DefinitionStmt))throw new Incomplete("Nondefinition in defs "+d);
    DefinitionStmt ds=(DefinitionStmt)d;
    if(!(ds.getLeftOp() instanceof Local))throw new Incomplete("Heap definition on query slice: "+d);
    edge(val(ds.getRightOp(),d,f),n,"assignment");return n;
  }
  boolean isRpc(InvokeExpr e){
    SootMethodRef r=e.getMethodRef();
    return r.getDeclaringClass().getName().equals(text("rpc_owner")) && r.getName().equals(text("rpc_method"));
  }
  void invoke(InvokeExpr e,Unit u,Frame f,String out){
    String owner=e.getMethodRef().getDeclaringClass().getName(), name=e.getMethodRef().getName();
    String sig=e.getMethodRef().getSignature();
    if(sig.equals("<e2e.shared.Inputs: java.lang.String source(java.lang.String)>")){
      nodes.get(out).put("kind","source");nodes.get(out).put("seed",List.of(f.loc(u)+":source"));return;
    }
    if(isRpc(e)){edge(rpc(e,u,f),out,"call_result");return;}
    if(e instanceof DynamicInvokeExpr){
      DynamicInvokeExpr d=(DynamicInvokeExpr)e;
      if(!d.getBootstrapMethodRef().getDeclaringClass().getName().equals("java.lang.invoke.StringConcatFactory"))
        throw new Incomplete("Unsupported invokedynamic bootstrap "+d);
      summary("StringConcatFactory: explicit result provenance is union of dynamic arguments; recipe constants contribute empty");
      for(Value a:e.getArgs())edge(val(a,u,f),out,"string_concat");return;
    }
    if(owner.equals("java.lang.String")&&name.equals("valueOf")){
      if(e.getArgCount()!=1)throw new Incomplete("Unsupported String.valueOf "+e);
      edge(val(e.getArg(0),u,f),out,"string_conversion");summary("String.valueOf: explicit provenance identity");return;
    }
    if(owner.equals("java.lang.String")&&(name.equals("toString")||name.equals("concat"))){
      edge(val(((InstanceInvokeExpr)e).getBase(),u,f),out,"string_identity");
      for(Value a:e.getArgs())edge(val(a,u,f),out,"string_concat");return;
    }
    if(owner.equals("java.lang.StringBuilder")&&(name.equals("append")||name.equals("toString"))){
      edge(val(((InstanceInvokeExpr)e).getBase(),u,f),out,"string_builder");
      for(Value a:e.getArgs())edge(val(a,u,f),out,"string_builder");
      summary("StringBuilder fluent append/toString: explicit provenance union; nonfluent mutation rejected by slice checks");return;
    }
    if(cfg.has("protobuf") && protobuf(e,u,f,out))return;
    if(owner.equals("java.util.Objects")&&name.equals("requireNonNull")){
      edge(val(e.getArg(0),u,f),out,"identity_summary");summary("Objects.requireNonNull: explicit provenance arg0");return;
    }
    SootMethod m;
    try{m=e.getMethod();}catch(Exception ex){throw new Incomplete("Unresolved query call "+sig);}
    if(!m.isConcrete()||m.isPhantom()||owner.startsWith("java.")||owner.startsWith("javax.")||owner.startsWith("io.grpc.")||owner.startsWith("com.google.protobuf.")||owner.startsWith("org.apache.dubbo."))
      throw new Incomplete("Unmodeled query call: "+sig+" at "+f.loc(u));
    if(activeMethods.contains(sig))throw new Incomplete("Recursive query call needs context fixpoint: "+sig);
    List<String> params=new ArrayList<>();for(Value a:e.getArgs())params.add(val(a,u,f));
    Frame callee=new Frame(m,f.loc(u),f.component,params);activeMethods.add(sig);
    int returns=0;for(Unit ru:callee.body.getUnits())if(ru instanceof ReturnStmt){edge(val(((ReturnStmt)ru).getOp(),ru,callee),out,"method_return");returns++;}
    activeMethods.remove(sig);if(returns==0)throw new Incomplete("Query call has no normal return: "+sig);
  }
  boolean protobuf(InvokeExpr e,Unit u,Frame f,String out){
    JsonObject p=cfg.getAsJsonObject("protobuf");
    String owner=e.getMethodRef().getDeclaringClass().getName(),name=e.getMethodRef().getName();
    for(String which:List.of("request","response")){
      JsonObject m=p.getAsJsonObject(which);String cls=m.get("class").getAsString(),field=m.get("field").getAsString();
      String cap=Character.toUpperCase(field.charAt(0))+field.substring(1);
      if(!(owner.equals(cls)||owner.equals(cls+"$Builder")))continue;
      summary("Protobuf "+cls+": validated one string field "+field+"; fluent setter overwrites field, build/getter preserve it");
      if(name.equals("newBuilder")&&e.getArgCount()==0){nodes.get(out).put("kind","proto_builder_empty");return true;}
      if(name.equals("set"+cap)&&e.getArgCount()==1){edge(val(e.getArg(0),u,f),out,"proto_field_set:"+field);return true;}
      if(name.equals("build")||name.equals("buildPartial")||name.equals("get"+cap)){
        if(!(e instanceof InstanceInvokeExpr))throw new Incomplete("Expected protobuf instance operation "+e);
        validateNoIgnoredMutation(((InstanceInvokeExpr)e).getBase(),u,f,cls);
        edge(val(((InstanceInvokeExpr)e).getBase(),u,f),out,"proto_field:"+field);return true;
      }
      throw new Incomplete("Unsupported protobuf query operation "+e);
    }return false;
  }
  void validateNoIgnoredMutation(Value base,Unit at,Frame f,String cls){
    // An ignored return from setField mutates an existing alias; this first
    // artifact deliberately requires fluent setters rather than guessing heap flow.
    for(Unit x:f.body.getUnits()){
      if(x==at)break;
      if(x instanceof InvokeStmt){InvokeExpr ie=((InvokeStmt)x).getInvokeExpr();
        if(ie instanceof InstanceInvokeExpr && ((InstanceInvokeExpr)ie).getBase().equivTo(base) &&
          ie.getMethodRef().getDeclaringClass().getName().equals(cls+"$Builder") && ie.getMethodRef().getName().startsWith("set"))
          throw new Incomplete("Nonfluent/aliased protobuf builder mutation requires heap model: "+f.loc(x));
      }
    }
  }
  String rpc(InvokeExpr e,Unit u,Frame f){
    String callsite=f.loc(u);if(rpcResults.containsKey(callsite))return rpcResults.get(callsite);
    if(!(e instanceof InstanceInvokeExpr)||e.getArgCount()!=1)throw new Incomplete("Only one-argument instance unary RPC supported: "+e);
    SootMethod handler=method(text("handler"));
    if(!handler.isConcrete())throw new Incomplete("Missing handler body "+handler);
    String clientReq=node(f,u,"request_object","rpc_request_object");
    edge(val(e.getArg(0),u,f),clientReq,"request_payload",callsite);
    String send=node(f,u,"request_send","rpc_request_send");edge(clientReq,send,"event_payload",callsite);
    Frame server=new Frame(handler,"rpc["+callsite+"]","provider",new ArrayList<>());
    Unit first=server.body.getUnits().getFirst();
    String recv=node(server,first,"request_receive","rpc_request_receive");edge(send,recv,"rpc_request",callsite);
    String formal=node(server,first,"request_object","rpc_request_object");edge(recv,formal,"event_payload",callsite);server.params.add(formal);
    String result=node(f,u,"response_receive","rpc_response_receive");rpcResults.put(callsite,result);
    String response=node(server,first,"response_object","rpc_response_object");
    if(text("protocol").equals("dubbo")){
      if(handler.getParameterCount()!=1 || !handler.getReturnType().equals(e.getMethodRef().getReturnType()))throw new Incomplete("Dubbo signature mismatch "+handler);
      int count=0;for(Unit ru:server.body.getUnits())if(ru instanceof ReturnStmt){edge(val(((ReturnStmt)ru).getOp(),ru,server),response,"handler_return",callsite);count++;}
      if(count==0)throw new Incomplete("Dubbo handler has no return "+handler);
    }else if(text("protocol").equals("grpc")){
      if(handler.getParameterCount()!=2 || !handler.getParameterType(0).equals(e.getArg(0).getType()))throw new Incomplete("gRPC request/handler type mismatch "+handler);
      String observer=node(server,first,"observer_identity","observer_identity");server.params.add(observer);
      int count=0,completed=0;
      for(Unit su:server.body.getUnits())if(su instanceof Stmt && ((Stmt)su).containsInvokeExpr()){
        InvokeExpr si=((Stmt)su).getInvokeExpr();
        if(si.getMethodRef().getDeclaringClass().getName().equals("io.grpc.stub.StreamObserver")){
          if(!(si instanceof InstanceInvokeExpr)||!originatesParameter(((InstanceInvokeExpr)si).getBase(),su,server,1,new HashSet<>()))
            throw new Incomplete("Observer not rooted in RPC response parameter: "+su);
          if(si.getMethodRef().getName().equals("onNext")){
            String event=node(server,su,"onNext","rpc_response_send");edge(val(si.getArg(0),su,server),event,"onNext_payload",callsite);edge(event,response,"response_payload",callsite);count++;
          }else if(si.getMethodRef().getName().equals("onCompleted")){completed++;}
          else throw new Incomplete("Unsupported gRPC response operation "+si);
        }
      }
      if(count!=1||completed!=1)throw new Incomplete("Unary fixture must have exactly one onNext and onCompleted, got "+count+"/"+completed);
    }else throw new Incomplete("Unknown protocol "+text("protocol"));
    edge(response,result,"rpc_response",callsite);rpcCount++;
    Map<String,Object> ev=new LinkedHashMap<>();ev.put("callsite",callsite);ev.put("rpc_signature",e.getMethodRef().getSignature());ev.put("handler",handler.getSignature());ev.put("context",server.context);evidence.add(ev);
    return result;
  }
  boolean originatesParameter(Value v,Unit at,Frame f,int p,Set<String> seen){
    if(v instanceof ParameterRef)return ((ParameterRef)v).getIndex()==p;
    if(v instanceof CastExpr)return originatesParameter(((CastExpr)v).getOp(),at,f,p,seen);
    if(v instanceof Local){String key=f.loc(at)+"|"+v;if(!seen.add(key))return false;
      List<Unit> ds=f.defs.getDefsOfAt((Local)v,at);if(ds.isEmpty())return false;
      for(Unit d:ds)if(!(d instanceof DefinitionStmt)||!originatesParameter(((DefinitionStmt)d).getRightOp(),d,f,p,new HashSet<>(seen)))return false;return true;
    }return false;
  }
  void validateProtobuf()throws Exception{
    if(!cfg.has("protobuf"))return;
    JsonObject p=cfg.getAsJsonObject("protobuf");
    for(String which:List.of("request","response")){
      JsonObject x=p.getAsJsonObject(which);String cls=x.get("class").getAsString(),field=x.get("field").getAsString();
      // Inspect generated class metadata reflectively in an isolated JVM classloader.
      Class<?> c=Class.forName(cls);Object descriptor=c.getMethod("getDescriptor").invoke(null);
      List<?> fields=(List<?>)descriptor.getClass().getMethod("getFields").invoke(descriptor);
      if(fields.size()!=1)throw new Incomplete("First artifact only supports one-field protobuf records: "+cls);
      Object fd=fields.get(0);String actual=String.valueOf(fd.getClass().getMethod("getName").invoke(fd));
      String type=String.valueOf(fd.getClass().getMethod("getJavaType").invoke(fd));
      if(!actual.equals(field)||!type.equals("STRING"))throw new Incomplete("Protobuf descriptor mismatch "+cls+" "+actual+" "+type);
      Map<String,Object> ev=new LinkedHashMap<>();ev.put("protobuf_class",cls);ev.put("field",actual);ev.put("java_type",type);ev.put("field_count",fields.size());evidence.add(ev);
    }
    Object file=Class.forName(p.get("descriptor_class").getAsString()).getMethod("getDescriptor").invoke(null);
    Object svc=file.getClass().getMethod("findServiceByName",String.class).invoke(file,p.get("service").getAsString());
    if(svc==null)throw new Incomplete("Missing protobuf service in generated descriptor");
    Object rpc=svc.getClass().getMethod("findMethodByName",String.class).invoke(svc,p.get("method").getAsString());
    if(rpc==null)throw new Incomplete("Missing protobuf RPC in generated service descriptor");
    for(String which:List.of("request","response")){
      Object typ=rpc.getClass().getMethod(which.equals("request")?"getInputType":"getOutputType").invoke(rpc);
      Object expected=Class.forName(p.getAsJsonObject(which).get("class").getAsString()).getMethod("getDescriptor").invoke(null);
      if(!typ.equals(expected))throw new Incomplete("Proto service "+which+" type does not match configured class");
    }
    boolean streaming=(boolean)rpc.getClass().getMethod("isClientStreaming").invoke(rpc)||(boolean)rpc.getClass().getMethod("isServerStreaming").invoke(rpc);
    if(streaming)throw new Incomplete("Only unary protobuf methods supported");
    Map<String,Object> ev=new LinkedHashMap<>();ev.put("proto_service",p.get("service").getAsString());ev.put("proto_rpc",p.get("method").getAsString());ev.put("descriptor_types_validated",true);ev.put("streaming",false);evidence.add(ev);
  }
  Set<String> allocationTypes(Value v,Unit at,Frame f,Set<String> seen){
    Set<String> r=new TreeSet<>();if(v instanceof NewExpr){r.add(((NewExpr)v).getBaseType().getClassName());return r;}
    if(v instanceof CastExpr)return allocationTypes(((CastExpr)v).getOp(),at,f,seen);
    if(v instanceof Local){String key=f.loc(at)+"|"+v;if(!seen.add(key))return r;
      for(Unit d:f.defs.getDefsOfAt((Local)v,at))if(d instanceof DefinitionStmt)r.addAll(allocationTypes(((DefinitionStmt)d).getRightOp(),d,f,new HashSet<>(seen)));
    }return r;
  }
  void validateDeployment(){
    SootMethod h=method(text("handler"));
    SootClass owner=Scene.v().forceResolve(text("rpc_owner"),SootClass.SIGNATURES);
    if(text("protocol").equals("dubbo")){
      boolean impl=false;
      for(SootClass c=h.getDeclaringClass();c!=null;){
        for(SootClass i:c.getInterfaces())if(i.getName().equals(owner.getName()))impl=true;
        c=c.hasSuperclass()?c.getSuperclass():null;
      }
      if(!impl)throw new Incomplete("Handler does not implement configured RPC protocol "+owner);
    }
    if(!cfg.has("registration_class"))throw new Incomplete("Deployment registration class required");
    SootClass rc=Scene.v().forceResolve(text("registration_class"),SootClass.BODIES);
    boolean registration=false,allocation=false;
    for(SootMethod m:rc.getMethods())if(m.isConcrete()){
      Frame registrationFrame=new Frame(m,"deployment","provider",new ArrayList<>());
      for(Unit u:registrationFrame.body.getUnits()){
      if(u instanceof Stmt && ((Stmt)u).containsInvokeExpr()){
        InvokeExpr e=((Stmt)u).getInvokeExpr();String n=e.getMethodRef().getName();
        if((text("protocol").equals("grpc")&&n.equals("addService")) || (text("protocol").equals("dubbo")&&(n.equals("setRef")||n.equals("ref")))){
          registration=true;
          Set<String> types=allocationTypes(e.getArg(0),u,registrationFrame,new HashSet<>());
          if(types.contains(h.getDeclaringClass().getName()))allocation=true;
          Map<String,Object> ev=new LinkedHashMap<>();ev.put("registration_method",m.getSignature());ev.put("registration_statement",u.toString());ev.put("registered_allocation_types",types);evidence.add(ev);
        }
      }
    }}
    if(!registration||!allocation)throw new Incomplete("Registration evidence missing for "+h.getDeclaringClass()+" in "+rc+" (register="+registration+", allocation="+allocation+")");
    metadata.put("deployment_validation","registration argument reaching definitions contain configured handler allocation; closed-world target selected by deployment manifest (not automatic RPCBridge discovery)");
  }
  Map<String,Object> run()throws Exception{
    G.reset();Options.v().set_prepend_classpath(true);Options.v().set_allow_phantom_refs(true);
    Options.v().set_whole_program(false);Options.v().set_keep_line_number(true);Options.v().set_keep_offset(true);
    Options.v().set_output_format(Options.output_format_none);Options.v().set_src_prec(Options.src_prec_class);
    Options.v().set_soot_classpath(text("classpath"));
    List<String> dirs=new ArrayList<>();for(JsonElement p:cfg.getAsJsonArray("process_dirs"))dirs.add(p.getAsString());Options.v().set_process_dir(dirs);
    Options.v().setPhaseOption("jb","use-original-names:true");
    Scene.v().loadNecessaryClasses();
    validateProtobuf();validateDeployment();
    SootMethod entry=method(text("entry"));Frame f=new Frame(entry,"entry","consumer",new ArrayList<>());
    Unit first=f.body.getUnits().getFirst();for(int i=0;i<entry.getParameterCount();i++){
      String p=node(f,first,"entry_param"+i,"external_parameter");f.params.add(p);
    }
    int sinks=0;
    for(Unit u:f.body.getUnits())if(u instanceof Stmt&&((Stmt)u).containsInvokeExpr()){
      InvokeExpr e=((Stmt)u).getInvokeExpr();if(e.getMethodRef().getSignature().equals("<e2e.shared.Outputs: void sink(java.lang.String,java.lang.String)>")){
        if(!(e.getArg(0) instanceof StringConstant))throw new Incomplete("Sink label must be a literal at "+f.loc(u));
        String n=node(f,u,"sink","sink"),label=((StringConstant)e.getArg(0)).value;
        nodes.get(n).put("sink_id",label);edge(val(e.getArg(1),u,f),n,"sink_argument");sinks++;
      }
    }
    if(sinks==0)throw new Incomplete("No configured sink in "+entry);
    // Include all actual source definitions and RPCs in entry, even when a
    // clean sink's query slice does not depend on them. This makes missing
    // communication coverage and mutation checks observable.
    for(Unit u:f.body.getUnits())if(u instanceof Stmt&&((Stmt)u).containsInvokeExpr()){
      InvokeExpr e=((Stmt)u).getInvokeExpr();
      if(isRpc(e) || e.getMethodRef().getSignature().equals("<e2e.shared.Inputs: java.lang.String source(java.lang.String)>")){
        if(u instanceof DefinitionStmt)definition(u,f);else if(isRpc(e))rpc(e,u,f);else val(e,u,f);
      }
    }
    if(rpcCount==0)throw new Incomplete("No actual configured RPC invocation in entry");
    metadata.put("status","complete_within_declared_scope");metadata.put("entry",entry.getSignature());metadata.put("protocol",text("protocol"));
    metadata.put("source_policy","return of e2e.shared.Inputs.source(String), source ID per real callsite and context");metadata.put("sink_policy","argument 1 of e2e.shared.Outputs.sink(String,String), literal argument 0 labels query");
    metadata.put("abstraction","finite powerset of source callsites; explicit flows; field-sensitive only for validated single-string-field protobuf types");
    metadata.put("scope","query-projected application methods; synchronous one-argument unary RPC; normal returns; no implicit/control flows, exceptions-as-values, aliasing heap, recursion, or asynchronous streaming");
    metadata.put("local_definitions","Soot SimpleLocalDefs on ExceptionalUnitGraph; each local assignment is a distinct definition; joins union reaching definitions");
    metadata.put("summaries",summaries);metadata.put("rpc_calls",rpcCount);metadata.put("sinks",sinks);metadata.put("evidence",evidence);
    metadata.put("configuration",cfg);
    Map<String,Object> graph=new LinkedHashMap<>();graph.put("schema_version","e2e-valueflow-v1");graph.put("nodes",nodes.values());graph.put("edges",edges.values());graph.put("metadata",metadata);return graph;
  }
  public static void main(String[]args)throws Exception{
    if(args.length!=2)throw new IllegalArgumentException("ValueFlowExporter config.json graph.json");
    Path output=Path.of(args[1]);Files.createDirectories(output.toAbsolutePath().getParent());
    JsonObject config=JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonObject();
    ValueFlowExporter exporter=new ValueFlowExporter(config);
    try{Map<String,Object> graph=exporter.run();Files.writeString(output,GSON.toJson(graph));
      StringBuilder dump=new StringBuilder();for(Frame f:exporter.frames.values())dump.append("// context: ").append(f.context).append("\n").append(f.body).append("\n");
      Files.writeString(output.resolveSibling(output.getFileName()+".jimple.txt"),dump);
      System.out.println("EXPORTED nodes="+exporter.nodes.size()+" edges="+exporter.edges.size()+" RPCs="+exporter.rpcCount);}
    catch(Exception ex){Map<String,Object> fail=new LinkedHashMap<>();fail.put("status","incomplete");fail.put("reason",ex.toString());fail.put("configuration",config);Files.writeString(output.resolveSibling(output.getFileName()+".failure.json"),GSON.toJson(fail));throw ex;}
  }
}
