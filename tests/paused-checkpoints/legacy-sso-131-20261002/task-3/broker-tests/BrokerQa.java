import net.muxigame.core.client.TerminalCredentialBrokerClient;
import java.util.concurrent.TimeUnit;

public final class BrokerQa {
    public static void main(String[] args) throws Exception {
        String actual=TerminalCredentialBrokerClient.fetch(System.getenv("QA_PIPE"),System.getenv("QA_CAPABILITY")).get(9,TimeUnit.SECONDS);
        if(!actual.equals(System.getenv("QA_EXPECTED")))throw new AssertionError("Native pipe outcome differs; credential values redacted");
        System.out.println("native pipe outcome verified; values redacted");
    }
}
