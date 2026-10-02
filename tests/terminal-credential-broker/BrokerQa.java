import net.muxigame.core.client.TerminalCredentialBrokerClient;
import java.util.concurrent.TimeUnit;

public final class BrokerQa {
    public static void main(String[] args) throws Exception {
        if("parallel".equals(System.getenv("QA_MODE"))){
            var first=TerminalCredentialBrokerClient.fetch(System.getenv("QA_PIPE"),System.getenv("QA_CAPABILITY"));
            var second=TerminalCredentialBrokerClient.fetch(System.getenv("QA_PIPE"),System.getenv("QA_CAPABILITY"));
            String a=first.get(9,TimeUnit.SECONDS),b=second.get(9,TimeUnit.SECONDS);
            if(!a.matches("[A-Za-z0-9_-]{43}") || !b.matches("[A-Za-z0-9_-]{43}") || a.equals(b))throw new AssertionError("Concurrent native credentials invalid; values redacted");
            System.out.println("concurrent renewal verified; values redacted");return;
        }
        String actual=TerminalCredentialBrokerClient.fetch(System.getenv("QA_PIPE"),System.getenv("QA_CAPABILITY")).get(9,TimeUnit.SECONDS);
        if(!actual.equals(System.getenv("QA_EXPECTED")))throw new AssertionError("Native pipe outcome differs; credential values redacted");
        System.out.println("native pipe outcome verified; values redacted");
    }
}
