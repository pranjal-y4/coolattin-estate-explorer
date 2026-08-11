/**
 * coolattin/static/js/home.js
 *
 * Home-page behaviour that previously lived in an inline <script> in index.html.
 * Moved out so script-src can drop 'unsafe-inline'.
 *
 * exploreOnMap is invoked by dom_actions.js via
 *   data-action="exploreOnMap" data-arg="<townland>" data-arg2="<surname>"
 */
function exploreOnMap(townland, surname) {
  const exploreSection = document.getElementById('explore');
  if (exploreSection) exploreSection.scrollIntoView({ behavior: 'smooth' });

  // Retry until townland options are loaded (data arrives async)
  let attempts = 0;
  function tryApply() {
    attempts++;
    const tlSelect = document.getElementById('townlandSelect');
    const snInput  = document.getElementById('surnameInput');
    const applyBtn = document.getElementById('applyFiltersBtn');
    if (!tlSelect || !snInput || !applyBtn) { if (attempts < 20) setTimeout(tryApply, 200); return; }

    // Options populated = more than just "All townlands"
    if (tlSelect.options.length <= 1 && attempts < 20) { setTimeout(tryApply, 200); return; }

    // Match townland name case-insensitively
    for (let i = 0; i < tlSelect.options.length; i++) {
      if (tlSelect.options[i].value.toLowerCase() === townland.toLowerCase()) {
        tlSelect.value = tlSelect.options[i].value;
        tlSelect.dispatchEvent(new Event('change'));
        break;
      }
    }
    snInput.value = surname;
    applyBtn.click();
  }
  setTimeout(tryApply, 400);
}

window.exploreOnMap = exploreOnMap;
