package net.muxigame.terminal.smoke;
/** TEST MOD ONLY. Rewrite only canonical issuer requests to this run's pinned TLS service. */
public final class NativeIssuerEndpoint {
 public static java.net.URI rewrite(java.net.URI original){
  String target=System.getProperty("muxi.sso.nativeAuthURL");
  if(target==null || !target.matches("https://(?:localhost|127\\.0\\.0\\.1):[0-9]{1,5}") || !"https".equals(original.getScheme()) || !"account.muxigame.com".equals(original.getHost()) || original.getUserInfo()!=null || original.getPort()!=-1 || !(original.getPath().startsWith("/api/") || original.getPath().equals("/oauth/userinfo")))throw new IllegalStateException("Unexpected isolated native transport");
  return java.net.URI.create(target).resolve(original.getRawPath());
 }
}
