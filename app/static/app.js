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

  function renderResults(items) {
    const byCat = new Map();
    for (const r of items) {
      if (!byCat.has(r.category)) byCat.set(r.category, []);
      byCat.get(r.category).push(r);
    }

    const parts = [];
    for (const [cat, rows] of byCat.entries()) {
      const pass = rows.filter((r) => r.status === "pass").length;
      const warn = rows.filter((r) => r.status === "warn").length;
      const fail = rows.filter((r) => r.status === "fail").length;
      parts.push(`
        <section class="category">
          <header>
            <h2>${esc(cat)}</h2>
            <span class="cat-summary">${rows.length} checks &middot;
              <span style="color:var(--pass)">${pass} pass</span>,
              <span style="color:var(--warn)">${warn} warn</span>,
              <span style="color:var(--fail)">${fail} fail</span>
            </span>
          </header>
          <table class="results">
            <thead><tr>
              <th>Name</th><th>Tool</th><th>Target</th>
              <th class="numeric">Attempts</th><th class="numeric">Duration</th>
              <th>Status</th><th>Detail</th>
            </tr></thead>
            <tbody>
              ${rows.map(rowHtml).join("")}
            </tbody>
          </table>
        </section>
      `);
    }
    if (!parts.length) {
      results.innerHTML = `<p style="color:var(--muted); text-align:center; padding: 40px;">
        No results yet. Click "Re-run checks" to start.
      </p>`;
      return;
    }
    results.innerHTML = parts.join("");
  }

  function rowHtml(r) {
    const output = (r.output || "").trim();
    const outputHtml = output
      ? `<details class="output"><summary>output</summary><pre>${esc(output)}</pre></details>`
      : "";
    return `
      <tr class="${esc(r.status)}">
        <td>${esc(r.name)}</td>
        <td>${esc(r.tool)}</td>
        <td class="target">${esc(r.target)}</td>
        <td class="numeric">${esc(r.attempts)}</td>
        <td class="numeric">${esc(r.duration_ms)} ms</td>
        <td><span class="status-pill ${esc(r.status)}">${esc(r.status)}</span></td>
        <td class="detail">${esc(r.detail)}${outputHtml}</td>
      </tr>
    `;
  }

  poll();
})();
