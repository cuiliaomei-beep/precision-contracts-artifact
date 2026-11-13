package e2e.grpc;
import io.grpc.examples.helloworld.GreeterGrpc;
import io.grpc.examples.helloworld.HelloRequest;
import io.grpc.examples.helloworld.HelloReply;
import io.grpc.stub.StreamObserver;
/** Clearly marked derived service: ignores request, returns a fixed response. */
public final class ConstantGreeter extends GreeterGrpc.GreeterImplBase {
  @Override public void sayHello(HelloRequest req, StreamObserver<HelloReply> observer) {
    HelloReply reply = HelloReply.newBuilder().setMessage("fixed-response").build();
    observer.onNext(reply);
    observer.onCompleted();
  }
}
