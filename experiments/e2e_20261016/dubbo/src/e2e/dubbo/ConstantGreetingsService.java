package e2e.dubbo;
import org.apache.dubbo.samples.api.GreetingsService;
/** Derived negative test, not the original service implementation. */
public final class ConstantGreetingsService implements GreetingsService {
    @Override public String sayHi(String name) { return "fixed-reply"; }
}
