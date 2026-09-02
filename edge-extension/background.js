const BRIDGE_URL = "ws://127.0.0.1:8766";
const EXTENSION_VERSION = chrome.runtime.getManifest().version;

const ALLOWED_HOSTS = [
  "taobao.com",
  "tmall.com",
  "jd.com",
  "ctrip.com",
  "dianping.com",
  "meituan.com",
  "zhihu.com",
  "douyin.com",
  "qq.com",
  "weixin.qq.com",
  "mp.weixin.qq.com",
  "bilibili.com",
];

let socket = null;
let reconnectTimer = null;
let heartbeatTimer = null;
let reconnectDelayMs = 1000;

function isAllowedUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return false;
    }
    const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
    return ALLOWED_HOSTS.some(
      (allowed) => host === allowed || host.endsWith(`.${allowed}`)
    );
  } catch {
    return false;
  }
}

function safeTab(tab) {
  const tabId = Number(tab.id || 0);
  const url = String(tab.url || "");
  if (!isAllowedUrl(url)) {
    return { tab_id: tabId, allowed: false };
  }
  return {
    tab_id: tabId,
    title: String(tab.title || "").slice(0, 500),
    url,
    active: Boolean(tab.active),
    allowed: true,
  };
}

function send(message) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(message));
  }
}

function scheduleReconnect() {
  if (reconnectTimer) {
    return;
  }
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connect();
  }, reconnectDelayMs);
  reconnectDelayMs = Math.min(reconnectDelayMs * 2, 10000);
}

function startHeartbeat() {
  clearInterval(heartbeatTimer);
  heartbeatTimer = setInterval(() => {
    send({ type: "heartbeat", at: Date.now() });
  }, 20000);
}

function connect() {
  if (socket && (
    socket.readyState === WebSocket.OPEN ||
    socket.readyState === WebSocket.CONNECTING
  )) {
    return;
  }

  try {
    socket = new WebSocket(BRIDGE_URL);
  } catch {
    scheduleReconnect();
    return;
  }

  socket.addEventListener("open", () => {
    reconnectDelayMs = 1000;
    send({
      type: "hello",
      version: EXTENSION_VERSION,
      browser: "edge",
    });
    startHeartbeat();
  });

  socket.addEventListener("message", async (event) => {
    let message;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    if (!message || message.type !== "request" || !message.id) {
      return;
    }

    try {
      const result = await handleRequest(
        String(message.action || ""),
        message.payload || {}
      );
      send({ type: "response", id: message.id, ok: true, result });
    } catch (error) {
      send({
        type: "response",
        id: message.id,
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  });

  socket.addEventListener("close", () => {
    clearInterval(heartbeatTimer);
    socket = null;
    scheduleReconnect();
  });

  socket.addEventListener("error", () => {
    try {
      socket.close();
    } catch {
      // close event schedules reconnect
    }
  });
}

async function sleep(ms) {
  await new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForTabReady(tabId, timeoutMs = 15000) {
  const initial = await chrome.tabs.get(tabId);
  if (initial.status === "complete") {
    return initial;
  }

  return await new Promise((resolve, reject) => {
    const timer = setTimeout(async () => {
      chrome.tabs.onUpdated.removeListener(listener);
      try {
        resolve(await chrome.tabs.get(tabId));
      } catch (error) {
        reject(error);
      }
    }, timeoutMs);

    const listener = async (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId || changeInfo.status !== "complete") {
        return;
      }
      clearTimeout(timer);
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(tab);
    };

    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function openTab(payload) {
  const url = String(payload.url || "");
  if (!isAllowedUrl(url)) {
    throw new Error("URL is outside the Edge bridge allowlist");
  }

  const tab = await chrome.tabs.create({
    url,
    active: payload.active !== false,
  });
  if (!tab.id) {
    throw new Error("Edge did not return a tab id");
  }

  await waitForTabReady(tab.id);
  const waitMs = Math.max(0, Math.min(Number(payload.wait_ms || 0), 5000));
  if (waitMs) {
    await sleep(waitMs);
  }

  const current = await chrome.tabs.get(tab.id);
  if (!isAllowedUrl(String(current.url || ""))) {
    try {
      await chrome.tabs.remove(tab.id);
    } catch {
      // best effort
    }
    throw new Error("Navigation ended outside the Edge bridge allowlist");
  }

  return safeTab(current);
}

async function listTabs() {
  const tabs = await chrome.tabs.query({});
  return { tabs: tabs.map(safeTab) };
}

async function resolveSnapshotTab(payload) {
  const requested = Number(payload.tab_id || 0);
  if (requested > 0) {
    return await chrome.tabs.get(requested);
  }

  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tabs.length) {
    throw new Error("No active Edge tab is available");
  }
  return tabs[0];
}

function collectPageSnapshot() {
  const candidates = [
    document.querySelector("#js_content"),
    document.querySelector("article"),
    document.querySelector("main"),
    document.querySelector("[role='main']"),
    document.body,
  ].filter(Boolean);

  let bestText = "";
  for (const node of candidates) {
    const text = String(node.innerText || node.textContent || "").trim();
    if (text.length > bestText.length) {
      bestText = text;
    }
    if (node.id === "js_content" && text) {
      bestText = text;
      break;
    }
  }

  const links = Array.from(document.querySelectorAll("a[href]"))
    .slice(0, 200)
    .map((anchor) => ({
      text: String(anchor.innerText || anchor.textContent || "").trim().slice(0, 200),
      href: String(anchor.href || ""),
    }));

  return {
    title: document.title || "",
    url: location.href,
    text: bestText.slice(0, 100000),
    links,
  };
}

async function snapshot(payload) {
  const tab = await resolveSnapshotTab(payload);
  if (!tab.id || !isAllowedUrl(String(tab.url || ""))) {
    throw new Error("The requested Edge tab is outside the bridge allowlist");
  }

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: collectPageSnapshot,
  });
  if (!results.length || !results[0].result) {
    throw new Error("No page snapshot was returned");
  }

  const result = results[0].result;
  if (!isAllowedUrl(String(result.url || ""))) {
    throw new Error("Page navigated outside the bridge allowlist");
  }
  return result;
}

async function closeTab(payload) {
  const tabId = Number(payload.tab_id || 0);
  if (tabId <= 0) {
    throw new Error("tab_id is required");
  }
  await chrome.tabs.remove(tabId);
  return { closed: true, tab_id: tabId };
}

async function handleRequest(action, payload) {
  switch (action) {
    case "ping":
      return { ok: true, version: EXTENSION_VERSION };
    case "list_tabs":
      return await listTabs();
    case "open_tab":
      return await openTab(payload);
    case "snapshot":
      return await snapshot(payload);
    case "close_tab":
      return await closeTab(payload);
    default:
      throw new Error(`Unsupported browser bridge action: ${action}`);
  }
}

chrome.runtime.onInstalled.addListener(() => connect());
chrome.runtime.onStartup.addListener(() => connect());
connect();
