package e2e.hdfs;

import com.google.gson.GsonBuilder;
import java.io.OutputStream;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.hadoop.conf.Configuration;
import org.apache.hadoop.hdfs.DFSClient;
import org.apache.hadoop.hdfs.HdfsConfiguration;
import org.apache.hadoop.hdfs.protocol.ClientProtocol;
import org.apache.hadoop.ipc.RemoteException;

/** Typed requests through actual DFSClient NameNode IPC/PB proxies, never mocks. */
public final class HdfsClient {
    static Map<String,Object> invoke(String id, ClientProtocol proxy, String path,
                                    long fileId, String holder, String expected) {
        Map<String,Object> r = new LinkedHashMap<>();
        r.put("id", id); r.put("path", path); r.put("file_id", fileId);
        r.put("holder", holder); r.put("expected", expected);
        try {
            proxy.fsync(path, fileId, holder, 0L);
            r.put("outcome", "normal");
        } catch (Exception error) {
            String kind = error instanceof RemoteException ? ((RemoteException)error).getClassName() : error.getClass().getName();
            r.put("outcome", kind);r.put("java_exception", error.getClass().getName());
            r.put("exception_message", error.getMessage());
        }
        r.put("pass", expected.equals(r.get("outcome")));
        return r;
    }

    public static void main(String[] args) throws Exception {
        Configuration conf = new HdfsConfiguration();
        conf.set("fs.defaultFS", args[0]);
        conf.setInt("ipc.client.connect.max.retries", 0);
        conf.setInt("ipc.client.connect.max.retries.on.timeouts", 0);
        conf.setInt("ipc.client.connect.timeout", 5000);
        boolean mutant = Boolean.parseBoolean(args[2]);
        String leaseError = "org.apache.hadoop.hdfs.server.namenode.LeaseExpiredException";
        Map<String,Object> report = new LinkedHashMap<>();
        report.put("pid", ProcessHandle.current().pid());report.put("uri", args[0]);report.put("mutant", mutant);
        List<Map<String,Object>> cases = new ArrayList<>();
        try (DFSClient a = new DFSClient(URI.create(args[0]), conf);
             DFSClient b = new DFSClient(URI.create(args[0]), conf)) {
            String path1 = "/m0-file-a", path2 = "/m0-file-b";
            long id1;
            String holderA = a.getClientName(), holderB = b.getClientName();
            try (OutputStream openedA = a.create(path1, false);
                 OutputStream openedB = b.create(path2, false)) {
                id1 = a.getFileInfo(path1).getFileId();
                long id2 = b.getFileInfo(path2).getFileId();
                if (id1 == id2 || holderA.equals(holderB)) throw new AssertionError("Fixture requires distinct inode/lease identities");
                Map<String,Object> fixture = new LinkedHashMap<>();
                fixture.put("file_a", path1);fixture.put("file_id_a", id1);fixture.put("owner_a", holderA);
                fixture.put("file_b", path2);fixture.put("file_id_b", id2);fixture.put("owner_b", holderB);
                fixture.put("both_files_open_under_construction", true);
                fixture.put("proxy_class_a", a.getNamenode().getClass().getName());
                fixture.put("proxy_class_b", b.getNamenode().getClass().getName());report.put("fixture", fixture);
                cases.add(invoke("matching_holder_a", a.getNamenode(), path1, id1, holderA, "normal"));
                cases.add(invoke("matching_holder_b", b.getNamenode(), path2, id2, holderB, "normal"));
                cases.add(invoke("cross_holder", b.getNamenode(), path1, id1, holderB, mutant ? "normal" : leaseError));
                cases.add(invoke("cross_valid_file_id_matching_second_holder", b.getNamenode(), path1, id2, holderB, "normal"));
                cases.add(invoke("cross_valid_file_id_wrong_holder", a.getNamenode(), path1, id2, holderA, mutant ? "normal" : leaseError));
                cases.add(invoke("unknown_file_id", a.getNamenode(), path1, Long.MAX_VALUE - 17L, holderA, "java.io.FileNotFoundException"));
            }
            cases.add(invoke("closed_file_rejected", a.getNamenode(), path1, id1, holderA, leaseError));
            long directoryId = a.getFileInfo("/").getFileId();
            Map<String,Object> directoryCase = invoke("directory_rejected", a.getNamenode(), "/", directoryId, holderA, leaseError);
            directoryCase.put("inode_kind", "directory");cases.add(directoryCase);
        }
        report.put("cases", cases);
        boolean passed = cases.size() == 8 && cases.stream().allMatch(r -> Boolean.TRUE.equals(r.get("pass")));
        report.put("all_pass", passed);
        Files.writeString(Path.of(args[1]), new GsonBuilder().serializeNulls().setPrettyPrinting().create().toJson(report));
        System.out.println("E2E_HDFS_RESULT " + args[1] + " all_pass=" + passed);
        if (!passed) System.exit(1);
    }
}
