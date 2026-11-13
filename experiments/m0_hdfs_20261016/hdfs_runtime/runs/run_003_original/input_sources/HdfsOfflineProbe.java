package e2e.hdfs;
import com.google.gson.GsonBuilder;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.hdfs.DFSClient;
import org.apache.hadoop.hdfs.HdfsConfiguration;
import org.apache.hadoop.ipc.RemoteException;

/** New client JVM performs a real typed fsync after the listener has stopped. */
public final class HdfsOfflineProbe {
    public static void main(String[] args) throws Exception {
        Configuration conf=new HdfsConfiguration();
        conf.setInt("ipc.client.connect.max.retries",0);
        conf.setInt("ipc.client.connect.max.retries.on.timeouts",0);
        conf.setInt("ipc.client.connect.timeout",5000);
        Map<String,Object> r=new LinkedHashMap<>();
        r.put("pid",ProcessHandle.current().pid());r.put("uri",args[0]);
        r.put("attempted_fsync",false);
        try (DFSClient client=new DFSClient(URI.create(args[0]),conf)) {
            r.put("proxy_class",client.getNamenode().getClass().getName());
            r.put("attempted_fsync",true);
            client.getNamenode().fsync("/m0-file-a",Long.parseLong(args[2]),args[3],0L);
            r.put("outcome","normal");r.put("pass",false);
        } catch(Exception error) {
            r.put("outcome",error.getClass().getName());r.put("message",error.getMessage());
            boolean networkFailure=false;
            for(Throwable cause=error;cause!=null;cause=cause.getCause()) {
                if(cause instanceof java.net.ConnectException || cause instanceof java.net.SocketTimeoutException
                    || cause instanceof java.net.NoRouteToHostException) networkFailure=true;
            }
            r.put("network_failure",networkFailure);r.put("remote_server_exception",error instanceof RemoteException);
            r.put("pass",Boolean.TRUE.equals(r.get("attempted_fsync")) && networkFailure && !(error instanceof RemoteException));
        }
        Files.writeString(Path.of(args[1]),new GsonBuilder().setPrettyPrinting().create().toJson(r));
        System.out.println("E2E_HDFS_OFFLINE "+r);
        if(!Boolean.TRUE.equals(r.get("pass")))System.exit(1);
    }
}
