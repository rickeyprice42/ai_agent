const avelinAgentUrl = String(window.AVELIN_AGENT_URL || "").trim();

window.AVELIN_SITE_CONFIG = Object.freeze({
  downloads: Object.freeze({
    fallbackPath: "./download.html",
    platforms: Object.freeze({
      windows: Object.freeze({ enabled: false, url: "" }),
      macos: Object.freeze({ enabled: false, url: "" }),
      ios: Object.freeze({ enabled: false, url: "" }),
      android: Object.freeze({ enabled: false, url: "" }),
    }),
  }),
  auth: Object.freeze({ enabled: /^https:\/\//i.test(avelinAgentUrl), path: avelinAgentUrl || "./app/" }),
});
