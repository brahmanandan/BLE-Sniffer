// app.js — entry point: shell + tabs + PWA service worker.

import { connect } from "./ws.js";
import { initShell, registerTab } from "./ui.js";
import { init as connectTab } from "./tab_connect.js";
import { init as keysTab } from "./tab_keys.js";
import { init as voiceTab } from "./tab_voice.js";
import { init as irTab } from "./tab_ir.js";
import { init as testTab } from "./tab_test.js";

registerTab("connect", connectTab);
registerTab("keys", keysTab);
registerTab("voice", voiceTab);
registerTab("ir", irTab);
registerTab("test", testTab);
initShell();
connect();

// Service workers need a secure context (https, or http://localhost).
if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("sw.js").catch((e) => console.warn("service worker:", e));
}
