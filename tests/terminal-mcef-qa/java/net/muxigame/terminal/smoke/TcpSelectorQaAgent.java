package net.muxigame.terminal.smoke;

/** QA JVM ONLY: Windows AF_UNIX support is advertised but connect fails in this execution host. */
public final class TcpSelectorQaAgent {
    public static void premain(String arguments,java.lang.instrument.Instrumentation instrumentation)throws Exception{
        var member=sun.misc.Unsafe.class.getDeclaredField("theUnsafe");member.setAccessible(true);var unsafe=(sun.misc.Unsafe)member.get(null);
        var type=Class.forName("sun.nio.ch.UnixDomainSockets");var supported=type.getDeclaredField("supported");
        unsafe.putBoolean(unsafe.staticFieldBase(supported),unsafe.staticFieldOffset(supported),false);
        try(var selector=java.nio.channels.Selector.open()){}
        System.out.println("QA only: TCP selector wakeup enabled; no production configuration changed");
    }
}
