package net.splitchain.wallet;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.security.KeyStore;
import java.security.SecureRandom;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class WalletStore {
    private static final String KEY_ALIAS = "splitchain-testnet-wallet-v1";
    private final SharedPreferences preferences;

    WalletStore(Context context) {
        preferences = context.getSharedPreferences("wallet", Context.MODE_PRIVATE);
    }

    String account() { return preferences.getString("account", ""); }
    boolean hasWallet() { return !account().isEmpty() && preferences.contains("secret"); }

    private SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (store.containsAlias(KEY_ALIAS)) return ((KeyStore.SecretKeyEntry) store.getEntry(KEY_ALIAS, null)).getSecretKey();
        KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        generator.init(new KeyGenParameterSpec.Builder(KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).setKeySize(256).build());
        return generator.generateKey();
    }

    void save(String account, String secret) throws Exception {
        if (!account.matches("[A-Za-z0-9_-]{1,64}") || !secret.matches("[0-9a-fA-F]{64}"))
            throw new IllegalArgumentException("Enter an account ID and its 64-character hex credential");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        byte[] encoded = cipher.doFinal(secret.toLowerCase(java.util.Locale.ROOT).getBytes(java.nio.charset.StandardCharsets.UTF_8));
        preferences.edit().putString("account", account)
                .putString("secret", Base64.encodeToString(encoded, Base64.NO_WRAP))
                .putString("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP))
                .putLong("next_nonce", 0).commit();
    }

    String secret() throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128,
                Base64.decode(preferences.getString("iv", ""), Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(preferences.getString("secret", ""), Base64.NO_WRAP)),
                java.nio.charset.StandardCharsets.UTF_8);
    }

    // Reserve before transmission: uncertain outcomes must never reuse an auth nonce.
    synchronized long reserveNonce() {
        long prior = preferences.getLong("next_nonce", 0);
        long now = System.currentTimeMillis();
        if (prior == Long.MAX_VALUE) throw new IllegalStateException("nonce exhausted");
        long nonce = Math.max(now, prior);
        if (!preferences.edit().putLong("next_nonce", nonce + 1).commit())
            throw new IllegalStateException("unable to save nonce");
        return nonce;
    }

    synchronized void setNextNonce(long next) {
        if (next < 0 || !preferences.edit().putLong("next_nonce", next).commit())
            throw new IllegalArgumentException("invalid nonce");
    }
    long nextNonce() { return preferences.getLong("next_nonce", 0); }
    void clear() { preferences.edit().clear().commit(); }
}
