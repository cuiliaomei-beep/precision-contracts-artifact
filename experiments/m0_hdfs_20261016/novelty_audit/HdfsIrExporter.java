import com.google.gson.*;
import soot.*;
import soot.jimple.*;
import soot.options.Options;
import soot.toolkits.graph.ExceptionalUnitGraph;
import soot.toolkits.scalar.SimpleLocalDefs;
import java.nio.file.*;
import java.util.*;

/** Actual classfile-to-structured-Jimple export, including exceptional CFG. */
public final class HdfsIrExporter {
  static Map<String,Object> map(Object...kv){Map<String,Object>m=new LinkedHashMap<>();for(int i=0;i<kv.length;i+=2)m.put(kv[i].toString(),kv[i+1]);return m;}
  static Object value(Value v){
    if(v instanceof Local)return map("op","local","name",v.toString(),"type",v.getType().toString());
    if(v instanceof NullConstant)return map("op","const","value",null);
    if(v instanceof StringConstant)return map("op","const","value",((StringConstant)v).value);
    if(v instanceof IntConstant)return map("op","const","value",((IntConstant)v).value);
    if(v instanceof LongConstant)return map("op","const","value",((LongConstant)v).value);
    if(v instanceof ClassConstant)return map("op","class_literal","name",((ClassConstant)v).getValue().replace('/','.'));
    if(v instanceof ParameterRef)return map("op","parameter","index",((ParameterRef)v).getIndex());
    if(v instanceof ThisRef)return map("op","this");
    if(v instanceof CaughtExceptionRef)return map("op","caught_exception");
    if(v instanceof CastExpr)return map("op","cast","type",v.getType().toString(),"value",value(((CastExpr)v).getOp()));
    if(v instanceof NewExpr)return map("op","new","type",v.getType().toString());
    if(v instanceof BinopExpr){BinopExpr b=(BinopExpr)v;return map("op","binary","operator",b.getSymbol().trim(),"left",value(b.getOp1()),"right",value(b.getOp2()));}
    if(v instanceof InstanceFieldRef){InstanceFieldRef f=(InstanceFieldRef)v;return map("op","field","base",value(f.getBase()),"signature",f.getFieldRef().getSignature());}
    if(v instanceof StaticFieldRef)return map("op","static_field","signature",((StaticFieldRef)v).getFieldRef().getSignature());
    if(v instanceof InvokeExpr){InvokeExpr e=(InvokeExpr)v;List<Object>a=new ArrayList<>();for(Value x:e.getArgs())a.add(value(x));return map("op","invoke","signature",e.getMethodRef().getSignature(),"base",e instanceof InstanceInvokeExpr?value(((InstanceInvokeExpr)e).getBase()):null,"args",a);}
    if(v instanceof LengthExpr)return map("op","length","value",value(((LengthExpr)v).getOp()));
    if(v instanceof ArrayRef){ArrayRef a=(ArrayRef)v;return map("op","array_read","base",value(a.getBase()),"index",value(a.getIndex()));}
    if(v instanceof NewArrayExpr){NewArrayExpr a=(NewArrayExpr)v;return map("op","new_array","type",a.getBaseType().toString(),"size",value(a.getSize()));}
    return map("op","unsupported","java_type",v.getClass().getName(),"text",v.toString());
  }
  static Object method(SootMethod m)throws Exception{
    if(!m.isConcrete()||m.isPhantom())throw new IllegalStateException("No concrete body "+m);
    Body b=m.retrieveActiveBody();ExceptionalUnitGraph cfg=new ExceptionalUnitGraph(b);SimpleLocalDefs defs=new SimpleLocalDefs(cfg);
    IdentityHashMap<Unit,Integer>ids=new IdentityHashMap<>();int n=0;for(Unit u:b.getUnits())ids.put(u,n++);
    List<Object>units=new ArrayList<>(),traps=new ArrayList<>();
    for(Unit u:b.getUnits()){
      Map<String,Object>x=map("id",ids.get(u),"text",u.toString(),"line",u.getJavaSourceStartLineNumber());
      List<Integer>successors=new ArrayList<>(),exceptional=new ArrayList<>();
      for(Unit t:cfg.getUnexceptionalSuccsOf(u))successors.add(ids.get(t));
      for(Unit t:cfg.getExceptionalSuccsOf(u))exceptional.add(ids.get(t));
      x.put("successors",successors);x.put("exceptional_successors",exceptional);
      if(u instanceof IdentityStmt){IdentityStmt s=(IdentityStmt)u;x.putAll(map("kind","identity","left",value(s.getLeftOp()),"right",value(s.getRightOp())));}
      else if(u instanceof AssignStmt){AssignStmt s=(AssignStmt)u;x.putAll(map("kind","assign","left",value(s.getLeftOp()),"right",value(s.getRightOp())));}
      else if(u instanceof IfStmt){IfStmt s=(IfStmt)u;x.putAll(map("kind","if","condition",value(s.getCondition()),"target",ids.get(s.getTarget())));}
      else if(u instanceof GotoStmt)x.putAll(map("kind","goto","target",ids.get(((GotoStmt)u).getTarget())));
      else if(u instanceof InvokeStmt)x.putAll(map("kind","invoke","value",value(((InvokeStmt)u).getInvokeExpr())));
      else if(u instanceof ReturnStmt)x.putAll(map("kind","return","value",value(((ReturnStmt)u).getOp())));
      else if(u instanceof ReturnVoidStmt)x.put("kind","return_void");
      else if(u instanceof ThrowStmt)x.putAll(map("kind","throw","value",value(((ThrowStmt)u).getOp())));
      else if(u instanceof NopStmt)x.put("kind","nop");
      else x.putAll(map("kind","unsupported","java_type",u.getClass().getName()));
      List<Object>uses=new ArrayList<>();for(ValueBox box:u.getUseBoxes())if(box.getValue() instanceof Local){List<Integer>ds=new ArrayList<>();for(Unit d:defs.getDefsOfAt((Local)box.getValue(),u))ds.add(ids.get(d));uses.add(map("local",box.getValue().toString(),"definitions",ds));}
      x.put("reaching_local_definitions",uses);units.add(x);
    }
    for(Trap t:b.getTraps())traps.add(map("begin",ids.get(t.getBeginUnit()),"end_exclusive",ids.get(t.getEndUnit()),"handler",ids.get(t.getHandlerUnit()),"exception",t.getException().getName()));
    List<String>declared=new ArrayList<>();for(SootClass e:m.getExceptions())declared.add(e.getName());
    return map("signature",m.getSignature(),"source_file",m.getDeclaringClass().getName().replace('.','/')+".java","phantom",m.isPhantom(),"declared_exceptions",declared,"units",units,"traps",traps,"jimple",b.toString());
  }
  public static void main(String[]args)throws Exception{
    JsonObject config=JsonParser.parseString(Files.readString(Path.of(args[0]))).getAsJsonObject();
    G.reset();Options.v().set_prepend_classpath(true);Options.v().set_allow_phantom_refs(true);Options.v().set_whole_program(false);
    Options.v().set_keep_line_number(true);Options.v().set_output_format(Options.output_format_none);Options.v().set_soot_classpath(config.get("classpath").getAsString());
    Scene.v().loadNecessaryClasses();List<Object>methods=new ArrayList<>();
    for(JsonElement e:config.getAsJsonArray("methods")){
      String signature=e.getAsString(),cls=signature.substring(1,signature.indexOf(':'));
      Scene.v().forceResolve(cls,SootClass.BODIES);methods.add(method(Scene.v().getMethod(signature)));
    }
    Map<String,Object>out=map("schema","hdfs-structured-jimple-v1","provenance","actual Hadoop3.3.6 classfiles through Soot4.5.0; no business Outcome facts supplied","methods",methods,"config",config);
    Files.writeString(Path.of(args[1]),new GsonBuilder().serializeNulls().setPrettyPrinting().disableHtmlEscaping().create().toJson(out));
    System.out.println("EXPORTED_METHODS="+methods.size());
  }
}
