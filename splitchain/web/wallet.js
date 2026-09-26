/* SplitChain valueless testnet wallet. Credentials and signing key live only in this tab. */
(() => {
  'use strict';
  const base = new URL('__SPLITCHAIN_BASE__', location.href);
  const rpc = new URL(base.pathname + 'rpc', base.origin);
  rpc.protocol = 'wss:';
  const digest = '88845d3acd1ab6ef28c378d2917939bc634cb7453dc8d2b7d51ad995ce18340a';
  const networkId = 'splitchain-public-testnet-candidate-1';
  const $ = id => document.getElementById(id);
  const encoder = new TextEncoder();
  let account = '', key = null, verified = false, busy = false, nextNonce = 0;
  const canonical = value => {
    if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
    if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
    return JSON.stringify(value);
  };
  const hex = data => [...new Uint8Array(data)].map(b => b.toString(16).padStart(2, '0')).join('');
  const note = message => { $('wallet-message').textContent = message; };
  async function json(url) {
    const response = await fetch(url, {cache:'no-store', redirect:'error', credentials:'omit'});
    if (!response.ok || response.url !== url.href) throw Error('Endpoint unavailable: ' + url.pathname);
    return response.json();
  }
  async function verify() {
    const [manifest, genesis] = await Promise.all([
      json(new URL('.well-known/splitchain-testnet.json', base)), json(new URL('genesis.json', base))
    ]);
    const expectedGenesis = {schema:'splitchain-genesis/v1', network_id:networkId, max_supply:21000000,
      allocations:{testnet_faucet:14000000, testnet_locked_reserve:7000000}, locked_accounts:['testnet_locked_reserve']};
    const computed = hex(await crypto.subtle.digest('SHA-256', encoder.encode('splitchain/canonical-genesis/v2\0' + canonical(genesis))));
    const expected = {schema:'splitchain-bootstrap/v1', network_id:networkId, genesis_digest:digest,
      manifest_url:new URL('.well-known/splitchain-testnet.json', base).href,
      genesis_url:new URL('genesis.json', base).href,
      status_url:new URL('status', base).href,
      rpc_url:rpc.href, units:'valueless-testnet'};
    if (base.protocol !== 'https:' || base.origin !== location.origin ||
        computed !== digest || canonical(genesis) !== canonical(expectedGenesis) ||
        canonical(manifest) !== canonical(expected)) throw Error('Published genesis or bootstrap differs from pinned testnet');
    verified = true;
  }
  function updateBranches(ledger) {
    const holder = $('wallet-branches'); holder.replaceChildren();
    const branches = ledger.branches.filter(b => (b.sender === account || b.receiver === account) &&
      ['offered','accepted','committed'].includes(b.state)).reverse();
    if (!branches.length) { holder.textContent = 'No pending transfers for this account.'; return; }
    for (const b of branches) {
      const row = document.createElement('div'); row.className = 'wallet-transfer';
      const details = document.createElement('p');
      details.textContent = `${b.value} test units · ${b.state} · ${b.sender} → ${b.receiver} · offer ${b.branch_id} · expires round ${b.expires_round}`;
      row.append(details);
      let method, params;
      if (b.receiver === account && b.state === 'offered') { method = 'accept'; params = {branch_id:b.branch_id, receiver:account}; }
      if (b.sender === account && b.state === 'accepted') { method = 'commit'; params = {branch_id:b.branch_id, sender:account, payload:{}}; }
      if (b.sender === account && b.state === 'offered') { method = 'cancel'; params = {branch_id:b.branch_id, actor:account}; }
      if (method) {
        const button = document.createElement('button'); button.type = 'button'; button.textContent = method + ' ' + b.branch_id;
        button.addEventListener('click', () => submit(method, params)); row.append(button);
      }
      holder.append(row);
    }
  }
  async function refresh() {
    try {
      if (!verified) await verify();
      const response = await json(new URL('status', base));
      const ledger = response.result;
      if (ledger?.schema !== 'splitchain-ledger/v1' || !ledger.canonical_head) throw Error('Invalid ledger status');
      $('node-state').textContent = 'Responding via ' + response.source;
      $('height').textContent = String(ledger.canonical_head.height);
      $('round').textContent = String(ledger.round);
      $('network-id').textContent = networkId;
      if (key) {
        const total = ledger.balances[account] || 0, locked = ledger.locked[account] || 0;
        $('wallet-balance').textContent = `${total} test units · ${total - locked} available · ${locked} reserved`;
        updateBranches(ledger);
      }
      return ledger;
    } catch (error) {
      verified = false; $('node-state').textContent = 'Unavailable'; note(error.message); throw error;
    }
  }
  function disconnect() {
    account = ''; key = null; nextNonce = 0;
    $('wallet-credential').value = ''; $('wallet-account').value = '';
    $('wallet-active').hidden = true; $('wallet-login').hidden = false;
    $('wallet-balance').textContent = ''; $('wallet-branches').replaceChildren();
    note('Wallet disconnected. Credentials were kept only in this tab.');
  }
  async function connect() {
    const actor = $('wallet-account').value.trim();
    const secret = $('wallet-credential').value.trim();
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(actor) || !/^[0-9a-fA-F]{64}$/.test(secret)) throw Error('Enter your issued account ID and 64-character hex credential');
    await verify();
    key = await crypto.subtle.importKey('raw', encoder.encode(secret), {name:'HMAC',hash:'SHA-256'}, false, ['sign']);
    $('wallet-credential').value = '';
    account = actor;
    $('wallet-id').textContent = actor;
    $('wallet-login').hidden = true; $('wallet-active').hidden = false;
    note('Wallet ready. Share your account ID to receive test units.');
    await refresh();
  }
  function rpcRequest(request) {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(rpc.href);
      let settled = false;
      const timeout = setTimeout(() => { settled = true; socket.close(); reject(Error('Gateway timed out; refresh before retrying')); }, 12000);
      socket.onopen = () => socket.send(JSON.stringify(request));
      socket.onmessage = event => {
        clearTimeout(timeout); settled = true; socket.close();
        try { resolve(JSON.parse(event.data)); } catch (_) { reject(Error('Invalid gateway response')); }
      };
      socket.onerror = () => { clearTimeout(timeout); if (!settled) { settled = true; reject(Error('Gateway unavailable; refresh before retrying')); } };
      socket.onclose = () => { clearTimeout(timeout); if (!settled) { settled = true; reject(Error('Gateway closed before responding; inspect status')); } };
    });
  }
  async function submit(method, params) {
    if (busy) return;
    busy = true;
    try {
      if (!key || !verified) throw Error('Connect your wallet to the pinned testnet first');
      const nonce = Math.max(Date.now(), nextNonce);
      if (!Number.isSafeInteger(nonce)) throw Error('Nonce exhausted');
      nextNonce = nonce + 1; $('wallet-nonce').value = String(nextNonce);
      const id = [...crypto.getRandomValues(new Uint8Array(4))].map(b => b.toString(16).padStart(2,'0')).join('');
      const request = {id, method, params};
      const message = {actor:account, id, method, nonce, params};
      const signature = hex(await crypto.subtle.sign('HMAC', key, encoder.encode(canonical(message))));
      request.auth = {actor:account, nonce, signature};
      note('Sending ' + method + '…');
      const response = await rpcRequest(request);
      if (response.id !== id) throw Error('Mismatched gateway response; inspect status');
      if (response.error) throw Error(response.error.message || 'Request rejected');
      note(method + ' accepted. Refreshing status…');
      await refresh();
    } catch (error) { note(error.message); }
    finally { busy = false; }
  }
  $('wallet-connect').addEventListener('click', async () => { try { await connect(); } catch (error) { note(error.message); } });
  $('wallet-disconnect').addEventListener('click', disconnect);
  $('wallet-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(account); note('Account ID copied.'); }
    catch (_) { note('Select and copy the account ID above.'); }
  });
  $('wallet-refresh').addEventListener('click', () => refresh().catch(() => {}));
  $('wallet-nonce').addEventListener('change', () => {
    const value = Number($('wallet-nonce').value);
    if (!Number.isSafeInteger(value) || value < nextNonce) { $('wallet-nonce').value = String(nextNonce); note('Nonce must be at least the next unused number.'); }
    else { nextNonce = value; note('Next nonce updated for this tab.'); }
  });
  $('wallet-send').addEventListener('click', async () => {
    try {
      const receiver = $('wallet-recipient').value.trim();
      const value = Number($('wallet-amount').value);
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(receiver) || receiver === account || !Number.isSafeInteger(value) || value <= 0)
        throw Error('Enter a different recipient and a positive whole amount');
      const ledger = await refresh();
      const available = (ledger.balances[account] || 0) - (ledger.locked[account] || 0);
      if (value > Math.floor(available / 2)) throw Error('An offer needs the amount plus equal stake; available: ' + available);
      await submit('offer', {sender:account, receiver, value});
    } catch (error) { note(error.message); }
  });
  refresh().catch(() => {});
})();
