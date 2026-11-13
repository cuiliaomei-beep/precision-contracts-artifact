package e2e.dubbo;
import e2e.shared.Inputs;
import e2e.shared.Outputs;
import org.apache.dubbo.samples.api.GreetingsService;
public final class TwoCallsConsumer {
    public static void analyze(GreetingsService service, String raw) {
        String input = Inputs.source(raw);
        String first = service.sayHi(input);
        String second = service.sayHi("clean-literal");
        Outputs.sink("two_calls_source", first);
        Outputs.sink("two_calls_clean", second);
    }
    public static void main(String[] args) {
        System.out.println("E2E_CONSUMER_PID\t" + ProcessHandle.current().pid());
        try {
            GreetingsService service = ClientRuntime.connect(args[1]);
            analyze(service, args[0]);
        } finally { ClientRuntime.close(); }
    }
}
