package e2e.grpc;
import e2e.shared.Inputs;
import e2e.shared.Outputs;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.examples.helloworld.GreeterGrpc;
import io.grpc.examples.helloworld.HelloRequest;
import io.grpc.examples.helloworld.HelloReply;
import java.util.concurrent.TimeUnit;
public final class TwoCallsClient {
  public static void main(String[] args) throws Exception {
    ManagedChannel channel = ManagedChannelBuilder.forAddress("127.0.0.1", Integer.parseInt(args[0]))
        .usePlaintext().build();
    try {
      GreeterGrpc.GreeterBlockingStub stub = GreeterGrpc.newBlockingStub(channel);
      String input = Inputs.source(args[1]);
      HelloRequest request = HelloRequest.newBuilder().setName(input).build();
      HelloReply response = stub.sayHello(request);
      Outputs.sink("first", response.getMessage());
      HelloRequest cleanRequest = HelloRequest.newBuilder().setName("clean-name").build();
      HelloReply cleanResponse = stub.sayHello(cleanRequest);
      Outputs.sink("second", cleanResponse.getMessage());
    } finally {
      channel.shutdownNow().awaitTermination(5, TimeUnit.SECONDS);
    }
  }
}
