package io.grpc.examples.helloworld;
import io.grpc.Grpc;
import io.grpc.InsecureServerCredentials;
import io.grpc.Server;
public final class OfficialServerHarness {
  public static void main(String[] args) throws Exception {
    final Server server = Grpc.newServerBuilderForPort(Integer.parseInt(args[0]),
        InsecureServerCredentials.create())
        .addService(new HelloWorldServer.GreeterImpl()).build().start();
    Runtime.getRuntime().addShutdownHook(new Thread(() -> server.shutdownNow()));
    System.out.println("READY\t" + server.getPort());
    System.out.flush();
    server.awaitTermination();
  }
}
