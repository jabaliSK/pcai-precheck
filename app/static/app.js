(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => (s == null ? "" : String(s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])));

  const runBtn = $("run-btn");
  const pdfBtn = $("pdf-btn");
  const runStatus = $("run-status");
  const progressFill = $("progress-fill");
  const progressText = $("progress-text");
  const currentTask = $("current-task");
  const results = $("results");
  const meta = $("meta");
  const version = $("version");

  let pollTimer = null;

  runBtn.addEventListener("click", async () => {
    runBtn.disabled = true;
    try {
      const r = await fetch("/api/run", { method: "POST" });
      if (r.status === 409) {
        const body = await r.json();
        console.warn("run rejected", body);
      }
    } catch (e) { console.error(e); }
    finally { setTimeout(() => (runBtn.disabled = false), 500); }
    schedulePoll(500);
  });

  function schedulePoll(delay) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, delay);
  }

  async function poll() {
    try {
      const r = await fetch("/api/results");
      const data = await r.json();
      render(data);
      schedulePoll(data.running ? 1500 : 5000);
    } catch (e) {
      console.error(e);
      schedulePoll(3000);
    }
  }

  function fmtTs(ts) {
    if (!ts) return "-";
    return new Date(ts * 1000).toLocaleString();
  }

  function render(data) {
    // header status
    runStatus.className = "badge " + (data.running ? "running" : (
      (data.summary && data.summary.fail > 0) ? "error" : (data.summary && data.summary.total > 0 ? "done" : "")
    ));
    runStatus.textContent = data.running
      ? "running…"
      : (data.summary && data.summary.total > 0 ? "complete" : "idle");
    runBtn.disabled = data.running;

    progressFill.style.width = (data.progress || 0) + "%";
    progressText.textContent = `${data.completed_steps || 0} / ${data.total_steps || 0}`;
    currentTask.textContent = data.current_task || "";

    const s = data.summary || {};
    $("cnt-pass").textContent = s.pass || 0;
    $("cnt-warn").textContent = s.warn || 0;
    $("cnt-fail").textContent = s.fail || 0;
    $("cnt-total").textContent = s.total || 0;

    meta.innerHTML = `
      <span class="kv"><b>Host:</b> ${esc(data.hostname)}</span>
      <span class="kv"><b>Started:</b> ${esc(fmtTs(data.started_at))}</span>
      <span class="kv"><b>Finished:</b> ${esc(fmtTs(data.finished_at))}</span>
      <span class="kv"><b>Retries:</b> ${esc(data.config && data.config.retries)}</span>
      <span class="kv"><b>Timeout:</b> ${esc(data.config && data.config.timeout)}s</span>
      <span class="kv"><b>Port:</b> ${esc(data.config && data.config.port)}</span>
      <span class="kv"><b>Fail on SSL:</b> ${esc(data.config && data.config.fail_on_ssl)}</span>
    `;
    version.textContent = `pcai-precheck v${esc(data.version)}`;

    renderSpeed(data.results || []);
    renderResults(data.results || []);
    pdfBtn.classList.toggle("disabled", !(data.results && data.results.length));
  }

  function renderSpeed(items) {
    const card = $("speed-card");
    const speed = items.filter((r) => r.category === "Network Speed");
    if (!speed.length) { card.hidden = true; return; }
    card.hidden = false;
    const find = (needle) => speed.find((r) => (r.name || "").toLowerCase().includes(needle));
    const setCell = (id, row) => {
      const el = $(id);
      if (!row) { el.textContent = "—"; el.className = "speed-value"; return; }
      el.className = "speed-value " + esc(row.status || "");
      el.textContent = row.detail ? row.detail.split(",")[0] : row.status;
    };
    setCell("speed-down", find("download"));
    setCell("speed-up", find("upload"));
    setCell("speed-latency", find("latency"));
  }

  // ---- Tool + domain grouped view ---------------------------------------
  // Preferred display order; anything else falls to the bottom alphabetically.
  const TOOL_ORDER = ["curl", "wget", "pip", "git", "huggingface_hub", "requests"];
  const TOOL_LABELS = {
    curl: "cURL",
    wget: "wget",
    pip: "pip (PyPI)",
    git: "git (GitHub)",
    huggingface_hub: "HF Download",
    requests: "Speedtest",
  };
  const TOOL_SUBTITLES = {
    curl: "HTTPS reachability probes (verify + insecure)",
    wget: "HTTPS reachability probes (verify + insecure)",
    pip: "Package index + download",
    git: "Shallow clone of a public repo",
    huggingface_hub: "Small-model download via HF client",
    requests: "Cloudflare download / upload / latency",
  };

  function domainOf(target) {
    if (!target) return "(unknown)";
    const m = /^https?:\/\/([^/\s:?#]+)/i.exec(target);
    if (m) return m[1];
    // Fallback: take the first whitespace/slash-delimited token.
    return String(target).split(/[\s/]|::/)[0] || String(target);
  }

  function worstStatus(rows) {
    const rank = { pass: 1, warn: 2, fail: 3 };
    let worst = "pass";
    for (const r of rows) {
      if ((rank[r.status] || 0) > (rank[worst] || 0)) worst = r.status;
    }
    return worst;
  }

  function toolSort(a, b) {
    const ai = TOOL_ORDER.indexOf(a), bi = TOOL_ORDER.indexOf(b);
    if (ai === -1 && bi === -1) return a.localeCompare(b);
    if (ai === -1) return 1;
    if (bi === -1) return -1;
    return ai - bi;
  }

  // Keep the currently-rendered groups around so the modal can look up rows.
  let currentGroups = new Map(); // key: `${tool}::${domain}` -> rows[]

  function renderResults(items) {
    // Drop the synthetic connectivity summary rows; they double-count the
    // real curl/wget sub-probes we're about to group.
    const rows = items.filter((r) => r.tool !== "curl+wget");

    // Group by tool, then by domain.
    const byTool = new Map();
    for (const r of rows) {
      if (!byTool.has(r.tool)) byTool.set(r.tool, new Map());
      const byDomain = byTool.get(r.tool);
      const d = domainOf(r.target);
      if (!byDomain.has(d)) byDomain.set(d, []);
      byDomain.get(d).push(r);
    }

    currentGroups = new Map();

    const parts = [];
    const tools = [...byTool.keys()].sort(toolSort);

    for (const tool of tools) {
      const byDomain = byTool.get(tool);
      const toolRows = [...byDomain.values()].flat();
      const tp = toolRows.filter((r) => r.status === "pass").length;
      const tw = toolRows.filter((r) => r.status === "warn").length;
      const tf = toolRows.filter((r) => r.status === "fail").length;

      const domains = [...byDomain.keys()].sort();
      const cards = domains.map((d) => {
        const drs = byDomain.get(d);
        const key = `${tool}::${d}`;
        currentGroups.set(key, drs);

        const st = worstStatus(drs);
        const dp = drs.filter((r) => r.status === "pass").length;
        const dw = drs.filter((r) => r.status === "warn").length;
        const df = drs.filter((r) => r.status === "fail").length;
        const totalMs = drs.reduce((s, r) => s + (r.duration_ms || 0), 0);

        return `
          <button type="button" class="domain-card ${esc(st)}" data-key="${esc(key)}">
            <span class="status-pill ${esc(st)}">${esc(st)}</span>
            <span class="domain-name">${esc(d)}</span>
            <span class="domain-counts">
              ${dp ? `<span class="chip pass" title="passed">${dp}</span>` : ""}
              ${dw ? `<span class="chip warn" title="warnings">${dw}</span>` : ""}
              ${df ? `<span class="chip fail" title="failed">${df}</span>` : ""}
              <span class="domain-total">${drs.length}&nbsp;test${drs.length === 1 ? "" : "s"}</span>
            </span>
            <span class="domain-time">${totalMs}&nbsp;ms</span>
            <span class="domain-chev" aria-hidden="true">›</span>
          </button>
        `;
      });

      parts.push(`
        <details class="tool-section"${tf > 0 ? " open" : ""}>
          <summary>
            <span class="tool-chev" aria-hidden="true">▸</span>
            <div class="tool-title">
              <h2>${esc(TOOL_LABELS[tool] || tool)}</h2>
              <p class="tool-sub">${esc(TOOL_SUBTITLES[tool] || tool)} &middot; ${domains.length} domain${domains.length === 1 ? "" : "s"}</p>
            </div>
            <span class="cat-summary">
              <span class="chip pass">${tp}</span>
              <span class="chip warn">${tw}</span>
              <span class="chip fail">${tf}</span>
              <span class="cat-total">${toolRows.length}</span>
            </span>
          </summary>
          <div class="domain-grid">
            ${cards.join("")}
          </div>
        </details>
      `);
    }

    if (!parts.length) {
      results.innerHTML = `<p class="empty">No results yet. Click &ldquo;Re-run checks&rdquo; to start.</p>`;
      return;
    }
    results.innerHTML = parts.join("");

    // Wire up modal on the new cards.
    results.querySelectorAll(".domain-card").forEach((btn) => {
      btn.addEventListener("click", () => openModal(btn.dataset.key));
    });
  }

  // ---- Details modal ----------------------------------------------------
  const modal = $("modal");
  const modalTitle = $("modal-title");
  const modalSubtitle = $("modal-subtitle");
  const modalBody = $("modal-body");

  function openModal(key) {
    const rows = currentGroups.get(key);
    if (!rows || !rows.length) return;
    const [tool, ...rest] = key.split("::");
    const domain = rest.join("::");

    const dp = rows.filter((r) => r.status === "pass").length;
    const dw = rows.filter((r) => r.status === "warn").length;
    const df = rows.filter((r) => r.status === "fail").length;
    const totalMs = rows.reduce((s, r) => s + (r.duration_ms || 0), 0);

    modalTitle.textContent = `${TOOL_LABELS[tool] || tool} \u00b7 ${domain}`;
    modalSubtitle.innerHTML = `
      <span class="chip pass">${dp} pass</span>
      <span class="chip warn">${dw} warn</span>
      <span class="chip fail">${df} fail</span>
      <span class="modal-count">${rows.length} test${rows.length === 1 ? "" : "s"} &middot; ${totalMs} ms total</span>
    `;
    modalBody.innerHTML = rows.map(testCardHtml).join("");
    modal.hidden = false;
    document.body.classList.add("modal-open");
  }

  function closeModal() {
    modal.hidden = true;
    document.body.classList.remove("modal-open");
  }

  modal.addEventListener("click", (e) => {
    const t = e.target;
    if (t && t.dataset && Object.prototype.hasOwnProperty.call(t.dataset, "close")) {
      closeModal();
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });

  function testCardHtml(r) {
    const output = (r.output || "").trim();
    const attempts = r.attempts || 1;
    return `
      <div class="test-card ${esc(r.status)}">
        <div class="test-head">
          <span class="status-pill ${esc(r.status)}">${esc(r.status)}</span>
          <span class="test-name">${esc(r.name)}</span>
          <span class="test-time">${esc(r.duration_ms)} ms &middot; ${esc(attempts)} attempt${attempts === 1 ? "" : "s"}</span>
        </div>
        <div class="test-detail">${esc(r.detail)}</div>
        <div class="test-target"><span class="test-target-label">target</span> <span class="mono">${esc(r.target)}</span></div>
        ${output ? `<pre class="test-output">${esc(output)}</pre>` : ""}
      </div>
    `;
  }

  poll();
})();
