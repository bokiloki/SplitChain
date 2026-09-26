package net.splitchain.wallet;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

final class Protocol {
    private Protocol() {}

    // Matches Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).
    static String canonical(Object input) throws JSONException {
        if (input instanceof JSONObject) {
            JSONObject obj = (JSONObject) input;
            List<String> keys = new ArrayList<>();
            java.util.Iterator<String> iterator = obj.keys();
            while (iterator.hasNext()) keys.add(iterator.next());
            Collections.sort(keys);
            StringBuilder out = new StringBuilder("{");
            for (String key : keys) {
                if (out.length() > 1) out.append(',');
                out.append(quote(key)).append(':').append(canonical(obj.get(key)));
            }
            return out.append('}').toString();
        }
        if (input instanceof JSONArray) {
            JSONArray array = (JSONArray) input;
            StringBuilder out = new StringBuilder("[");
            for (int i = 0; i < array.length(); i++) {
                if (i > 0) out.append(',');
                out.append(canonical(array.get(i)));
            }
            return out.append(']').toString();
        }
        if (input instanceof String) return quote((String) input);
        if (input == JSONObject.NULL) return "null";
        if (input instanceof Boolean || input instanceof Integer || input instanceof Long) return input.toString();
        throw new JSONException("unsupported canonical JSON value");
    }

    // JSONObject.quote escapes '/' on some Android versions; Python's encoder does not.
    private static String quote(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '\"': out.append("\\\""); break;
                case '\\': out.append("\\\\"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 32) out.append(String.format(java.util.Locale.ROOT, "\\u%04x", (int)c));
                    else out.append(c);
            }
        }
        return out.append('\"').toString();
    }

    static String hex(byte[] bytes) {
        char[] digits = "0123456789abcdef".toCharArray();
        char[] result = new char[bytes.length * 2];
        for (int i = 0; i < bytes.length; i++) {
            result[i * 2] = digits[(bytes[i] & 0xff) >>> 4];
            result[i * 2 + 1] = digits[bytes[i] & 15];
        }
        return new String(result);
    }

    static String genesisDigest(JSONObject genesis) throws Exception {
        MessageDigest sha = MessageDigest.getInstance("SHA-256");
        sha.update("splitchain/canonical-genesis/v2\0".getBytes(StandardCharsets.US_ASCII));
        sha.update(canonical(genesis).getBytes(StandardCharsets.UTF_8));
        return hex(sha.digest());
    }

    static JSONObject sign(String id, String method, JSONObject params, String actor, long nonce, String secret) throws Exception {
        JSONObject request = new JSONObject().put("id", id).put("method", method).put("params", params);
        JSONObject message = new JSONObject().put("actor", actor).put("id", id)
                .put("method", method).put("nonce", nonce).put("params", params);
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        String signature = hex(mac.doFinal(canonical(message).getBytes(StandardCharsets.UTF_8)));
        return request.put("auth", new JSONObject().put("actor", actor).put("nonce", nonce).put("signature", signature));
    }
}
