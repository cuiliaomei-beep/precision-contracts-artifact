package e2e.dubbo;
import org.apache.dubbo.config.ApplicationConfig;
import org.apache.dubbo.config.ReferenceConfig;
import org.apache.dubbo.config.RegistryConfig;
import org.apache.dubbo.config.bootstrap.DubboBootstrap;
import org.apache.dubbo.samples.api.GreetingsService;
/** Framework lifecycle excluded from the application analysis entry body. */
public final class ClientRuntime {
    private static DubboBootstrap bootstrap;
    public static GreetingsService connect(String port) {
        ReferenceConfig<GreetingsService> reference = new ReferenceConfig<>();
        reference.setInterface(GreetingsService.class);
        reference.setUrl("tri://127.0.0.1:" + port);
        reference.setRegistry(new RegistryConfig("N/A"));
        reference.setInjvm(false);
        reference.setCheck(true);
        reference.setRetries(0);
        reference.setTimeout(3000);
        bootstrap = DubboBootstrap.getInstance()
            .application(new ApplicationConfig("e2e-dubbo-consumer"))
            .reference(reference).start();
        return reference.get();
    }
    public static void close() { if (bootstrap != null) bootstrap.destroy(); }
}
