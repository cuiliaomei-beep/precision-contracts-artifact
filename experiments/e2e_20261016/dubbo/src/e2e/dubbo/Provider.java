package e2e.dubbo;
import org.apache.dubbo.config.ApplicationConfig;
import org.apache.dubbo.config.ProtocolConfig;
import org.apache.dubbo.config.RegistryConfig;
import org.apache.dubbo.config.ServiceConfig;
import org.apache.dubbo.config.bootstrap.DubboBootstrap;
import org.apache.dubbo.samples.api.GreetingsService;
import org.apache.dubbo.samples.provider.GreetingsServiceImpl;
public final class Provider {
    public static void main(String[] args) throws Exception {
        GreetingsService implementation = args[0].equals("constant")
            ? new ConstantGreetingsService() : new GreetingsServiceImpl();
        ServiceConfig<GreetingsService> service = new ServiceConfig<>();
        service.setInterface(GreetingsService.class);
        service.setRef(implementation);
        service.setRegistry(new RegistryConfig("N/A"));
        ProtocolConfig protocol = new ProtocolConfig("tri", Integer.parseInt(args[1]));
        protocol.setHost("127.0.0.1");
        DubboBootstrap bootstrap = DubboBootstrap.getInstance()
            .application(new ApplicationConfig("e2e-dubbo-provider"))
            .protocol(protocol).service(service).start();
        System.out.println("E2E_PROVIDER_READY\t" + ProcessHandle.current().pid());
        System.out.flush();
        try { System.in.read(); } finally { bootstrap.destroy(); }
    }
}
