package net.splitchain.wallet;

import android.content.Context;
import org.json.JSONObject;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

final class Network {
    static final String BASE = "https://bokiloki.ddns.net/splitchain/";
    static final String RPC = "wss://bokiloki.ddns.net/splitchain/rpc";
    private final OkHttpClient client = new OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build();
    private final Context context;
    Network(Context context) { this.context = context.getApplicationContext(); }

    private JSONObject fetch(String url) throws Exception {
        try (Response response = client.newCall(new Request.Builder().url(url).build()).execute()) {
            if (!response.isSuccessful() || response.body() == null || !response.request().url().toString().equals(url))
                throw new IllegalStateException("Network request failed: " + url);
            String body = response.body().string();
            if (body.length() > 65536) throw new IllegalStateException("Network document too large");
            return new JSONObject(body);
        }
    }

    // Compare complete manifest and pinned genesis; no network supplied RPC URL is trusted.
    void verify() throws Exception {
        byte[] bytes;
        try (InputStream in = context.getAssets().open("testnet-genesis.json")) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] part = new byte[4096];
            int n;
            while ((n = in.read(part)) != -1) out.write(part, 0, n);
            bytes = out.toByteArray();
        }
        JSONObject pinned = new JSONObject(new String(bytes, StandardCharsets.UTF_8));
        String digest = Protocol.genesisDigest(pinned);
        JSONObject expected = new JSONObject().put("schema", "splitchain-bootstrap/v1")
                .put("network_id", pinned.getString("network_id")).put("genesis_digest", digest)
                .put("manifest_url", BASE + ".well-known/splitchain-testnet.json")
                .put("genesis_url", BASE + "genesis.json")
                .put("status_url", BASE + "status").put("rpc_url", RPC)
                .put("units", "valueless-testnet");
        JSONObject manifest = fetch(BASE + ".well-known/splitchain-testnet.json");
        JSONObject genesis = fetch(BASE + "genesis.json");
        if (!Protocol.canonical(expected).equals(Protocol.canonical(manifest)) ||
                !Protocol.canonical(pinned).equals(Protocol.canonical(genesis)))
            throw new IllegalStateException("Published testnet differs from the wallet's pinned genesis");
    }

    JSONObject status() throws Exception { return fetch(BASE + "status").getJSONObject("result"); }

    interface Result { void complete(JSONObject response, Exception error); }
    void submit(JSONObject request, Result callback) {
        client.newWebSocket(new Request.Builder().url(RPC).build(), new WebSocketListener() {
            private boolean completed;
            private synchronized void done(JSONObject value, Exception error, WebSocket socket) {
                if (completed) return;
                completed = true;
                socket.close(1000, "done");
                callback.complete(value, error);
            }
            @Override public void onOpen(WebSocket socket, Response response) {
                if (!socket.send(request.toString())) done(null, new IllegalStateException("send failed"), socket);
            }
            @Override public void onMessage(WebSocket socket, String text) {
                try { done(new JSONObject(text), null, socket); }
                catch (Exception error) { done(null, error, socket); }
            }
            @Override public void onFailure(WebSocket socket, Throwable error, Response response) {
                done(null, new Exception("RPC unavailable: " + error.getMessage(), error), socket);
            }
        });
    }
}
