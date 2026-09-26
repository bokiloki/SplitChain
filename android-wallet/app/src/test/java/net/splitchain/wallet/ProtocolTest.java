package net.splitchain.wallet;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.assertEquals;

public class ProtocolTest {
    @Test public void signsSameBytesAsPythonTestnetClient() throws Exception {
        JSONObject params = new JSONObject().put("sender", "alice").put("receiver", "bob").put("value", 7);
        JSONObject request = Protocol.sign("test1234", "offer", params, "alice", 1700000000000L,
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        assertEquals("ad0d1a17ca213e164f6e9d3d8878c27a7cbccc674ff1e87249205ab45121691f",
                request.getJSONObject("auth").getString("signature"));
    }

    @Test public void pinsActualGenesisDigest() throws Exception {
        JSONObject genesis = new JSONObject("{\"schema\":\"splitchain-genesis/v1\",\"network_id\":\"splitchain-public-testnet-candidate-1\",\"max_supply\":21000000,\"allocations\":{\"testnet_faucet\":14000000,\"testnet_locked_reserve\":7000000},\"locked_accounts\":[\"testnet_locked_reserve\"]}");
        assertEquals("88845d3acd1ab6ef28c378d2917939bc634cb7453dc8d2b7d51ad995ce18340a",
                Protocol.genesisDigest(genesis));
    }
}
