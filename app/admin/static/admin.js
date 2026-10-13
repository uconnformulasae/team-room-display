'use strict';
/* Small helpers for the admin pages. Every page also works without JavaScript. */

// Ask before deleting: forms with data-confirm="Question?" need a yes first.
// Once a form is really on its way, disable its button so a double tap can't save twice.
document.addEventListener('submit', (event) => {
  const form = event.target;
  if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
    event.preventDefault();
    return;
  }
  if (event.submitter) {
    setTimeout(() => { event.submitter.disabled = true; }, 0); // after the browser has read the form
  }
});
window.addEventListener('pageshow', () => {
  document.querySelectorAll('button:disabled').forEach((button) => { button.disabled = false; });
});
