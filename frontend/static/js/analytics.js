document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("canvas[id]").forEach(canvas => {
    const configEl = document.getElementById(canvas.id + "-config");
    if (!configEl) return;
    try {
      const config = JSON.parse(configEl.textContent);
      new Chart(canvas, config);
    } catch (e) {
      console.warn("Chart init failed for", canvas.id, e);
    }
  });
});
