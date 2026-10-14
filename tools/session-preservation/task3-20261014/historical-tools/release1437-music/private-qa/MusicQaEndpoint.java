package net.muxigame.musiccompat.qa;

import java.net.URI;
import java.net.http.HttpRequest;

/** Private QA mod only: only music API traffic, never JWKS/OSS/OAuth. */
public final class MusicQaEndpoint {
    private MusicQaEndpoint() {}
    public static HttpRequest rewrite(HttpRequest request) {
        URI original=request.uri();
        if (!"account.muxigame.com".equals(original.getHost())) return request;
        String path=original.getRawPath();
        boolean music=path.equals("/api/launcher/music/uploads")
            ||path.matches("/api/launcher/music/uploads/[0-9a-f-]{36}/(complete|cancel)")
            ||path.equals("/api/minecraft/music/burn-receipt/redeem")
            ||path.equals("/api/launcher/music/playback")
            ||path.equals("/api/music/capabilities");
        if (!music) return request;
        String target=System.getProperty("muxi.sso.nativeAuthURL", "");
        if (!Boolean.getBoolean("muxi.music.qa.enabled") || !Boolean.getBoolean("muxi.sso.realNative")
            ||!target.matches("https://127\\.0\\.0\\.1:[0-9]{1,5}")
            ||!"https".equals(original.getScheme())||original.getPort()!=-1
            ||original.getUserInfo()!=null||original.getRawQuery()!=null||original.getRawFragment()!=null)
            throw new IllegalStateException("isolated_music_transport_refused");
        URI endpoint=URI.create(target);
        if (endpoint.getPort()<1||endpoint.getPort()>65535) throw new IllegalStateException("isolated_music_transport_refused");
        if (path.equals("/api/music/capabilities") ? !request.method().equals("GET") : !request.method().equals("POST"))
            throw new IllegalStateException("isolated_music_method_refused");
        String cap=System.getenv("MUXI_MUSIC_QA_NETWORK_CAP");
        if(cap==null||!cap.matches("[A-Za-z0-9_-]{32,128}"))throw new IllegalStateException("qa_ephemeral_capability_required");
        return HttpRequest.newBuilder(request,(name,value)->!name.equalsIgnoreCase("X-Muxi-Music-QA")).uri(endpoint.resolve(path)).header("X-Muxi-Music-QA",cap).build();
    }
}
