import com.google.gson.*;
import soot.*;
import soot.jimple.spark.*;
import soot.jimple.spark.pag.*;
import soot.jimple.spark.sets.*;
import soot.options.Options;
import soot.util.BitVector;
import java.nio.file.*;
import java.util.*;

/** Exports a real Spark PAG and its solution. Does not infer a partition. */
public final class SparkPagExporter {
  static Map<String,Object> m(Object...kv) {
    Map<String,Object> r=new LinkedHashMap<>();
    for(int i=0;i<kv.length;i+=2)r.put(kv[i].toString(),kv[i+1]); return r;
  }
  static String id(Node n){n=n.getReplacement();return (n instanceof AllocNode?"a":n instanceof AllocDotField?"h":n instanceof FieldRefNode?"f":"v")+n.getNumber();}
  static String fieldId(SparkField f){
    if(f instanceof ArrayElement)return "<array_element>";
    if(f instanceof SootField)return ((SootField)f).getSignature();
    throw new IllegalStateException("Unsupported SparkField "+f.getClass().getName());
  }
  static List<String> pts(Node n) {
    List<String> r=new ArrayList<>(); n.getReplacement().getP2Set().forall(new P2SetVisitor(){public void visit(Node x){r.add(id(x));}});
    Collections.sort(r);return r;
  }
  static List<String> strings(JsonArray a){List<String>r=new ArrayList<>();for(JsonElement x:a)r.add(x.getAsString());return r;}
  public static void main(String[] args)throws Exception {
    long start=System.nanoTime();
    JsonObject c=JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonObject();
    G.reset();Options.v().set_prepend_classpath(false);Options.v().set_whole_program(true);
    Options.v().set_allow_phantom_refs(true);Options.v().set_output_format(Options.output_format_none);
    Options.v().set_keep_line_number(true);Options.v().set_process_dir(strings(c.getAsJsonArray("process_dirs")));
    Options.v().set_soot_classpath(c.get("classpath").getAsString());
    if(c.has("exclude")){Options.v().set_exclude(strings(c.getAsJsonArray("exclude")));Options.v().set_no_bodies_for_excluded(true);}
    Options.v().set_main_class(c.get("main_class").getAsString());
    Options.v().setPhaseOption("cg","verbose:false");
    Scene.v().loadNecessaryClasses();
    SootClass main=Scene.v().getSootClass(c.get("main_class").getAsString());
    main.setApplicationClass();Scene.v().setMainClass(main);
    SootMethod entry=main.getMethod("void main(java.lang.String[])");Scene.v().setEntryPoints(Collections.singletonList(entry));
    PackManager.v().getPack("wjpp").apply();
    Map<String,String> opts=new HashMap<>();
    opts.put("enabled","true");opts.put("on-fly-cg","false");
    opts.put("simplify-sccs","false");opts.put("simplify-offline","false");
    opts.put("field-based","false");opts.put("types-for-sites","false");
    opts.put("merge-stringbuffer","false");opts.put("simulate-natives","true");
    opts.put("propagator","worklist");opts.put("verbose","false");
    if(c.has("spark_options"))for(Map.Entry<String,JsonElement>e:c.getAsJsonObject("spark_options").entrySet())opts.put(e.getKey(),e.getValue().getAsString());
    long buildStart=System.nanoTime();SparkTransformer.v().transform("cg.spark",opts);
    long sparkEnd=System.nanoTime();
    PAG pag=(PAG)Scene.v().getPointsToAnalysis();
    if(!pag.newInstanceSources().isEmpty()||!pag.assignInstanceSources().isEmpty())
      throw new IllegalStateException("Reflective newInstance constraints unsupported by four-rule export");
    List<AllocNode>allocs=new ArrayList<>();for(AllocNode n:pag.getAllocNodeNumberer())allocs.add(n);
    List<VarNode>vars=new ArrayList<>();for(VarNode n:pag.getVarNodeNumberer())if(n.getReplacement()==n)vars.add(n);
    List<AllocDotField>heaps=new ArrayList<>();for(AllocDotField n:pag.getAllocDotFieldNodeNumberer())if(n.getReplacement()==n)heaps.add(n);
    List<Object>nodes=new ArrayList<>(),allocNodes=new ArrayList<>(),alloc=new ArrayList<>(),assign=new ArrayList<>(),store=new ArrayList<>(),load=new ArrayList<>(),heapTruth=new ArrayList<>();
    Map<String,Type>types=new TreeMap<>();Map<String,SparkField>fields=new TreeMap<>();
    Map<String,List<String>> truth=new TreeMap<>();
    for(AllocNode n:allocs)allocNodes.add(m("id",id(n),"type",n.getType().toString(),"label",n.toString(),"method",n.getMethod()==null?null:n.getMethod().getSignature()));
    for(VarNode n:vars){
      String method=n instanceof LocalVarNode&&((LocalVarNode)n).getMethod()!=null?((LocalVarNode)n).getMethod().getSignature():null;
      nodes.add(m("id",id(n),"type",n.getType()==null?null:n.getType().toString(),"label",n.toString(),"method",method));
      types.put(String.valueOf(n.getType()),n.getType());truth.put(id(n),pts(n));
    }
    for(AllocNode n:pag.allocSources())for(Node dst:pag.allocLookup(n))alloc.add(Arrays.asList(id(n),id(dst)));
    for(VarNode n:pag.simpleSources())for(Node dst:pag.simpleLookup(n))assign.add(Arrays.asList(id(n),id(dst)));
    for(VarNode n:pag.storeSources())for(Node raw:pag.storeLookup(n)){
      FieldRefNode f=(FieldRefNode)raw;String field=fieldId(f.getField());fields.put(field,f.getField());
      store.add(Arrays.asList(id(n),id(f.getBase()),field));
    }
    for(FieldRefNode f:pag.loadSources())for(Node dst:pag.loadLookup(f)){
      String field=fieldId(f.getField());fields.put(field,f.getField());load.add(Arrays.asList(id(f.getBase()),field,id(dst)));
    }
    for(AllocDotField n:heaps){
      heapTruth.add(m("id",id(n),"base",id(n.getBase()),"field",fieldId(n.getField()),"type",n.getType()==null?null:n.getType().toString(),"points_to",pts(n)));
      types.put(String.valueOf(n.getType()),n.getType());
    }
    for(SparkField f:fields.values())types.put(String.valueOf(f.getType()),f.getType());
    // Masks are exported from declared analysis types, never from solved points-to sets.
    Map<String,Object> masks=new TreeMap<>();
    for(Map.Entry<String,Type> e:types.entrySet()){
      BitVector mask=e.getValue()==null?null:pag.getTypeManager().get(e.getValue());
      List<String>allowed=new ArrayList<>();for(AllocNode a:allocs)if(mask==null||mask.get(a.getNumber()))allowed.add(id(a));
      Collections.sort(allowed);masks.put(e.getKey(),allowed);
    }
    List<Object>fieldDefs=new ArrayList<>();for(Map.Entry<String,SparkField>e:fields.entrySet())fieldDefs.add(m("id",e.getKey(),"type",String.valueOf(e.getValue().getType()),"heap_filter_type",null));
    List<String>phantoms=new ArrayList<>(),nativeMethods=new ArrayList<>();int appClasses=0,classes=0,reachable=0,units=0;
    for(SootClass sc:Scene.v().getClasses()){classes++;if(sc.isApplicationClass())appClasses++;if(sc.isPhantom())phantoms.add(sc.getName());}
    for(Iterator<MethodOrMethodContext> it=Scene.v().getReachableMethods().listener();it.hasNext();){
      SootMethod sm=it.next().method();reachable++;if(sm.isNative())nativeMethods.add(sm.getSignature());
      else if(sm.hasActiveBody())units+=sm.getActiveBody().getUnits().size();
    }
    Collections.sort(phantoms);Collections.sort(nativeMethods);
    Map<String,Object>out=m("schema","spark-pag-four-constraints-v1","config",new Gson().fromJson(c,Map.class),"spark_options",opts,
      "nodes",nodes,"allocations",allocNodes,"alloc",alloc,"assign",assign,"store",store,"load",load,
      "fields",fieldDefs,"allowed_by_type",masks,"spark_variable_points_to",truth,"spark_heap_points_to",heapTruth,
      "metadata",m("entry",entry.getSignature(),"classes",classes,"application_classes",appClasses,"reachable_methods",reachable,
       "reachable_jimple_units",units,"phantom_classes",phantoms,"reachable_native_methods",nativeMethods,
       "spark_build_and_solve_ms",(sparkEnd-buildStart)/1e6,"before_export_total_ms",(sparkEnd-start)/1e6,
       "raw_constraints_not_final_solution_expansion",true,"scope","Exact exported PAG semantics; Java whole-program soundness not claimed. Reflection defaults, native simulation and all phantom classes are explicit."));
    try(java.io.Writer writer=Files.newBufferedWriter(Path.of(args[1]))){new GsonBuilder().disableHtmlEscaping().create().toJson(out,writer);}
    System.out.println(new Gson().toJson(m("variables",vars.size(),"allocations",allocs.size(),"heap_slots_after_spark",heaps.size(),
      "alloc",alloc.size(),"assign",assign.size(),"store",store.size(),"load",load.size(),"phantom_classes",phantoms.size(),"spark_ms",(sparkEnd-buildStart)/1e6)));
  }
}
