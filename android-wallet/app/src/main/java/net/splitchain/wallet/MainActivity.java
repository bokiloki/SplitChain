package net.splitchain.wallet;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.text.InputType;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int BG = Color.rgb(9, 22, 24);
    private static final int FG = Color.rgb(236, 248, 241);
    private static final int MUTED = Color.rgb(160, 184, 176);
    private static final int ACCENT = Color.rgb(90, 225, 161);
    private LinearLayout page;
    private TextView banner;
    private WalletStore store;
    private Network network;
    private boolean verified;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        store = new WalletStore(this);
        network = new Network(this);
        render(null);
        refresh();
    }

    @Override protected void onDestroy() { worker.shutdownNow(); super.onDestroy(); }
    private int dp(int size) { return (int) (size * getResources().getDisplayMetrics().density + 0.5f); }
    private TextView text(String label, int size, int color) {
        TextView view = new TextView(this);
        view.setText(label); view.setTextSize(size); view.setTextColor(color);
        view.setPadding(0, dp(9), 0, dp(9));
        page.addView(view);
        return view;
    }
    private EditText input(String hint, int type) {
        EditText view = new EditText(this);
        view.setHint(hint); view.setHintTextColor(MUTED); view.setTextColor(FG);
        view.setSingleLine(true); view.setInputType(type);
        page.addView(view, new LinearLayout.LayoutParams(-1, dp(55)));
        return view;
    }
    private void button(String title, Runnable action) {
        Button view = new Button(this);
        view.setText(title); view.setAllCaps(false); view.setTextColor(BG);
        view.setBackgroundTintList(android.content.res.ColorStateList.valueOf(ACCENT));
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(-1, dp(52));
        params.setMargins(0, dp(7), 0, dp(9));
        page.addView(view, params);
        view.setOnClickListener(v -> action.run());
    }
    private void heading(String title) { TextView view = text(title, 21, FG); view.setTypeface(null, Typeface.BOLD); }
    private void notice(String message) { banner.setText(message); }
    private void popup(String message) { new AlertDialog.Builder(this).setMessage(message).setPositiveButton("OK", null).show(); }

    private void render(JSONObject ledger) {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true); scroll.setBackgroundColor(BG);
        page = new LinearLayout(this); page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(23), dp(27), dp(23), dp(50));
        scroll.addView(page); setContentView(scroll);
        TextView brand = text("SPLITCHAIN  /  TESTNET", 25, ACCENT); brand.setTypeface(null, Typeface.BOLD);
        text("Valueless test units · public sandbox", 13, MUTED);
        banner = text(verified ? "Connected to pinned testnet" : "Checking testnet…", 14, ACCENT);
        if (!store.hasWallet()) {
            heading("Import a testnet wallet");
            text("Request an account ID and credential from the testnet operator. Keep the credential private; share only your account ID.", 15, MUTED);
            EditText account = input("Account ID", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
            EditText secret = input("64-character hex credential", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
            button("Save wallet", () -> {
                try { store.save(account.getText().toString().trim(), secret.getText().toString().trim()); render(null); refresh(); }
                catch (Exception e) { popup(e.getMessage()); }
            });
            return;
        }
        heading("Receive");
        text("Your account ID", 13, MUTED);
        TextView address = text(store.account(), 19, FG); address.setTextIsSelectable(true);
        button("Copy account ID", () -> {
            ((ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE)).setPrimaryClip(ClipData.newPlainText("SplitChain account", store.account()));
            Toast.makeText(this, "Account ID copied", Toast.LENGTH_SHORT).show();
        });
        text("Give this ID to the sender. Accept their offer below; the sender must then commit it.", 14, MUTED);
        if (ledger != null) {
            JSONObject balances = ledger.optJSONObject("balances"), locked = ledger.optJSONObject("locked");
            long balance = balances == null ? 0 : balances.optLong(store.account(), 0);
            long stake = locked == null ? 0 : locked.optLong(store.account(), 0);
            heading(balance + " test units");
            text("Available: " + (balance - stake) + " · Reserved: " + stake + " · Round: " + ledger.optLong("round"), 14, MUTED);
        }
        button("Refresh balance and offers", this::refresh);
        heading("Send");
        EditText recipient = input("Recipient account ID", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        EditText amount = input("Whole test units", InputType.TYPE_CLASS_NUMBER);
        button("Create offer", () -> {
            try {
                String to = recipient.getText().toString().trim();
                long value = Long.parseLong(amount.getText().toString());
                if (!to.matches("[A-Za-z0-9_-]{1,64}") || to.equals(store.account()) || value <= 0)
                    throw new IllegalArgumentException("Enter a different recipient and a positive whole amount");
                if (ledger == null) throw new IllegalStateException("Refresh the testnet status first");
                JSONObject balances = ledger.getJSONObject("balances"), locked = ledger.getJSONObject("locked");
                long available = balances.optLong(store.account(), 0) - locked.optLong(store.account(), 0);
                if (value > available / 2) throw new IllegalArgumentException("An offer reserves the amount plus equal temporary stake; available balance is " + available);
                action("offer", new JSONObject().put("sender", store.account()).put("receiver", to).put("value", value));
            } catch (Exception e) { popup(e.getMessage()); }
        });
        heading("Pending transfers");
        JSONArray branches = ledger == null ? new JSONArray() : ledger.optJSONArray("branches");
        int count = 0;
        if (branches != null) for (int i = branches.length() - 1; i >= 0; i--) {
            JSONObject branch = branches.optJSONObject(i);
            if (branch == null) continue;
            String from = branch.optString("sender"), to = branch.optString("receiver"), state = branch.optString("state");
            if (!store.account().equals(from) && !store.account().equals(to)) continue;
            if (!state.equals("offered") && !state.equals("accepted") && !state.equals("committed")) continue;
            count++;
            String id = branch.optString("branch_id");
            text(branch.optLong("value") + " units  ·  " + state + "\n" + from + " → " + to + "\nOffer " + id + " · expires round " + branch.optLong("expires_round"), 14, FG);
            if (store.account().equals(to) && state.equals("offered")) button("Accept " + id, () -> {
                try { action("accept", new JSONObject().put("branch_id", id).put("receiver", store.account())); }
                catch (Exception e) { popup(e.getMessage()); }
            });
            if (store.account().equals(from) && state.equals("accepted")) button("Commit " + id, () -> {
                try { action("commit", new JSONObject().put("branch_id", id).put("sender", store.account()).put("payload", new JSONObject())); }
                catch (Exception e) { popup(e.getMessage()); }
            });
            if (store.account().equals(from) && state.equals("offered")) button("Cancel " + id, () -> {
                try { action("cancel", new JSONObject().put("branch_id", id).put("actor", store.account())); }
                catch (Exception e) { popup(e.getMessage()); }
            });
        }
        if (count == 0) text("No pending offers for this wallet.", 14, MUTED);
        text("Committed transfers become spendable after the testnet advances through finality rounds.", 14, MUTED);
        button("Account settings", this::settings);
    }

    private void refresh() {
        notice("Checking published testnet and node status…");
        worker.execute(() -> {
            try {
                network.verify();
                JSONObject ledger = network.status();
                runOnUiThread(() -> { verified = true; render(ledger); });
            } catch (Exception error) {
                runOnUiThread(() -> { verified = false; notice("Unavailable: " + error.getMessage()); });
            }
        });
    }

    private void action(String method, JSONObject params) {
        if (!verified) { popup("Connect to the pinned testnet before sending"); return; }
        String actor = store.account();
        try {
            long nonce = store.reserveNonce();
            JSONObject request = Protocol.sign(UUID.randomUUID().toString().replace("-", "").substring(0, 8),
                    method, params, actor, nonce, store.secret());
            notice("Sending " + method + "…");
            network.submit(request, (response, error) -> runOnUiThread(() -> {
                if (error != null) { notice("Outcome uncertain. Refresh before trying again."); popup(error.getMessage()); return; }
                if (response.has("error")) {
                    notice("Request rejected");
                    popup(response.optJSONObject("error").optString("message", "Unknown RPC error"));
                } else { notice("Request accepted"); refresh(); }
            }));
        } catch (Exception e) { popup(e.getMessage()); }
    }

    private void settings() {
        EditText next = new EditText(this);
        next.setInputType(InputType.TYPE_CLASS_NUMBER);
        next.setText(Long.toString(store.nextNonce()));
        new AlertDialog.Builder(this).setTitle("Next auth nonce")
                .setMessage("Only change this if the operator says your account has used a higher nonce on another device. Use a number greater than the last accepted nonce.")
                .setView(next).setPositiveButton("Save", (dialog, which) -> {
                    try { store.setNextNonce(Long.parseLong(next.getText().toString())); }
                    catch (Exception e) { popup("Enter a valid positive number"); }
                }).setNeutralButton("Remove wallet", (dialog, which) ->
                        new AlertDialog.Builder(this).setTitle("Remove credential from this device?")
                                .setPositiveButton("Remove", (d, w) -> { store.clear(); render(null); })
                                .setNegativeButton("Cancel", null).show())
                .setNegativeButton("Cancel", null).show();
    }
}
