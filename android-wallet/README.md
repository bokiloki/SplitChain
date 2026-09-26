# SplitChain Android testnet wallet

Experimental Android app for **valueless test units** on `https://bokiloki.ddns.net/splitchain/`. The app is a client; it does not add a consensus validator. It checks the published bootstrap manifest and genesis against the bundled genesis before reading status or sending requests. HTTP is TLS only, and the account credential is encrypted using Android Keystore. This is not audited software for assets of value.

## Build and install

1. Open `android-wallet/` in Android Studio with JDK 17 and Android SDK 35; let it install the Android Gradle plugin 8.9.0 and Gradle 8.11.1. This folder intentionally contains no downloaded Gradle wrapper binary: Android Studio can import and sync the Gradle project directly.
2. Select **Build → Build Bundle(s) / APK(s) → Build APK(s)** and install the resulting `app/build/outputs/apk/debug/app-debug.apk` on Android 8.0 (API 26) or newer.
3. To run protocol tests, use **Run → All Tests**, or `gradle testDebugUnitTest` if you already have Gradle 8.11.1 installed.

There is no prebuilt APK in the repository. Build and inspect the app yourself before giving it to testers. Never distribute a wallet credential in the repository or a public chat.

## Provision a tester

The testnet operator must add an account credential to the server config, restart the nodes and privately provide the tester with the account ID and 64-character hex credential. Follow `scripts/create_testnet_wallet.py` and the testnet operator documentation in this repository. An account ID is safe to share; the credential signs transfers and must remain secret. The account starts with no balance; the faucet account sends it test units using the same offer → accept → commit flow.

1. Enter your issued account ID and credential on the phone. The app stores the credential encrypted at rest; it displays and copies the public account ID for receiving.
2. To send, provide another tester's account ID and a whole-unit amount. The offer temporarily reserves twice the amount from your available balance, including an equal stake.
3. The recipient refreshes and taps **Accept** on the incoming offer. The sender refreshes and taps **Commit**. After the server advances three finality rounds, refresh to see spendable balance.
4. Offers can expire after six rounds. Use **Cancel** on an unaccepted offer when necessary.

Each signed request reserves a new millisecond-based monotonic nonce before transmission, including when the outcome is uncertain. If the server says a nonce was already used, set **Account settings → Next auth nonce** higher than the account's last accepted nonce. Using the same credential on multiple phones can cause nonce conflicts; give each tester their own account. After any network failure, refresh before retrying to avoid duplicate offers.

The operator can monitor `https://bokiloki.ddns.net/splitchain/nodes` and the read-only `/status` endpoint. A public gateway and healthy nodes are prerequisites; a phone cannot fix server-side 502/503 errors.
