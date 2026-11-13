package e2e.hdfs;

import com.google.gson.GsonBuilder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;
import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.hdfs.HdfsConfiguration;
import org.apache.hadoop.hdfs.MiniDFSCluster;

/** Real HDFS MiniDFSCluster server. All test requests run in a different JVM. */
public final class HdfsServer {
    public static void main(String[] args) throws Exception {
        Path state = Path.of(args[0]).toAbsolutePath();
        Path ready = Path.of(args[1]).toAbsolutePath();
        Files.createDirectories(state);
        System.setProperty(MiniDFSCluster.PROP_TEST_BUILD_DATA, state.toString());
        Configuration conf = new HdfsConfiguration();
        conf.set(MiniDFSCluster.HDFS_MINIDFS_BASEDIR, state.toString());
        conf.set("hadoop.tmp.dir", state.resolve("tmp").toString());
        conf.setBoolean("dfs.permissions.enabled", false);
        conf.setLong("dfs.namenode.resource.du.reserved", 0);
        conf.setLong("dfs.datanode.du.reserved", 0);
        conf.setInt("dfs.replication", 1);
        conf.setInt("dfs.namenode.handler.count", 2);
        conf.setInt("dfs.datanode.handler.count", 2);
        conf.setInt("dfs.datanode.max.transfer.threads", 8);
        conf.setInt("dfs.heartbeat.interval", 1);
        conf.set("dfs.namenode.http-address", "127.0.0.1:0");
        MiniDFSCluster cluster = null;
        try {
            cluster = new MiniDFSCluster.Builder(conf).numDataNodes(1).format(true).build();
            cluster.waitActive();
            Map<String,Object> status = new LinkedHashMap<>();
            status.put("pid", ProcessHandle.current().pid());
            status.put("uri", cluster.getURI().toString());
            status.put("rpc_address", cluster.getNameNode().getNameNodeAddress().toString());
            status.put("name_node_class", cluster.getNameNode().getClass().getName());
            status.put("rpc_server_class", cluster.getNameNode().getRpcServer().getClass().getName());
            status.put("fsnamesystem_code_source", org.apache.hadoop.hdfs.server.namenode.FSNamesystem.class.getProtectionDomain().getCodeSource().getLocation().toString());
            status.put("hadoop_version", org.apache.hadoop.util.VersionInfo.getVersion());
            status.put("hadoop_revision", org.apache.hadoop.util.VersionInfo.getRevision());
            Files.writeString(ready, new GsonBuilder().setPrettyPrinting().create().toJson(status));
            System.out.println("E2E_HDFS_READY " + status);
            System.out.flush();
            System.in.read();
        } finally {
            if (cluster != null) cluster.shutdown(true);
        }
    }
}
