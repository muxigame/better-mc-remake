package net.muxigame.terminal.smoke;

import com.cinemamod.mcef.MCEFBrowser;
import com.google.gson.*;
import java.net.URI;
import java.net.http.*;
import java.time.Duration;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.client.multiplayer.ClientPacketListener;
import org.cef.CefClient;
import org.cef.browser.*;
import org.cef.callback.*;
import org.cef.handler.*;
import org.cef.misc.*;
import org.cef.network.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.function.Consumer;

/** TEST MOD ONLY. Synthetic identities with actual isolated Auth/Core/Web TLS; no production configuration. */
public final class SSOFixture {
    public static final String ENTRY="https://mc.muxigame.com/api/v1/auth/terminal";
    public static final String POST=ENTRY+"/exchange",ACCOUNT="https://mc.muxigame.com/account.html";
    public static final String BODY="{\"ticket\":\"synthetic-one-use-fixture\",\"verifier\":\"synthetic-native-fixture\",\"requestId\":\"fixture\"}";
    public static volatile Consumer<String> pending;
    public static volatile int requestCalls;
    public static volatile int posts,requests;
    public static volatile String postedBody="",postedMethod="",postedOrigin="",postedAction="";
    public static volatile boolean holdEntry;
    private static final HttpClient HTTP=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(3)).build();
    public static JsonObject control(String action,JsonObject value){
        try{
            value.addProperty("action",action);
            if(action.equals("ticket") || action.equals("validate"))value.addProperty("gameUid",Minecraft.getInstance().getUser().getName());
            var request=HttpRequest.newBuilder(URI.create(System.getProperty("muxi.container.fixtureUrl")+"/backend"))
                .timeout(Duration.ofSeconds(20)).header("Content-Type","application/json").header("X-Muxi-QA-Native",System.getenv("MUXI_SSO_QA_CONTROL"))
                .POST(HttpRequest.BodyPublishers.ofString(value.toString(),StandardCharsets.UTF_8)).build();
            var response=HTTP.send(request,HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            if(response.statusCode()!=200)throw new IllegalStateException("isolated native QA control failed");
            return JsonParser.parseString(response.body()).getAsJsonObject();
        }catch(Exception error){throw new IllegalStateException("isolated native QA control failed: "+error.getClass().getSimpleName());}
    }
    public static void validate(Consumer<Boolean> callback){callback.accept(control("validate",new JsonObject()).get("valid").getAsBoolean());}
    private static LocalPlayer identity;
    private static final ThreadLocal<Deque<LocalPlayer>> saved=ThreadLocal.withInitial(LinkedList::new);
    private SSOFixture() {}
    public static void enter(){
        if(!Minecraft.getInstance().isSameThread())throw new AssertionError("SSO identity fixture must run on game thread");
        try{
            if(identity==null){
                var field=sun.misc.Unsafe.class.getDeclaredField("theUnsafe");field.setAccessible(true);var unsafe=(sun.misc.Unsafe)field.get(null);
                identity=(LocalPlayer)unsafe.allocateInstance(LocalPlayer.class);
                var connection=(ClientPacketListener)unsafe.allocateInstance(ClientPacketListener.class);
                var member=LocalPlayer.class.getField("connection");member.setAccessible(true);member.set(identity,connection);
            }
            saved.get().push(Minecraft.getInstance().player);Minecraft.getInstance().player=identity;
        }catch(Exception e){throw new RuntimeException(e);}
    }
    public static void leave(){Minecraft.getInstance().player=saved.get().pop();}
    public static Runnable wrap(Runnable original){return ()->{enter();try{original.run();}finally{leave();}};}
    public static void deliver(){var callback=pending;pending=null;if(callback==null)throw new AssertionError("No synthetic ticket request");callback.accept(Boolean.getBoolean("muxi.sso.liveBackend")?control("ticket",new JsonObject()).get("payload").getAsString():BODY);}
    public static void install(MCEFBrowser browser){
        try{
            CefClient client=browser.getClient();
            var load=CefClient.class.getDeclaredField("loadHandler_");load.setAccessible(true);
            if(load.get(client) instanceof com.cinemamod.mcef.MCEFClient owner)owner.addLoadHandler(new CefLoadHandlerAdapter(){
                @Override public void onLoadingStateChange(CefBrowser b,boolean loading,boolean back,boolean forward){System.out.println("QA_ACCOUNT_LOADING="+loading);}
                @Override public void onLoadEnd(CefBrowser b,CefFrame f,int status){System.out.println("QA_ACCOUNT_END_MAIN="+f.isMain()+" STATUS="+status+" ENTRY="+ENTRY.equals(f.getURL())+" ACCOUNT="+ACCOUNT.equals(f.getURL()));}
                @Override public void onLoadError(CefBrowser b,CefFrame f,ErrorCode code,String message,String url){System.out.println("QA_ACCOUNT_ERROR_MAIN="+f.isMain()+" CODE="+code);}
            });
            var field=CefClient.class.getDeclaredField("requestHandler_");field.setAccessible(true);
            var delegate=(CefRequestHandler)field.get(client);
            client.removeRequestHandler();client.addRequestHandler(new CefRequestHandlerAdapter(){
                @Override public boolean onBeforeBrowse(CefBrowser b,CefFrame f,CefRequest r,boolean user,boolean redirect){return delegate.onBeforeBrowse(b,f,r,user,redirect);}
                @Override public boolean onOpenURLFromTab(CefBrowser b,CefFrame f,String url,boolean gesture){return delegate.onOpenURLFromTab(b,f,url,gesture);}
                @Override public boolean onCertificateError(CefBrowser b,CefLoadHandler.ErrorCode code,String url,CefCallback callback){
                    if(Boolean.getBoolean("muxi.sso.directTls") && code==CefLoadHandler.ErrorCode.ERR_CERT_AUTHORITY_INVALID && url.startsWith("https://mc.muxigame.com/") && pinnedFixture()){
                        callback.Continue();System.out.println("QA_EXACT_LOOPBACK_CERT_PIN_VERIFIED=true");return true;
                    }
                    return delegate.onCertificateError(b,code,url,callback);
                }
                @Override public CefResourceRequestHandler getResourceRequestHandler(CefBrowser b,CefFrame f,CefRequest r,boolean nav,boolean download,String initiator,BoolRef disable){
                    var original=delegate.getResourceRequestHandler(b,f,r,nav,download,initiator,disable);
                    return new CefResourceRequestHandlerAdapter(){
                        @Override public boolean onBeforeResourceLoad(CefBrowser b,CefFrame f,CefRequest r){if(Boolean.getBoolean("muxi.sso.directTls")){observe(r);if(holdEntry && ENTRY.equals(r.getURL()))try{Thread.sleep(1000);}catch(InterruptedException ignored){}}return original.onBeforeResourceLoad(b,f,r);}
                        @Override public CefCookieAccessFilter getCookieAccessFilter(CefBrowser b,CefFrame f,CefRequest r){return original.getCookieAccessFilter(b,f,r);}
                        @Override public void onProtocolExecution(CefBrowser b,CefFrame f,CefRequest r,BoolRef allow){original.onProtocolExecution(b,f,r,allow);}
                        @Override public CefResourceHandler getResourceHandler(CefBrowser b,CefFrame f,CefRequest r){return Boolean.getBoolean("muxi.sso.directTls")?null:response(r);}
                    };
                }
            });
        }catch(Exception e){throw new RuntimeException(e);}
    }
    // TEST MOD ONLY: no OS trust change. CEF's private context ignores the SPKI switch;
    // authorize only this process's mapped loopback certificate after a real TLS handshake.
    private static boolean pinnedFixture(){
        try{
            byte[] expected=Base64.getDecoder().decode(System.getProperty("muxi.sso.spki"));
            var trust=new javax.net.ssl.X509TrustManager(){
                public java.security.cert.X509Certificate[] getAcceptedIssuers(){return new java.security.cert.X509Certificate[0];}
                public void checkClientTrusted(java.security.cert.X509Certificate[] chain,String type)throws java.security.cert.CertificateException{throw new java.security.cert.CertificateException();}
                public void checkServerTrusted(java.security.cert.X509Certificate[] chain,String type)throws java.security.cert.CertificateException{
                    try{chain[0].checkValidity();byte[] actual=java.security.MessageDigest.getInstance("SHA-256").digest(chain[0].getPublicKey().getEncoded());
                        if(!java.security.MessageDigest.isEqual(expected,actual))throw new java.security.cert.CertificateException();
                    }catch(java.security.NoSuchAlgorithmException error){throw new java.security.cert.CertificateException(error);}
                }
            };
            var context=javax.net.ssl.SSLContext.getInstance("TLS");context.init(null,new javax.net.ssl.TrustManager[]{trust},null);
            try(var socket=(javax.net.ssl.SSLSocket)context.getSocketFactory().createSocket()){
                socket.connect(new java.net.InetSocketAddress("127.0.0.1",Integer.parseInt(System.getProperty("muxi.sso.sitePort"))),2000);socket.setSoTimeout(2000);socket.startHandshake();
            }
            return true;
        }catch(Exception ignored){return false;}
    }
    private static void observe(CefRequest request){
        requests++;String url=request.getURL();
        if(Boolean.getBoolean("muxi.sso.realNative") && url.startsWith(ACCOUNT+"?terminal_view=")){
            posts++; // Count actual account GET openings, never an exchange POST.
            if(!"GET".equals(request.getMethod()))throw new AssertionError("Native account view must use GET");
        }
        if(POST.equals(url)){
            posts++;postedMethod=request.getMethod();postedOrigin=request.getHeaderByName("Origin");postedAction=request.getHeaderByName("X-Muxi-Terminal-Action");
            var elements=new Vector<CefPostDataElement>();if(request.getPostData()!=null)request.getPostData().getElements(elements);
            var output=new java.io.ByteArrayOutputStream();for(var element:elements){byte[] bytes=new byte[element.getBytesCount()];element.getBytes(bytes.length,bytes);output.writeBytes(bytes);}
            postedBody=output.toString(StandardCharsets.UTF_8);
        }
    }
    private static CefResourceHandler response(CefRequest request){
        observe(request);String url=request.getURL();
        if(Boolean.getBoolean("muxi.sso.liveBackend")){
            JsonObject input=new JsonObject();input.addProperty("url",url);input.addProperty("method",request.getMethod());
            var headers=new HashMap<String,String>();request.getHeaderMap(headers);
            input.add("headers",new Gson().toJsonTree(headers));
            var data=new java.io.ByteArrayOutputStream();var elements=new Vector<CefPostDataElement>();
            if(request.getPostData()!=null)request.getPostData().getElements(elements);
            for(var element:elements){byte[] bytes=new byte[element.getBytesCount()];element.getBytes(bytes.length,bytes);data.writeBytes(bytes);}
            input.addProperty("body",Base64.getEncoder().encodeToString(data.toByteArray()));
            JsonObject result=control("proxy",input);
            byte[] body=Base64.getDecoder().decode(result.get("body").getAsString());
            var responseHeaders=new HashMap<String,String>();result.getAsJsonObject("headers").entrySet().forEach(e->responseHeaders.put(e.getKey(),e.getValue().getAsString()));
            boolean entry=ENTRY.equals(url);
            return new CefResourceHandlerAdapter(){
                private int offset;
                @Override public boolean processRequest(CefRequest r,CefCallback callback){
                    if(entry && holdEntry){Thread worker=new Thread(()->{try{Thread.sleep(1000);}catch(InterruptedException ignored){}callback.Continue();});worker.setDaemon(true);worker.start();}
                    else callback.Continue();return true;
                }
                @Override public void getResponseHeaders(CefResponse response,IntRef length,StringRef redirect){
                    response.setStatus(result.get("status").getAsInt());responseHeaders.forEach((name,value)->response.setHeaderByName(name.equals("set-cookie")?"Set-Cookie":name,value,true));
                    response.setMimeType(responseHeaders.getOrDefault("content-type","application/octet-stream").split(";",2)[0]);length.set(body.length);
                    // Preserve actual HTTP 303 + Location semantics. An explicit CEF redirect override preserves POST.
                }
                @Override public boolean readResponse(byte[] buffer,int requested,IntRef read,CefCallback callback){
                    int count=Math.min(Math.min(requested,buffer.length),body.length-offset);if(count<=0){read.set(0);return false;}
                    System.arraycopy(body,offset,buffer,0,count);offset+=count;read.set(count);return true;
                }
            };
        }
        String html=ENTRY.equals(url)?"<html><body>QA entry<iframe src='"+ENTRY+"?child=1'></iframe></body></html>":
            "<html><body style='background:#d4f0d1;font:28px sans-serif;padding:30px'><h1>QA account signed in</h1><p>Synthetic single-use native POST. Persistent terminal shell.</p></body></html>";
        boolean entry=ENTRY.equals(url);boolean post=POST.equals(url);
        byte[] body=html.getBytes(StandardCharsets.UTF_8);
        return new CefResourceHandlerAdapter(){
            private int offset;
            @Override public boolean processRequest(CefRequest r,CefCallback callback){
                if(entry && holdEntry){Thread worker=new Thread(()->{try{Thread.sleep(1000);}catch(InterruptedException ignored){}callback.Continue();});worker.setDaemon(true);worker.start();}
                else callback.Continue();return true;
            }
            @Override public void getResponseHeaders(CefResponse response,IntRef length,StringRef redirect){
                response.setStatus(post?302:200);response.setMimeType("text/html");length.set(body.length);
                if(post){response.setHeaderByName("Location",ACCOUNT,true);redirect.set(ACCOUNT);}
                response.setHeaderByName("Cache-Control","no-store",true);
            }
            @Override public boolean readResponse(byte[] data,int requested,IntRef read,CefCallback callback){
                int count=Math.min(Math.min(requested,data.length),body.length-offset);if(count<=0){read.set(0);return false;}
                System.arraycopy(body,offset,data,0,count);offset+=count;read.set(count);return true;
            }
        };
    }
}
