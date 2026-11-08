/* =====================================================================
   Nuansa Wangi - Dashboard Inventaris (vanilla JS, tanpa build step)
   ===================================================================== */
(function () {
  "use strict";

  var STORAGE_KEY = "nw_api_key";
  var REFRESH_MS = 10000;

  var auth = null;
  var items = [];
  var branches = [];
  var timer = null;
  var loading = false;
  var toastTimer = null;

  function $(id) {
    return document.getElementById(id);
  }

  function getKey() {
    return sessionStorage.getItem(STORAGE_KEY) || "";
  }

  function setKey(key) {
    if (key) sessionStorage.setItem(STORAGE_KEY, key);
    else sessionStorage.removeItem(STORAGE_KEY);
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function errorMessage(payload, fallback) {
    var detail = payload && payload.detail;
    if (!detail) return fallback;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map(function (item) {
          return item && item.msg ? item.msg : JSON.stringify(item);
        })
        .join("; ");
    }
    return detail.message || fallback;
  }

  function toast(message, kind, ms) {
    var el = $("toast");
    el.textContent = message;
    el.className = "toast" + (kind ? " " + kind : "");
    el.hidden = false;
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      el.hidden = true;
    }, ms || 3200);
  }

  async function request(path, options) {
    var opts = options || {};
    var noLogout = !!opts.noLogout;
    var init = Object.assign({}, opts);
    delete init.noLogout;

    var headers = Object.assign({}, init.headers || {});
    var key = getKey();
    if (key) headers["X-API-Key"] = key;
    init.headers = headers;

    var res = await fetch(path, init);
    var text = await res.text();
    var payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (e) {
        payload = text;
      }
    }

    if (!res.ok) {
      if (res.status === 401 && !noLogout) {
        logout("Kunci API tidak valid atau cabang dinonaktifkan.");
      }
      var err = new Error(errorMessage(payload, "HTTP " + res.status));
      err.status = res.status;
      err.payload = payload;
      throw err;
    }
    return payload;
  }

  // ------------------------------------------------------------------
  // Tampilan login / app
  // ------------------------------------------------------------------
  function showLogin() {
    $("app-view").hidden = true;
    $("login-view").hidden = false;
    stopAuto();
  }

  function showApp() {
    $("login-view").hidden = true;
    $("app-view").hidden = false;
    startAuto();
  }

  function logout(message) {
    setKey("");
    auth = null;
    items = [];
    branches = [];
    stopAuto();
    $("api-key").value = "";
    $("identity").textContent = "-";
    showLogin();
    if (message) toast(message, "error", 4200);
  }

  async function loadHealth() {
    try {
      var res = await fetch("/health");
      if (!res.ok) return;
      var data = await res.json();
      $("backend-badge").textContent = data.backend || "-";
      $("backend-badge").className = "badge";
      $("rate-info").textContent = "API v" + (data.version || "?");
    } catch (e) {
      $("backend-badge").textContent = "offline";
      $("backend-badge").className = "badge off";
    }
  }

  // ------------------------------------------------------------------
  // Data
  // ------------------------------------------------------------------
  async function loadItems() {
    var data = await request("/items");
    items = data.items || [];
    renderItems();
    fillSkuOptions();
  }

  async function loadLogs() {
    var params = new URLSearchParams();
    if (auth && auth.is_admin) {
      var branch = $("filter-branch").value;
      if (branch) params.set("branch_id", branch);
    }
    var sku = $("filter-sku").value;
    if (sku) params.set("sku", sku);
    var status = $("filter-status").value;
    if (status) params.set("status", status);
    params.set("limit", $("filter-limit").value || "50");

    var data = await request("/logs?" + params.toString());
    renderLogs(data.logs || []);
    $("logs-count").textContent = (data.count || 0) + " baris";
  }

  async function loadSummary() {
    if (!auth || !auth.is_admin) return;
    var s = await request("/summary");
    $("stat-items").textContent = s.item_count;
    $("stat-qty").textContent = s.total_qty;
    $("stat-success").textContent = s.success;
    $("stat-rejected").textContent = s.rejected;
    $("stat-last").textContent = s.last_tx ? fmtTime(s.last_tx) : "belum ada";
  }

  async function loadBranches() {
    if (!auth || !auth.is_admin) return;
    var data = await request("/branches");
    branches = data.branches || [];

    var updateSelect = $("update-branch");
    var filterSelect = $("filter-branch");
    var keepUpdate = updateSelect.value;
    var keepFilter = filterSelect.value;

    updateSelect.innerHTML = branches
      .map(function (b) {
        return '<option value="' + esc(b.id) + '">' + esc(b.id) + " &middot; " + esc(b.name) + "</option>";
      })
      .join("");

    filterSelect.innerHTML =
      '<option value="">Semua cabang</option>' +
      branches
        .map(function (b) {
          return '<option value="' + esc(b.id) + '">' + esc(b.id) + " &middot; " + esc(b.name) + "</option>";
        })
        .join("");

    if (keepUpdate) updateSelect.value = keepUpdate;
    if (keepFilter) filterSelect.value = keepFilter;
  }

  async function refreshData(quiet) {
    if (loading) return;
    loading = true;
    try {
      await Promise.all([loadItems(), loadLogs(), loadSummary()]);
      $("items-updated").textContent = "diperbarui " + new Date().toLocaleTimeString("id-ID");
      $("live-badge").className = "badge live";
      $("live-badge").textContent = "\u25CF live";
    } catch (e) {
      $("live-badge").className = "badge off";
      $("live-badge").textContent = "\u25CF terputus";
      if (!quiet) toast(e.message, "error", 4200);
    } finally {
      loading = false;
    }
  }

  async function loadAll() {
    if (auth.is_admin) {
      await loadBranches();
      $("summary-section").hidden = false;
      $("branch-field").hidden = false;
      $("filter-branch").hidden = false;
    } else {
      $("summary-section").hidden = true;
      $("branch-field").hidden = true;
      $("filter-branch").hidden = true;
    }

    $("identity").textContent = auth.is_admin
      ? "Admin \u00B7 akses semua cabang"
      : "Cabang \u00B7 " + auth.branch_id;

    await refreshData(false);
  }

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  function renderItems() {
    var grid = $("items-grid");
    if (!items.length) {
      grid.innerHTML = '<p class="muted">Belum ada data stok.</p>';
      return;
    }
    grid.innerHTML = items
      .map(function (item) {
        var qty = Number(item.qty);
        var cls = qty <= 0 ? " out" : qty <= 20 ? " low" : "";
        return (
          '<article class="item-card' + cls + '">' +
          '<span class="item-tag">' + esc(item.category) + "</span>" +
          '<div class="item-sku">' + esc(item.sku) + "</div>" +
          '<div class="item-name">' + esc(item.name) + "</div>" +
          '<div><span class="item-qty">' + qty + "</span>" +
          '<span class="item-unit">' + esc(item.unit) + "</span></div>" +
          "</article>"
        );
      })
      .join("");
  }

  function fillSkuOptions() {
    var select = $("update-sku");
    var filterSku = $("filter-sku");
    var keep = select.value;
    var keepFilter = filterSku.value;

    select.innerHTML = items
      .map(function (item) {
        return '<option value="' + esc(item.sku) + '">' + esc(item.name) + " (" + esc(item.sku) + ")</option>";
      })
      .join("");

    filterSku.innerHTML =
      '<option value="">Semua SKU</option>' +
      items
        .map(function (item) {
          return '<option value="' + esc(item.sku) + '">' + esc(item.sku) + "</option>";
        })
        .join("");

    if (keep) select.value = keep;
    if (keepFilter) filterSku.value = keepFilter;
    updateUnitHint();
  }

  function selectedItem() {
    var sku = $("update-sku").value;
    for (var i = 0; i < items.length; i++) {
      if (items[i].sku === sku) return items[i];
    }
    return null;
  }

  function updateUnitHint() {
    var item = selectedItem();
    var hint = $("unit-hint");
    if (!item) {
      hint.textContent = "";
      return;
    }
    var action = $("update-action").value;
    hint.textContent =
      "Stok sekarang: " + item.qty + " " + item.unit +
      ". Satuan transaksi: " + item.unit +
      (action === "SUBTRACT" ? " (dikurangi)" : " (ditambahkan)") + ".";
  }

  function fmtTime(value) {
    if (!value) return "-";
    var d = new Date(value);
    if (isNaN(d.getTime())) return String(value);
    return d.toLocaleString("id-ID", { dateStyle: "short", timeStyle: "medium" });
  }

  function renderLogs(logs) {
    var body = $("logs-body");
    if (!logs.length) {
      body.innerHTML = '<tr><td colspan="6" class="muted center">Belum ada transaksi.</td></tr>';
      return;
    }
    body.innerHTML = logs
      .map(function (t) {
        var delta = Number(t.delta);
        var deltaCls = delta < 0 ? "delta-neg" : "delta-pos";
        var deltaText = (delta > 0 ? "+" : "") + delta;
        var status = String(t.status || "").toLowerCase();
        return (
          "<tr>" +
          '<td class="nowrap">' + esc(fmtTime(t.created_at)) + "</td>" +
          "<td>" + esc(t.branch_id) + "</td>" +
          "<td>" + esc(t.sku) + "</td>" +
          '<td class="' + deltaCls + ' nowrap">' + esc(deltaText) + "</td>" +
          '<td class="nowrap">' + esc(t.qty_before) + " &rarr; " + esc(t.qty_after) + "</td>" +
          '<td><span class="pill ' + esc(status) + '">' + esc(t.status) + "</span></td>" +
          "</tr>"
        );
      })
      .join("");
  }

  // ------------------------------------------------------------------
  // Auto refresh
  // ------------------------------------------------------------------
  function startAuto() {
    stopAuto();
    timer = setInterval(function () {
      if (!document.hidden && auth && !loading) refreshData(true);
    }, REFRESH_MS);
  }

  function stopAuto() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  // ------------------------------------------------------------------
  // Event: login
  // ------------------------------------------------------------------
  $("login-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var key = $("api-key").value.trim();
    var errorEl = $("login-error");
    var button = $("login-btn");
    errorEl.hidden = true;
    if (!key) return;

    button.disabled = true;
    button.textContent = "Memeriksa kunci\u2026";
    setKey(key);
    try {
      auth = await request("/auth/me", { noLogout: true });
      var me = auth;
      $("api-key").value = "";
      showApp();
      await loadHealth();
      await loadAll();
      toast(
        me.is_admin ? "Masuk sebagai admin." : "Masuk sebagai " + me.branch_id + ".",
        "success"
      );
    } catch (e) {
      setKey("");
      auth = null;
      errorEl.textContent = e.message || "Gagal masuk.";
      errorEl.hidden = false;
      showLogin();
    } finally {
      button.disabled = false;
      button.textContent = "Masuk";
    }
  });

  // ------------------------------------------------------------------
  // Event: app
  // ------------------------------------------------------------------
  $("logout-btn").addEventListener("click", function () {
    logout(null);
    toast("Kunci dihapus dari sesi browser ini.", null, 2600);
  });

  $("refresh-btn").addEventListener("click", function () {
    refreshData(false);
  });

  $("logs-filter").addEventListener("change", function () {
    loadLogs().catch(function (e) {
      toast(e.message, "error", 4200);
    });
  });

  $("update-sku").addEventListener("change", updateUnitHint);
  $("update-action").addEventListener("change", updateUnitHint);

  $("update-form").addEventListener("submit", async function (event) {
    event.preventDefault();
    var errorEl = $("update-error");
    var button = $("update-btn");
    errorEl.hidden = true;

    var branchId = auth && auth.is_admin ? $("update-branch").value : auth ? auth.branch_id : "";
    var payload = {
      branch_id: branchId,
      sku: $("update-sku").value,
      action: $("update-action").value,
      quantity: parseInt($("update-qty").value, 10),
      note: $("update-note").value.trim() || undefined,
      request_id:
        "dash-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10)
    };

    if (!payload.branch_id) {
      errorEl.textContent = "Pilih cabang terlebih dahulu.";
      errorEl.hidden = false;
      return;
    }

    button.disabled = true;
    button.textContent = "Mengirim\u2026";
    try {
      var result = await request("/items/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      toast(
        "Stok " + result.sku + " sekarang " + result.qty_after + " " + result.unit +
          (result.idempotent_replay ? " (replay)" : "") + ".",
        "success"
      );
      $("update-note").value = "";
      $("update-qty").value = "1";
      await refreshData(false);
    } catch (e) {
      errorEl.textContent = e.message;
      errorEl.hidden = false;
    } finally {
      button.disabled = false;
      button.textContent = "Kirim Transaksi";
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden && auth) refreshData(true);
  });

  // ------------------------------------------------------------------
  // Start
  // ------------------------------------------------------------------
  (async function init() {
    if (!getKey()) {
      showLogin();
      return;
    }
    try {
      auth = await request("/auth/me", { noLogout: true });
      showApp();
      await loadHealth();
      await loadAll();
    } catch (e) {
      logout(null);
    }
  })();
})();
