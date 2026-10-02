package net.muxigame.terminal.smoke;
/** QA-only observational diagnostics: no request bodies, headers, credentials or identity changes. */
public final class NativeProofDiagnostics {
 private NativeProofDiagnostics(){}
 public static String errors(Throwable error){
  StringBuilder result=new StringBuilder();int count=0;
  for(Throwable e=error;e!=null&&count++<6;e=e.getCause()){if(result.length()>0)result.append(',');result.append(e.getClass().getName());}
  return result.toString();
 }
 public static synchronized void record(String phase,String values){
  String line=java.time.Instant.now()+" phase="+phase+" "+values;
  try{java.nio.file.Files.writeString(java.nio.file.Path.of("native-proof-diagnostics.log"),line+System.lineSeparator(),java.nio.charset.StandardCharsets.UTF_8,java.nio.file.StandardOpenOption.CREATE,java.nio.file.StandardOpenOption.APPEND);}catch(java.io.IOException ignored){}
  System.out.println("QA_NATIVE_PROOF "+line);
 }
}
